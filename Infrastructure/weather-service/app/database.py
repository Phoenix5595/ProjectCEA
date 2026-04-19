"""Database manager for TimescaleDB operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from shared.db import create_pool, db_config_from_env
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for weather service."""

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
        self._pool = await create_pool(self.db_config, application_name="weather_service")
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

    async def ensure_hierarchy(self, room_name: str, device_name: str) -> tuple[int, int]:
        """
        Ensure Outside room and weather device exist in database, create if needed.

        Args:
            room_name: Room name (should be "Outside")
            device_name: Device name (e.g., "Weather Station YUL")

        Returns:
            Tuple of (room_id, device_id)
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

                # Get or create device (no rack needed for weather station)
                device_row = await conn.fetchrow(
                    """SELECT device_id FROM device
                       WHERE rack_id IS NULL AND name = $1""",
                    device_name,
                )

                if device_row:
                    device_id = device_row["device_id"]
                    logger.info(f"Device already exists: {device_name} (ID: {device_id})")
                else:
                    # Create device (rack_id is NULL for weather station)
                    device_id = await conn.fetchval(
                        """INSERT INTO device (rack_id, name, type)
                           VALUES ($1, $2, $3) RETURNING device_id""",
                        None,  # No rack for weather station
                        device_name,
                        "Weather Station",
                    )
                    logger.info(f"Created device: {device_name} (ID: {device_id})")

                return room_id, device_id

    async def register_weather_sensors(self, device_id: int) -> dict[str, int]:
        """
        Register weather sensors in the database.

        Args:
            device_id: Device ID for the weather station

        Returns:
            Dict mapping sensor_name to sensor_id
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Define weather sensors
                sensors = [
                    ("outside_temp", "°C", "temperature"),
                    ("outside_rh", "%", "humidity"),
                    ("outside_pressure", "hPa", "pressure"),
                    ("outside_wind_speed", "m/s", "wind_speed"),
                    ("outside_wind_direction", "degrees", "wind_direction"),
                    ("outside_precipitation", "mm", "precipitation"),
                ]

                sensor_ids = {}
                for sensor_name, unit, data_type in sensors:
                    # Check if sensor already exists
                    sensor_row = await conn.fetchrow(
                        "SELECT sensor_id FROM sensor WHERE device_id = $1 AND name = $2",
                        device_id,
                        sensor_name,
                    )

                    if sensor_row:
                        sensor_ids[sensor_name] = sensor_row["sensor_id"]
                    else:
                        sensor_id = await conn.fetchval(
                            """INSERT INTO sensor (device_id, name, unit, data_type)
                               VALUES ($1, $2, $3, $4) RETURNING sensor_id""",
                            device_id,
                            sensor_name,
                            unit,
                            data_type,
                        )
                        sensor_ids[sensor_name] = sensor_id
                        logger.info(f"Registered sensor: {sensor_name} (ID: {sensor_id})")

                return sensor_ids

    async def store_weather_measurements(
        self,
        sensor_ids: dict[str, int],
        weather_data: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> bool:
        """
        Store weather measurements in the database.

        Args:
            sensor_ids: Dict mapping sensor_name to sensor_id
            weather_data: Dict with weather parameters (temperature, relative_humidity, etc.)
            timestamp: Timestamp for measurements (defaults to now or from weather_data)

        Returns:
            True if successful, False otherwise
        """
        if timestamp is None:
            timestamp = weather_data.get("timestamp", datetime.now())

        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Map weather_data keys to sensor names
                    sensor_mapping = {
                        "outside_temp": "temperature",
                        "outside_rh": "relative_humidity",
                        "outside_pressure": "pressure",
                        "outside_wind_speed": "wind_speed",
                        "outside_wind_direction": "wind_direction",
                        "outside_precipitation": "precipitation",
                    }

                    # Insert all measurements
                    for sensor_name, data_key in sensor_mapping.items():
                        sensor_id = sensor_ids.get(sensor_name)
                        if sensor_id and data_key in weather_data:
                            value = weather_data[data_key]
                            # Skip None values (e.g., precipitation may not be available)
                            if value is not None:
                                await conn.execute(
                                    """INSERT INTO measurement (time, sensor_id, value, status)
                                       VALUES ($1, $2, $3, $4)
                                       ON CONFLICT (time, sensor_id) DO UPDATE
                                       SET value = EXCLUDED.value, status = EXCLUDED.status""",
                                    timestamp,
                                    sensor_id,
                                    float(value),
                                    "ok",
                                )

            return True
        except Exception as e:
            logger.error(f"Error storing weather measurements: {e}")
            return False
