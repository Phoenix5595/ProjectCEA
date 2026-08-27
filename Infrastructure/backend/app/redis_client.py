"""Redis client utilities for reading live sensor state."""

from __future__ import annotations

import redis.asyncio as redis

from shared.infra_logging import get_logger
from shared.redis_client import close_async, create_async_client

logger = get_logger(__name__)

_redis_pool: redis.ConnectionPool | None = None
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis | None:
    """Get or create Redis client connection.

    Returns ``None`` on connect failure (keeps the pre-lift warn-and-
    continue contract — callers no-op when Redis is unavailable so the
    historical/DB path still serves). ``create_async_client`` itself
    raises ``redis.exceptions.ConnectionError`` which we catch here.
    """
    global _redis_client, _redis_pool

    if _redis_client is not None:
        return _redis_client

    try:
        _redis_client, _redis_pool = await create_async_client(
            decode_responses=True,
            max_connections=10,
            name="backend-redis",
        )
        return _redis_client
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {e}. Live sensor data will not be available.")
        _redis_client = None
        _redis_pool = None
        return None


async def get_sensor_value(sensor_name: str) -> float | None:
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
        key = await _canonical_sensor_key(client, sensor_name)
        if key is None:
            return None
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


async def get_sensor_timestamp(sensor_name: str) -> int | None:
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
        key = await _canonical_sensor_key(client, sensor_name, timestamp=True)
        if key is None:
            return None
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


async def get_all_sensor_values() -> dict[str, float]:
    """Get all current sensor values from Redis.

    Returns:
        Dictionary mapping sensor_name -> value
    """
    client = await get_redis_client()
    if not client:
        return {}

    try:
        # Scan canonical sensor keys without blocking Redis (avoid KEYS).
        value_keys: list[str] = []
        async for key in client.scan_iter(match="cea:sensor:*:*:*", count=500):
            if key.endswith(("_ts", "_last_good")):
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
        for key, value in zip(value_keys, values, strict=False):
            if value is not None:
                try:
                    sensor_name = key.rsplit(":", maxsplit=1)[-1]
                    result[sensor_name] = float(value)
                except (ValueError, TypeError):
                    continue

        return result
    except Exception as e:
        logger.warning(f"Error reading all sensor values from Redis: {e}")
        return {}


async def get_all_sensor_timestamps(sensor_names: list[str]) -> dict[str, int]:
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
        result = {}
        for sensor_name in sensor_names:
            key = await _canonical_sensor_key(client, sensor_name, timestamp=True)
            if key is None:
                continue
            value = await client.get(key)
            if value is not None:
                try:
                    result[sensor_name] = int(value)
                except (ValueError, TypeError):
                    continue

        return result
    except Exception as e:
        logger.warning(f"Error reading sensor timestamps in batch: {e}")
        return {}


async def _canonical_sensor_key(
    client: redis.Redis, sensor_name: str, *, timestamp: bool = False
) -> str | None:
    """Resolve a unique topology-qualified current value or timestamp key."""
    suffix = "_ts" if timestamp else ""
    pattern = f"cea:sensor:*:*:{sensor_name}{suffix}"
    async for key in client.scan_iter(match=pattern, count=100):
        return str(key)
    return None


async def close_redis_client():
    """Close Redis client connection (best-effort, SIGTERM-safe)."""
    global _redis_client, _redis_pool
    await close_async(_redis_client, _redis_pool, name="backend-redis")
    _redis_client = None
    _redis_pool = None
