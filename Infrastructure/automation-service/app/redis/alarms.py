"""Alarm management mixin for Redis client."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any

from app.redis.schema import alarm_key, alarm_pattern, all_alarm_pattern
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


def _decode_alarm(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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
                alarm = json.loads(
                    alarm_data.decode() if isinstance(alarm_data, bytes) else str(alarm_data)
                )
                alarm["acknowledged"] = True
                alarm["acknowledged_at"] = int(datetime.now(UTC).timestamp() * 1000)
                alarm["acknowledged_by"] = "api_client"
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
            alarms: dict[str, dict[str, Any]] = {}
            for key in self.redis_client.scan_iter(match=alarm_pattern(location, cluster)):
                raw_key = key.decode() if isinstance(key, bytes) else str(key)
                parts = raw_key.split(":", 4)
                if len(parts) != 5 or parts[:2] != ["cea", "alarm"] or not parts[4]:
                    continue
                alarm = _decode_alarm(self.redis_client.get(key))
                if alarm is None or not alarm.get("active", False):
                    continue
                alarm_name = parts[4]
                alarms[alarm_name] = {
                    **alarm,
                    "location": location,
                    "cluster": cluster,
                    "alarm_name": alarm_name,
                }
            return alarms
        except Exception as error:
            logger.warning(f"Error reading alarms: {error}")
            return {}

    def read_all_alarms(self) -> dict[str, dict[str, Any]]:
        """Scan every canonical alarm key and return active durable records."""
        if not self.redis_enabled or not self.redis_client:
            return {}

        try:
            alarms: dict[str, dict[str, Any]] = {}
            for key in self.redis_client.scan_iter(match=all_alarm_pattern()):
                raw_key = key.decode() if isinstance(key, bytes) else str(key)
                parts = raw_key.split(":", 4)
                if len(parts) != 5 or parts[:2] != ["cea", "alarm"]:
                    continue
                location, cluster, alarm_name = parts[2], parts[3], parts[4]
                if not location or not cluster or not alarm_name:
                    continue
                alarm = _decode_alarm(self.redis_client.get(key))
                if alarm is None or not alarm.get("active", False):
                    continue
                alarms[f"{location}:{cluster}:{alarm_name}"] = {
                    **alarm,
                    "location": location,
                    "cluster": cluster,
                    "alarm_name": alarm_name,
                }
            return alarms
        except Exception as error:
            logger.warning(f"Error reading all alarms: {error}")
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
