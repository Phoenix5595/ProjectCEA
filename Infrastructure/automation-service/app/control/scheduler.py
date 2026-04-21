"""Time-based scheduler for device control.

Light (master): two periods only — sun (lights on) and moon (lights off). Intensity is
binary: in sun window → schedule target + ramps; outside → moon = 0%.
Climate (slave): driven by climate_periods table. Periods define setpoints and ramp_minutes.
Climate periods are derived from light bounds but stored in climate_periods table.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from shared.infra_logging import get_logger

# Timezone constant for consistent scheduling
LOCAL_TZ = ZoneInfo("America/Toronto")

logger = get_logger(__name__)


class Scheduler:
    """Manages time-based device schedules."""

    def __init__(self, schedules: list[dict[str, Any]], climate_periods_repo=None):
        """Initialize scheduler.

        Args:
            schedules: List of schedule dictionaries from database or config
            climate_periods_repo: Optional ClimatePeriodRepository for time-based periods
        """
        self.schedules = schedules
        self._climate_periods_repo = climate_periods_repo
        self._light_ramp_state: dict[tuple[str, str, str], dict[str, Any]] = {}
        logger.info(f"Initialized scheduler with {len(schedules)} schedules")

    def set_climate_periods_repo(self, repo) -> None:
        """Set climate periods repository for dual-read support."""
        self._climate_periods_repo = repo

    async def get_climate_period_setpoints(
        self, location: str, cluster: str, current_time: datetime | None = None
    ) -> dict[str, Any] | None:
        """Get setpoints from active climate period (dual-read pattern)."""
        if not self._climate_periods_repo:
            return None

        if current_time is None:
            current_time = datetime.now(tz=LOCAL_TZ)

        time_str = f"{current_time.hour:02d}:{current_time.minute:02d}"

        try:
            period = await self._climate_periods_repo.get_active_period(location, cluster, time_str)
            if period:
                return {
                    "heating_setpoint": period.get("heating_setpoint"),
                    "cooling_setpoint": period.get("cooling_setpoint"),
                    "vpd_setpoint": period.get("vpd_setpoint"),
                    "co2_setpoint": period.get("co2_setpoint"),
                    "ramp_minutes": period.get("ramp_minutes", 0),
                    "period_name": period.get("period_name"),
                    "source": "climate_periods",
                }
        except Exception as e:
            logger.error(f"Error getting climate period setpoints: {e}")

        return None

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
    ) -> float:
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
            Target intensity (0-100%) as a float. 0.0 when in moon (no matching sun row,
            or matched MOON/NIGHT schedule). When multiple rows exist per device (MOON + SUN),
            rows outside the current time window are skipped so the correct row can match.
        """
        if current_time is None:
            current_time = datetime.now(tz=LOCAL_TZ)

        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
        # Ramp math must subtract datetimes of the same kind (naive vs aware).
        schedule_tz = current_time.tzinfo

        ramp_key = (location, cluster, device_name)

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
                    # More than one row per light (e.g. MOON + SUN); try the next schedule.
                    continue

                # Moon schedule (or legacy NIGHT): lights off. Clear ramp and return 0%.
                # Sun schedule (or legacy DAY): lights on, use target_intensity below.
                if schedule.get("mode", "").upper() in ("MOON", "NIGHT"):
                    if ramp_key in self._light_ramp_state:
                        del self._light_ramp_state[ramp_key]
                    return 0.0

                target_intensity = schedule.get("target_intensity")
                if target_intensity is None:
                    continue

                # Minimum light intensity (10%) - lowest setting at which lights emit light
                MINIMUM_LIGHT_INTENSITY = 10.0

                try:
                    t = float(target_intensity)
                    effective_minimum = (
                        t if t < MINIMUM_LIGHT_INTENSITY else MINIMUM_LIGHT_INTENSITY
                    )
                except Exception:
                    effective_minimum = float(MINIMUM_LIGHT_INTENSITY)

                ramp_up_duration = schedule.get("ramp_up_duration", 0) or 0
                ramp_down_duration = schedule.get("ramp_down_duration", 0) or 0

                if schedule_tz is None:
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
                else:
                    start_datetime = datetime.combine(
                        current_time.date(), start_time, tzinfo=schedule_tz
                    )
                    end_datetime = datetime.combine(
                        current_time.date(), end_time, tzinfo=schedule_tz
                    )
                    if start_time > end_time:
                        if current_time_obj >= start_time:
                            start_datetime = datetime.combine(
                                current_time.date(), start_time, tzinfo=schedule_tz
                            )
                            end_datetime = datetime.combine(
                                current_time.date() + timedelta(days=1),
                                end_time,
                                tzinfo=schedule_tz,
                            )
                        else:
                            start_datetime = datetime.combine(
                                current_time.date() - timedelta(days=1),
                                start_time,
                                tzinfo=schedule_tz,
                            )
                            end_datetime = datetime.combine(
                                current_time.date(), end_time, tzinfo=schedule_tz
                            )

                time_since_start = (current_time - start_datetime).total_seconds() / 60.0
                time_until_end = (end_datetime - current_time).total_seconds() / 60.0

                if ramp_up_duration > 0 and time_since_start < ramp_up_duration:
                    # Ramp up period: Always ramp from 10% (minimum) to sun target (target_intensity)
                    # Initialize ramp state if missing - calculate expected intensity from time
                    if ramp_key not in self._light_ramp_state:
                        # Calculate where we SHOULD be based on elapsed time (handles service restarts)
                        progress_from_time = min(max(time_since_start / ramp_up_duration, 0.0), 1.0)
                        expected_from_time = (
                            MINIMUM_LIGHT_INTENSITY
                            + (target_intensity - MINIMUM_LIGHT_INTENSITY) * progress_from_time
                        )
                        expected_intensity = expected_from_time
                        if current_intensity is not None:
                            try:
                                ci = float(current_intensity)
                                t_float = float(target_intensity)
                                ci_clamped = max(MINIMUM_LIGHT_INTENSITY, min(ci, t_float))
                                expected_intensity = ci_clamped
                            except (TypeError, ValueError):
                                expected_intensity = expected_from_time
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

                    elapsed = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
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
                        MINIMUM_LIGHT_INTENSITY,
                        target_intensity
                        if target_intensity is not None
                        else MINIMUM_LIGHT_INTENSITY,
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

                    elapsed = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
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
                            + (ramp_state["target_intensity"] - ramp_state["start_intensity"])
                            * progress
                        )

                    effective_minimum_val = min(MINIMUM_LIGHT_INTENSITY, target_intensity)
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

        # No active schedule found = moon by definition
        # Clear any ramp state for this device
        if ramp_key in self._light_ramp_state:
            del self._light_ramp_state[ramp_key]

        return 0.0

    def is_in_photoperiod(self, location: str, cluster: str, current_time: datetime) -> bool:
        """True if current time is in the room's sun (lights-on) window for climate logic.

        Uses per-light SUN/DAY rows when present; otherwise ``room_schedule`` with mode
        light/DAY. When any per-light SUN/DAY row exists, ``room_schedule`` is ignored
        for the photoperiod window (narrow per-light sun wins).
        """
        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()

        matching: list[dict[str, Any]] = []
        for schedule in self.schedules:
            if not schedule.get("enabled", True):
                continue
            if schedule.get("location") != location or schedule.get("cluster") != cluster:
                continue
            day_of_week = schedule.get("day_of_week")
            if day_of_week is not None and day_of_week != current_weekday:
                continue
            matching.append(schedule)

        if not matching:
            return False

        sun_rows = [
            s
            for s in matching
            if s.get("device_name") != "room_schedule"
            and str(s.get("mode", "")).upper() in ("SUN", "DAY")
        ]
        if sun_rows:
            rows = sun_rows
        else:
            rows = [
                s
                for s in matching
                if s.get("device_name") == "room_schedule"
                and str(s.get("mode", "")).upper() in ("LIGHT", "DAY")
            ]

        if not rows:
            return False

        for schedule in rows:
            mode = str(schedule.get("mode", "")).upper()
            if mode in ("MOON", "NIGHT"):
                continue
            start_time = self._parse_time(schedule.get("start_time"))
            end_time = self._parse_time(schedule.get("end_time"))
            if not start_time or not end_time:
                continue
            if start_time > end_time:
                in_range = current_time_obj >= start_time or current_time_obj < end_time
            else:
                in_range = start_time <= current_time_obj < end_time
            if in_range:
                return True
        return False

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

        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()
        ramp_key = (location, cluster, device_name)

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
                    is_in_range = current_time_obj >= start_time or current_time_obj < end_time
                else:
                    is_in_range = start_time <= current_time_obj < end_time

                if not is_in_range:
                    continue

                # Found the active schedule - get target_intensity
                target_intensity = schedule.get("target_intensity")

                if target_intensity is None:
                    # No target intensity in schedule, use effective as nominal
                    target_intensity = effective_intensity

                # Get ramp progress from ramp state
                ramp_progress = None
                if ramp_key in self._light_ramp_state:
                    ramp_state = self._light_ramp_state[ramp_key]
                    elapsed = (
                        current_time - ramp_state["ramp_start_timestamp"]
                    ).total_seconds() / 60.0
                    ramp_progress = min(max(elapsed / ramp_state["ramp_duration"], 0.0), 1.0)
                    if ramp_progress >= 1.0:
                        ramp_progress = None  # Ramp complete

                return {
                    "effective_intensity": effective_intensity,
                    "nominal_intensity": target_intensity,
                    "ramp_progress": ramp_progress,
                }

        return None

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
