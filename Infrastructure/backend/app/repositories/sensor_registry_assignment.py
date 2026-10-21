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

from .sensor_registry_channels import CAN_POSITION_RACKS, FLOWER_ROOM, RS485_BED_CAPACITY


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
        * A fifth RS-485 probe on a bed → 409 ``bed_capacity``.
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
        bed = body.bed
        if bed not in RS485_BEDS:
            raise ValidationAPIError(message=f"bed must be one of {list(RS485_BEDS)}")

        bed_rack_id = await conn.fetchval(
            """
            SELECT r.rack_id
            FROM rack r JOIN room f ON f.room_id = r.room_id
            WHERE f.name = $1 AND r.name = $2
            FOR UPDATE OF r
            """,
            FLOWER_ROOM,
            bed,
        )
        if bed_rack_id is None:
            raise ValidationAPIError(message=f"Flower bed {bed!r} does not exist")

        occupied_count = await conn.fetchval(
            """
            SELECT count(*) FROM sensor_registry
            WHERE bus = 'rs485' AND rack_id = $1 AND registry_id <> $2
            """,
            bed_rack_id,
            row["registry_id"],
        )
        if int(occupied_count) >= RS485_BED_CAPACITY:
            raise ConflictError(
                message=f"{bed} already holds {RS485_BED_CAPACITY} probes and is at capacity",
                error_code="bed_capacity",
            )

        if row["device_id"] is not None:
            await conn.execute(
                "UPDATE device SET rack_id = $2 WHERE device_id = $1", row["device_id"], bed_rack_id
            )

        await conn.execute(
            """
            UPDATE sensor_registry
            SET rack_id = $2,
                room_id = NULL,
                location_in_room = NULL,
                updated_at = NOW()
            WHERE registry_id = $1
            """,
            row["registry_id"],
            bed_rack_id,
        )

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
