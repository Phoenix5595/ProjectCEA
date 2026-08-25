"""Alarm and failsafe state management mixin for StateManager.

Provides read/write/clear operations for alarms and failsafe state.
Alarms and failsafe entries are stored without TTL so they persist
until explicitly acknowledged or cleared.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import Any, TypedDict

from app.redis.schema import (
    alarm_key,
    alarm_prefix,
    legacy_failsafe_key,
)
from app.state._types import CacheEntry
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class AlarmDict(TypedDict):
    """Alarm entry as stored in Redis / in-memory cache."""

    type: str
    message: str
    severity: str
    timestamp: str
    acknowledged: bool


class FailsafeState(TypedDict, total=False):
    """Failsafe state for a location/cluster pair."""

    active: bool
    reason: str
    timestamp: str
    device: str
    severity: str


class AlarmMixin:
    """Mixin adding alarm and failsafe state methods to StateManager."""

    # ------------------------------------------------------------------
    # Alarm & Failsafe state management (migrated from Redis mixin)
    # ------------------------------------------------------------------
    async def get_alarms(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Return all active alarms for a given location/cluster.

        Combines in-memory alarm cache (no TTL) with Redis-backed alarms.
        Returns a mapping of alarm_name -> alarm_data.
        """
        alarms: dict[str, dict[str, Any]] = {}
        prefix = alarm_prefix(location, cluster)

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
        key = alarm_key(location, cluster, alarm_name)
        timestamp_ms = int(datetime.now().timestamp() * 1000)

        try:
            async with self._lock:
                existing = self._alarms_cache.get(key)
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
                self._alarms_cache[key] = CacheEntry(value=alarm, expires_at=float("inf"))

            # Persist to Redis without TTL (alarm persists until acknowledged/cleared).
            if self._redis_enabled and self._redis_client:
                try:
                    await asyncio.to_thread(self._redis_client.set, key, json.dumps(alarm))
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
            logger.warning(f"StateManager: Failed to write alarm {key}: {e}")
            return False

    async def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        key = alarm_key(location, cluster, alarm_name)
        try:
            updated = False
            # Update in-memory cache first
            async with self._lock:
                if key in self._alarms_cache:
                    alarm = (
                        dict(self._alarms_cache[key].value)
                        if isinstance(self._alarms_cache[key].value, dict)
                        else {}
                    )
                    alarm["acknowledged"] = True
                    self._alarms_cache[key] = CacheEntry(value=alarm, expires_at=float("inf"))
                    updated = True
                    data = alarm
                else:
                    data = None

            if updated and data is not None and self._redis_enabled and self._redis_client:
                await asyncio.to_thread(self._redis_client.set, key, json.dumps(data))
                logger.info(f"Alarm acknowledged: {location}/{cluster}/{alarm_name}")
                return True

            # Fallback: try Redis directly
            if self._redis_enabled and self._redis_client:
                raw = await asyncio.to_thread(self._redis_client.get, key)
                if raw:
                    alarm = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                    alarm["acknowledged"] = True
                    await asyncio.to_thread(self._redis_client.set, key, json.dumps(alarm))
                    # Update in-memory cache if present
                    async with self._lock:
                        self._alarms_cache[key] = CacheEntry(value=alarm, expires_at=float("inf"))
                    logger.info(f"Alarm acknowledged (Redis): {location}/{cluster}/{alarm_name}")
                    return True
            return False
        except Exception as e:
            logger.warning(f"StateManager: Error acknowledging alarm: {e}")
            return False

    async def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        key = alarm_key(location, cluster, alarm_name)
        try:
            cleared = False
            async with self._lock:
                if key in self._alarms_cache:
                    alarm = (
                        dict(self._alarms_cache[key].value)
                        if isinstance(self._alarms_cache[key].value, dict)
                        else {}
                    )
                    alarm["active"] = False
                    self._alarms_cache[key] = CacheEntry(value=alarm, expires_at=float("inf"))
                    cleared = True
            if cleared:
                if self._redis_enabled and self._redis_client:
                    await asyncio.to_thread(self._redis_client.set, key, json.dumps(alarm))
                logger.info(f"Alarm cleared: {location}/{cluster}/{alarm_name}")
                return True

            # Fallback: try reading and clearing in Redis
            if self._redis_enabled and self._redis_client:
                raw = await asyncio.to_thread(self._redis_client.get, key)
                if raw:
                    alarm = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                    alarm["active"] = False
                    await asyncio.to_thread(self._redis_client.set, key, json.dumps(alarm))
                    logger.info(f"Alarm cleared (Redis): {location}/{cluster}/{alarm_name}")
                    return True
            return False
        except Exception as e:
            logger.warning(f"StateManager: Error clearing alarm: {e}")
            return False

    async def get_failsafe(self, location: str, cluster: str) -> dict[str, Any] | None:
        key = legacy_failsafe_key(location, cluster)
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
        key = legacy_failsafe_key(location, cluster)
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
        key = legacy_failsafe_key(location, cluster)
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
