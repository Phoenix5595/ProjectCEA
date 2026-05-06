"""Schedule state management mixin for Redis client."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.redis.schema import (
    get_with_backward_compat,
    schedule_key,
    set_with_backward_compat,
)
from shared.infra_logging import get_logger
from shared.redis_keys import schedule_state_infix

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class SchedulesMixin:
    """Mixin providing schedule state management."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_schedule_state(
        self, location: str, cluster: str, schedule_data: dict[str, Any]
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            set_with_backward_compat(
                self.redis_client,
                "schedule:state:{location}:{cluster}",
                schedule_key,
                json.dumps(schedule_data),
                None,
                location=location,
                cluster=cluster,
            )
            # Also write the third legacy schedule-state form so older readers
            # that weren't migrated to either of the above shapes keep working.
            self.redis_client.set(
                schedule_state_infix(location, cluster), json.dumps(schedule_data)
            )
            logger.info(f"Wrote schedule state to Redis for {location}/{cluster}")
            return True
        except Exception as e:
            logger.warning(f"Error writing schedule state to Redis: {e}")
            return False

    def read_schedule_state(self, location: str, cluster: str) -> dict[str, Any] | None:
        if not self.redis_enabled or not self.redis_client:
            return None

        try:
            state_data = get_with_backward_compat(
                self.redis_client,
                "schedule:state:{location}:{cluster}",
                schedule_key,
                location=location,
                cluster=cluster,
            )

            if state_data:
                return json.loads(str(state_data))
            # Third legacy form fallback.
            legacy_infix = self.redis_client.get(schedule_state_infix(location, cluster))
            if legacy_infix:
                return json.loads(str(legacy_infix))
        except Exception as e:
            logger.debug(f"Error reading schedule state from Redis: {e}")
        return None
