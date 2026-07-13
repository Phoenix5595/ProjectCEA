"""Non-light schedule methods (DAY/NIGHT rows)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class SchedulesMixin:
    """Mixin for non-light schedule activation and state queries."""

    def is_schedule_active(
        self, location: str, cluster: str, device_name: str, current_time: datetime | None = None
    ) -> tuple[bool, int | None]:
        """Check if a schedule is active for a device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)

        Returns:
            Tuple of (is_active, schedule_id)
        """
        if current_time is None:
            from app.control.scheduler import LOCAL_TZ

            current_time = datetime.now(tz=LOCAL_TZ)

        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday

        for schedule in self.schedules:
            if not schedule.get("enabled", True):
                continue

            if (
                schedule.get("location") == location
                and schedule.get("cluster") == cluster
                and schedule.get("device_name") == device_name
            ):
                # Check day of week
                day_of_week = schedule.get("day_of_week")
                if day_of_week is not None and day_of_week != current_weekday:
                    continue

                # Check time range
                start_time = self._parse_time(schedule.get("start_time"))
                end_time = self._parse_time(schedule.get("end_time"))

                if start_time and end_time:
                    # Handle overnight schedules (e.g., 22:00 to 06:00)
                    if start_time > end_time:
                        # Overnight schedule
                        if current_time_obj >= start_time or current_time_obj < end_time:
                            schedule_id = schedule.get("id")  # May be None if from config
                            return (True, schedule_id)
                    else:
                        # Normal schedule
                        if start_time <= current_time_obj < end_time:
                            schedule_id = schedule.get("id")  # May be None if from config
                            return (True, schedule_id)

        return (False, None)

    def get_schedule_state(
        self, location: str, cluster: str, device_name: str, current_time: datetime | None = None
    ) -> int | None:
        """Get schedule state for a device (1 = ON, 0 = OFF, None = no schedule).

        For **light** devices: uses ``is_in_photoperiod()`` from the mode_params cache.
        Returns 1 if in photoperiod (sun), 0 if not (moon).
        For **non-light** devices: uses ``self.schedules`` DAY/NIGHT rows (unchanged).

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)

        Returns:
            1 if schedule wants device ON, 0 if OFF, None if no active schedule
        """
        # Light devices: use is_in_photoperiod from mode_params cache
        if self._is_light_device(location, cluster, device_name):
            if current_time is None:
                from app.control.scheduler import LOCAL_TZ

                current_time = datetime.now(tz=LOCAL_TZ)
            return 1 if self.is_in_photoperiod(location, cluster, current_time) else 0

        # Non-light devices: use self.schedules (unchanged behavior)
        is_active, schedule_id = self.is_schedule_active(
            location, cluster, device_name, current_time
        )
        if is_active:
            # Moon (or legacy NIGHT) = OFF; sun (or legacy DAY) = ON
            for schedule in self.schedules:
                if schedule.get("id") == schedule_id:
                    mode = schedule.get("mode", "").upper()
                    if mode in ("MOON", "NIGHT"):
                        return 0
                    return 1
            # If schedule not found by ID, default to ON (backward compatibility)
            return 1
        return None

    def get_active_schedule_details(
        self, location: str, cluster: str, device_name: str, current_time: datetime | None = None
    ) -> dict[str, Any] | None:
        """Get details of active schedule including ramp durations and photoperiod.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)

        Returns:
            Dict with schedule details (ramp_up_duration, ramp_down_duration, start_time, end_time, photoperiod_hours)
            or None if no active schedule
        """
        if current_time is None:
            from app.control.scheduler import LOCAL_TZ

            current_time = datetime.now(tz=LOCAL_TZ)

        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()

        for schedule in self.schedules:
            if not schedule.get("enabled", True):
                continue

            if (
                schedule.get("location") == location
                and schedule.get("cluster") == cluster
                and schedule.get("device_name") == device_name
            ):
                day_of_week = schedule.get("day_of_week")
                if day_of_week is not None and day_of_week != current_weekday:
                    continue

                start_time = self._parse_time(schedule.get("start_time"))
                end_time = self._parse_time(schedule.get("end_time"))

                if not start_time or not end_time:
                    continue

                is_in_range = False
                if start_time > end_time:
                    is_in_range = current_time_obj >= start_time or current_time_obj < end_time
                else:
                    is_in_range = start_time <= current_time_obj < end_time

                if is_in_range:
                    # Calculate photoperiod (duration of schedule in hours)
                    start_minutes = start_time.hour * 60 + start_time.minute
                    end_minutes = end_time.hour * 60 + end_time.minute
                    if end_minutes < start_minutes:
                        photoperiod_hours = (end_minutes + 1440 - start_minutes) / 60.0
                    else:
                        photoperiod_hours = (end_minutes - start_minutes) / 60.0

                    return {
                        "ramp_up_duration": schedule.get("ramp_up_duration"),
                        "ramp_down_duration": schedule.get("ramp_down_duration"),
                        "start_time": schedule.get("start_time"),
                        "end_time": schedule.get("end_time"),
                        "photoperiod_hours": photoperiod_hours,
                    }

        return None
