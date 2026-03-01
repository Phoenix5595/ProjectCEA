"""Sensor value management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class SensorsMixin:
    """Mixin providing sensor last-good-value management."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_last_good_value(
        self, cluster: str, sensor_name: str, value: float, timestamp: int | None = None
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            last_good_key = f"sensor:{cluster}:{sensor_name}:last_good"
            timestamp_ms = timestamp or int(datetime.now().timestamp() * 1000)

            last_good_data = {"value": value, "timestamp": timestamp_ms}

            ttl = 40  # Default hold period (30s) + buffer (10s)

            self.redis_client.setex(last_good_key, ttl, json.dumps(last_good_data))
            return True
        except Exception as e:
            logger.debug(f"Error writing last good value: {e}")
            return False

    def read_last_good_value(self, cluster: str, sensor_name: str) -> dict[str, Any] | None:
        if not self.redis_enabled or not self.redis_client:
            return None

        try:
            last_good_key = f"sensor:{cluster}:{sensor_name}:last_good"
            last_good_data = self.redis_client.get(last_good_key)

            if last_good_data:
                return json.loads(str(last_good_data))
        except Exception as e:
            logger.debug(f"Error reading last good value: {e}")
        return None

    def check_last_good_age(
        self, cluster: str, sensor_name: str, max_age_seconds: int = 30
    ) -> tuple[bool, float | None]:
        if not self.redis_enabled or not self.redis_client:
            return False, None

        try:
            last_good = self.read_last_good_value(cluster, sensor_name)
            if last_good is None:
                return False, None

            timestamp_ms = last_good.get("timestamp", 0)
            now_ms = int(datetime.now().timestamp() * 1000)
            age_seconds = (now_ms - timestamp_ms) / 1000.0

            return age_seconds <= max_age_seconds, age_seconds
        except Exception as e:
            logger.debug(f"Error checking last good age: {e}")
            return False, None
