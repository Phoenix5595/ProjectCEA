"""Failsafe management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class FailsafeMixin:
    """Mixin providing failsafe management functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def read_failsafe(self, location: str, cluster: str) -> dict[str, Any] | None:
        if not self.redis_enabled or not self.redis_client:
            return None

        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            failsafe_data = self.redis_client.get(failsafe_key)
            if failsafe_data:
                return json.loads(str(failsafe_data))
        except Exception as e:
            logger.debug(f"Error reading failsafe: {e}")
        return None

    def write_failsafe(
        self,
        location: str,
        cluster: str,
        reason: str,
        triggered_by: str,
        timestamp: int | None = None,
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            timestamp_ms = timestamp or int(datetime.now().timestamp() * 1000)

            failsafe_data = {"reason": reason, "triggered_by": triggered_by, "since": timestamp_ms}

            self.redis_client.set(failsafe_key, json.dumps(failsafe_data))
            logger.warning(
                f"Failsafe triggered for {location}/{cluster}: {reason} (triggered by: {triggered_by})"
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing failsafe to Redis: {e}")
            return False

    def clear_failsafe(self, location: str, cluster: str) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            self.redis_client.delete(failsafe_key)
            logger.info(f"Failsafe cleared for {location}/{cluster}")
            return True
        except Exception as e:
            logger.warning(f"Error clearing failsafe: {e}")
            return False
