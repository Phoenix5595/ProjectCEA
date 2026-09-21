"""Database manager for TimescaleDB operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from shared.db import create_pool, db_config_from_env
from shared.db_batch_writer import insert_measurements_async
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for soil sensor service."""

    def __init__(self, db_config: dict[str, Any] | None = None):
        """Initialize database manager.

        Args:
            db_config: Database connection config dict with host, database, user, password, port.
                      If None, uses environment variables or defaults.
        """
        self.db_config = db_config if db_config is not None else db_config_from_env()
        self._pool: asyncpg.Pool | None = None
        self._db_connected = False

    async def initialize(self) -> bool:
        """Initialize database connection.

        Returns:
            True if successful, False otherwise
        """
        try:
            await self._connect_db()
            return True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            return False

    async def _connect_db(self) -> None:
        """Connect to TimescaleDB; retry/backoff handled by shared.db.create_pool."""
        self._pool = await create_pool(self.db_config, application_name="soil_sensor_service")
        self._db_connected = True

    async def _get_pool(self) -> asyncpg.Pool:
        """Get database connection pool."""
        pool = self._pool
        if not pool:
            await self._connect_db()
            pool = self._pool
        if not pool:
            raise RuntimeError("Failed to connect to database")
        return pool

    async def close(self) -> None:
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
            self._db_connected = False
            logger.info("Database connection closed")

    async def load_registry_sensors(self) -> list[dict[str, Any]]:
        """Load all known RS-485 probes from sensor_registry.

        Returns dicts ordered by numeric hardware address:
        {registry_id, hardware_address, rack_id, bed_name}
        where rack_id/bed_name are None while the probe is unassigned.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sr.registry_id, sr.hardware_address, sr.rack_id, rack.name AS bed_name
                FROM sensor_registry sr
                LEFT JOIN rack rack ON rack.rack_id = sr.rack_id
                WHERE sr.bus = 'rs485'
                ORDER BY sr.hardware_address
                """
            )
        return [
            {
                "registry_id": int(row["registry_id"]),
                "hardware_address": int(row["hardware_address"]),
                "rack_id": int(row["rack_id"]) if row["rack_id"] is not None else None,
                "bed_name": row["bed_name"],
            }
            for row in rows
        ]

    async def upsert_unassigned_probe(self, modbus_id: int) -> int | None:
        """Upsert a discovered Modbus address as an unassigned registry row."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO sensor_registry (bus, hardware_address, display_name)
                VALUES ('rs485', $1, $2)
                ON CONFLICT (bus, hardware_address) DO NOTHING
                RETURNING registry_id
                """,
                modbus_id,
                f"soil_sensor_{modbus_id}",
            )

    async def register_sensor_device(
        self, serial_number: str, probe_name: str
    ) -> tuple[int, dict[str, int]]:
        """
        Register a soil-probe device keyed by serial_number and its 4 metric
        channels. Devices are never named by bed, so multiple probes in one
        bed never collapse into one device.

        Existing devices keep whatever channel names they already have
        (deployed naming); fresh devices get canonical ``{probe_name}_{type}``
        channels.

        Args:
            serial_number: unique probe serial (e.g. "MODBUS-226")
            probe_name: canonical probe name used for fresh channels

        Returns:
            Tuple of (device_id, dict mapping sensor_type to sensor_id)
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            device_row = await conn.fetchrow(
                "SELECT device_id FROM device WHERE serial_number = $1", serial_number
            )

            if device_row:
                device_id = device_row["device_id"]
            else:
                device_id = await conn.fetchval(
                    """INSERT INTO device (rack_id, name, type, serial_number)
                           VALUES (NULL, $2, $3, $4) RETURNING device_id""",
                    probe_name,
                    "RS485 Soil Sensor",
                    serial_number,
                )
                logger.info(f"Created device: {probe_name} (ID: {device_id})")

            # Reuse whatever metric channels already exist on this device,
            # keyed by their canonical suffix.
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
            for row in existing:
                name: str = row["name"]
                for wire_key, sensor_type in (
                    ("temperature", "temperature"),
                    ("humidity", "humidity"),
                    ("ec", "ec"),
                    ("ph", "ph"),
                ):
                    if name.endswith(f"_{wire_key}") and wire_key not in sensor_ids:
                        sensor_ids[wire_key] = int(row["sensor_id"])

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
                sensor_id = await conn.fetchval(
                    """INSERT INTO sensor (device_id, name, unit, data_type)
                           VALUES ($1, $2, $3, $4) RETURNING sensor_id""",
                    device_id,
                    sensor_full_name,
                    unit,
                    data_type,
                )
                sensor_ids[sensor_type] = int(sensor_id)
                logger.info(f"Registered sensor: {sensor_full_name} (ID: {sensor_id})")

            return device_id, sensor_ids

    async def store_measurements(
        self,
        sensor_ids: dict[str, int],
        readings: dict[str, float],
        timestamp: datetime | None = None,
    ) -> bool:
        """
        Store sensor measurements in the database.

        Args:
            sensor_ids: Dict mapping sensor_type to sensor_id
            readings: Dict with temperature, humidity, ec, ph values
            timestamp: Timestamp for measurements (defaults to now)

        Returns:
            True if successful, False otherwise
        """
        if timestamp is None:
            timestamp = datetime.now()

        pool = await self._get_pool()
        rows = [
            (timestamp, sensor_id, float(readings[sensor_type]), "ok")
            for sensor_type, sensor_id in sensor_ids.items()
            if sensor_type in readings
        ]
        try:
            await insert_measurements_async(pool, rows)
            return True
        except Exception as e:
            logger.error(f"Error storing measurements: {e}")
            return False

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
