"""PID parameter cache mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from app.redis.schema import (
    get_with_backward_compat,
    pid_key,
    set_with_backward_compat,
)
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class PIDMixin:
    """Mixin providing PID parameter cache functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def read_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Read PID parameters from Redis cache.

        Args:
            device_type: Device type (e.g., 'heater', 'co2')

        Returns:
            Dict with kp, ki, kd, source, updated_at, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None

        try:
            pid_data = get_with_backward_compat(
                self.redis_client,
                "pid:parameters:{device_type}",
                pid_key,
                device_type,
            )

            if pid_data:
                return json.loads(str(pid_data))
        except Exception as e:
            logger.debug(f"Error reading PID parameters from Redis: {e}")
        return None

    def write_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = "api",
        updated_at: int | None = None,
    ) -> bool:
        """Write PID parameters to Redis cache.

        Args:
            device_type: Device type
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            source: Source of parameters ('api', 'config')
            updated_at: Timestamp in milliseconds (default: current time)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = updated_at or int(datetime.now().timestamp() * 1000)
            pid_ttl = 300  # 5 minutes for PID parameters

            pid_data = {"kp": kp, "ki": ki, "kd": kd, "source": source, "updated_at": timestamp_ms}

            set_with_backward_compat(
                self.redis_client,
                "pid:parameters:{device_type}",
                pid_key,
                json.dumps(pid_data),
                pid_ttl,
                device_type,
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing PID parameters to Redis: {e}")
            return False
