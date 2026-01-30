"""Time-based scheduler for device control.

Light (master): two periods only — sun (lights on) and moon (lights off). Intensity is
binary: in sun window → schedule target + ramps; outside → moon = 0%. Climate (slave):
PRE_DAY, DAY, PRE_NIGHT, NIGHT are derived from light bounds and used for setpoints only.
DAY has the same length as sun; NIGHT has the same duration as moon. PRE_DAY/PRE_NIGHT
exist only if their duration is above 0.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from shared.logging import get_logger

# Timezone constant for consistent scheduling
LOCAL_TZ = ZoneInfo("America/Toronto")
from typing import Any

logger = get_logger(__name__)


def is_time_in_range(t: int, start: int, end: int) -> bool:
    """Check if time t (in minutes since midnight) is in range [start, end).
    Handles overnight wrap-around correctly.

    Args:
        t: Time in minutes since midnight (0-1439)
        start: Start time in minutes since midnight (0-1439)
        end: End time in minutes since midnight (0-1439)

    Returns:
        True if t is in range [start, end), False otherwise
    """
    if start <= end:
        return start <= t < end
    else:
        return t >= start or t < end


class Scheduler:
    """Manages time-based device schedules."""

    # Minimum light intensity (10%) - lowest setting at which lights emit light
    MINIMUM_LIGHT_INTENSITY = 10.0

    def __init__(self, schedules: list[dict[str, Any]]):
        """Initialize scheduler.

        Args:
            schedules: List of schedule dictionaries from database or config
        """
        self.schedules = schedules
        # Light ramp state persistence (similar to effective setpoint ramp state)
        # Key: (location, cluster, device_name), Value: ramp state dict
        self._light_ramp_state: dict[tuple[str, str, str], dict[str, Any]] = {}
        logger.info(f"Initialized scheduler with {len(schedules)} schedules")

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

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)

        Returns:
            1 if schedule wants device ON, 0 if OFF, None if no active schedule
        """
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

    def get_schedule_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime | None = None,
        current_intensity: float | None = None,
    ) -> float | None:
        """Get target intensity for a device from active schedule, with ramp calculation.

        Light period only: sun (lights on) or moon (0%). Returns 0.0 when no schedule
        matches (moon by definition) or when the matched schedule has mode MOON or NIGHT.
        Otherwise returns intensity from sun schedule (with ramps).

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
            current_intensity: Current light intensity (0-100%), used as ramp start point

        Returns:
            Target intensity (0-100%). 0.0 when in moon (no match or moon/NIGHT schedule).
            Non-None when a sun schedule matches (with target_intensity and optional ramps).
        """
        if current_time is None:
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

                if not is_in_range:
                    ramp_key = (location, cluster, device_name)
                    if ramp_key in self._light_ramp_state:
                        del self._light_ramp_state[ramp_key]
                    continue

                # Moon schedule (or legacy NIGHT): lights off. Clear ramp and return 0%.
                # Sun schedule (or legacy DAY): lights on, use target_intensity below.
                if schedule.get("mode", "").upper() in ("MOON", "NIGHT"):
                    ramp_key = (location, cluster, device_name)
                    if ramp_key in self._light_ramp_state:
                        del self._light_ramp_state[ramp_key]
                    return 0.0

                target_intensity = schedule.get("target_intensity")
                if target_intensity is None:
                    ramp_key = (location, cluster, device_name)
                    if ramp_key in self._light_ramp_state:
                        del self._light_ramp_state[ramp_key]
                    return None

                try:
                    t = float(target_intensity)
                    effective_minimum = (
                        t if t < self.MINIMUM_LIGHT_INTENSITY else self.MINIMUM_LIGHT_INTENSITY
                    )
                except Exception:
                    effective_minimum = float(self.MINIMUM_LIGHT_INTENSITY)

                ramp_up_duration = schedule.get("ramp_up_duration", 0) or 0
                ramp_down_duration = schedule.get("ramp_down_duration", 0) or 0

                start_datetime = datetime.combine(current_time.date(), start_time)
                end_datetime = datetime.combine(current_time.date(), end_time)
                if start_time > end_time:
                    if current_time_obj >= start_time:
                        start_datetime = datetime.combine(current_time.date(), start_time)
                        end_datetime = datetime.combine(
                            current_time.date() + timedelta(days=1), end_time
                        )
                    else:
                        start_datetime = datetime.combine(
                            current_time.date() - timedelta(days=1), start_time
                        )
                        end_datetime = datetime.combine(current_time.date(), end_time)

                time_since_start = (current_time - start_datetime).total_seconds() / 60.0
                time_until_end = (end_datetime - current_time).total_seconds() / 60.0
                ramp_key = (location, cluster, device_name)

                if ramp_up_duration > 0 and time_since_start < ramp_up_duration:
                    # Ramp up period: Always ramp from 10% (minimum) to sun target (target_intensity)
                    # Initialize ramp state if missing - calculate expected intensity from time
                    if ramp_key not in self._light_ramp_state:
                        # Calculate where we SHOULD be based on elapsed time (handles service restarts)
                        progress_from_time = min(max(time_since_start / ramp_up_duration, 0.0), 1.0)
                        expected_intensity = (
                            self.MINIMUM_LIGHT_INTENSITY
                            + (target_intensity - self.MINIMUM_LIGHT_INTENSITY) * progress_from_time
                        )
                        self._light_ramp_state[ramp_key] = {
                            "start_intensity": expected_intensity,
                            "target_intensity": target_intensity,
                            "ramp_start_timestamp": current_time,
                            "ramp_duration": ramp_up_duration - time_since_start,
                            "ramp_type": "up",
                        }
                        logger.debug(
                            f"Resuming light ramp up for {device_name} ({location}/{cluster}): "
                            f"{expected_intensity:.1f}% -> {target_intensity:.1f}% (sun target) "
                            f"(remaining: {ramp_up_duration - time_since_start:.1f}min)"
                        )

                    ramp_state = self._light_ramp_state[ramp_key]

                    # Calculate current effective intensity based on existing ramp state
                    state_duration = ramp_state.get("ramp_duration") or ramp_up_duration or 0.0001
                    elapsed_from_state = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
                    progress_state = min(max(elapsed_from_state / state_duration, 0.0), 1.0)
                    current_effective = (
                        ramp_state["start_intensity"]
                        + (ramp_state["target_intensity"] - ramp_state["start_intensity"])
                        * progress_state
                    )

                    # Remaining time in the scheduled ramp window
                    remaining_time = max(ramp_up_duration - time_since_start, 0.0)

                    # Recalculate ramp to finish within remaining time if target changed or timing drifted
                    if (ramp_state.get("target_intensity") != target_intensity) or (
                        ramp_state.get("ramp_duration") != remaining_time
                    ):
                        ramp_state["start_intensity"] = current_effective
                        ramp_state["target_intensity"] = target_intensity
                        ramp_state["ramp_start_timestamp"] = current_time
                        ramp_state["ramp_duration"] = remaining_time
                        logger.debug(
                            f"Recalculating ramp up for {device_name} ({location}/{cluster}): "
                            f"{current_effective:.1f}% -> {target_intensity:.1f}% over {remaining_time:.1f}min "
                            f"(speeds up if needed)"
                        )

                    ramp_state = self._light_ramp_state[ramp_key]
                    ramp_duration = ramp_state.get("ramp_duration") or 0.0

                    if ramp_duration <= 0:
                        intensity = target_intensity
                        del self._light_ramp_state[ramp_key]
                        return max(0.0, min(target_intensity, intensity))

                    elapsed = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
                    progress = min(max(elapsed / ramp_duration, 0.0), 1.0)

                    if progress >= 1.0:
                        intensity = target_intensity
                        del self._light_ramp_state[ramp_key]
                        logger.debug(
                            f"Light ramp up complete for {device_name} ({location}/{cluster}): "
                            f"intensity={intensity:.1f}% (sun target reached)"
                        )
                    else:
                        intensity = (
                            ramp_state["start_intensity"]
                            + (target_intensity - ramp_state["start_intensity"]) * progress
                        )
                        intensity = min(intensity, target_intensity)

                    return max(0.0, min(target_intensity, intensity))

                # RAMP DOWN: Ramp from sun target to effective minimum (0% at moon, 10% otherwise)
                elif ramp_down_duration > 0 and time_until_end < ramp_down_duration:
                    ramp_down_start = end_datetime - timedelta(minutes=ramp_down_duration)
                    # Allow 0% at moon; 10% minimum during sun
                    effective_minimum = min(
                        self.MINIMUM_LIGHT_INTENSITY,
                        target_intensity
                        if target_intensity is not None
                        else self.MINIMUM_LIGHT_INTENSITY,
                    )

                    if ramp_key not in self._light_ramp_state:
                        # Calculate where we SHOULD be based on elapsed time (handles service restarts)
                        time_into_ramp_down = ramp_down_duration - time_until_end
                        progress_from_time = min(
                            max(time_into_ramp_down / ramp_down_duration, 0.0), 1.0
                        )
                        expected_intensity = (
                            target_intensity
                            + (effective_minimum - target_intensity) * progress_from_time
                        )
                        self._light_ramp_state[ramp_key] = {
                            "start_intensity": expected_intensity,
                            "target_intensity": effective_minimum,
                            "ramp_start_timestamp": current_time,
                            "ramp_duration": time_until_end,
                            "ramp_type": "down",
                            "schedule_target_intensity": target_intensity,
                        }
                        logger.debug(
                            f"Resuming light ramp down for {device_name} ({location}/{cluster}): "
                            f"{expected_intensity:.1f}% -> {effective_minimum:.1f}% "
                            f"(remaining: {time_until_end:.1f}min)"
                        )

                    ramp_state = self._light_ramp_state[ramp_key]

                    # Calculate current effective intensity based on existing ramp state
                    state_duration = ramp_state.get("ramp_duration") or ramp_down_duration or 0.0001
                    elapsed_from_state = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
                    progress_state = min(max(elapsed_from_state / state_duration, 0.0), 1.0)
                    current_effective = (
                        ramp_state["start_intensity"]
                        + (ramp_state["target_intensity"] - ramp_state["start_intensity"])
                        * progress_state
                    )

                    # Remaining time until end of schedule (finish ramp within window)
                    remaining_time = max(time_until_end, 0.0)

                    # Recalculate ramp down to continue smoothly to effective minimum in remaining time
                    if (ramp_state.get("schedule_target_intensity") != target_intensity) or (
                        ramp_state.get("ramp_duration") != remaining_time
                    ):
                        ramp_state["start_intensity"] = current_effective
                        ramp_state["target_intensity"] = effective_minimum
                        ramp_state["ramp_start_timestamp"] = current_time
                        ramp_state["ramp_duration"] = remaining_time
                        ramp_state["schedule_target_intensity"] = target_intensity
                        logger.debug(
                            f"Recalculating ramp down for {device_name} ({location}/{cluster}): "
                            f"{current_effective:.1f}% -> {ramp_state['target_intensity']:.1f}% over {remaining_time:.1f}min"
                        )

                    ramp_state = self._light_ramp_state[ramp_key]
                    ramp_duration = ramp_state.get("ramp_duration") or 0.0

                    if ramp_duration <= 0:
                        intensity = ramp_state["target_intensity"]
                        del self._light_ramp_state[ramp_key]
                        logger.debug(
                            f"Light ramp down complete for {device_name} ({location}/{cluster}): "
                            f"intensity={intensity:.1f}% (minimum)"
                        )
                        return ramp_state["target_intensity"]

                    elapsed = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
                    progress = min(max(elapsed / ramp_duration, 0.0), 1.0)

                    if progress >= 1.0:
                        intensity = ramp_state["target_intensity"]
                        del self._light_ramp_state[ramp_key]
                        logger.debug(
                            f"Light ramp down complete for {device_name} ({location}/{cluster}): "
                            f"intensity={intensity:.1f}% (minimum)"
                        )
                    else:
                        # Linear interpolation from start_intensity to target (effective minimum)
                        intensity = (
                            ramp_state["start_intensity"]
                            + (ramp_state["target_intensity"] - ramp_state["start_intensity"])
                            * progress
                        )

                    effective_minimum_val = min(self.MINIMUM_LIGHT_INTENSITY, target_intensity)
                    return max(effective_minimum_val, min(intensity, target_intensity))

                # STEADY STATE: Return sun target directly (not in ramp up or ramp down)
                else:
                    # Steady state - return sun target (target_intensity) directly
                    if ramp_key in self._light_ramp_state:
                        del self._light_ramp_state[ramp_key]
                    clamped_intensity = max(0.0, min(100.0, target_intensity))
                    logger.debug(
                        f"Steady state intensity for {device_name} ({location}/{cluster}): "
                        f"{clamped_intensity:.1f}% (sun target from schedule: {target_intensity:.1f}%)"
                    )
                    return clamped_intensity

        # No active schedule found = moon by definition. Clear ramp and return 0%.
        ramp_key = (location, cluster, device_name)
        if ramp_key in self._light_ramp_state:
            del self._light_ramp_state[ramp_key]

        return 0.0

    def get_light_intensity_details(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime | None = None,
        current_intensity: float | None = None,
    ) -> dict[str, Any] | None:
        """Get detailed light intensity information including effective, nominal, and ramp progress.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
            current_intensity: Current light intensity (0-100%)

        Returns:
            Dict with keys: effective_intensity, nominal_intensity, ramp_progress
            or None if no active schedule
        """
        if current_time is None:
            current_time = datetime.now(tz=LOCAL_TZ)

        # Get effective intensity (with ramp)
        effective_intensity = self.get_schedule_intensity(
            location, cluster, device_name, current_time, current_intensity
        )

        if effective_intensity is None:
            return None

        # Find the active schedule directly (same logic as get_schedule_intensity)
        # This ensures we get the same schedule that get_schedule_intensity found
        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday

        target_intensity = None
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

                if not start_time or not end_time:
                    continue

                is_in_range = False
                if start_time > end_time:
                    # Overnight schedule
                    is_in_range = current_time_obj >= start_time or current_time_obj < end_time
                else:
                    # Normal schedule
                    is_in_range = start_time <= current_time_obj < end_time

                if is_in_range:
                    # Found the active schedule - get target_intensity
                    target_intensity = schedule.get("target_intensity")
                    break

        if target_intensity is None:
            # No target intensity in schedule, use effective as nominal
            target_intensity = effective_intensity

        # Get ramp progress from ramp state
        ramp_key = (location, cluster, device_name)
        ramp_progress = None
        if ramp_key in self._light_ramp_state:
            ramp_state = self._light_ramp_state[ramp_key]
            elapsed = (current_time - ramp_state["ramp_start_timestamp"]).total_seconds() / 60.0
            ramp_progress = min(max(elapsed / ramp_state["ramp_duration"], 0.0), 1.0)
            if ramp_progress >= 1.0:
                ramp_progress = None  # Ramp complete

        return {
            "effective_intensity": effective_intensity,
            "nominal_intensity": target_intensity,
            "ramp_progress": ramp_progress,
        }

    def _parse_time(self, time_str: str | None) -> time | None:
        """Parse time string to time object.

        Args:
            time_str: Time string in format "HH:MM" or "HH:MM:SS"

        Returns:
            time object or None
        """
        if time_str is None:
            return None

        try:
            if isinstance(time_str, time):
                return time_str

            parts = str(time_str).split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                return time(hour, minute, second)
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing time '{time_str}': {e}")

        return None

    def update_schedules(self, schedules: list[dict[str, Any]]):
        """Update schedules list."""
        self.schedules = schedules
        logger.info(f"Updated schedules: {len(schedules)} schedules")

    def get_climate_mode(
        self,
        location: str,
        cluster: str,
        current_time: datetime | None = None,
        day_start_time: str | None = None,
        day_end_time: str | None = None,
        pre_day_duration: int | None = None,
        pre_night_duration: int | None = None,
    ) -> tuple[str, int, int] | None:
        """Get current climate mode (PRE_DAY, DAY, PRE_NIGHT, NIGHT) for a location/cluster.

        Climate periods are slave to light: computed from light sun/moon bounds
        (day_start_time, day_end_time) plus climate pre_day_duration, pre_night_duration.
        Used for setpoints only; does not drive light intensity. DAY is slave to sun,
        NIGHT is slave to moon.

        CRITICAL: Returns DISCRETE mode only (mode, mode_start_time, mode_end_time).
        Does NOT calculate ramp progress - that belongs in control engine.

        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current time (default: now)
            day_start_time: Day start time in HH:MM format (from light schedule)
            day_end_time: Day end time in HH:MM format (from light schedule)
            pre_day_duration: Pre-day duration in minutes (from climate schedule)
            pre_night_duration: Pre-night duration in minutes (from climate schedule)

        Returns:
            Tuple of (mode, mode_start_minutes, mode_end_minutes) or None if insufficient data
            mode: 'PRE_DAY', 'DAY', 'PRE_NIGHT', or 'NIGHT'
            mode_start_minutes: Start time in minutes since midnight (0-1439)
            mode_end_minutes: End time in minutes since midnight (0-1439)
        """
        if current_time is None:
            current_time = datetime.now(tz=LOCAL_TZ)

        # Need day/night times to calculate periods
        if not day_start_time or not day_end_time:
            return None

        # Convert times to minutes since midnight
        day_start_min = self._time_to_minutes(day_start_time)
        day_end_min = self._time_to_minutes(day_end_time)
        current_min = current_time.hour * 60 + current_time.minute

        # Default durations to 0 if not provided
        pre_day_dur = pre_day_duration or 0
        pre_night_dur = pre_night_duration or 0

        # Calculate pre-day period: starts at (day_start_time - pre_day_duration) % 1440, ends at day_start_time
        # PRE_DAY occurs DURING the night light period (lights still OFF)
        pre_day_start_min = (day_start_min - pre_day_dur) % 1440
        pre_day_end_min = day_start_min

        # Calculate pre-night period: starts at (day_end_time - pre_night_duration) % 1440, ends at day_end_time
        # PRE_NIGHT occurs DURING the day light period (lights still ON)
        pre_night_start_min = (day_end_min - pre_night_dur) % 1440
        pre_night_end_min = day_end_min

        # Calculate DAY period: starts at day_start_time, ends when PRE_NIGHT starts
        # Pure DAY period (lights ON + climate DAY), PRE_NIGHT replaces the end
        day_start_actual_min = day_start_min
        day_end_actual_min = pre_night_start_min

        # Calculate NIGHT period: starts at day_end_time, ends when PRE_DAY starts
        # Pure NIGHT period (lights OFF + climate NIGHT), PRE_DAY replaces the end
        night_start_actual_min = day_end_min
        night_end_actual_min = pre_day_start_min

        # Check which period we're in (priority: PRE_DAY > DAY > PRE_NIGHT > NIGHT)
        # PRE_DAY period
        if pre_day_dur > 0 and is_time_in_range(current_min, pre_day_start_min, pre_day_end_min):
            return ("PRE_DAY", pre_day_start_min, pre_day_end_min)

        # DAY period (only if not in PRE_NIGHT)
        if pre_night_dur > 0:
            # If PRE_NIGHT duration > 0, DAY period ends when PRE_NIGHT starts
            if is_time_in_range(current_min, day_start_actual_min, day_end_actual_min):
                return ("DAY", day_start_actual_min, day_end_actual_min)
        else:
            # If PRE_NIGHT duration = 0, DAY period runs full day
            if is_time_in_range(current_min, day_start_min, day_end_min):
                return ("DAY", day_start_min, day_end_min)

        # PRE_NIGHT period
        if pre_night_dur > 0 and is_time_in_range(
            current_min, pre_night_start_min, pre_night_end_min
        ):
            return ("PRE_NIGHT", pre_night_start_min, pre_night_end_min)

        # NIGHT period (everything else)
        if is_time_in_range(current_min, night_start_actual_min, night_end_actual_min):
            return ("NIGHT", night_start_actual_min, night_end_actual_min)

        # Fallback: if we somehow don't match any period, return NIGHT
        return ("NIGHT", night_start_actual_min, night_end_actual_min)

    def _time_to_minutes(self, time_str: str | None) -> int:
        """Convert time string (HH:MM) to minutes since midnight.

        Args:
            time_str: Time string in format "HH:MM"

        Returns:
            Minutes since midnight (0-1439)
        """
        if time_str is None:
            return 0

        try:
            if isinstance(time_str, time):
                return time_str.hour * 60 + time_str.minute

            parts = str(time_str).split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                # Handle HH:MM:SS format by taking only the minutes part (ignore seconds)
                return (hour * 60 + minute) % 1440
        except (ValueError, IndexError) as e:
            logger.error(f"Error converting time '{time_str}' to minutes: {e}")

        return 0

    def validate_climate_schedule_conflicts(
        self, day_start_time: str, day_end_time: str, pre_day_duration: int, pre_night_duration: int
    ) -> tuple[bool, str | None]:
        """Validate climate schedule for conflicts.

        Args:
            day_start_time: Day start time in HH:MM format
            day_end_time: Day end time in HH:MM format
            pre_day_duration: Pre-day duration in minutes (0-180)
            pre_night_duration: Pre-night duration in minutes (0-180)

        Returns:
            Tuple of (is_valid, error_message)

        Validation Rules:
            - Both durations must be 0-180 minutes (practical real-world limit)
            - pre_day_duration must be strictly shorter than night_length
            - pre_night_duration must be strictly shorter than day_length
            - Combined durations must be less than night_length (no overlap)
        """
        # Convert to minutes
        day_start_min = self._time_to_minutes(day_start_time)
        day_end_min = self._time_to_minutes(day_end_time)

        # Calculate day and night lengths
        if day_end_min > day_start_min:
            day_length = day_end_min - day_start_min
            night_length = 1440 - day_length
        else:
            night_length = day_start_min - day_end_min
            day_length = 1440 - night_length

        # Hard limits: 0-180 minutes (3 hours max - practical real-world limit)
        MAX_RAMP_DURATION = 180

        # Validate non-negative first
        if pre_day_duration < 0:
            return (False, "pre_day_duration must be >= 0")

        if pre_night_duration < 0:
            return (False, "pre_night_duration must be >= 0")

        # Validate max durations (180 min practical limit)
        if pre_day_duration > MAX_RAMP_DURATION:
            return (
                False,
                f"pre_day_duration ({pre_day_duration} min) exceeds maximum ({MAX_RAMP_DURATION} min)",
            )

        if pre_night_duration > MAX_RAMP_DURATION:
            return (
                False,
                f"pre_night_duration ({pre_night_duration} min) exceeds maximum ({MAX_RAMP_DURATION} min)",
            )

        # pre_day can NEVER be as long as night (must be strictly shorter)
        if pre_day_duration >= night_length:
            return (
                False,
                f"pre_day_duration ({pre_day_duration} min) must be shorter than night_length ({night_length} min)",
            )

        # pre_night can NEVER be as long as day (must be strictly shorter)
        if pre_night_duration >= day_length:
            return (
                False,
                f"pre_night_duration ({pre_night_duration} min) must be shorter than day_length ({day_length} min)",
            )

        # Combined constraint: no overlap during night period
        if pre_day_duration + pre_night_duration >= night_length:
            return (
                False,
                f"pre_day_duration ({pre_day_duration} min) + pre_night_duration ({pre_night_duration} min) must be less than night_length ({night_length} min)",
            )

        return (True, None)
