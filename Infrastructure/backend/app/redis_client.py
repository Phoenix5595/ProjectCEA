"""Redis client utilities for reading live sensor state."""

from __future__ import annotations

import redis.asyncio as redis

from shared.infra_logging import get_logger
from shared.redis_client import close_async, create_async_client
from shared.redis_keys import sensor_full_ts

logger = get_logger(__name__)

_redis_pool: redis.ConnectionPool | None = None
_redis_client: redis.Redis | None = None
_SensorSnapshotRecord = tuple[str, str, str, float, int | None]


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


async def _read_sensor_snapshot() -> dict[str, _SensorSnapshotRecord]:
    """Read a bounded, qualified snapshot of current sensor values and timestamps."""
    client = await get_redis_client()
    if not client:
        return {}

    try:
        candidates: list[tuple[str, str, str, str]] = []
        seen_keys: set[str] = set()
        async for raw_key in client.scan_iter(match="cea:sensor:*:*:*", count=500):
            key = str(raw_key)
            if key.endswith(("_ts", "_last_good")) or key in seen_keys:
                continue
            if not key.startswith("cea:sensor:"):
                continue
            parts = key.removeprefix("cea:sensor:").split(":", maxsplit=2)
            if len(parts) != 3:
                continue
            location, cluster, sensor_name = parts
            if not location or not cluster or not sensor_name:
                continue

            seen_keys.add(key)
            candidates.append((key, location, cluster, sensor_name))
            if len(candidates) >= 5000:
                break

        if not candidates:
            return {}

        values = await client.mget([key for key, _, _, _ in candidates])
        timestamp_keys = [
            sensor_full_ts(location, cluster, sensor_name)
            for _, location, cluster, sensor_name in candidates
        ]
        timestamps = await client.mget(timestamp_keys)

        snapshot: dict[str, _SensorSnapshotRecord] = {}
        for candidate, raw_value, raw_timestamp in zip(candidates, values, timestamps, strict=True):
            _, location, cluster, sensor_name = candidate
            try:
                value = float(raw_value)
            except (ValueError, TypeError):
                continue

            timestamp_ms: int | None = None
            if raw_timestamp is not None:
                try:
                    timestamp_ms = int(raw_timestamp)
                except (ValueError, TypeError):
                    timestamp_ms = None

            previous = snapshot.get(sensor_name)
            if previous is not None:
                logger.warning(
                    "Redis sensor-name collision for %s: keeping %s/%s after %s/%s",
                    sensor_name,
                    location,
                    cluster,
                    previous[0],
                    previous[1],
                )
            snapshot[sensor_name] = (
                location,
                cluster,
                sensor_name,
                value,
                timestamp_ms,
            )

        return snapshot
    except Exception as exc:
        logger.warning("Error reading sensor snapshot from Redis: %s", exc)
        return {}


async def close_redis_client():
    """Close Redis client connection (best-effort, SIGTERM-safe)."""
    global _redis_client, _redis_pool
    await close_async(_redis_client, _redis_pool, name="backend-redis")
    _redis_client = None
    _redis_pool = None
