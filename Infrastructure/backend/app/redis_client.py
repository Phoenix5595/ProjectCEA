"""Redis client utilities for reading live sensor state."""
import redis.asyncio as redis
import logging
from typing import Optional, Dict, Any, List
import os

logger = logging.getLogger(__name__)

# Redis connection pool (singleton)
_redis_pool: Optional[redis.ConnectionPool] = None
_redis_client: Optional[redis.Redis] = None


async def get_redis_client() -> Optional[redis.Redis]:
    """Get or create Redis client connection."""
    global _redis_client, _redis_pool
    
    if _redis_client is not None:
        try:
            # Test connection
            await _redis_client.ping()
            return _redis_client
        except Exception:
            # Connection lost, reset
            _redis_client = None
            _redis_pool = None
    
    # Create new connection
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    try:
        _redis_pool = redis.ConnectionPool.from_url(
            redis_url,
            decode_responses=True,
            max_connections=10
        )
        _redis_client = redis.Redis(connection_pool=_redis_pool)
        
        # Test connection
        await _redis_client.ping()
        logger.info(f"Connected to Redis at {redis_url}")
        return _redis_client
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {e}. Live sensor data will not be available.")
        return None


async def get_sensor_value(sensor_name: str) -> Optional[float]:
    """Get current sensor value from Redis.
    
    Args:
        sensor_name: Sensor name (e.g., 'dry_bulb_b', 'co2_f')
    
    Returns:
        Sensor value as float, or None if not found or Redis unavailable
    """
    client = await get_redis_client()
    if not client:
        return None
    
    try:
        key = f"sensor:{sensor_name}"
        value = await client.get(key)
        if value is not None:
            return float(value)
        return None
    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing sensor value for {sensor_name}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading sensor {sensor_name} from Redis: {e}")
        return None


async def get_sensor_timestamp(sensor_name: str) -> Optional[int]:
    """Get sensor timestamp from Redis.
    
    Args:
        sensor_name: Sensor name (e.g., 'dry_bulb_b', 'co2_f')
    
    Returns:
        Timestamp in milliseconds, or None if not found
    """
    client = await get_redis_client()
    if not client:
        return None
    
    try:
        key = f"sensor:{sensor_name}:ts"
        value = await client.get(key)
        if value is not None:
            return int(value)
        return None
    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing timestamp for {sensor_name}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading timestamp for {sensor_name} from Redis: {e}")
        return None


async def get_all_sensor_values() -> Dict[str, float]:
    """Get all current sensor values from Redis.
    
    Returns:
        Dictionary mapping sensor_name -> value
    """
    client = await get_redis_client()
    if not client:
        return {}
    
    try:
        # Scan sensor keys without blocking Redis (avoid KEYS)
        value_keys: List[str] = []
        async for key in client.scan_iter(match="sensor:*", count=500):
            if key.endswith(':ts'):
                continue
            value_keys.append(key)
            # Safety cap to avoid huge batches
            if len(value_keys) >= 5000:
                break
        
        if not value_keys:
            return {}
        
        # Get all values in one call
        values = await client.mget(value_keys)
        
        result = {}
        for key, value in zip(value_keys, values):
            if value is not None:
                try:
                    # Remove 'sensor:' prefix
                    sensor_name = key.replace('sensor:', '')
                    result[sensor_name] = float(value)
                except (ValueError, TypeError):
                    continue
        
        return result
    except Exception as e:
        logger.warning(f"Error reading all sensor values from Redis: {e}")
        return {}


async def get_all_sensor_timestamps(sensor_names: List[str]) -> Dict[str, int]:
    """Get timestamps for multiple sensors in batch.
    
    Args:
        sensor_names: List of sensor names
    
    Returns:
        Dictionary mapping sensor_name -> timestamp_ms
    """
    client = await get_redis_client()
    if not client:
        return {}
    
    if not sensor_names:
        return {}
    
    try:
        # Build keys for all sensors
        ts_keys = [f"sensor:{name}:ts" for name in sensor_names]
        
        # Get all timestamps in one call
        values = await client.mget(ts_keys)
        
        result = {}
        for sensor_name, value in zip(sensor_names, values):
            if value is not None:
                try:
                    result[sensor_name] = int(value)
                except (ValueError, TypeError):
                    continue
        
        return result
    except Exception as e:
        logger.warning(f"Error reading sensor timestamps in batch: {e}")
        return {}


async def close_redis_client():
    """Close Redis client connection."""
    global _redis_client, _redis_pool
    
    if _redis_client:
        try:
            await _redis_client.close()
        except Exception:
            pass
        _redis_client = None
    
    if _redis_pool:
        try:
            await _redis_pool.disconnect()
        except Exception:
            pass
        _redis_pool = None

