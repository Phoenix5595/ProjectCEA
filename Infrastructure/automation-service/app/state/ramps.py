"""Ramp state management mixin for StateManager.

Provides get/set/clear operations for active and persisted ramp state,
stored in Redis with TTL-based expiration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from typing import Any, TypedDict

from app.redis.schema import legacy_ramp_key, legacy_ramp_persist_key
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class RampState(TypedDict):
    """Active or persisted ramp state for setpoint transitions."""

    location: str
    cluster: str
    setpoint_type: str
    start_value: float
    target_value: float
    start_time: str
    end_time: str
    ramp_minutes: float


class RampMixin:
    """Mixin adding ramp state methods to StateManager."""

    # ------------------------------------------------------------------
    # Ramp state API (migrated from Redis ramps mixin)
    # All ramp state keys use the same pattern as before:
    #   Active ramp: ramp:{location}:{cluster}:{setpoint_type} with TTL 10
    #   Persisted ramp: ramp_persist:{location}:{cluster}:{setpoint_type} with TTL 7200
    # ------------------------------------------------------------------
    async def get_ramp_state(
        self, location: str, cluster: str, setpoint_type: str
    ) -> RampState | None:
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
        key = legacy_ramp_key(location, cluster, setpoint_type)
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
            return {"raw": data_str}  # pyright: ignore[reportReturnType]

    async def set_ramp_state(
        self, location: str, cluster: str, setpoint_type: str, ramp_data: dict[str, Any]
    ) -> None:
        """Set active ramp state for a given location/cluster/setpoint_type.

        ramp_data is stored as JSON string in Redis to preserve structure.
        TTL is 10 seconds for active ramps.
        """
        key = legacy_ramp_key(location, cluster, setpoint_type)
        try:
            ramp_json = json.dumps(ramp_data)
            await self.set(key, ramp_json, ttl=10)
        except Exception:
            # Do not fail control loop on serialization errors
            logger.exception("StateManager: Failed to set ramp state")

    async def clear_ramp_state(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear active ramp state for a given location/cluster/setpoint_type."""
        key = legacy_ramp_key(location, cluster, setpoint_type)
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
            key = legacy_ramp_persist_key(location, cluster, setpoint_type)
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
            key = legacy_ramp_persist_key(location, cluster, setpoint_type)
            await asyncio.to_thread(self._redis_client.delete, key) if self._redis_client else None
            return True
        except Exception as e:
            logger.warning(f"StateManager: Failed to clear persisted ramp: {e}")
            return False
