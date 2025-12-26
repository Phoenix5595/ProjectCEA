"""Database manager for TimescaleDB operations."""
import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import asyncpg

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for weather service."""
    
    def __init__(self, db_config: Optional[Dict[str, Any]] = None):
        """Initialize database manager.
        
        Args:
            db_config: Database connection config dict with host, database, user, password, port.
                      If None, uses environment variables or defaults.
        """
        self.db_config = db_config or {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "database": os.getenv("POSTGRES_DB", "cea_sensors"),
            "user": os.getenv("POSTGRES_USER", "cea_user"),
            "password": os.getenv("POSTGRES_PASSWORD", "Lenin1917"),
            "port": int(os.getenv("POSTGRES_PORT", "5432"))
        }
        self._pool: Optional[asyncpg.Pool] = None
        self._db_connected = False
        self._retry_delay = 1.0
        self._max_retry_delay = 60.0
    
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
        """Connect to TimescaleDB with retry logic."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self._pool = await asyncpg.create_pool(
                    host=self.db_config["host"],
                    database=self.db_config["database"],
                    user=self.db_config["user"],
                    password=self.db_config["password"],
                    port=self.db_config["port"],
                    min_size=2,
                    max_size=10
                )
                self._db_connected = True
                self._retry_delay = 1.0
                logger.info("Connected to TimescaleDB")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = min(self._retry_delay * (2 ** attempt), self._max_retry_delay)
                    logger.warning(f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ConnectionError(f"Failed to connect to TimescaleDB after {max_retries} attempts: {e}")
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Get database connection pool."""
        if not self._pool:
            await self._connect_db()
        return self._pool
    
    async def close(self) -> None:
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
            self._db_connected = False
            logger.info("Database connection closed")
    
    async def ensure_hierarchy(
        self, 
        room_name: str,
        device_name: str
    ) -> Tuple[int, int]:
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
                    "SELECT room_id FROM room WHERE name = $1",
                    room_name
                )
                if room_row:
                    room_id = room_row['room_id']
                else:
                    room_id = await conn.fetchval(
                        "INSERT INTO room (name) VALUES ($1) RETURNING room_id",
                        room_name
                    )
                    logger.info(f"Created room: {room_name}")
                
                # Get or create device (no rack needed for weather station)
                device_row = await conn.fetchrow(
                    """SELECT device_id FROM device 
                       WHERE rack_id IS NULL AND name = $1""",
                    device_name
                )
                
                if device_row:
                    device_id = device_row['device_id']
                    logger.info(f"Device already exists: {device_name} (ID: {device_id})")
                else:
                    # Create device (rack_id is NULL for weather station)
                    device_id = await conn.fetchval(
                        """INSERT INTO device (rack_id, name, type)
                           VALUES ($1, $2, $3) RETURNING device_id""",
                        None,  # No rack for weather station
                        device_name,
                        "Weather Station"
                    )
                    logger.info(f"Created device: {device_name} (ID: {device_id})")
                
                return room_id, device_id
    
    async def register_weather_sensors(
        self,
        device_id: int
    ) -> Dict[str, int]:
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
                    ('outside_temp', '°C', 'temperature'),
                    ('outside_rh', '%', 'humidity'),
                    ('outside_pressure', 'hPa', 'pressure'),
                    ('outside_wind_speed', 'm/s', 'wind_speed'),
                    ('outside_wind_direction', 'degrees', 'wind_direction'),
                    ('outside_precipitation', 'mm', 'precipitation')
                ]
                
                sensor_ids = {}
                for sensor_name, unit, data_type in sensors:
                    # Check if sensor already exists
                    sensor_row = await conn.fetchrow(
                        "SELECT sensor_id FROM sensor WHERE device_id = $1 AND name = $2",
                        device_id, sensor_name
                    )
                    
                    if sensor_row:
                        sensor_ids[sensor_name] = sensor_row['sensor_id']
                    else:
                        sensor_id = await conn.fetchval(
                            """INSERT INTO sensor (device_id, name, unit, data_type)
                               VALUES ($1, $2, $3, $4) RETURNING sensor_id""",
                            device_id, sensor_name, unit, data_type
                        )
                        sensor_ids[sensor_name] = sensor_id
                        logger.info(f"Registered sensor: {sensor_name} (ID: {sensor_id})")
                
                return sensor_ids
    
    async def store_weather_measurements(
        self,
        sensor_ids: Dict[str, int],
        weather_data: Dict[str, Any],
        timestamp: Optional[datetime] = None
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
            timestamp = weather_data.get('timestamp', datetime.now())
        
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Map weather_data keys to sensor names
                    sensor_mapping = {
                        'outside_temp': 'temperature',
                        'outside_rh': 'relative_humidity',
                        'outside_pressure': 'pressure',
                        'outside_wind_speed': 'wind_speed',
                        'outside_wind_direction': 'wind_direction',
                        'outside_precipitation': 'precipitation'
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
                                    timestamp, sensor_id, float(value), 'ok'
                                )
            
            return True
        except Exception as e:
            logger.error(f"Error storing weather measurements: {e}")
            return False












