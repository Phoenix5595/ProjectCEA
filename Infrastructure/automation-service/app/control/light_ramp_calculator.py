"""Light intensity ramp calculator.

Handles light ramp calculations for sun schedules, including ramp up, ramp down,
and steady state intensity computation. Tracks ramp state per device.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class LightRampCalculator:
    """Calculates light intensity with ramp support for sun schedules."""

    # Minimum light intensity (10%) - lowest setting at which lights emit light
    MINIMUM_LIGHT_INTENSITY = 10.0

    def __init__(self):
        """Initialize light ramp calculator."""
        self._light_ramp_state: dict[tuple[str, str, str], dict[str, Any]] = {}

    def get_schedule_intensity(
        self,
        schedule: dict[str, Any],
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime,
        current_intensity: float | None = None,
    ) -> float | None:
        """Get target intensity for a device from schedule, with ramp calculation.

        Light period only: sun (lights on) or moon (0%). Returns 0.0 when the matched
        schedule has mode MOON or NIGHT. Otherwise returns intensity from sun schedule
        (with ramps).

        Args:
            schedule: Schedule dictionary from database or config
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time
            current_intensity: Current light intensity (0-100%), used as ramp start point

        Returns:
            Target intensity (0-100%). 0.0 when in moon (moon/NIGHT schedule).
            Non-None when a sun schedule matches (with target_intensity and optional ramps).
        """
        current_time_obj = current_time.time()

        # Check enabled
        if not schedule.get("enabled", True):
            return None

        # Check day of week
        day_of_week = schedule.get("day_of_week")
        if day_of_week is not None:
            current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
            if day_of_week != current_weekday:
                return None

        start_time = self._parse_time(schedule.get("start_time"))
        end_time = self._parse_time(schedule.get("end_time"))
        if not start_time or not end_time:
            return None

        is_in_range = False
        if start_time > end_time:
            is_in_range = current_time_obj >= start_time or current_time_obj < end_time
        else:
            is_in_range = start_time <= current_time_obj < end_time

        ramp_key = (location, cluster, device_name)

        if not is_in_range:
            if ramp_key in self._light_ramp_state:
                del self._light_ramp_state[ramp_key]
            return None

        # Moon schedule (or legacy NIGHT): lights off. Clear ramp and return 0%.
        # Sun schedule (or legacy DAY): lights on, use target_intensity below.
        if schedule.get("mode", "").upper() in ("MOON", "NIGHT"):
            if ramp_key in self._light_ramp_state:
                del self._light_ramp_state[ramp_key]
            return 0.0

        target_intensity = schedule.get("target_intensity")
        if target_intensity is None:
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
                end_datetime = datetime.combine(current_time.date() + timedelta(days=1), end_time)
            else:
                start_datetime = datetime.combine(
                    current_time.date() - timedelta(days=1), start_time
                )
                end_datetime = datetime.combine(current_time.date(), end_time)

        time_since_start = (current_time - start_datetime).total_seconds() / 60.0
        time_until_end = (end_datetime - current_time).total_seconds() / 60.0

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
                logger.info(
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
                + (ramp_state["target_intensity"] - ramp_state["start_intensity"]) * progress_state
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
                logger.info(
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

            elapsed = (current_time - ramp_state["ramp_start_timestamp"]).total_seconds() / 60.0
            progress = min(max(elapsed / ramp_duration, 0.0), 1.0)

            if progress >= 1.0:
                intensity = target_intensity
                del self._light_ramp_state[ramp_key]
                logger.info(
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
            # Allow 0% at moon; 10% minimum during sun
            effective_minimum = min(
                self.MINIMUM_LIGHT_INTENSITY,
                target_intensity if target_intensity is not None else self.MINIMUM_LIGHT_INTENSITY,
            )

            if ramp_key not in self._light_ramp_state:
                # Calculate where we SHOULD be based on elapsed time (handles service restarts)
                time_into_ramp_down = ramp_down_duration - time_until_end
                progress_from_time = min(max(time_into_ramp_down / ramp_down_duration, 0.0), 1.0)
                expected_intensity = (
                    target_intensity + (effective_minimum - target_intensity) * progress_from_time
                )
                self._light_ramp_state[ramp_key] = {
                    "start_intensity": expected_intensity,
                    "target_intensity": effective_minimum,
                    "ramp_start_timestamp": current_time,
                    "ramp_duration": time_until_end,
                    "ramp_type": "down",
                    "schedule_target_intensity": target_intensity,
                }
                logger.info(
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
                + (ramp_state["target_intensity"] - ramp_state["start_intensity"]) * progress_state
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
                logger.info(
                    f"Recalculating ramp down for {device_name} ({location}/{cluster}): "
                    f"{current_effective:.1f}% -> {ramp_state['target_intensity']:.1f}% over {remaining_time:.1f}min"
                )

            ramp_state = self._light_ramp_state[ramp_key]
            ramp_duration = ramp_state.get("ramp_duration") or 0.0

            if ramp_duration <= 0:
                intensity = ramp_state["target_intensity"]
                del self._light_ramp_state[ramp_key]
                logger.info(
                    f"Light ramp down complete for {device_name} ({location}/{cluster}): "
                    f"intensity={intensity:.1f}% (minimum)"
                )
                return ramp_state["target_intensity"]

            elapsed = (current_time - ramp_state["ramp_start_timestamp"]).total_seconds() / 60.0
            progress = min(max(elapsed / ramp_duration, 0.0), 1.0)

            if progress >= 1.0:
                intensity = ramp_state["target_intensity"]
                del self._light_ramp_state[ramp_key]
                logger.info(
                    f"Light ramp down complete for {device_name} ({location}/{cluster}): "
                    f"intensity={intensity:.1f}% (minimum)"
                )
            else:
                # Linear interpolation from start_intensity to target (effective minimum)
                intensity = (
                    ramp_state["start_intensity"]
                    + (ramp_state["target_intensity"] - ramp_state["start_intensity"]) * progress
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
        # Get effective intensity (with ramp)
        effective_intensity = self.get_schedule_intensity(
            schedule, location, cluster, device_name, current_time, current_intensity
        )

        if effective_intensity is None:
            return None

        current_time_obj = current_time.time()

        # Check day of week
        day_of_week = schedule.get("day_of_week")
        if day_of_week is not None:
            current_weekday = current_time.weekday()
            if day_of_week != current_weekday:
                return None

        # Check time range
        start_time = self._parse_time(schedule.get("start_time"))
        end_time = self._parse_time(schedule.get("end_time"))

        if not start_time or not end_time:
            return None

        is_in_range = False
        if start_time > end_time:
            is_in_range = current_time_obj >= start_time or current_time_obj < end_time
        else:
            is_in_range = start_time <= current_time_obj < end_time

        if not is_in_range:
            return None

        # Found the active schedule - get target_intensity
        target_intensity = schedule.get("target_intensity")

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
