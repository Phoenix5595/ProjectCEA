from __future__ import annotations

from typing import Any

from app.middleware.exception_handler import (
    ConflictError,
    NotFoundError,
    ValidationAPIError,
)
from app.sensor_registry_models import (
    RS485_BEDS,
    CanAssignmentRequest,
    Rs485AssignmentRequest,
    SensorRegistryRecord,
)
from shared.cluster_topology import known_rooms, sensor_name_like_pattern, sensor_url_clusters_for

from .sensor_registry_channels import CAN_POSITION_RACKS, FLOWER_ROOM, VEG_ROOM


class SensorRegistryAssignmentMixin:
    # ------------------------------------------------------------------
    # Assignment (one transaction; lock the target rack/position)
    # ------------------------------------------------------------------

    async def assign(
        self, registry_id: int, body: CanAssignmentRequest | Rs485AssignmentRequest
    ) -> SensorRegistryRecord:
        """Validate and apply an assignment atomically.

        * Body discriminator must match the stored bus (422 otherwise).
        * CAN positions must satisfy the shared topology (422 otherwise).
        * RS-485 placements close the open ``soil_probe_placement`` row as
          ``moved`` and insert the new one with one shared timestamp.
        * An occupied CAN room position → 409 ``position_occupied``.
        * CAN channel renames that would collide → 409 ``channel_name_conflict``
          with the assignment left unchanged.
        """
        async with self._acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT registry_id, bus, room_id, rack_id, location_in_room, device_id
                FROM sensor_registry
                WHERE registry_id = $1
                FOR UPDATE
                """,
                registry_id,
            )
            if row is None:
                raise NotFoundError(message=f"Registry record {registry_id} not found")

            stored_bus: str = row["bus"]
            if isinstance(body, CanAssignmentRequest):
                if stored_bus != "can":
                    raise ValidationAPIError(
                        message=f"Registry record {registry_id} is an RS-485 probe; "
                        "a CAN assignment body is not valid for it"
                    )
                await self._assign_can(conn, row, body)
            else:
                assert isinstance(body, Rs485AssignmentRequest)
                if stored_bus != "rs485":
                    raise ValidationAPIError(
                        message=f"Registry record {registry_id} is a CAN node; "
                        "an RS-485 bed assignment body is not valid for it"
                    )
                await self._assign_rs485(conn, row, body)

        return await self.get_record(registry_id)

    async def _assign_can(self, conn: Any, row: Any, body: CanAssignmentRequest) -> None:
        room = body.room
        location = body.location_in_room

        if room not in known_rooms():
            raise ValidationAPIError(
                message=f"Unknown room {room!r}; known rooms: {sorted(known_rooms())}"
            )
        valid_locations = sensor_url_clusters_for(room)
        if location not in valid_locations:
            raise ValidationAPIError(
                message=f"{location!r} is not a controllable position for {room!r}; "
                f"valid options: {list(valid_locations)}"
            )

        rack_id = await conn.fetchval(
            """
            SELECT r.rack_id
            FROM rack r JOIN room f ON f.room_id = r.room_id
            WHERE f.name = $1 AND r.name = $2
            FOR UPDATE OF r
            """,
            room,
            CAN_POSITION_RACKS[location],
        )
        if rack_id is None:
            rack_id = await conn.fetchval(
                """
                INSERT INTO rack (room_id, name)
                SELECT room_id, $2 FROM room WHERE name = $1
                RETURNING rack_id
                """,
                room,
                CAN_POSITION_RACKS[location],
            )
        if rack_id is None:
            raise ValidationAPIError(message=f"Room {room!r} does not exist")

        occupied = await conn.fetchval(
            """
            SELECT registry_id FROM sensor_registry
            WHERE bus = 'can'
              AND room_id = (SELECT room_id FROM room WHERE name = $1)
              AND location_in_room = $2
              AND registry_id <> $3
            """,
            room,
            location,
            row["registry_id"],
        )
        if occupied is not None:
            raise ConflictError(
                message=f"{room!r} position {location!r} is already held by registry record {occupied}",
                error_code="position_occupied",
            )

        device_id = row["device_id"]
        if device_id is not None:
            await self._rename_can_channels(
                conn, int(device_id), self._canonical_suffix(room, location)
            )
            await conn.execute(
                "UPDATE device SET rack_id = $2 WHERE device_id = $1", device_id, rack_id
            )

        await conn.execute(
            """
            UPDATE sensor_registry
            SET room_id = (SELECT room_id FROM room WHERE name = $2),
                location_in_room = $3,
                rack_id = NULL,
                updated_at = NOW()
            WHERE registry_id = $1
            """,
            row["registry_id"],
            room,
            location,
        )

    async def _assign_rs485(self, conn: Any, row: Any, body: Rs485AssignmentRequest) -> None:
        registry_id = row["registry_id"]
        if body.room == FLOWER_ROOM:
            if body.bed not in RS485_BEDS:
                raise ValidationAPIError(message=f"bed must be one of {list(RS485_BEDS)}")
            target = await conn.fetchrow(
                """
                SELECT r.rack_id, r.room_id
                FROM rack r JOIN room f ON f.room_id = r.room_id
                WHERE f.name = $1 AND r.name = $2
                FOR UPDATE OF r
                """,
                FLOWER_ROOM,
                body.bed,
            )
            if target is None:
                raise ValidationAPIError(message=f"Flower bed {body.bed!r} does not exist")
            room_id, rack_id = target["room_id"], target["rack_id"]
        else:
            room_id = await conn.fetchval("SELECT room_id FROM room WHERE name = $1", VEG_ROOM)
            if room_id is None:
                raise ValidationAPIError(message=f"Room {VEG_ROOM!r} does not exist")
            rack_id = None

        ts = await conn.fetchval("SELECT clock_timestamp()")
        await conn.execute(
            """
            UPDATE soil_probe_placement
            SET removed_at = $2, closed_reason = 'moved'
            WHERE registry_id = $1 AND removed_at IS NULL
            """,
            registry_id,
            ts,
        )
        await conn.execute(
            """
            INSERT INTO soil_probe_placement (registry_id, room_id, rack_id, x_frac, y_frac, placed_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            registry_id,
            room_id,
            rack_id,
            body.x_frac,
            body.y_frac,
            ts,
        )
        await conn.execute(
            """
            UPDATE sensor_registry
            SET rack_id = $2,
                room_id = $3,
                location_in_room = NULL,
                updated_at = NOW()
            WHERE registry_id = $1
            """,
            registry_id,
            rack_id,
            None if rack_id is not None else room_id,
        )
        if row["device_id"] is not None:
            await conn.execute(
                "UPDATE device SET rack_id = $2 WHERE device_id = $1", row["device_id"], rack_id
            )

    async def unassign(self, registry_id: int) -> SensorRegistryRecord:
        """Move an RS-485 probe to Unassigned, closing its placement."""
        async with self._acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT registry_id, bus, device_id
                FROM sensor_registry
                WHERE registry_id = $1
                FOR UPDATE
                """,
                registry_id,
            )
            if row is None:
                raise NotFoundError(message=f"Registry record {registry_id} not found")
            if row["bus"] != "rs485":
                raise ConflictError(
                    message=f"Registry record {registry_id} is a CAN node; only RS-485 probes "
                    "are unassigned here",
                    error_code="not_rs485",
                )
            await conn.execute(
                """
                UPDATE soil_probe_placement
                SET removed_at = clock_timestamp(), closed_reason = 'unassigned'
                WHERE registry_id = $1 AND removed_at IS NULL
                """,
                registry_id,
            )
            await conn.execute(
                """
                UPDATE sensor_registry
                SET room_id = NULL, rack_id = NULL, location_in_room = NULL, updated_at = NOW()
                WHERE registry_id = $1
                """,
                registry_id,
            )
            if row["device_id"] is not None:
                await conn.execute(
                    "UPDATE device SET rack_id = NULL WHERE device_id = $1", row["device_id"]
                )
        return await self.get_record(registry_id)

    @staticmethod
    def _canonical_suffix(room: str, location: str) -> str:
        pattern = sensor_name_like_pattern(room, location)
        if pattern and pattern.startswith("%"):
            return pattern[1:]
        return ""

    async def _rename_can_channels(self, conn: Any, device_id: int, suffix: str) -> None:
        """Rename the device's known metric channels to the canonical suffix.

        Strips an existing ``_f``/``_b``/``_v`` suffix from a known base and
        applies the target suffix. Any channel outside the known base list,
        or a pre-existing same-device channel with the target name, raises
        ``channel_name_conflict`` — the transaction keeps the assignment.
        """
        rows = await conn.fetch(
            "SELECT sensor_id, name FROM sensor WHERE device_id = $1", device_id
        )

        renames: list[tuple[int, str, str]] = []
        for row in rows:
            name: str = row["name"]
            base = self._channel_base(name)
            if base is None:
                raise ConflictError(
                    message=f"Device {device_id} channel {name!r} is outside the canonical "
                    "CAN channel list; refusing to rename",
                    error_code="channel_name_conflict",
                )
            target = f"{base}{suffix}"
            if target != name:
                renames.append((int(row["sensor_id"]), name, target))

        renamed_ids = [sensor_id for sensor_id, _old, _target in renames]
        for _sensor_id, _old, target in renames:
            clash = await conn.fetchval(
                """
                SELECT 1 FROM sensor
                WHERE device_id = $1 AND name = $2
                  AND NOT (sensor_id = ANY($3::int[]))
                """,
                device_id,
                target,
                renamed_ids,
            )
            if clash is not None:
                raise ConflictError(
                    message=f"Device {device_id} already has a channel named {target!r}; "
                    "renaming would merge two metrics",
                    error_code="channel_name_conflict",
                )

        for sensor_id, _old, target in renames:
            await conn.execute(
                "UPDATE sensor SET name = $2 WHERE sensor_id = $1", sensor_id, target
            )
