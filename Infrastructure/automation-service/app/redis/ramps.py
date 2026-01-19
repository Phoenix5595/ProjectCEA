"""Ramp state management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import TYPE_CHECKING, Any

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class RampsMixin:
    """Mixin providing ramp state management functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_ramp_state(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        current_effective_setpoint: float,
        ramp_start_timestamp: datetime,
        ramp_duration: int,
        target_setpoint: float,
    ) -> bool:
        """Write ramp state to Redis.

        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint being ramped
            current_effective_setpoint: Current effective setpoint value
            ramp_start_timestamp: When the ramp started
            ramp_duration: Ramp duration in minutes
            target_setpoint: Target setpoint value

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            ramp_ttl = 10
            ramp_data = {
                "current_effective_setpoint": current_effective_setpoint,
                "ramp_start_timestamp": ramp_start_timestamp.isoformat(),
                "ramp_duration": ramp_duration,
                "target_setpoint": target_setpoint,
            }
            self.redis_client.setex(ramp_key, ramp_ttl, json.dumps(ramp_data))
            logger.debug(
                f"Wrote ramp state for {setpoint_type} ({location}/{cluster}): "
                f"current={current_effective_setpoint:.2f}, target={target_setpoint:.2f}, "
                f"duration={ramp_duration}min"
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing ramp state to Redis: {e}")
            return False

    def read_ramp_state(
        self, location: str, cluster: str, setpoint_type: str
    ) -> dict[str, Any] | None:
        """Read ramp state from Redis.

        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint

        Returns:
            Dict with ramp state, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            ramp_data = self.redis_client.get(ramp_key)
            if ramp_data:
                return json.loads(str(ramp_data))
        except Exception as e:
            logger.debug(f"Error reading ramp state from Redis: {e}")
        return None

    def clear_ramp_state(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear ramp state from Redis.

        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            self.redis_client.delete(ramp_key)
            logger.debug(f"Cleared ramp state for {setpoint_type} ({location}/{cluster})")
            return True
        except Exception as e:
            logger.warning(f"Error clearing ramp state from Redis: {e}")
            return False

    def persist_ramp(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        start_value: float,
        target_value: float,
        duration_minutes: int,
        start_time: datetime,
    ) -> bool:
        """Persist ramp state for recovery after restart (2 hour TTL)."""
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            key = f"ramp_persist:{location}:{cluster}:{setpoint_type}"
            data = {
                "start_value": start_value,
                "target_value": target_value,
                "duration_minutes": duration_minutes,
                "start_time": start_time.isoformat(),
            }
            self.redis_client.setex(key, 7200, json.dumps(data))
            logger.debug(f"Persisted ramp {location}/{cluster}/{setpoint_type}")
            return True
        except Exception as e:
            logger.warning(f"Failed to persist ramp: {e}")
            return False

    def get_persisted_ramps(self) -> list:
        """Get all persisted ramps for restoration."""
        if not self.redis_enabled or not self.redis_client:
            return []
        try:
            keys = self.redis_client.keys("ramp_persist:*")
            ramps = []
            now = datetime.now()
            for key in keys:
                try:
                    data = self.redis_client.get(key)
                    if not data:
                        continue
                    ramp = json.loads(data)
                    start_time = datetime.fromisoformat(ramp["start_time"])
                    end_time = start_time + timedelta(minutes=ramp["duration_minutes"])

                    if now >= end_time:
                        self.redis_client.delete(key)
                        continue

                    parts = key.decode() if isinstance(key, bytes) else key
                    parts = parts.split(":")
                    if len(parts) >= 4:
                        ramps.append(
                            {
                                "location": parts[1],
                                "cluster": parts[2],
                                "setpoint_type": parts[3],
                                "start_value": ramp["start_value"],
                                "target_value": ramp["target_value"],
                                "duration_minutes": ramp["duration_minutes"],
                                "start_time": start_time,
                            }
                        )
                except Exception as e:
                    logger.warning(f"Error reading persisted ramp {key}: {e}")
            return ramps
        except Exception as e:
            logger.error(f"Failed to get persisted ramps: {e}")
            return []

    def clear_persisted_ramp(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear a persisted ramp after completion."""
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            key = f"ramp_persist:{location}:{cluster}:{setpoint_type}"
            self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Failed to clear persisted ramp: {e}")
            return False
