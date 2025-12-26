"""Writer for Redis Stream, TimescaleDB and Redis state keys."""
import psycopg2
import psycopg2.extras
import redis
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger(__name__)


class DataWriter:
    """Writes processed CAN data to Redis Stream, TimescaleDB and Redis state keys."""
    
    def __init__(self, 
                 db_config: Dict[str, str] = None,
                 redis_url: str = None,
                 redis_ttl: int = 10,
                 stream_name: str = "sensor:raw"):
        """Initialize data writer.
        
        Args:
            db_config: TimescaleDB connection config
            redis_url: Redis connection URL
            redis_ttl: TTL for Redis keys in seconds
            stream_name: Redis Stream name (default: sensor:raw)
        """
        self.db_config = db_config or {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "database": os.getenv("POSTGRES_DB", "cea_sensors"),
            "user": os.getenv("POSTGRES_USER", "cea_user"),
            "password": os.getenv("POSTGRES_PASSWORD", "Lenin1917")
        }
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_ttl = redis_ttl
        self.stream_name = stream_name
        
        self.db_conn: Optional[psycopg2.extensions.connection] = None
        self.redis_client: Optional[redis.Redis] = None
        
        self.db_enabled = False
        self.redis_enabled = False
        
        # Cache for device and sensor lookups (avoid repeated queries)
        self.device_cache: Dict[str, int] = {}  # {device_name: device_id}
        self.sensor_cache: Dict[Tuple[int, str], int] = {}  # {(device_id, sensor_name): sensor_id}
    
    def connect_db(self) -> bool:
        """Connect to TimescaleDB with optimizations for high throughput."""
        try:
            # Add connection parameters for better performance
            db_config_optimized = self.db_config.copy()
            # Use autocommit for faster writes (each statement commits immediately)
            self.db_conn = psycopg2.connect(**db_config_optimized)
            self.db_conn.autocommit = True  # Auto-commit for faster writes
            self.db_enabled = True
            logger.info("Connected to TimescaleDB (autocommit enabled for high throughput)")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            self.db_enabled = False
            return False
    
    def connect_redis(self) -> bool:
        """Connect to Redis."""
        try:
            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=False,  # Keep binary for stream writes
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.redis_client.ping()
            self.redis_enabled = True
            logger.info(f"Connected to Redis at {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}. Continuing without Redis.")
            self.redis_enabled = False
            return False
    
    def write_to_stream(self, msg, decoded_data: Dict[str, Any]) -> bool:
        """Write CAN message to Redis Stream.
        
        Args:
            msg: CAN message object
            decoded_data: Decoded CAN frame data
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            if not self.connect_redis():
                return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            raw_data = ' '.join(f'{b:02X}' for b in msg.data)
            
            # Create stream entry with type="can" marker
            stream_data = {
                b'id': f"0x{msg.arbitration_id:03X}".encode(),
                b'data': raw_data.encode(),
                b'ts': str(timestamp_ms).encode(),
                b'dlc': str(msg.dlc).encode(),
                b'type': b'can'  # Mark as CAN sensor data
            }
            
            # Add decoded data if available
            if decoded_data:
                decoded_json = json.dumps(decoded_data)
                stream_data[b'decoded'] = decoded_json.encode()
            
            # Write to Redis Stream with automatic trimming (keep last 100,000 messages)
            self.redis_client.xadd(self.stream_name, stream_data, maxlen=100000, approximate=True)
            return True
        except Exception as e:
            # Don't log error for every message, just occasionally
            if not hasattr(self, '_stream_error_count'):
                self._stream_error_count = 0
            self._stream_error_count += 1
            
            if self._stream_error_count <= 5:
                logger.warning(f"Error writing to Redis Stream: {e}")
            elif self._stream_error_count == 6:
                logger.warning("Redis Stream errors continuing (suppressing further messages)...")
            
            return False
    
    def write_to_db(self, decoded: Dict[str, Any], raw_data: str, 
                    sensors: List[Tuple[str, float, str]], timestamp: datetime) -> bool:
        """Write decoded data to TimescaleDB measurement table.
        
        Args:
            decoded: Decoded CAN frame data
            raw_data: Raw hex data string
            sensors: List of (sensor_name, value, unit) tuples
            timestamp: Timestamp for the data
        
        Returns:
            True if successful, False otherwise
        """
        if not self.db_enabled:
            if not self.connect_db():
                return False
        
        if not sensors:
            return True
        
        try:
            cursor = self.db_conn.cursor()
            node_id = decoded.get('node_id')
            
            if not node_id:
                logger.warning(f"Missing node_id, skipping measurement write")
                return False
            
            # Get device_id from node_id (use cache to avoid repeated queries)
            device_name = f"Node {node_id}"
            if device_name not in self.device_cache:
                cursor.execute("""
                    SELECT device_id FROM device WHERE name = %s
                """, (device_name,))
                device_row = cursor.fetchone()
                if not device_row:
                    logger.warning(f"Device not found: {device_name}, skipping measurement write")
                    return False
                self.device_cache[device_name] = device_row[0]
            device_id = self.device_cache[device_name]
            
            # Insert measurements for each sensor (use cache to avoid repeated queries)
            measurements = []
            for sensor_name, value, unit in sensors:
                cache_key = (device_id, sensor_name)
                if cache_key not in self.sensor_cache:
                    cursor.execute("""
                        SELECT sensor_id FROM sensor 
                        WHERE device_id = %s AND name = %s
                    """, (device_id, sensor_name))
                    sensor_row = cursor.fetchone()
                    if not sensor_row:
                        logger.debug(f"Sensor not found: {sensor_name} (device: {device_id}), skipping")
                        continue
                    self.sensor_cache[cache_key] = sensor_row[0]
                sensor_id = self.sensor_cache[cache_key]
                
                measurements.append((timestamp, sensor_id, value, None))  # status is None for now
            
            if not measurements:
                return True
            
            # Batch insert measurements (autocommit handles commit automatically)
            # Use larger page_size for better performance
            psycopg2.extras.execute_batch(
                cursor,
                """
                INSERT INTO measurement (time, sensor_id, value, status)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (time, sensor_id) DO UPDATE
                SET value = EXCLUDED.value, status = EXCLUDED.status
                """,
                measurements,
                page_size=500  # Larger batch for faster inserts
            )
            
            # No need to commit - autocommit is enabled
            return True
        
        except psycopg2.OperationalError as e:
            logger.error(f"TimescaleDB connection error: {e}")
            self.db_conn = None
            self.db_enabled = False
            return False
        except Exception as e:
            logger.error(f"Error writing to TimescaleDB: {e}")
            # With autocommit, no rollback needed
            return False
    
    def write_to_redis_state(self, sensors: List[Tuple[str, float, str]], timestamp_ms: int) -> bool:
        """Write sensor values to Redis state keys.
        
        Args:
            sensors: List of (sensor_name, value, unit) tuples
            timestamp_ms: Timestamp in milliseconds
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            if not self.connect_redis():
                return False
        
        if not sensors:
            return True
        
        try:
            # Create a separate Redis client for state writes (decode_responses=True)
            redis_state_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Use pipeline for batch operations
            pipe = redis_state_client.pipeline()
            
            for sensor_name, value, unit in sensors:
                # Set sensor value with TTL
                key = f"sensor:{sensor_name}"
                pipe.setex(key, self.redis_ttl, str(value))
                
                # Set timestamp
                ts_key = f"sensor:{sensor_name}:ts"
                pipe.setex(ts_key, self.redis_ttl, str(timestamp_ms))
            
            # Execute all commands
            pipe.execute()
            redis_state_client.close()
            return True
        
        except redis.exceptions.ConnectionError as e:
            logger.warning(f"Redis connection error: {e}")
            return False
        except Exception as e:
            logger.warning(f"Error writing to Redis state: {e}")
            return False
    
    def write(self, msg, decoded: Dict[str, Any], raw_data: str, 
              sensors: List[Tuple[str, float, str]], 
              timestamp: datetime, timestamp_ms: int) -> Dict[str, bool]:
        """Write data to Redis Stream, TimescaleDB and Redis state immediately.
        
        Args:
            msg: CAN message object
            decoded: Decoded CAN frame data
            raw_data: Raw hex data string
            sensors: List of sensor values to write to Redis and DB
            timestamp: Timestamp for database
            timestamp_ms: Timestamp in milliseconds for Redis
        
        Returns:
            Dictionary with 'stream', 'db' and 'redis' keys indicating success
        """
        result = {'stream': False, 'db': False, 'redis': False}
        
        # Write to Redis Stream first
        result['stream'] = self.write_to_stream(msg, decoded)
        
        # Write to database immediately (live processing)
        result['db'] = self.write_to_db(decoded, raw_data, sensors, timestamp)
        
        # Write to Redis state immediately
        result['redis'] = self.write_to_redis_state(sensors, timestamp_ms)
        
        return result
    
    def close(self):
        """Close all connections."""
        if self.db_conn:
            try:
                self.db_conn.close()
            except Exception:
                pass
            self.db_conn = None
        
        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception:
                pass
            self.redis_client = None

