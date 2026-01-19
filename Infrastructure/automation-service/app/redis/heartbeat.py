"""Heartbeat management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class HeartbeatMixin:
    """Mixin providing heartbeat management functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_heartbeat(self, service_name: str) -> bool:
        """Write heartbeat for a service.

        Args:
            service_name: Service name (e.g., 'automation-service', 'sensor:clusterA')

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            heartbeat_key = f"heartbeat:{service_name}"
            timestamp_ms = int(datetime.now().timestamp() * 1000)

            # TTL depends on service type
            if service_name == "automation-service":
                ttl = 5  # 5 seconds for automation service
            elif service_name.startswith("sensor:"):
                ttl = 10  # 10 seconds for sensor gateways
            else:
                ttl = 5  # Default 5 seconds

            self.redis_client.setex(heartbeat_key, ttl, str(timestamp_ms))
            return True
        except Exception as e:
            logger.debug(f"Error writing heartbeat: {e}")
            return False

    def check_heartbeat(
        self, service_name: str, max_age_seconds: int = 5
    ) -> tuple[bool, float | None]:
        """Check if service heartbeat is fresh.

        Args:
            service_name: Service name
            max_age_seconds: Maximum age in seconds to consider service alive

        Returns:
            Tuple of (is_alive, age_seconds)
        """
        if not self.redis_enabled or not self.redis_client:
            return False, None

        try:
            heartbeat_key = f"heartbeat:{service_name}"
            heartbeat_str = self.redis_client.get(heartbeat_key)

            if heartbeat_str is None:
                return False, None

            heartbeat_ms = int(str(heartbeat_str))
            now_ms = int(datetime.now().timestamp() * 1000)
            age_seconds = (now_ms - heartbeat_ms) / 1000.0

            return age_seconds <= max_age_seconds, age_seconds
        except Exception as e:
            logger.debug(f"Error checking heartbeat: {e}")
            return False, None
