"""Stream operations mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING

from app.redis.schema import automation_state_key
from shared.infra_logging import get_logger
from shared.redis_keys import SENSOR_RAW_MAXLEN, SENSOR_RAW_STREAM

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class StreamsMixin:
    """Mixin providing Redis stream write operations."""

    redis_enabled: bool
    redis_client: redis.Redis | None
    stream_client: redis.Redis | None
    redis_ttl: int

    def write_to_stream(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None = None,
        duty_cycle_percent: float | None = None,
        active_rule_ids: list[int] | None = None,
        active_schedule_ids: list[int] | None = None,
        control_reason: str | None = None,
    ) -> bool:
        if not self.redis_enabled or not self.stream_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)

            stream_data = {
                b"id": f"automation_{location}_{cluster}_{device_name}_{timestamp_ms}".encode(),
                b"ts": str(timestamp_ms).encode(),
                b"type": b"automation",
                b"location": location.encode(),
                b"cluster": cluster.encode(),
                b"device_name": device_name.encode(),
                b"device_state": str(device_state).encode(),
                b"device_mode": device_mode.encode(),
            }

            if pid_output is not None:
                stream_data[b"pid_output"] = str(pid_output).encode()
            if duty_cycle_percent is not None:
                stream_data[b"duty_cycle_percent"] = str(duty_cycle_percent).encode()
            if active_rule_ids:
                stream_data[b"active_rule_ids"] = json.dumps(active_rule_ids).encode()
            if active_schedule_ids:
                stream_data[b"active_schedule_ids"] = json.dumps(active_schedule_ids).encode()
            if control_reason:
                stream_data[b"control_reason"] = control_reason.encode()

            self.stream_client.xadd(  # type: ignore
                SENSOR_RAW_STREAM, stream_data, maxlen=SENSOR_RAW_MAXLEN, approximate=True
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis Stream: {e}")
            return False

    def write_to_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None = None,
        duty_cycle_percent: float | None = None,
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            state_key = automation_state_key(location, cluster, device_name)

            state_data = {
                "state": device_state,
                "mode": device_mode,
                "pid_output": pid_output,
                "duty_cycle_percent": duty_cycle_percent,
                "timestamp_ms": timestamp_ms,
            }

            pipe = self.redis_client.pipeline()
            pipe.setex(state_key, self.redis_ttl, json.dumps(state_data))
            pipe.setex(f"{state_key}:ts", self.redis_ttl, str(timestamp_ms))
            pipe.execute()

            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis state: {e}")
            return False
