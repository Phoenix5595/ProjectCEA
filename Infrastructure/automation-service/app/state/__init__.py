"""State manager module for automation service.

Provides the StateManager class for in-memory state management with TTL
(time-to-live) support and Redis fallback for cross-service visibility.

USAGE:
    from app.state import StateManager

    state = StateManager(default_ttl=60.0, max_entries=1000)
    await state.set_redis_client(redis_client)
    await state.initialize_from_redis()

    value = await state.get("some:key")
    await state.set("some:key", {"data": "value"}, ttl=120.0)
    await state.delete("some:key")
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import time
from typing import TYPE_CHECKING, Any, TypeVar

from app.redis.schema import mode_key
from app.state._types import CacheEntry
from app.state.alarms import AlarmMixin
from app.state.pid import PIDMixin
from app.state.ramps import RampMixin
from app.state.redis_client import RedisClientMixin
from shared.infra_logging import get_logger

# Optional schema validation support and TTL enforcement
try:
    from app.redis.ttl import get_ttl_by_key_type  # type: ignore
    from app.redis.validation import SchemaValidationMixin  # type: ignore
except Exception:
    # If validation TTL modules are not available in the runtime, fall back gracefully
    class _SchemaValidationMixinFallback:  # type: ignore
        pass

    SchemaValidationMixin = _SchemaValidationMixinFallback  # type: ignore

    def get_ttl_by_key_type(_key_type: str):  # type: ignore
        return None


if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)

T = TypeVar("T")


class StateManager(SchemaValidationMixin, PIDMixin, RampMixin, AlarmMixin, RedisClientMixin):
    """In-memory state manager with TTL and Redis fallback.

    Provides fast in-memory caching for internal state with:
    - TTL (time-to-live) support for automatic expiration
    - Memory bounds via max_entries to prevent unbounded growth
    - Thread-safe operations using asyncio.Lock
    - Graceful fallback to Redis on cache miss
    - Dual-write to both cache and Redis for cross-service visibility
    """

    def __init__(
        self,
        default_ttl: float = 60.0,
        max_entries: int = 1000,
        validate_keys: bool = False,
    ) -> None:
        """Initialize the state manager."""
        # Initialize mixin (if present) to enable validation utilities.
        # No-op if the MRO does not require extra args.
        with suppress(TypeError):
            super().__init__()

        self._cache: dict[str, CacheEntry[Any]] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._redis_client: redis.Redis | None = None
        self._redis_enabled: bool = False
        # In-memory alarm and failsafe stores (no TTL) to persist until acknowledged/cleared
        self._alarms_cache: dict[str, CacheEntry[Any]] = {}
        self._failsafe_cache: dict[str, CacheEntry[Any]] = {}
        # Whether to validate keys and enforce TTL categories on set
        self._validate_keys: bool = validate_keys

    def set_redis_client(self, redis_client: redis.Redis | None) -> None:
        """Set the Redis client for fallback and dual-write."""
        self._redis_client = redis_client
        self._redis_enabled = redis_client is not None
        if self._redis_enabled:
            logger.info("StateManager: Redis fallback enabled")
        else:
            logger.info("StateManager: Running in memory-only mode")

    async def get(self, key: str) -> Any | None:
        """Get value from cache with Redis fallback."""
        async with self._lock:
            entry = self._cache.get(key)

            if entry is not None:
                if time.time() <= entry.expires_at:
                    logger.debug(f"StateManager: Cache hit for '{key}'")
                    return entry.value
                else:
                    del self._cache[key]
                    logger.debug(f"StateManager: Cache expired for '{key}'")

        return await self._get_from_redis(key)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
        redis_ttl: int | None = None,
        skip_redis: bool = False,
    ) -> None:
        """Set value in cache with TTL, optionally writing to Redis."""
        if getattr(self, "_validate_keys", False):
            try:
                valid = False
                if hasattr(self, "validate_key_format"):
                    valid = bool(self.validate_key_format(key))
                if not valid:
                    logger.warning("StateManager: Key format validation failed for '%s'", key)
            except Exception as ve:
                logger.warning(
                    "StateManager: Key validation raised exception for '%s': %s", key, ve
                )

        effective_ttl = ttl if ttl is not None else self._default_ttl
        key_type = key.split(":")[0] if isinstance(key, str) and ":" in key else key
        ttl_from_key = None
        try:
            ttl_from_key = get_ttl_by_key_type(key_type)  # type: ignore
        except Exception:
            ttl_from_key = None
        redis_ttl_effective = redis_ttl if redis_ttl is not None else int(effective_ttl)
        if ttl_from_key is not None and redis_ttl is None:
            redis_ttl_effective = int(ttl_from_key)
            logger.debug(
                "StateManager: Enforcing Redis TTL from key type '%s' => %ds for key '%s'",
                key_type,
                ttl_from_key,
                key,
            )

        expires_at = time.time() + effective_ttl

        async with self._lock:
            if len(self._cache) >= self._max_entries and key not in self._cache:
                await self._evict_oldest()

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
            )
            logger.debug(f"StateManager: Set '{key}' with TTL {effective_ttl}s")

        if not skip_redis:
            await self._write_to_redis(key, value, redis_ttl_effective)

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        """Delete key from cache and optionally Redis."""
        was_in_cache = False

        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                was_in_cache = True
                logger.debug(f"StateManager: Deleted '{key}' from cache")

        if not skip_redis:
            await self._delete_from_redis(key)

        return was_in_cache

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache (and is not expired)."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if time.time() > entry.expires_at:
                del self._cache[key]
                return False
        return True

    async def get_mode(self, location: str, cluster: str) -> str | None:
        """Get mode for a specific location/cluster from cache."""
        key = mode_key(location, cluster)
        return await self.get(key)

    async def set_mode(self, location: str, cluster: str, mode: str, source: str = "api") -> None:
        """Set mode for a specific location/cluster."""
        key = mode_key(location, cluster)
        await self.set(key, mode, ttl=300)

    async def delete_mode(self, location: str, cluster: str) -> bool:
        """Delete mode for a specific location/cluster from cache and Redis."""
        key = mode_key(location, cluster)
        return await self.delete(key)

    async def clear(self) -> int:
        """Clear all entries from the cache."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"StateManager: Cleared {count} entries from cache")
            return count

    async def cleanup_expired(self) -> int:
        """Remove all expired entries from the cache."""
        now = time.time()
        expired_keys: list[str] = []

        async with self._lock:
            for key, entry in list(self._cache.items()):
                if now > entry.expires_at:
                    expired_keys.append(key)

            for key in expired_keys:
                del self._cache[key]

        if expired_keys:
            logger.debug(f"StateManager: Cleaned up {len(expired_keys)} expired entries")

        return len(expired_keys)

    async def get_stats(self) -> dict[str, int | float | str]:
        """Get cache statistics."""
        async with self._lock:
            now = time.time()
            expired_count = sum(1 for entry in self._cache.values() if now > entry.expires_at)

            return {
                "total_entries": len(self._cache),
                "expired_entries": expired_count,
                "active_entries": len(self._cache) - expired_count,
                "max_entries": self._max_entries,
                "default_ttl": self._default_ttl,
                "redis_enabled": self._redis_enabled,
            }


# Singleton instance for convenience
_state_manager: StateManager | None = None


def get_state_manager() -> StateManager:
    """Get the global StateManager instance."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


def reset_state_manager() -> None:
    """Reset the global StateManager instance."""
    global _state_manager
    _state_manager = None


__all__ = [
    "StateManager",
    "CacheEntry",
    "get_state_manager",
    "reset_state_manager",
]
