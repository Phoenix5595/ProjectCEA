"""Light intensity ramp calculator.

Compatibility adapter that delegates to Scheduler so ramp logic stays in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.control.scheduler import Scheduler
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class LightRampCalculator:
    """Compatibility wrapper around :class:`Scheduler` light logic."""

    def __init__(self):
        """Initialize light ramp calculator adapter."""
        self._scheduler = Scheduler([])
        self._schedule_rows: list[dict[str, Any]] = []

    def _update_schedule(self, schedule: dict[str, Any]) -> None:
        self._schedule_rows = [schedule]
        self._scheduler.update_schedules(self._schedule_rows)

    def get_schedule_intensity(
        self,
        schedule: dict[str, Any],
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime,
        current_intensity: float | None = None,
    ) -> float | None:
        self._update_schedule(schedule)
        details = self._scheduler.get_light_intensity_details(
            location, cluster, device_name, current_time, current_intensity
        )
        if details is None:
            return None
        return float(details["effective_intensity"])

    def get_light_intensity_details(
        self,
        schedule: dict[str, Any],
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime,
        current_intensity: float | None = None,
    ) -> dict[str, Any] | None:
        """Get detailed light intensity information including effective, nominal, and ramp progress.

        Args:
            schedule: Schedule dictionary from database or config
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time
            current_intensity: Current light intensity (0-100%)

        Returns:
            Dict with keys: effective_intensity, nominal_intensity, ramp_progress
            or None if no active schedule
        """
        self._update_schedule(schedule)
        return self._scheduler.get_light_intensity_details(
            location, cluster, device_name, current_time, current_intensity
        )
