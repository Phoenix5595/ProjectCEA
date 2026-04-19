"""State manager module for automation service.

This module provides the StateManager class for in-memory state management
with TTL (time-to-live) support and Redis fallback for cross-service visibility.

DATA FLOW (Source of Truth):
============================
Redis is the source of truth for most data. StateManager is a read-through cache.

| Data Type          | Source of Truth | StateManager Role          |
|--------------------|-----------------|----------------------------|
| Effective Setpoints| Redis           | Cache only (DEAD CODE - see below) |
| Schedules          | PostgreSQL      | Cache-aside (populated on read) |
| Modes              | Redis           | Dual-write (cache + Redis) |
| PID params         | Redis           | Dual-write (cache + Redis) |
| Ramp state         | Redis           | Dual-write (cache + Redis) |
| Alarms/Failsafe    | Redis           | Dual-write (no TTL)        |

IMPORTANT: SetpointRepository writes effective_setpoints directly to Redis via
redis_client.write_effective_setpoints(). StateManager.get_effective_setpoints()
and set_effective_setpoints() are DEAD CODE - never called by production code.
They are removed to eliminate confusion.

Schedules use cache-aside: DB is source, StateManager caches on read,
invalidation happens via event bus + direct delete on write.

USAGE:
    from app.state import StateManager

    state = StateManager(default_ttl=60.0, max_entries=1000)
    await state.set_redis_client(redis_client)
    await state.initialize_from_redis()

    # Get/set values (dual-write to Redis by default)
    value = await state.get("some:key")
    await state.set("some:key", {"data": "value"}, ttl=120.0)
    await state.delete("some:key")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import time
from typing import TYPE_CHECKING, Any, Generic, TypeVar

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


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with value and expiration time.

    Attributes:
        value: The cached value
        expires_at: Unix timestamp when this entry expires
        created_at: Unix timestamp when this entry was created
    """

    value: T
    expires_at: float
    created_at: float = field(default_factory=time.time)


