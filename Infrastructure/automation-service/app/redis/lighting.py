"""Lighting management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from app.redis.schema import legacy_light_state_key
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class LightingMixin:
    """Mixin providing light intensity management."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_light_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        intensity: float,
        voltage: float,
        board_id: int,
        channel: int,
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            light_key = legacy_light_state_key(location, cluster, device_name)

            light_data = {
                "intensity": intensity,
                "voltage": voltage,
                "board_id": board_id,
                "channel": channel,
                "timestamp_ms": timestamp_ms,
            }

            self.redis_client.set(light_key, json.dumps(light_data))
            return True
        except Exception as e:
            logger.warning(f"Error writing light intensity to Redis: {e}")
            return False

    def read_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        if not self.redis_enabled or not self.redis_client:
            return None

        try:
            light_key = legacy_light_state_key(location, cluster, device_name)
            light_data = self.redis_client.get(light_key)
            if light_data:
                return json.loads(str(light_data))
        except Exception as e:
            logger.debug(f"Error reading light intensity from Redis: {e}")
        return None
