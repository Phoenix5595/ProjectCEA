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

    async def ensure_hierarchy(self, room_name: str, bed_name: str) -> tuple[int, int]:
        """
        Ensure room/bed exist in database, create if needed.

        Returns:
            Tuple of (room_id, rack_id)
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get or create room
                room_row = await conn.fetchrow(
                    "SELECT room_id FROM room WHERE name = $1", room_name
                )
                if room_row:
                    room_id = room_row["room_id"]
                else:
                    room_id = await conn.fetchval(
                        "INSERT INTO room (name) VALUES ($1) RETURNING room_id", room_name
                    )
                    logger.info(f"Created room: {room_name}")

                # Get or create rack (bed)
                rack_row = await conn.fetchrow(
                    "SELECT rack_id FROM rack WHERE room_id = $1 AND name = $2", room_id, bed_name
                )
                if rack_row:
                    rack_id = rack_row["rack_id"]
                else:
                    rack_id = await conn.fetchval(
                        "INSERT INTO rack (room_id, name) VALUES ($1, $2) RETURNING rack_id",
                        room_id,
                        bed_name,
                    )
                    logger.info(f"Created bed (rack): {bed_name}")

                return room_id, rack_id

    async def register_sensor_device(
        self, rack_id: int, sensor_name: str, modbus_id: int, bed_name: str
    ) -> tuple[int, dict[str, int]]:
        """
        Register a soil sensor device and its 4 sensors in the database.

        Args:
            rack_id: Rack (bed) ID
            sensor_name: Base name for the sensor (e.g., "soil_sensor_front_bed")
            modbus_id: Modbus slave ID
            bed_name: Bed name for device description

        Returns:
            Tuple of (device_id, dict mapping sensor_type to sensor_id)
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check if device already exists
                device_row = await conn.fetchrow(
                    "SELECT device_id FROM device WHERE rack_id = $1 AND name = $2",
                    rack_id,
                    f"Soil Sensor - {bed_name}",
                )

                if device_row:
                    device_id = device_row["device_id"]
                    logger.info(
                        f"Device already exists: Soil Sensor - {bed_name} (ID: {device_id})"
                    )
                else:
                    # Create device
                    device_id = await conn.fetchval(
                        """INSERT INTO device (rack_id, name, type, serial_number)
                           VALUES ($1, $2, $3, $4) RETURNING device_id""",
                        rack_id,
                        f"Soil Sensor - {bed_name}",
                        "RS485 Soil Sensor",
                        f"MODBUS-{modbus_id}",
                    )
                    logger.info(f"Created device: Soil Sensor - {bed_name} (ID: {device_id})")

                # Register 4 sensors
                sensor_types = [
                    ("temperature", "°C", "temperature"),
                    ("humidity", "%", "humidity"),
                    ("ec", "µS/cm", "electrical_conductivity"),
                    ("ph", "pH", "ph"),
                ]

                sensor_ids = {}
                for sensor_type, unit, data_type in sensor_types:
                    sensor_full_name = f"{sensor_name}_{sensor_type}"

                    # Check if sensor already exists
                    sensor_row = await conn.fetchrow(
                        "SELECT sensor_id FROM sensor WHERE device_id = $1 AND name = $2",
                        device_id,
                        sensor_full_name,
                    )

                    if sensor_row:
                        sensor_ids[sensor_type] = sensor_row["sensor_id"]
                    else:
                        sensor_id = await conn.fetchval(
                            """INSERT INTO sensor (device_id, name, unit, data_type)
                               VALUES ($1, $2, $3, $4) RETURNING sensor_id""",
                            device_id,
                            sensor_full_name,
                            unit,
                            data_type,
                        )
                        sensor_ids[sensor_type] = sensor_id
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
