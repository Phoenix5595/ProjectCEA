"""Alarm manager for tracking alarms and enforcing failsafe."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.database import DatabaseManager
from app.events.operational_models import (
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_ports import OperationalEventSink
from app.redis_client import AutomationRedisClient
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class AlarmManager:
    """Manages alarms and failsafe enforcement."""

    def __init__(
        self,
        redis_client: AutomationRedisClient,
        database: DatabaseManager | None = None,
        event_sink: OperationalEventSink | None = None,
    ):
        """Initialize alarm manager.

        Args:
            redis_client: AutomationRedisClient instance
            database: Optional DatabaseManager for logging alarms
        """
        self.redis_client = redis_client
        self.database = database
        self._event_sink = event_sink
        self._active_alarms: dict[str, dict[str, Any]] = {}  # Cache of active alarms
        self._active_failsafes: set[tuple[str, str]] = set()

    def raise_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, message: str
    ) -> bool:
        """Raise an alarm.

        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
            severity: Alarm severity ('info', 'warning', 'critical')
            message: Alarm message

        Returns:
            True if successful, False otherwise
        """
        # Write to Redis
        success = self.redis_client.write_alarm(location, cluster, alarm_name, severity, message)

        if success:
            # Cache alarm
            key = f"{location}:{cluster}:{alarm_name}"
            self._active_alarms[key] = {
                "location": location,
                "cluster": cluster,
                "alarm_name": alarm_name,
                "severity": severity,
                "message": message,
                "active": True,
            }

            # If critical, trigger failsafe
            if severity == "critical":
                self._trigger_failsafe(location, cluster, "critical_alarm", alarm_name)

            # Log to database if available
            if self.database:
                # Could add alarm logging table if needed
                pass

        return success

    def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Clear an alarm (set active=False).

        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier

        Returns:
            True if successful, False otherwise
        """
        success = self.redis_client.clear_alarm(location, cluster, alarm_name)

        if success:
            # Remove from cache
            key = f"{location}:{cluster}:{alarm_name}"
            self._active_alarms.pop(key, None)

        return success

    def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Acknowledge an alarm.

        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier

        Returns:
            True if successful, False otherwise
        """
        return self.redis_client.acknowledge_alarm(location, cluster, alarm_name)

    def get_alarms(
        self, location: str | None = None, cluster: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Get all active alarms.

        Args:
            location: Optional location filter
            cluster: Optional cluster filter

        Returns:
            Dict mapping alarm key to alarm data
        """
        if location and cluster:
            return self.redis_client.read_alarms(location, cluster)

        # Get all alarms (would need to scan all locations/clusters)
        # For now, return cached alarms
        return self._active_alarms.copy()

    def check_critical_alarms(self, location: str, cluster: str) -> bool:
        """Check if there are any critical alarms for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name

        Returns:
            True if critical alarms exist, False otherwise
        """
        alarms = self.redis_client.read_alarms(location, cluster)
        for _alarm_name, alarm_data in alarms.items():
            if alarm_data.get("severity") == "critical" and alarm_data.get("active", False):
                return True
        return False

    def _trigger_failsafe(
        self, location: str, cluster: str, reason: str, triggered_by: str
    ) -> None:
        """Trigger failsafe mode for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name
            reason: Failsafe reason
            triggered_by: What triggered the failsafe
        """
        # Set mode to failsafe
        self.redis_client.write_mode(location, cluster, "failsafe", source="system")

        # Write failsafe details
        self.redis_client.write_failsafe(location, cluster, reason, triggered_by)
        failsafe_key = (location, cluster)
        if failsafe_key not in self._active_failsafes:
            self._active_failsafes.add(failsafe_key)
            self._emit_failsafe_event("system.failsafe_triggered", location, cluster, reason)

        logger.critical(
            f"FAILSAFE TRIGGERED: {location}/{cluster} - {reason} (triggered by: {triggered_by})"
        )

    def clear_failsafe(self, location: str, cluster: str) -> bool:
        """Clear failsafe mode if conditions are met.

        Args:
            location: Location name
            cluster: Cluster name

        Returns:
            True if failsafe cleared, False if conditions not met
        """
        # Check if critical alarms still exist
        if self.check_critical_alarms(location, cluster):
            logger.warning(
                f"Cannot clear failsafe for {location}/{cluster}: critical alarms still active"
            )
            return False

        # Clear failsafe state
        success = self.redis_client.clear_failsafe(location, cluster)

        if success:
            # Set mode back to auto
            self.redis_client.write_mode(location, cluster, "auto", source="system")
            failsafe_key = (location, cluster)
            if failsafe_key in self._active_failsafes:
                self._active_failsafes.remove(failsafe_key)
                self._emit_failsafe_event("system.failsafe_cleared", location, cluster, None)
            logger.info(f"Failsafe cleared for {location}/{cluster}")

        return success

    def _emit_failsafe_event(
        self, event_type: str, location: str, cluster: str, detail: str | None
    ) -> None:
        if self._event_sink is None:
            return
        self._event_sink.emit_nowait(
            OperationalEvent(
                occurred_at=datetime.now(UTC),
                source=EventSource.SYSTEM,
                category=EventCategory.SYSTEM,
                severity=EventSeverity.CRITICAL,
                event_type=event_type,
                entity=EntityContext(
                    entity_type="cluster",
                    entity_id=f"{location}/{cluster}",
                    location=location,
                    cluster=cluster,
                ),
                payload=SystemPayload(component="alarm_manager", state=event_type, detail=detail),
            )
        )

    def update_alarm_cache(self) -> None:
        """Update internal alarm cache from Redis.

        This should be called periodically to keep cache in sync.
        """
        # For now, alarms are read directly from Redis
        # Cache update could be implemented if needed for performance
        pass
