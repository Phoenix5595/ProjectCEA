"""Redis fallback and persistence mixin for StateManager.

Provides private methods that interact directly with the Redis client,
plus initialization from Redis on startup.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.state._types import CacheEntry
from shared.infra_logging import get_logger

logger = get_logger(__name__)


def _serialize_for_redis(value: Any) -> str:
    """Serialize values for Redis. Dict/list/tuple use JSON (not str() repr)."""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, str):
        return value
    return str(value)


def _deserialize_redis_payload(raw: Any) -> Any:
    """Parse JSON payloads written by _serialize_for_redis; pass through non-JSON strings."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if len(s) == 0:
        return raw
    if s[0] in '[{"-0123456789tfn':
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            if s.startswith("[") or s.startswith("{"):
                logger.warning("StateManager: Corrupt Redis value (repr, not JSON); dropping entry")
                return None
    return raw


class RedisClientMixin:
    """Mixin adding Redis fallback and persistence methods to StateManager."""

    async def initialize_from_redis(self, keys: list[str] | None = None) -> int:
        """Initialize cache from Redis keys.

        Loads existing state from Redis into the in-memory cache.
        This is useful for service restarts to restore previous state.

        Args:
            keys: Specific keys to load, or None to load all matching pattern

        Returns:
            Number of keys loaded
        """
        if not self._redis_enabled or not self._redis_client:
            logger.info("StateManager: Redis not available, skipping initialization")
            return 0

        loaded = 0

        try:
            if keys:
                # Load specific keys
                for key in keys:
                    value = await self._get_from_redis_raw(key)
                    if value is not None:
                        async with self._lock:
                            if len(self._cache) >= self._max_entries:
                                await self._evict_oldest()
                            self._cache[key] = CacheEntry(
                                value=value,
                                expires_at=time.time() + self._default_ttl,
                            )
                        loaded += 1
            else:
                logger.info("StateManager: No keys specified for initialization")

            logger.info(f"StateManager: Initialized {loaded} entries from Redis")
        except Exception as e:
            logger.warning(f"StateManager: Error initializing from Redis: {e}")

        return loaded

    async def _evict_oldest(self) -> None:
        """Evict the oldest (earliest expiration) entry from cache.

        Must be called while holding the lock.
        """
        if not self._cache:
            return

        # Find entry with earliest expiration time
        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].expires_at)
        del self._cache[oldest_key]
        logger.debug(f"StateManager: Evicted oldest entry '{oldest_key}'")

    async def _get_from_redis(self, key: str) -> Any | None:
        """Get value from Redis fallback.

        Args:
            key: Key to retrieve from Redis

        Returns:
            Value from Redis or None if not found
        """
        if not self._redis_enabled or not self._redis_client:
            return None

        try:
            raw = await asyncio.to_thread(self._redis_client.get, key)
            value = _deserialize_redis_payload(raw)
            if raw is not None and value is None:
                # Corrupt legacy entry (repr, not JSON): remove so next read hits DB
                await self._delete_from_redis(key)
                return None
            if value is not None:
                logger.debug(f"StateManager: Redis fallback hit for '{key}'")
                # Try to honor Redis TTL for in-memory cache expiration
                ttl_seconds: int | None = None
                try:
                    ttl_ret = await asyncio.to_thread(self._redis_client.ttl, key)  # type: ignore[arg-type]
                    if ttl_ret is not None:
                        ttl_seconds = int(ttl_ret)
                except Exception:
                    ttl_seconds = None

                effective_ttl = (
                    ttl_seconds
                    if ttl_seconds is not None and ttl_seconds > 0
                    else self._default_ttl
                )

                # Cache the value for future access using TTL synchronized with Redis
                async with self._lock:
                    if len(self._cache) >= self._max_entries:
                        await self._evict_oldest()
                    self._cache[key] = CacheEntry(
                        value=value,
                        expires_at=time.time() + int(effective_ttl),
                    )
                return value
        except Exception as e:
            logger.warning(f"StateManager: Redis fallback error for '{key}': {e}")

        return None

    async def _get_from_redis_raw(self, key: str) -> Any | None:
        """Get raw value from Redis without caching.

        Args:
            key: Key to retrieve from Redis

        Returns:
            Value from Redis or None if not found
        """
        if not self._redis_enabled or not self._redis_client:
            return None

        try:
            value = await asyncio.to_thread(self._redis_client.get, key)
            return value
        except Exception as e:
            logger.warning(f"StateManager: Redis read error for '{key}': {e}")
            return None

    async def _write_to_redis(self, key: str, value: Any, ttl: int) -> None:
        """Write value to Redis for cross-service visibility.

        Args:
            key: Key to set in Redis
            value: Value to set
            ttl: TTL in seconds for Redis key
        """
        if not self._redis_enabled or not self._redis_client:
            return

        try:
            payload = _serialize_for_redis(value)
            await asyncio.to_thread(
                self._redis_client.setex,
                key,
                ttl,
                payload,
            )
            logger.debug(f"StateManager: Wrote '{key}' to Redis with TTL {ttl}s")
        except Exception as e:
            logger.warning(f"StateManager: Redis write error for '{key}': {e}")

    async def _delete_from_redis(self, key: str) -> None:
        """Delete key from Redis.

        Args:
            key: Key to delete from Redis
        """
        if not self._redis_enabled or not self._redis_client:
            return

        try:
            await asyncio.to_thread(self._redis_client.delete, key)
            logger.debug(f"StateManager: Deleted '{key}' from Redis")
        except Exception as e:
            logger.warning(f"StateManager: Redis delete error for '{key}': {e}")
