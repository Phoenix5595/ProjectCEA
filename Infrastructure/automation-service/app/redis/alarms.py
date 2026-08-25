"""Alarm management mixin for Redis client."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from app.redis.schema import alarm_key, alarm_pattern
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class AlarmsMixin:
    """Mixin providing alarm management functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def write_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, message: str
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)

            existing_data = self.redis_client.get(alarm_key(location, cluster, alarm_name))
            if existing_data:
                existing = json.loads(str(existing_data))
                since = existing.get("since", timestamp_ms)
            else:
                since = timestamp_ms

            alarm_data = {
                "active": True,
                "severity": severity,
                "message": message,
                "since": since,
                "acknowledged": False,
            }

            self.redis_client.set(alarm_key(location, cluster, alarm_name), json.dumps(alarm_data))

            if severity == "critical":
                logger.error(f"CRITICAL ALARM: {location}/{cluster}/{alarm_name}: {message}")
            elif severity == "warning":
                logger.warning(f"WARNING ALARM: {location}/{cluster}/{alarm_name}: {message}")
            else:
                logger.info(f"INFO ALARM: {location}/{cluster}/{alarm_name}: {message}")

            return True
        except Exception as e:
            logger.warning(f"Error writing alarm to Redis: {e}")
            return False

    def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            alarm_data = self.redis_client.get(alarm_key(location, cluster, alarm_name))

            if alarm_data:
                alarm = json.loads(str(alarm_data))
                alarm["acknowledged"] = True
                self.redis_client.set(alarm_key(location, cluster, alarm_name), json.dumps(alarm))
                logger.info(f"Alarm acknowledged: {location}/{cluster}/{alarm_name}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Error acknowledging alarm: {e}")
            return False

    def read_alarms(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        if not self.redis_enabled or not self.redis_client:
            return {}

        try:
            # Scan the canonical key pattern
            alarms = {}

            for key in self.redis_client.scan_iter(match=alarm_pattern(location, cluster)):
                alarm_data = self.redis_client.get(key)
                if alarm_data:
                    try:
                        alarm = json.loads(str(alarm_data))
                        if alarm.get("active", False):
                            alarm_name = key.split(":")[-1]
                            alarms[alarm_name] = alarm
                    except (json.JSONDecodeError, IndexError):
                        pass

            return alarms
        except Exception as e:
            logger.warning(f"Error reading alarms: {e}")
            return {}

    def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            alarm_data = self.redis_client.get(alarm_key(location, cluster, alarm_name))

            if alarm_data:
                alarm = json.loads(str(alarm_data))
                alarm["active"] = False
                self.redis_client.set(alarm_key(location, cluster, alarm_name), json.dumps(alarm))
                logger.info(f"Alarm cleared: {location}/{cluster}/{alarm_name}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Error clearing alarm: {e}")
            return False
