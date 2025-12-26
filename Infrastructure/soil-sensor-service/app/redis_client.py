"""Redis client for publishing sensor updates."""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client for publishing sensor data updates."""
    
    def __init__(self, redis_url: Optional[str] = None):
        """Initialize Redis client.
        
        Args:
            redis_url: Redis connection URL. If None, uses environment variable or default.
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client: Optional[redis.Redis] = None
        self.stream_client: Optional[redis.Redis] = None  # Separate client for stream (binary mode)
        self.redis_enabled = False
        self.redis_ttl = 10  # 10 seconds TTL for state keys (consistent with CAN sensors)
    
    async def connect(self) -> bool:
        """Connect to Redis.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Connect for state keys (decode_responses=True)
            self.redis_client = await redis.from_url(
                self.redis_url,
                decode_responses=True
            )
            await self.redis_client.ping()
            
            # Connect for stream writes (decode_responses=False for binary)
            self.stream_client = await redis.from_url(
                self.redis_url,
                decode_responses=False
            )
            await self.stream_client.ping()
            
            self.redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            return False
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()
        if self.stream_client:
            await self.stream_client.close()
        self.redis_enabled = False
        logger.info("Redis connection closed")
    
    async def publish_sensor_update(
        self,
        sensor_name: str,
        value: float,
        unit: str,
        bed_name: str,
        location: str = "Flower Room"
    ) -> bool:
        """
        Publish sensor update to Redis channels and store in state.
        
        Args:
            sensor_name: Full sensor name (e.g., "soil_sensor_front_bed_temperature")
            value: Sensor value
            unit: Unit of measurement
            bed_name: Bed name (e.g., "Front Bed")
            location: Location/room name
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            return False
        
        try:
            timestamp = datetime.now()
            timestamp_ms = int(timestamp.timestamp() * 1000)
            
            # Create message
            message = {
                "sensor_name": sensor_name,
                "value": value,
                "unit": unit,
                "timestamp": timestamp.isoformat(),
                "location": location,
                "bed": bed_name
            }
            
            # Publish to channels
            await self.redis_client.publish(
                "sensor:update",
                json.dumps(message)
            )
            await self.redis_client.publish(
                "sensor:update:soil",
                json.dumps(message)
            )
            
            # Store in state with TTL
            state_key = f"sensor:{sensor_name}"
            ts_key = f"sensor:{sensor_name}:ts"
            
            pipe = self.redis_client.pipeline()
            pipe.setex(state_key, self.redis_ttl, str(value))
            pipe.setex(ts_key, self.redis_ttl, str(timestamp_ms))
            await pipe.execute()
            
            return True
        except Exception as e:
            logger.warning(f"Error publishing to Redis: {e}")
            return False
    
    async def write_to_stream(
        self,
        sensor_base_name: str,
        readings: Dict[str, float],
        bed_name: str,
        location: str = "Flower Room"
    ) -> bool:
        """Write sensor readings to Redis Stream (sensor:raw).
        
        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Bed name
            location: Location/room name
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.stream_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            # Create stream entry with type="soil" marker
            stream_data = {
                b'id': f"{sensor_base_name}_{timestamp_ms}".encode(),
                b'ts': str(timestamp_ms).encode(),
                b'type': b'soil',  # Mark as soil sensor data
                b'sensor_name': sensor_base_name.encode(),
                b'bed_name': bed_name.encode(),
                b'location': location.encode(),
                b'readings': json.dumps(readings).encode()
            }
            
            # Write to Redis Stream with automatic trimming (keep last 100,000 messages)
            await self.stream_client.xadd('sensor:raw', stream_data, maxlen=100000, approximate=True)
            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis Stream: {e}")
            return False
    
    async def publish_all_readings(
        self,
        sensor_base_name: str,
        readings: Dict[str, float],
        bed_name: str,
        location: str = "Flower Room"
    ) -> bool:
        """
        Publish all sensor readings for a soil sensor.
        
        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Bed name
            location: Location/room name
        
        Returns:
            True if all published successfully, False otherwise
        """
        units = {
            'temperature': '°C',
            'humidity': '%',
            'ec': 'µS/cm',
            'ph': 'pH'
        }
        
        success = True
        for sensor_type, value in readings.items():
            if sensor_type in units:
                sensor_name = f"{sensor_base_name}_{sensor_type}"
                result = await self.publish_sensor_update(
                    sensor_name, value, units[sensor_type], bed_name, location
                )
                if not result:
                    success = False
        
        return success

