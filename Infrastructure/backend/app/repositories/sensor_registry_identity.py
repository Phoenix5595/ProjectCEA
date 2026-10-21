from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.middleware.exception_handler import ConflictError, NotFoundError
from app.sensor_registry_models import (
    IdentityRequest,
    NewIdentityRequest,
    SameIdentityRequest,
    SensorRegistryRecord,
)
from shared.sensor_identity import SENSOR_IDENTITY_LOCK_NAMESPACE

from .sensor_registry_channels import CAN_POSITION_RACKS, SensorRegistryChannelsMixin

# Keep the same threshold as soil and CAN discovery: brief outages are not
# sufficient evidence to reinterpret a device's physical identity.
ABSENCE = timedelta(minutes=10)


class SensorRegistryIdentityMixin:
    async def resolve_identity(
        self, registry_id: int, body: IdentityRequest
    ) -> SensorRegistryRecord:
        """Confirm a pending address as either an older identity or a new one."""
        if isinstance(body, NewIdentityRequest):
            async with self._acquire() as conn, conn.transaction():
                pending = await conn.fetchrow(
                    """
                    SELECT identity_status
                    FROM sensor_registry
                    WHERE registry_id = $1
                    FOR UPDATE
                    """,
                    registry_id,
                )
                if pending is None:
                    raise NotFoundError(
                        message=f"Registry record {registry_id} not found",
                        error_code="registry_not_found",
                    )
                if pending["identity_status"] != "pending":
                    raise ConflictError(
                        message="This registry record has no pending identity decision.",
                        error_code="identity_not_pending",
                    )
                await conn.execute(
                    """
                    UPDATE sensor_registry
                    SET identity_status = 'confirmed',
                        pending_candidate_registry_id = NULL,
                        pending_since = NULL,
                        updated_at = clock_timestamp()
                    WHERE registry_id = $1
                    """,
                    registry_id,
                )
            return await self.get_record(registry_id)

        assert isinstance(body, SameIdentityRequest)
        target_registry_id = body.target_registry_id
        locked_ids = sorted({registry_id, target_registry_id})
        async with self._acquire() as conn, conn.transaction():
            for locked_id in locked_ids:
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock($1::integer, $2::integer)",
                    SENSOR_IDENTITY_LOCK_NAMESPACE,
                    locked_id,
                )
            rows = await conn.fetch(
                """
                SELECT sr.registry_id, sr.bus, sr.hardware_address, sr.sensor_uid,
                       sr.identity_status, sr.pending_candidate_registry_id,
                       sr.pending_since, sr.last_seen, sr.device_id, sr.display_name,
                       sr.room_id, sr.rack_id, sr.location_in_room, room.name AS room_name
                FROM sensor_registry sr
                LEFT JOIN room room ON room.room_id = sr.room_id
                WHERE sr.registry_id = ANY($1::bigint[])
                ORDER BY sr.registry_id
                FOR UPDATE OF sr
                """,
                locked_ids,
            )
            by_id = {int(row["registry_id"]): row for row in rows}
            pending = by_id.get(registry_id)
            if pending is None:
                raise NotFoundError(
                    message=f"Registry record {registry_id} not found",
                    error_code="registry_not_found",
                )
            if pending["identity_status"] != "pending" or pending["pending_since"] is None:
                raise ConflictError(
                    message="This registry record has no pending identity decision.",
                    error_code="identity_not_pending",
                )

            target = by_id.get(target_registry_id)
            target_eligible = (
                target is not None
                and target_registry_id != registry_id
                and target["bus"] == pending["bus"]
                and target["identity_status"] == "detached"
            )
            if (
                target is not None
                and target["bus"] == pending["bus"]
                and target["identity_status"] == "confirmed"
            ):
                target_eligible = bool(
                    await conn.fetchval(
                        """
                        SELECT $1::timestamptz < clock_timestamp() - $2::interval
                        """,
                        target["last_seen"],
                        ABSENCE,
                    )
                )
            if not target_eligible:
                raise ConflictError(
                    message="The selected registry record is not an eligible identity target.",
                    error_code="target_not_eligible",
                )

            pending_since = pending["pending_since"]
            pending_device_id = await self._ensure_identity_device(conn, pending)
            first_measurement = await conn.fetchval(
                """
                SELECT min(m.time)
                FROM measurement AS m
                JOIN sensor AS s ON s.sensor_id = m.sensor_id
                WHERE s.device_id = $1
                """,
                pending_device_id,
            )
            merge_from = (
                min(pending_since, first_measurement)
                if first_measurement is not None
                else pending_since
            )
            archived = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.archive_availability
                    WHERE source_table = 'measurement'
                      AND source_rows_removed
                      AND start_at < clock_timestamp()
                      AND end_at > $1
                )
                """,
                merge_from,
            )
            if archived:
                raise ConflictError(
                    message="The pending identity range includes measurements already removed by archive deletion.",
                    error_code="identity_range_archived",
                )

            await conn.execute("SET LOCAL lock_timeout = '1s'")
            await conn.fetch(
                """
                SELECT decompress_chunk(
                    format('%I.%I', c.chunk_schema, c.chunk_name)::regclass,
                    if_compressed => true
                )
                FROM timescaledb_information.chunks AS c
                WHERE c.hypertable_schema = 'public'
                  AND c.hypertable_name = 'measurement'
                  AND c.is_compressed
                  AND c.range_start < clock_timestamp()
                  AND c.range_end > $1
                """,
                merge_from,
            )

            target_device_id = await self._ensure_identity_device(conn, target)
            channel_pairs = await self._identity_channel_pairs(
                conn, pending, target, pending_device_id, target_device_id
            )
            for pending_sensor_id, target_sensor_id in channel_pairs:
                await conn.execute(
                    """
                    UPDATE measurement
                    SET sensor_id = $2
                    WHERE sensor_id = $1
                    """,
                    pending_sensor_id,
                    target_sensor_id,
                )

            if pending["hardware_address"] is None:
                raise ConflictError(
                    message="A pending identity has no current hardware address.",
                    error_code="identity_not_pending",
                )
            if target["hardware_address"] is not None:
                await conn.execute(
                    """
                    UPDATE sensor_address_history
                    SET valid_to = $2
                    WHERE registry_id = $1 AND valid_to IS NULL
                    """,
                    target_registry_id,
                    pending_since,
                )

            await conn.execute("DELETE FROM sensor WHERE device_id = $1", pending_device_id)
            await conn.execute("DELETE FROM device WHERE device_id = $1", pending_device_id)
            await conn.execute("DELETE FROM sensor_registry WHERE registry_id = $1", registry_id)
            await conn.execute(
                """
                UPDATE sensor_registry
                SET hardware_address = $2,
                    identity_status = 'confirmed',
                    pending_candidate_registry_id = NULL,
                    pending_since = NULL,
                    last_seen = $3,
                    updated_at = clock_timestamp()
                WHERE registry_id = $1
                """,
                target_registry_id,
                pending["hardware_address"],
                pending["last_seen"],
            )
            await conn.execute(
                """
                INSERT INTO sensor_address_history (
                    registry_id, bus, hardware_address, valid_from
                )
                VALUES ($1, $2, $3, $4)
                """,
                target_registry_id,
                pending["bus"],
                pending["hardware_address"],
                pending_since,
            )

        return await self.get_record(target_registry_id)

    async def _ensure_identity_device(self, conn: Any, row: Any) -> int:
        device_id = row["device_id"]
        if device_id is not None:
            return int(device_id)

        if row["bus"] == "can" and row["room_id"] is not None:
            rack_id = await conn.fetchval(
                """
                SELECT rack_id
                FROM rack
                WHERE room_id = $1 AND name = $2
                """,
                row["room_id"],
                CAN_POSITION_RACKS.get(row["location_in_room"], "Main"),
            )
        else:
            rack_id = row["rack_id"]
        device_type = "sensor-node" if row["bus"] == "can" else "RS485 Soil Sensor"
        device_id = await conn.fetchval(
            """
            INSERT INTO device (rack_id, name, type, serial_number)
            VALUES ($1, $2, $3, $4)
            RETURNING device_id
            """,
            rack_id,
            row["display_name"],
            device_type,
            f"SENSOR-{row['sensor_uid']}",
        )
        await conn.execute(
            """
            UPDATE sensor_registry
            SET device_id = $2, updated_at = clock_timestamp()
            WHERE registry_id = $1 AND device_id IS NULL
            """,
            row["registry_id"],
            device_id,
        )
        return int(device_id)

    async def _identity_channel_pairs(
        self,
        conn: Any,
        pending: Any,
        target: Any,
        pending_device_id: int,
        target_device_id: int,
    ) -> list[tuple[int, int]]:
        pending_channels = await conn.fetch(
            """
            SELECT sensor_id, name, unit, data_type, channel, calibration_offset
            FROM sensor
            WHERE device_id = $1
            ORDER BY sensor_id
            """,
            pending_device_id,
        )
        target_channels = await conn.fetch(
            """
            SELECT sensor_id, name, unit, data_type, channel, calibration_offset
            FROM sensor
            WHERE device_id = $1
            ORDER BY sensor_id
            """,
            target_device_id,
        )
        target_by_key: dict[tuple[str, int | None], list[Any]] = {}
        for channel in target_channels:
            key = self._identity_channel_key(target["bus"], channel)
            target_by_key.setdefault(key, []).append(channel)

        pairs: list[tuple[int, int]] = []
        seen_keys: set[tuple[str, int | None]] = set()
        target_names = {channel["name"] for channel in target_channels}
        for channel in pending_channels:
            key = self._identity_channel_key(pending["bus"], channel)
            if key in seen_keys:
                raise ConflictError(
                    message="The pending device has ambiguous metric channels.",
                    error_code="identity_channel_mismatch",
                )
            seen_keys.add(key)
            matches = target_by_key.get(key, [])
            if len(matches) > 1:
                raise ConflictError(
                    message="The target device has ambiguous metric channels.",
                    error_code="identity_channel_mismatch",
                )
            if matches:
                pairs.append((int(channel["sensor_id"]), int(matches[0]["sensor_id"])))
                continue

            target_name = self._identity_target_channel_name(target, channel)
            if target_name in target_names:
                raise ConflictError(
                    message="A target metric name conflicts with a different channel type.",
                    error_code="identity_channel_mismatch",
                )
            target_sensor_id = await conn.fetchval(
                """
                INSERT INTO sensor (
                    device_id, name, unit, data_type, channel, calibration_offset
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING sensor_id
                """,
                target_device_id,
                target_name,
                channel["unit"],
                channel["data_type"],
                channel["channel"],
                channel["calibration_offset"],
            )
            target_channel = {
                "sensor_id": int(target_sensor_id),
                "name": target_name,
                "unit": channel["unit"],
                "data_type": channel["data_type"],
                "channel": channel["channel"],
            }
            target_by_key.setdefault(key, []).append(target_channel)
            target_names.add(target_name)
            pairs.append((int(channel["sensor_id"]), int(target_sensor_id)))
        return pairs

    @staticmethod
    def _identity_channel_key(bus: str, channel: Any) -> tuple[str, int | None]:
        name = str(channel["name"])
        channel_number = channel["channel"]
        if bus == "can":
            return SensorRegistryChannelsMixin._channel_base(name) or name, channel_number
        for suffix in ("temperature", "humidity", "ec", "ph"):
            if name.endswith(f"_{suffix}"):
                return suffix, channel_number
        return str(channel["data_type"]), channel_number

    def _identity_target_channel_name(self, target: Any, channel: Any) -> str:
        name = str(channel["name"])
        if target["bus"] == "rs485":
            for suffix in ("temperature", "humidity", "ec", "ph"):
                if name.endswith(f"_{suffix}"):
                    return f"{target['display_name']}_{suffix}"
            return name

        base = self._channel_base(name)
        if base is None:
            return name
        suffix = ""
        if target["room_name"] is not None and target["location_in_room"] is not None:
            suffix = self._canonical_suffix(target["room_name"], target["location_in_room"])
        return f"{base}{suffix}"
