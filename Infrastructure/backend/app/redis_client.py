"""Redis client utilities for reading live sensor state."""

from __future__ import annotations

import asyncio

import redis.asyncio as redis

from shared.infra_logging import get_logger
from shared.redis_client import close_async, create_async_client
from shared.redis_keys import sensor_full_ts

logger = get_logger(__name__)

_redis_pool: redis.ConnectionPool | None = None
_redis_client: redis.Redis | None = None
_SensorSnapshotRecord = tuple[str, str, str, float, int | None]

# One lazily created, loop-owned lock serializes client creation and
# teardown. ``_redis_loop`` records the loop the lock was created on so
# sequential fully-shut-down test loops can replace the pair, while a
# live second loop cannot steal resources from the owner loop.
_redis_lock: asyncio.Lock | None = None
_redis_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_redis_lock() -> asyncio.Lock:
    """Return the module lock, bound to the running loop.

    Reuses the lock on the owning loop. A different loop may replace it
    only when the old loop is closed, no resource reference remains and
    the lock is unlocked. Otherwise live callers on another loop raise.
    """
    global _redis_lock, _redis_lock_loop

    loop = asyncio.get_running_loop()
    if _redis_lock is not None and _redis_lock_loop is loop:
        return _redis_lock

    if (
        _redis_lock is not None
        and _redis_lock_loop is not loop
        and (_redis_lock_loop is None or _redis_lock_loop.is_closed())
        and _redis_client is None
        and _redis_pool is None
        and not _redis_lock.locked()
    ):
        _redis_lock = asyncio.Lock()
        _redis_lock_loop = loop
        return _redis_lock

    if _redis_lock is None:
        _redis_lock = asyncio.Lock()
        _redis_lock_loop = loop
        return _redis_lock

    raise RuntimeError("Backend Redis resources belong to another event loop")


async def get_redis_client() -> redis.Redis | None:
    """Get or create Redis client connection.

    Returns ``None`` on connect failure (keeps the pre-lift warn-and-
    continue contract — callers no-op when Redis is unavailable so the
    historical/DB path still serves). ``create_async_client`` itself
    raises ``redis.exceptions.ConnectionError`` which we catch here.

    Creation and teardown are serialized by one module lock; client and
    pool are published together only after a successful connect, and
    cancellation propagates (publishing nothing).
    """
    global _redis_client, _redis_pool

    async with _get_redis_lock():
        if _redis_client is not None:
            return _redis_client

        try:
            client, pool = await create_async_client(
                decode_responses=True,
                max_connections=10,
                name="backend-redis",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Failed to connect to Redis: {e}. Live sensor data will not be available."
            )
            _redis_client = None
            _redis_pool = None
            return None
        _redis_client = client
        _redis_pool = pool
        return _redis_client


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

        # One paired read: interleave each candidate's value key with its
        # timestamp key into a single MGET (at most 2 * 5000 = 10000 keys).
        # A strict cardinality check replaces the cross-call zip; a torn or
        # truncated reply enters the existing warning/empty-snapshot path.
        keys: list[str] = []
        for key, ts_location, ts_cluster, ts_name in candidates:
            keys.append(key)
            keys.append(sensor_full_ts(ts_location, ts_cluster, ts_name))
        results = await client.mget(keys)

        expected = 2 * len(candidates)
        if len(results) != expected:
            logger.warning(
                "Redis sensor snapshot MGET returned %d results for %d candidates"
                " (expected %d); using empty snapshot",
                len(results),
                len(candidates),
                expected,
            )
            return {}

        snapshot: dict[str, _SensorSnapshotRecord] = {}
        for candidate, raw_value, raw_timestamp in zip(
            candidates, results[0::2], results[1::2], strict=True
        ):
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
    """Close Redis client connection (best-effort, SIGTERM-safe).

    Serialized with creation on the same lock; both globals are detached
    before the shared ``close_async`` teardown so no concurrent getter can
    observe a closed client.
    """
    global _redis_client, _redis_pool

    async with _get_redis_lock():
        client = _redis_client
        pool = _redis_pool
        _redis_client = None
        _redis_pool = None
        if client is None and pool is None:
            return
        await close_async(client, pool, name="backend-redis")
