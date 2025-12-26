"""Database manager for TimescaleDB operations."""
import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import asyncpg

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for soil sensor service."""
    
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
        bed_name: str
    ) -> Tuple[int, int]:
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
                
                # Get or create rack (bed)
                rack_row = await conn.fetchrow(
                    "SELECT rack_id FROM rack WHERE room_id = $1 AND name = $2",
                    room_id, bed_name
                )
                if rack_row:
                    rack_id = rack_row['rack_id']
                else:
                    rack_id = await conn.fetchval(
                        "INSERT INTO rack (room_id, name) VALUES ($1, $2) RETURNING rack_id",
                        room_id, bed_name
                    )
                    logger.info(f"Created bed (rack): {bed_name}")
                
                return room_id, rack_id
    
    async def register_sensor_device(
        self,
        rack_id: int,
        sensor_name: str,
        modbus_id: int,
        bed_name: str
    ) -> Tuple[int, Dict[str, int]]:
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
                    rack_id, f"Soil Sensor - {bed_name}"
                )
                
                if device_row:
                    device_id = device_row['device_id']
                    logger.info(f"Device already exists: Soil Sensor - {bed_name} (ID: {device_id})")
                else:
                    # Create device
                    device_id = await conn.fetchval(
                        """INSERT INTO device (rack_id, name, type, serial_number)
                           VALUES ($1, $2, $3, $4) RETURNING device_id""",
                        rack_id,
                        f"Soil Sensor - {bed_name}",
                        "RS485 Soil Sensor",
                        f"MODBUS-{modbus_id}"
                    )
                    logger.info(f"Created device: Soil Sensor - {bed_name} (ID: {device_id})")
                
                # Register 4 sensors
                sensor_types = [
                    ('temperature', '°C', 'temperature'),
                    ('humidity', '%', 'humidity'),
                    ('ec', 'µS/cm', 'electrical_conductivity'),
                    ('ph', 'pH', 'ph')
                ]
                
                sensor_ids = {}
                for sensor_type, unit, data_type in sensor_types:
                    sensor_full_name = f"{sensor_name}_{sensor_type}"
                    
                    # Check if sensor already exists
                    sensor_row = await conn.fetchrow(
                        "SELECT sensor_id FROM sensor WHERE device_id = $1 AND name = $2",
                        device_id, sensor_full_name
                    )
                    
                    if sensor_row:
                        sensor_ids[sensor_type] = sensor_row['sensor_id']
                    else:
                        sensor_id = await conn.fetchval(
                            """INSERT INTO sensor (device_id, name, unit, data_type)
                               VALUES ($1, $2, $3, $4) RETURNING sensor_id""",
                            device_id, sensor_full_name, unit, data_type
                        )
                        sensor_ids[sensor_type] = sensor_id
                        logger.info(f"Registered sensor: {sensor_full_name} (ID: {sensor_id})")
                
                return device_id, sensor_ids
    
    async def store_measurements(
        self,
        sensor_ids: Dict[str, int],
        readings: Dict[str, float],
        timestamp: Optional[datetime] = None
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
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Insert all measurements
                    for sensor_type, sensor_id in sensor_ids.items():
                        if sensor_type in readings:
                            await conn.execute(
                                """INSERT INTO measurement (time, sensor_id, value, status)
                                   VALUES ($1, $2, $3, $4)
                                   ON CONFLICT (time, sensor_id) DO UPDATE
                                   SET value = EXCLUDED.value, status = EXCLUDED.status""",
                                timestamp, sensor_id, readings[sensor_type], 'ok'
                            )
            
            return True
        except Exception as e:
            logger.error(f"Error storing measurements: {e}")
            return False
    
    async def get_sensor_id(self, sensor_name: str) -> Optional[int]:
        """Get sensor_id by sensor name."""
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT sensor_id FROM sensor WHERE name = $1",
                    sensor_name
                )
                return row['sensor_id'] if row else None
        except Exception as e:
            logger.error(f"Error getting sensor_id: {e}")
            return None