class StateManager(SchemaValidationMixin):
    """In-memory state manager with TTL and Redis fallback.

    Provides fast in-memory caching for internal state with:
    - TTL (time-to-live) support for automatic expiration
    - Memory bounds via max_entries to prevent unbounded growth
    - Thread-safe operations using asyncio.Lock
    - Graceful fallback to Redis on cache miss
    - Dual-write to both cache and Redis for cross-service visibility

    This is designed for state that's only used within automation-service.
    Cross-service state (sensor:*, automation:*) should stay in Redis directly.

    Attributes:
        default_ttl: Default TTL in seconds for cache entries
        max_entries: Maximum number of entries in cache before eviction
    """

    def __init__(
        self,
        default_ttl: float = 60.0,
        max_entries: int = 1000,
        validate_keys: bool = False,
    ) -> None:
        """Initialize the state manager.

        Args:
            default_ttl: Default TTL in seconds for cache entries (default: 60s)
            max_entries: Maximum entries before LRU eviction (default: 1000)
        """
        # Initialize mixin (if present) to enable validation utilities
        try:
            super().__init__()
        except TypeError:
            # No-op if the MRO does not require extra args
            pass

        self._cache: dict[str, CacheEntry[Any]] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._redis_client: redis.Redis | None = None
        self._redis_enabled: bool = False
        # In-memory alarm and failsafe stores (no TTL) to persist until acknowledged/cleared
        # Keys for alarms follow the Redis key pattern: alarm:<location>:<cluster>:<alarm_name>
        self._alarms_cache: dict[str, CacheEntry[Any]] = {}
        # Falls back to a dedicated in-memory store for failsafe state
        self._failsafe_cache: dict[str, CacheEntry[Any]] = {}
        # Whether to validate keys and enforce TTL categories on set
        self._validate_keys: bool = validate_keys

    def set_redis_client(self, redis_client: redis.Redis | None) -> None:
        """Set the Redis client for fallback and dual-write.

        Args:
            redis_client: Redis client instance or None to disable Redis
        """
        self._redis_client = redis_client
        self._redis_enabled = redis_client is not None
        if self._redis_enabled:
            logger.info("StateManager: Redis fallback enabled")
        else:
            logger.info("StateManager: Running in memory-only mode")

    async def get(self, key: str) -> Any | None:
        """Get value from cache with Redis fallback.

        First checks the in-memory cache. If the entry is missing or expired,
        falls back to Redis if available.

        Args:
            key: Cache key to retrieve

        Returns:
            Cached value or None if not found/expired
        """
        async with self._lock:
            entry = self._cache.get(key)

            if entry is not None:
                # Check if expired
                if time.time() <= entry.expires_at:
                    logger.debug(f"StateManager: Cache hit for '{key}'")
                    return entry.value
                else:
                    # Expired, remove from cache
                    del self._cache[key]
                    logger.debug(f"StateManager: Cache expired for '{key}'")

        # Cache miss or expired - try Redis fallback
        return await self._get_from_redis(key)

    # ------------------------------------------------------------------
    # PID Parameter API (migrated from Redis mixin)
    # ------------------------------------------------------------------
    async def get_pid_params(self, device_type: str) -> dict[str, Any] | None:
        """Get PID parameters for a given device type from cache/Redis.

        Args:
            device_type: Device type (e.g., 'heater', 'co2')

        Returns:
            Dict containing kp/ki/kd and metadata, or None if not found
        """
        pid_key = f"pid:parameters:{device_type}"
        data = await self.get(pid_key)
        if data is None:
            return None

        if isinstance(data, (bytes, bytearray)):
            data_str = data.decode()
        elif isinstance(data, str):
            data_str = data
        else:
            return data  # Already a dict-like object

        try:
            return json.loads(data_str)
        except Exception:
            return {"raw": data_str}

    async def set_pid_params(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = "api",
        updated_at: int | None = None,
    ) -> bool:
        """Set PID parameters for a given device type with a 300s TTL.

        Args:
            device_type: Device type
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            source: Source of parameters ('api', 'config')
            updated_at: Optional timestamp in milliseconds

        Returns:
            True if written successfully, False otherwise
        """
        pid_key = f"pid:parameters:{device_type}"
        timestamp_ms = updated_at or int(time.time() * 1000)
        payload = {
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "source": source,
            "updated_at": timestamp_ms,
        }
        try:
            await self.set(pid_key, json.dumps(payload), ttl=300)
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to set PID params for {device_type}: {e}")
            return False

    # Autotune state handling for PID controllers
    async def get_autotune_state(self, device_type: str) -> dict[str, Any] | None:
        """Get autotune state for a device's PID controller."""
        key = f"pid:autotune:{device_type}"
        data = await self.get(key)
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            data_str = data.decode()
        elif isinstance(data, str):
            data_str = data
        else:
            return data  # dict-like
        try:
            return json.loads(data_str)
        except Exception:
            return {"raw": data_str}

    async def set_autotune_state(
        self, device_type: str, state: dict[str, Any], ttl: int = 300
    ) -> None:
        """Set autotune state for a device's PID controller with a TTL."""
        key = f"pid:autotune:{device_type}"
        try:
            await self.set(key, json.dumps(state), ttl=ttl)
        except Exception as e:
            logger.warning(f"StateManager: Failed to set autotune state for {device_type}: {e}")

    # ------------------------------------------------------------------
    # Ramp state API (migrated from Redis ramps mixin)
    # All ramp state keys use the same pattern as before:
    #   Active ramp: ramp:{location}:{cluster}:{setpoint_type} with TTL 10
    #   Persisted ramp: ramp_persist:{location}:{cluster}:{setpoint_type} with TTL 7200
    # ------------------------------------------------------------------
    async def get_ramp_state(
        self, location: str, cluster: str, setpoint_type: str
    ) -> dict[str, Any] | None:
        """Get active ramp state for a given location/cluster/setpoint_type.

        Returns the ramp data as a dict, or None if not present.
        Expected ramp data shape (as previously stored in Redis):
        {
            "current_effective_setpoint": <float>,
            "ramp_start_timestamp": <ISO8601 str>,
            "ramp_duration": <int>,
            "target_setpoint": <float>,
        }
        """
        key = f"ramp:{location}:{cluster}:{setpoint_type}"
        data = await self.get(key)
        if data is None:
            return None
        # Normalize to a dict
        if isinstance(data, (bytes, bytearray)):
            data_str = data.decode()
        elif isinstance(data, str):
            data_str = data
        else:
            # In-memory storage might already be a dict
            return data  # type: ignore[return-value]
        try:
            return json.loads(data_str)
        except Exception:
            return {"raw": data_str}

    async def set_ramp_state(
        self, location: str, cluster: str, setpoint_type: str, ramp_data: dict[str, Any]
    ) -> None:
        """Set active ramp state for a given location/cluster/setpoint_type.

        ramp_data is stored as JSON string in Redis to preserve structure.
        TTL is 10 seconds for active ramps.
        """
        key = f"ramp:{location}:{cluster}:{setpoint_type}"
        try:
            ramp_json = json.dumps(ramp_data)
            await self.set(key, ramp_json, ttl=10)
        except Exception:
            # Do not fail control loop on serialization errors
            logger.exception("StateManager: Failed to set ramp state")

    async def clear_ramp_state(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear active ramp state for a given location/cluster/setpoint_type."""
        key = f"ramp:{location}:{cluster}:{setpoint_type}"
        return await self.delete(key)

    async def persist_ramp(
        self, location: str, cluster: str, setpoint_type: str, ramp_data: dict[str, Any]
    ) -> bool:
        """Persist ramp state for restart recovery (2 hour TTL).

        ramp_data should contain keys like:
          - start_value
          - target_value
          - duration_minutes
          - start_time (ISO8601 string)
        Returns True on success, False otherwise.
        """
        if not self._redis_enabled or not self._redis_client:
            return False
        try:
            key = f"ramp_persist:{location}:{cluster}:{setpoint_type}"
            ramp_json = json.dumps(ramp_data)
            # 7200 seconds TTL for persisted ramps
            await asyncio.to_thread(self._redis_client.setex, key, 7200, ramp_json)  # type: ignore[arg-type]
            logger.info(f"Persisted ramp for {location}/{cluster}/{setpoint_type} (TTL=7200s)")
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to persist ramp: {e}")
            return False

    async def get_persisted_ramps(self) -> list[dict[str, Any]]:
        """Return all currently persisted ramps from Redis."""
        if not self._redis_enabled or not self._redis_client:
            return []
        try:
            # Obtain all keys for persisted ramps
            keys_raw = self._redis_client.keys("ramp_persist:")
            keys = list(keys_raw) if keys_raw is not None else []  # ensure iterable
            ramps: list[dict[str, Any]] = []
            now = datetime.now()
            for key in keys:
                try:
                    data = self._redis_client.get(key)  # type: ignore
                    if not data:
                        continue
                    data_str = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
                    ramp = json.loads(data_str)
                    start_time = datetime.fromisoformat(ramp["start_time"])
                    end_time = start_time + timedelta(minutes=int(ramp["duration_minutes"]))
                    if now >= end_time:
                        # Expired; remove key
                        self._redis_client.delete(key)
                        continue
                    # Extract location/cluster/setpoint_type from key
                    parts = key.decode() if isinstance(key, bytes) else key
                    parts = parts.split(":")
                    if len(parts) >= 4:
                        ramps.append(
                            {
                                "location": parts[1],
                                "cluster": parts[2],
                                "setpoint_type": parts[3],
                                "start_value": ramp.get("start_value"),
                                "target_value": ramp.get("target_value"),
                                "duration_minutes": ramp.get("duration_minutes"),
                                "start_time": start_time,
                            }
                        )
                except Exception as e:
                    logger.warning(f"StateManager: Error reading persisted ramp {key}: {e}")
            return ramps
        except Exception as e:
            logger.error(f"StateManager: Failed to get persisted ramps: {e}")
            return []

    async def clear_persisted_ramp(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear a specific persisted ramp."""
        if not self._redis_enabled or not self._redis_client:
            return False
        try:
            key = f"ramp_persist:{location}:{cluster}:{setpoint_type}"
            await asyncio.to_thread(self._redis_client.delete, key) if self._redis_client else None
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to clear persisted ramp: {e}")
            return False

    async def set(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
        redis_ttl: int | None = None,
        skip_redis: bool = False,
    ) -> None:
        """Set value in cache with TTL, optionally writing to Redis.

        Stores the value in the in-memory cache with the specified TTL.
        Also writes to Redis for cross-service visibility unless skip_redis=True.

        Args:
            key: Cache key to set
            value: Value to cache
            ttl: TTL in seconds (uses default_ttl if None)
            redis_ttl: TTL for Redis in seconds (uses ttl if None)
            skip_redis: If True, skip writing to Redis (internal state only)
        """
        # Optional key validation (non-breaking by default)
        if getattr(self, "_validate_keys", False):
            try:
                valid = False
                # Use mixin-provided validation if available
                if hasattr(self, "validate_key_format"):
                    valid = bool(self.validate_key_format(key))
                if not valid:
                    logger.warning("StateManager: Key format validation failed for '%s'", key)
            except Exception as ve:
                logger.warning(
                    "StateManager: Key validation raised exception for '%s': %s", key, ve
                )

        effective_ttl = ttl if ttl is not None else self._default_ttl
        # TTL override for Redis based on key type (category enforcement)
        key_type = key.split(":")[0] if isinstance(key, str) and ":" in key else key
        ttl_from_key = None
        try:
            ttl_from_key = get_ttl_by_key_type(key_type)  # type: ignore
        except Exception:
            ttl_from_key = None
        redis_ttl_effective = redis_ttl if redis_ttl is not None else int(effective_ttl)
        if ttl_from_key is not None and redis_ttl is None:
            # Enforce TTL category for Redis when not explicitly provided
            redis_ttl_effective = int(ttl_from_key)
            logger.debug(
                "StateManager: Enforcing Redis TTL from key type '%s' => %ds for key '%s'",
                key_type,
                ttl_from_key,
                key,
            )

        expires_at = time.time() + effective_ttl

        async with self._lock:
            # Check if we need to evict
            if len(self._cache) >= self._max_entries and key not in self._cache:
                await self._evict_oldest()

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=expires_at,
            )
            logger.debug(f"StateManager: Set '{key}' with TTL {effective_ttl}s")

        # Dual-write to Redis for cross-service visibility
        if not skip_redis:
            await self._write_to_redis(key, value, redis_ttl_effective)

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        """Delete key from cache and optionally Redis.

        Args:
            key: Cache key to delete
            skip_redis: If True, skip deleting from Redis

        Returns:
            True if key was in cache, False otherwise
        """
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
        """Check if key exists in cache (and is not expired).

        Args:
            key: Cache key to check

        Returns:
            True if key exists and is not expired
        """
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if time.time() > entry.expires_at:
                del self._cache[key]
                return False
        return True

    # ------------------------------
    # Mode-specific convenience API
    # ------------------------------
    async def get_mode(self, location: str, cluster: str) -> str | None:
        """Get mode for a specific location/cluster from cache.

        This is a small convenience wrapper around the generic get
        so callers can query mode state directly without composing keys.
        """
        key = f"mode:{location}:{cluster}"
        return await self.get(key)

    async def set_mode(self, location: str, cluster: str, mode: str, source: str = "api") -> None:
        """Set mode for a specific location/cluster.

        Writes to the in-memory cache with a TTL and also propagates
        the value to Redis for cross-service visibility.

        TTL for mode keys is fixed at 300 seconds (5 minutes).
        """
        key = f"mode:{location}:{cluster}"
        await self.set(key, mode, ttl=300)  # 5 minutes TTL and Redis write

    async def delete_mode(self, location: str, cluster: str) -> bool:
        """Delete mode for a specific location/cluster from cache and Redis."""
        key = f"mode:{location}:{cluster}"
        return await self.delete(key)

    async def clear(self) -> int:
        """Clear all entries from the cache.

        Returns:
            Number of entries cleared
        """
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"StateManager: Cleared {count} entries from cache")
            return count

    async def cleanup_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            Number of entries removed
        """
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

    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
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

    # ------------------------------------------------------------------
    # Alarm & Failsafe state management (migrated from Redis mixin)
    # ------------------------------------------------------------------
    async def get_alarms(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Return all active alarms for a given location/cluster.

        Combines in-memory alarm cache (no TTL) with Redis-backed alarms.
        Returns a mapping of alarm_name -> alarm_data.
        """
        alarms: dict[str, dict[str, Any]] = {}
        prefix = f"alarm:{location}:{cluster}:"

        # In-memory alarms
        async with self._lock:
            for key, entry in list(self._alarms_cache.items()):
                if key.startswith(prefix):
                    alarm_name = key.split(":")[-1]
                    alarm = entry.value if isinstance(entry.value, dict) else {}
                    if alarm.get("active", False):
                        alarms[alarm_name] = alarm

        # Redis-backed alarms
        if self._redis_enabled and self._redis_client:
            try:
                for key in self._redis_client.scan_iter(match=prefix + "*"):
                    raw = self._redis_client.get(key)
                    if raw:
                        s = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                        try:
                            alarm = json.loads(s)
                        except Exception:
                            continue
                        if alarm.get("active", False):
                            alarm_name = (
                                key.decode().split(":")[-1]
                                if isinstance(key, (bytes, bytearray))
                                else key.split(":")[-1]
                            )
                            alarms[alarm_name] = alarm
            except Exception as e:
                logger.warning(f"StateManager: Error reading alarms from Redis: {e}")

        return alarms

    async def write_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, message: str
    ) -> bool:
        """Write or update an alarm in-memory (no TTL) and persist to Redis."""
        alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
        timestamp_ms = int(datetime.now().timestamp() * 1000)

        try:
            async with self._lock:
                existing = self._alarms_cache.get(alarm_key)
                since = (
                    existing.value.get("since")
                    if existing and isinstance(existing.value, dict)
                    else timestamp_ms
                )
                alarm = {
                    "active": True,
                    "severity": severity,
                    "message": message,
                    "since": since,
                    "acknowledged": False,
                }
                self._alarms_cache[alarm_key] = CacheEntry(value=alarm, expires_at=float("inf"))

            # Persist to Redis without TTL (alarm persists until acknowledged/cleared)
            if self._redis_enabled and self._redis_client:
                try:
                    await asyncio.to_thread(self._redis_client.set, alarm_key, json.dumps(alarm))
                except Exception as e:
                    logger.warning(f"StateManager: Failed to write alarm to Redis: {e}")

            if severity == "critical":
                logger.error(f"CRITICAL ALARM: {location}/{cluster}/{alarm_name}: {message}")
            elif severity == "warning":
                logger.warning(f"WARNING ALARM: {location}/{cluster}/{alarm_name}: {message}")
            else:
                logger.info(f"INFO ALARM: {location}/{cluster}/{alarm_name}: {message}")

            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to write alarm {alarm_key}: {e}")
            return False

    async def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
        try:
            updated = False
            # Update in-memory cache first
            async with self._lock:
                if alarm_key in self._alarms_cache:
                    alarm = (
                        dict(self._alarms_cache[alarm_key].value)
                        if isinstance(self._alarms_cache[alarm_key].value, dict)
                        else {}
                    )
                    alarm["acknowledged"] = True
                    self._alarms_cache[alarm_key] = CacheEntry(value=alarm, expires_at=float("inf"))
                    updated = True
                    data = alarm
                else:
                    data = None

            if updated and data is not None and self._redis_enabled and self._redis_client:
                await asyncio.to_thread(self._redis_client.set, alarm_key, json.dumps(data))
                logger.info(f"Alarm acknowledged: {location}/{cluster}/{alarm_name}")
                return True

            # Fallback: try Redis directly
            if self._redis_enabled and self._redis_client:
                raw = await asyncio.to_thread(self._redis_client.get, alarm_key)
                if raw:
                    alarm = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                    alarm["acknowledged"] = True
                    await asyncio.to_thread(self._redis_client.set, alarm_key, json.dumps(alarm))
                    # Update in-memory cache if present
                    async with self._lock:
                        self._alarms_cache[alarm_key] = CacheEntry(
                            value=alarm, expires_at=float("inf")
                        )
                    logger.info(f"Alarm acknowledged (Redis): {location}/{cluster}/{alarm_name}")
                    return True
            return False
        except Exception as e:
            logger.warning(f"StateManager: Error acknowledging alarm: {e}")
            return False

    async def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
        try:
            cleared = False
            async with self._lock:
                if alarm_key in self._alarms_cache:
                    alarm = (
                        dict(self._alarms_cache[alarm_key].value)
                        if isinstance(self._alarms_cache[alarm_key].value, dict)
                        else {}
                    )
                    alarm["active"] = False
                    self._alarms_cache[alarm_key] = CacheEntry(value=alarm, expires_at=float("inf"))
                    cleared = True
            if cleared:
                if self._redis_enabled and self._redis_client:
                    await asyncio.to_thread(self._redis_client.set, alarm_key, json.dumps(alarm))
                logger.info(f"Alarm cleared: {location}/{cluster}/{alarm_name}")
                return True

            # Fallback: try reading and clearing in Redis
            if self._redis_enabled and self._redis_client:
                raw = await asyncio.to_thread(self._redis_client.get, alarm_key)
                if raw:
                    alarm = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                    alarm["active"] = False
                    await asyncio.to_thread(self._redis_client.set, alarm_key, json.dumps(alarm))
                    logger.info(f"Alarm cleared (Redis): {location}/{cluster}/{alarm_name}")
                    return True
            return False
        except Exception as e:
            logger.warning(f"StateManager: Error clearing alarm: {e}")
            return False

    async def get_failsafe(self, location: str, cluster: str) -> dict[str, Any] | None:
        key = f"failsafe:{location}:{cluster}"
        # In-memory first; if the local lookup races with eviction or hits a
        # corrupted cache entry, log and fall through to the Redis fallback
        # below rather than fail the read.
        try:
            async with self._lock:
                if key in self._failsafe_cache:
                    val = self._failsafe_cache[key].value
                    return val if isinstance(val, dict) else None
        except Exception as e:
            logger.debug("failsafe in-memory cache lookup failed for %s: %s", key, e)

        # Redis fallback
        if self._redis_enabled and self._redis_client:
            try:
                raw = await asyncio.to_thread(self._redis_client.get, key)
                if raw:
                    s = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                    data = json.loads(s)
                    async with self._lock:
                        self._failsafe_cache[key] = CacheEntry(value=data, expires_at=float("inf"))
                    return data
            except Exception as e:
                logger.warning(f"StateManager: Error reading failsafe from Redis: {e}")
        return None

    async def set_failsafe(self, location: str, cluster: str, state: dict[str, Any]) -> bool:
        key = f"failsafe:{location}:{cluster}"
        try:
            async with self._lock:
                self._failsafe_cache[key] = CacheEntry(value=state, expires_at=float("inf"))
            if self._redis_enabled and self._redis_client:
                await asyncio.to_thread(self._redis_client.set, key, json.dumps(state))
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to set failsafe: {e}")
            return False

    async def clear_failsafe(self, location: str, cluster: str) -> bool:
        key = f"failsafe:{location}:{cluster}"
        try:
            async with self._lock:
                if key in self._failsafe_cache:
                    del self._failsafe_cache[key]
            if self._redis_enabled and self._redis_client:
                await asyncio.to_thread(self._redis_client.delete, key)
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to clear failsafe: {e}")
            return False

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


# Singleton instance for convenience
_state_manager: StateManager | None = None


def get_state_manager() -> StateManager:
    """Get the global StateManager instance.

    Creates a new instance if one doesn't exist.

    Returns:
        Global StateManager instance
    """
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


def reset_state_manager() -> None:
    """Reset the global StateManager instance.

    Useful for testing or service restart.
    """
    global _state_manager
    _state_manager = None


__all__ = [
    "StateManager",
    "CacheEntry",
    "get_state_manager",
    "reset_state_manager",
]
