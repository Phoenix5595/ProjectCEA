"""Database manager for TimescaleDB operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from shared.db import DatabasePoolOwner
from shared.db_batch_writer import insert_measurements_async
from shared.infra_logging import get_logger
from shared.sensor_identity import SENSOR_IDENTITY_LOCK_NAMESPACE

logger = get_logger(__name__)

# Ten minutes separates a short bus interruption from a possible identity change.
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
                       sr.sensor_uid, sr.device_id, sr.identity_status
                FROM sensor_registry sr
                LEFT JOIN rack rack ON rack.rack_id = sr.rack_id
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

    async def _insert_probe(
        self,
        conn: Any,
        modbus_id: int,
        observed_at: datetime,
        identity_status: str,
        pending_candidate_registry_id: int | None,
        pending_since: datetime | None,
    ) -> Any:
        row = await conn.fetchrow(
            """
            INSERT INTO sensor_registry (
                bus, hardware_address, display_name, identity_status,
                pending_candidate_registry_id, pending_since,
                first_seen, last_seen, updated_at
            )
            VALUES ('rs485', $1, $2, $3, $4, $5, $6, $6, $6)
            RETURNING registry_id, bus, hardware_address, rack_id, device_id, sensor_uid,
                      identity_status, pending_since, last_seen
            """,
            modbus_id,
            f"soil_sensor_{modbus_id}",
            identity_status,
            pending_candidate_registry_id,
            pending_since,
            observed_at,
        )
        await conn.execute(
            """
            INSERT INTO sensor_address_history (registry_id, bus, hardware_address, valid_from)
            VALUES ($1, 'rs485', $2, $3)
            """,
            row["registry_id"],
            modbus_id,
            observed_at,
        )
        return row

    async def observe_probe(self, modbus_id: int) -> dict[str, Any]:
        """Record a real bus response and apply the ten-minute identity rule."""
        pool = await self._get_pool()
        probe_name = f"soil_sensor_{modbus_id}"
        async with pool.acquire() as conn, conn.transaction():
            observed_at = await conn.fetchval("SELECT clock_timestamp()")
            row = await conn.fetchrow(
                """
                SELECT registry_id, bus, hardware_address, rack_id, device_id, sensor_uid,
                       identity_status, pending_since, last_seen
                FROM sensor_registry
                WHERE bus = 'rs485' AND hardware_address = $1
                FOR UPDATE
                """,
                modbus_id,
            )

            if row is not None and row["identity_status"] == "confirmed":
                if row["last_seen"] < observed_at - ABSENCE:
                    registry_id = int(row["registry_id"])
                    # A legacy unmatched row needs a permanent device before
                    # it can be retained as a detached identity candidate.
                    await self._ensure_registry_device(conn, row, probe_name)
                    await conn.execute(
                        """
                        UPDATE sensor_address_history
                        SET valid_to = $2
                        WHERE registry_id = $1 AND valid_to IS NULL
                        """,
                        registry_id,
                        observed_at,
                    )
                    await conn.execute(
                        """
                        UPDATE sensor_registry
                        SET hardware_address = NULL, identity_status = 'detached',
                            pending_candidate_registry_id = NULL, pending_since = NULL,
                            updated_at = $2
                        WHERE registry_id = $1
                        """,
                        registry_id,
                        observed_at,
                    )
                    row = await self._insert_probe(
                        conn,
                        modbus_id,
                        observed_at,
                        "pending",
                        registry_id,
                        observed_at,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        UPDATE sensor_registry
                        SET last_seen = $2, updated_at = $2
                        WHERE registry_id = $1
                        RETURNING registry_id, bus, hardware_address, rack_id, device_id,
                                  sensor_uid, identity_status, pending_since, last_seen
                        """,
                        row["registry_id"],
                        observed_at,
                    )
            elif row is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE sensor_registry
                    SET last_seen = $2, updated_at = $2
                    WHERE registry_id = $1
                    RETURNING registry_id, bus, hardware_address, rack_id, device_id,
                              sensor_uid, identity_status, pending_since, last_seen
                    """,
                    row["registry_id"],
                    observed_at,
                )
            else:
                # A linked row is a permanent identity; placement is separate.
                stale = await conn.fetch(
                    """
                    SELECT registry_id
                    FROM sensor_registry
                    WHERE bus = 'rs485' AND device_id IS NOT NULL
                      AND identity_status IN ('confirmed', 'detached')
                      AND last_seen < $1::timestamptz - $2::interval
                    ORDER BY registry_id
                    """,
                    observed_at,
                    ABSENCE,
                )
                identity_status = "pending" if stale else "confirmed"
                candidate_id = int(stale[0]["registry_id"]) if len(stale) == 1 else None
                pending_since = observed_at if stale else None
                row = await self._insert_probe(
                    conn,
                    modbus_id,
                    observed_at,
                    identity_status,
                    candidate_id,
                    pending_since,
                )

            device_id = await self._ensure_registry_device(conn, row, probe_name)
            current = await conn.fetchrow(
                """
                SELECT sr.registry_id, sr.hardware_address, sr.rack_id, sr.sensor_uid,
                       sr.identity_status, sr.pending_candidate_registry_id,
                       sr.pending_since, sr.last_seen, rack.name AS bed_name
                FROM sensor_registry sr
                LEFT JOIN rack rack ON rack.rack_id = sr.rack_id
                WHERE sr.registry_id = $1
                """,
                row["registry_id"],
            )
            return {
                "registry_id": int(current["registry_id"]),
                "hardware_address": int(current["hardware_address"]),
                "rack_id": int(current["rack_id"]) if current["rack_id"] is not None else None,
                "bed_name": current["bed_name"],
                "sensor_uid": current["sensor_uid"],
                "device_id": device_id,
                "identity_status": current["identity_status"],
                "pending_candidate_registry_id": current["pending_candidate_registry_id"],
                "pending_since": current["pending_since"],
                "last_seen": current["last_seen"],
            }

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
