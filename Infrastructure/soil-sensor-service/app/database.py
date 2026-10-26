"""Database manager for TimescaleDB operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from shared.db import DatabasePoolOwner
from shared.db_batch_writer import insert_measurements_async
from shared.infra_logging import get_logger
from shared.sensor_identity import SENSOR_IDENTITY_LOCK_NAMESPACE

logger = get_logger(__name__)

# A probe silent this long loses its placement (closed as `disconnected`, R9).
ABSENCE = timedelta(minutes=10)


class DatabaseManager(DatabasePoolOwner):
    """Manages TimescaleDB database connections and operations for soil sensor service."""

    def __init__(self, db_config: dict[str, Any] | None = None):
        """Initialize database manager.

        Args:
            db_config: Database connection config dict with host, database, user, password, port.
                      If None, uses environment variables or defaults.
        """
        super().__init__(
            db_config,
            application_name="soil_sensor_service",
            logger=logger,
        )

    async def load_registry_sensors(self) -> list[dict[str, Any]]:
        """Load current RS-485 addresses; detached identities are not pollable."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sr.registry_id, sr.hardware_address, sr.rack_id, rack.name AS bed_name,
                       COALESCE(bed_room.name, room.name) AS room_name,
                       sr.sensor_uid, sr.device_id, sr.identity_status
                FROM sensor_registry sr
                LEFT JOIN rack rack ON rack.rack_id = sr.rack_id
                LEFT JOIN room bed_room ON bed_room.room_id = rack.room_id
                LEFT JOIN room room ON room.room_id = sr.room_id
                WHERE sr.bus = 'rs485' AND sr.hardware_address IS NOT NULL
                ORDER BY sr.hardware_address
                """
            )
        return [
            {
                "registry_id": int(row["registry_id"]),
                "hardware_address": int(row["hardware_address"]),
                "rack_id": int(row["rack_id"]) if row["rack_id"] is not None else None,
                "bed_name": row["bed_name"],
                "room_name": row["room_name"],
                "sensor_uid": row["sensor_uid"],
                "device_id": int(row["device_id"]) if row["device_id"] is not None else None,
                "identity_status": row["identity_status"],
            }
            for row in rows
        ]

    async def _ensure_registry_device(self, conn: Any, row: Any, probe_name: str) -> int:
        device_id = row["device_id"]
        if device_id is None:
            device_id = await conn.fetchval(
                """
                INSERT INTO device (rack_id, name, type, serial_number)
                VALUES ($1, $2, 'RS485 Soil Sensor', 'SENSOR-' || $3::text)
                RETURNING device_id
                """,
                row["rack_id"],
                probe_name,
                str(row["sensor_uid"]),
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

    async def _insert_probe(self, conn: Any, modbus_id: int) -> Any:
        """Register a new confirmed RS-485 identity at ``modbus_id`` (unassigned)."""
        row = await conn.fetchrow(
            """
            INSERT INTO sensor_registry (
                bus, hardware_address, display_name, identity_status,
                first_seen, last_seen, updated_at
            )
            VALUES ('rs485', $1, $2, 'confirmed',
                    clock_timestamp(), clock_timestamp(), clock_timestamp())
            RETURNING registry_id, hardware_address, rack_id, room_id, device_id, sensor_uid,
                      first_seen
            """,
            modbus_id,
            f"soil_sensor_{modbus_id}",
        )
        await conn.execute(
            """
            INSERT INTO sensor_address_history (registry_id, bus, hardware_address, valid_from)
            VALUES ($1, 'rs485', $2, $3)
            """,
            row["registry_id"],
            modbus_id,
            row["first_seen"],
        )
        return row

    async def _lock_probe(self, conn: Any, modbus_id: int) -> Any:
        return await conn.fetchrow(
            """
            SELECT registry_id, hardware_address, rack_id, room_id, device_id, sensor_uid
            FROM sensor_registry
            WHERE bus = 'rs485' AND hardware_address = $1
            FOR UPDATE
            """,
            modbus_id,
        )

    async def _observe_locked(self, conn: Any, row: Any) -> dict[str, Any]:
        """Refresh ``last_seen``, ensure the device and apply the reconnect rule."""
        registry_id = int(row["registry_id"])
        address = int(row["hardware_address"])
        await conn.execute(
            """
            UPDATE sensor_registry
            SET last_seen = clock_timestamp(), updated_at = clock_timestamp()
            WHERE registry_id = $1
            """,
            registry_id,
        )
        device_id = await self._ensure_registry_device(conn, row, f"soil_sensor_{address}")
        has_open = await conn.fetchval(
            "SELECT 1 FROM soil_probe_placement WHERE registry_id = $1 AND removed_at IS NULL",
            registry_id,
        )
        if has_open is None:
            await self._restore_placement(conn, registry_id, address, device_id)
        return await self._probe_entry(conn, registry_id)

    async def _restore_placement(
        self, conn: Any, registry_id: int, address: int, device_id: int
    ) -> None:
        """Return a reconnecting probe to the spot it lost by disconnecting (R9)."""
        last = await conn.fetchrow(
            """
            SELECT room_id, rack_id, x_frac, y_frac, closed_reason
            FROM soil_probe_placement
            WHERE registry_id = $1
            ORDER BY placed_at DESC, placement_id DESC
            LIMIT 1
            """,
            registry_id,
        )
        if last is None or last["closed_reason"] != "disconnected":
            return
        if last["rack_id"] is None:
            await conn.execute(
                """
                INSERT INTO soil_probe_placement (registry_id, room_id, placed_at)
                VALUES ($1, $2, clock_timestamp())
                """,
                registry_id,
                last["room_id"],
            )
            await conn.execute(
                """
                UPDATE sensor_registry
                SET room_id = $2, rack_id = NULL, location_in_room = NULL,
                    updated_at = clock_timestamp()
                WHERE registry_id = $1
                """,
                registry_id,
                last["room_id"],
            )
            await conn.execute("UPDATE device SET rack_id = NULL WHERE device_id = $1", device_id)
            logger.info(f"soil probe {address} returned to Veg Room")
            return

        rack_id = int(last["rack_id"])
        # Serializes with the backend assignment, which locks the same bed row.
        bed_name = await conn.fetchval(
            "SELECT name FROM rack WHERE rack_id = $1 FOR UPDATE", rack_id
        )
        taken = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM soil_probe_placement p
                LEFT JOIN soil_bed_profile b ON b.rack_id = p.rack_id
                WHERE p.rack_id = $1 AND p.removed_at IS NULL AND p.registry_id <> $4
                  AND sqrt(power((p.x_frac - $2) * COALESCE(b.width_ft, 4.0), 2)
                           + power((p.y_frac - $3) * COALESCE(b.length_ft, 4.0), 2)) <= 1.0
            )
            """,
            rack_id,
            last["x_frac"],
            last["y_frac"],
            registry_id,
        )
        if taken:
            logger.info(f"soil probe {address} stays unassigned: spot taken")
            return
        await conn.execute(
            """
            INSERT INTO soil_probe_placement (registry_id, room_id, rack_id, x_frac, y_frac, placed_at)
            VALUES ($1, $2, $3, $4, $5, clock_timestamp())
            """,
            registry_id,
            last["room_id"],
            rack_id,
            last["x_frac"],
            last["y_frac"],
        )
        await conn.execute(
            """
            UPDATE sensor_registry
            SET rack_id = $2, room_id = NULL, location_in_room = NULL,
                updated_at = clock_timestamp()
            WHERE registry_id = $1
            """,
            registry_id,
            rack_id,
        )
        await conn.execute("UPDATE device SET rack_id = $2 WHERE device_id = $1", device_id, rack_id)
        logger.info(
            f"soil probe {address} returned to {bed_name} ({last['x_frac']}, {last['y_frac']})"
        )

    async def _probe_entry(self, conn: Any, registry_id: int) -> dict[str, Any]:
        row = await conn.fetchrow(
            """
            SELECT sr.registry_id, sr.hardware_address, sr.device_id, rack.name AS bed_name,
                   CASE WHEN sr.rack_id IS NOT NULL THEN bed_room.name ELSE room.name END
                       AS room_name
            FROM sensor_registry sr
            LEFT JOIN rack rack ON rack.rack_id = sr.rack_id
            LEFT JOIN room bed_room ON bed_room.room_id = rack.room_id
            LEFT JOIN room room ON room.room_id = sr.room_id
            WHERE sr.registry_id = $1
            """,
            registry_id,
        )
        return {
            "registry_id": int(row["registry_id"]),
            "hardware_address": int(row["hardware_address"]),
            "device_id": int(row["device_id"]),
            "bed_name": row["bed_name"],
            "room_name": row["room_name"],
        }

    async def observe_known_probe(self, modbus_id: int) -> dict[str, Any] | None:
        """Record a response from a registered address; ``None`` when unknown."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await self._lock_probe(conn, modbus_id)
            if row is None:
                return None
            return await self._observe_locked(conn, row)

    async def adopt_probe(self, modbus_id: int) -> dict[str, Any]:
        """Observe ``modbus_id``, registering it unassigned when unknown."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await self._lock_probe(conn, modbus_id)
            if row is not None:
                return await self._observe_locked(conn, row)
            row = await self._insert_probe(conn, modbus_id)
            probe_name = f"soil_sensor_{modbus_id}"
            device_id = await self._ensure_registry_device(conn, row, probe_name)
            await self._ensure_soil_channels(conn, device_id, probe_name)
            logger.info(f"registered soil probe at address {modbus_id}")
            return await self._probe_entry(conn, int(row["registry_id"]))

    async def reserved_addresses(self) -> set[int]:
        """Every RS-485 address ever used by an identity; never reassigned."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT hardware_address FROM sensor_registry
                WHERE bus = 'rs485' AND hardware_address IS NOT NULL
                UNION
                SELECT hardware_address FROM sensor_address_history WHERE bus = 'rs485'
                """
            )
        return {int(row["hardware_address"]) for row in rows}

    async def close_absent_placements(self) -> list[int]:
        """Close placements of probes silent for ``ABSENCE`` as ``disconnected``."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT p.placement_id, p.registry_id, sr.device_id,
                       GREATEST(sr.last_seen, p.placed_at) AS removed_at
                FROM soil_probe_placement p
                JOIN sensor_registry sr ON sr.registry_id = p.registry_id
                WHERE p.removed_at IS NULL
                  AND sr.last_seen < clock_timestamp() - $1::interval
                ORDER BY p.registry_id
                FOR UPDATE OF p, sr
                """,
                ABSENCE,
            )
            for row in rows:
                await conn.execute(
                    """
                    UPDATE soil_probe_placement
                    SET removed_at = $2, closed_reason = 'disconnected'
                    WHERE placement_id = $1
                    """,
                    row["placement_id"],
                    row["removed_at"],
                )
                await conn.execute(
                    """
                    UPDATE sensor_registry
                    SET room_id = NULL, rack_id = NULL, location_in_room = NULL,
                        updated_at = clock_timestamp()
                    WHERE registry_id = $1
                    """,
                    row["registry_id"],
                )
                if row["device_id"] is not None:
                    await conn.execute(
                        "UPDATE device SET rack_id = NULL WHERE device_id = $1", row["device_id"]
                    )
        return [int(row["registry_id"]) for row in rows]

    async def _resolve_current_registry_row(
        self, conn: Any, registry_id: int, modbus_id: int
    ) -> Any:
        """Lock and resolve the live address even if a same-identity merge won."""
        locked_ids = {registry_id}
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock_shared($1::integer, $2::integer)",
            SENSOR_IDENTITY_LOCK_NAMESPACE,
            registry_id,
        )
        while True:
            row = await conn.fetchrow(
                """
                SELECT registry_id, bus, hardware_address, rack_id, device_id, sensor_uid
                FROM sensor_registry
                WHERE bus = 'rs485' AND hardware_address = $1
                """,
                modbus_id,
            )
            if row is None:
                raise RuntimeError(f"RS-485 registry record for address {modbus_id} is unavailable")
            current_id = int(row["registry_id"])
            if current_id in locked_ids:
                return row
            await conn.fetchval(
                "SELECT pg_advisory_xact_lock_shared($1::integer, $2::integer)",
                SENSOR_IDENTITY_LOCK_NAMESPACE,
                current_id,
            )
            locked_ids.add(current_id)

    async def _ensure_soil_channels(
        self, conn: Any, device_id: int, probe_name: str
    ) -> tuple[dict[str, int], dict[str, str]]:
        existing = await conn.fetch(
            """
            SELECT sensor_id, name FROM sensor
            WHERE device_id = $1
              AND (name LIKE '%\\_temperature' OR name LIKE '%\\_humidity'
                   OR name LIKE '%\\_ec' OR name LIKE '%\\_ph')
            """,
            device_id,
        )
        sensor_ids: dict[str, int] = {}
        sensor_names: dict[str, str] = {}
        for sensor in existing:
            name = str(sensor["name"])
            for sensor_type in ("temperature", "humidity", "ec", "ph"):
                if name.endswith(f"_{sensor_type}") and sensor_type not in sensor_ids:
                    sensor_ids[sensor_type] = int(sensor["sensor_id"])
                    sensor_names[sensor_type] = name

        sensor_types = [
            ("temperature", "°C", "temperature"),
            ("humidity", "%", "humidity"),
            ("ec", "µS/cm", "electrical_conductivity"),
            ("ph", "pH", "ph"),
        ]
        for sensor_type, unit, data_type in sensor_types:
            if sensor_type in sensor_ids:
                continue
            sensor_full_name = f"{probe_name}_{sensor_type}"
            sensor = await conn.fetchrow(
                """
                INSERT INTO sensor (device_id, name, unit, data_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (device_id, name) DO UPDATE SET name = EXCLUDED.name
                RETURNING sensor_id, name
                """,
                device_id,
                sensor_full_name,
                unit,
                data_type,
            )
            sensor_ids[sensor_type] = int(sensor["sensor_id"])
            sensor_names[sensor_type] = str(sensor["name"])
            logger.info(f"Registered sensor: {sensor_full_name} (ID: {sensor['sensor_id']})")
        return sensor_ids, sensor_names

    async def register_sensor_device(
        self, registry_id: int, modbus_id: int, probe_name: str
    ) -> tuple[int, int, dict[str, int], dict[str, str]]:
        """Resolve one live address and its metric channels under the identity lock."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await self._resolve_current_registry_row(conn, registry_id, modbus_id)
            device_id = await self._ensure_registry_device(conn, row, probe_name)
            sensor_ids, sensor_names = await self._ensure_soil_channels(conn, device_id, probe_name)
            return int(row["registry_id"]), device_id, sensor_ids, sensor_names

    async def store_measurements(
        self,
        registry_id: int,
        modbus_id: int,
        probe_name: str,
        readings: dict[str, float],
        timestamp: datetime | None = None,
    ) -> tuple[int, dict[str, int], dict[str, str]]:
        """Resolve channels and insert a poll while holding the shared identity lock."""
        if timestamp is None:
            timestamp = datetime.now()

        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                row = await self._resolve_current_registry_row(conn, registry_id, modbus_id)
                device_id = await self._ensure_registry_device(conn, row, probe_name)
                sensor_ids, sensor_names = await self._ensure_soil_channels(
                    conn, device_id, probe_name
                )
                rows = [
                    (timestamp, sensor_id, float(readings[sensor_type]), "ok")
                    for sensor_type, sensor_id in sensor_ids.items()
                    if sensor_type in readings
                ]
                await insert_measurements_async(conn, rows)
                return int(row["registry_id"]), sensor_ids, sensor_names
        except Exception as e:
            logger.error(f"Error storing measurements: {e}")
            raise

    async def get_sensor_id(self, sensor_name: str) -> int | None:
        """Get sensor_id by sensor name."""
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT sensor_id FROM sensor WHERE name = $1", sensor_name
                )
                return row["sensor_id"] if row else None
        except Exception as e:
            logger.error(f"Error getting sensor_id: {e}")
            return None
