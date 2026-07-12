"""Time-based scheduler for device control.

Light (master): two periods only -- sun (lights on) and moon (lights off). Intensity is
binary: in sun window -> schedule target + ramps; outside -> moon = 0%.
Climate (slave): driven by climate_periods table. Periods define setpoints and ramp_minutes.
Climate periods are derived from light bounds but stored in climate_periods table.

Architecture (production-safety rewrite):
  - Photoperiod bounds come from ``_mode_params`` cache (mode_parameters table).
  - Per-light target intensity comes from ``_light_intensities`` cache
    (light_target_intensity table, keyed by ``(device_id, mode_id)``).
  - Supplemental/override programs come from ``_light_programs`` cache
    (light_programs table, pre-indexed by ``(location, cluster)``).
  - Device -> device_id mapping comes from ``_device_lookup`` cache.
  - All caches are in-memory Python dicts updated via atomic reference swaps.
  - Failsafe: missing mode_params -> is_in_photoperiod returns True, intensity 10%.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from shared.infra_logging import get_logger

# Timezone constant for consistent scheduling
LOCAL_TZ = ZoneInfo("America/Toronto")

# Minimum light intensity (10%) - lowest setting at which lights emit light
MINIMUM_LIGHT_INTENSITY = 10.0

logger = get_logger(__name__)


class Scheduler:
    """Manages time-based device schedules."""

    def __init__(self, schedules: list[dict[str, Any]], climate_periods_repo=None):
        """Initialize scheduler.

        Args:
            schedules: List of schedule dictionaries from database or config.
                Non-light DAY/NIGHT rows are still used for non-light device scheduling.
            climate_periods_repo: Optional ClimatePeriodRepository for time-based periods
        """
        self.schedules = schedules
        self._climate_periods_repo = climate_periods_repo
        self._light_ramp_state: dict[tuple[Any, ...], dict[str, Any]] = {}

        # --- In-memory caches (populated by update_*() methods) ---
        # {(location, cluster): {mode_id, day_start, night_start, ramp_up, ramp_down}}
        self._mode_params: dict[tuple[str, str], dict[str, Any]] = {}
        # {(device_id, mode_id): target_intensity}
        self._light_intensities: dict[tuple[int, int], float] = {}
        # All enabled programs (raw list from repo)
        self._light_programs: list[dict[str, Any]] = []
        # Pre-indexed: {(location, cluster): [program dicts]}
        self._light_programs_by_room: dict[tuple[str, str], list[dict[str, Any]]] = {}
        # {(location, cluster, device_name): {device_id, device_type, ...}}
        self._device_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
        # Set to True after first update_*() call; control loop checks before ticking
        self._ready: bool = False

        logger.info(f"Initialized scheduler with {len(schedules)} schedules")

    # ------------------------------------------------------------------
    # Cache update methods (atomic reference swaps)
    # ------------------------------------------------------------------

    def update_mode_parameters(self, params: dict[tuple[str, str], dict[str, Any]]) -> None:
        """Atomically swap the mode_parameters cache.

        Args:
            params: ``{(location, cluster): {mode_id, day_start, night_start,
                ramp_up, ramp_down}}`` where day_start/night_start are ``time``
                objects or ``"HH:MM"`` strings, ramp_up/ramp_down are int minutes.
        """
        new_dict = dict(params)
        self._mode_params = new_dict
        self._ready = True
        logger.info(f"Updated mode_parameters cache: {len(new_dict)} rooms")

    def update_light_intensities(self, intensities: dict[tuple[int, int], float]) -> None:
        """Atomically swap the light_target_intensity cache.

        Args:
            intensities: ``{(device_id, mode_id): target_intensity}``
        """
        new_dict = dict(intensities)
        self._light_intensities = new_dict
        self._ready = True
        logger.info(f"Updated light_intensities cache: {len(new_dict)} entries")

    def update_light_programs(self, programs: list[dict[str, Any]]) -> None:
        """Atomically swap the light_programs cache and pre-index by room.

        Args:
            programs: List of program dicts from ``LightProgramsRepository.get_all_programs()``.
        """
        new_list = list(programs)
        new_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for prog in new_list:
            key = (prog.get("location", ""), prog.get("cluster", ""))
            new_index.setdefault(key, []).append(prog)
        self._light_programs = new_list
        self._light_programs_by_room = new_index
        self._ready = True
        logger.info(
            f"Updated light_programs cache: {len(new_list)} programs across {len(new_index)} rooms"
        )

    def update_device_lookup(self, devices: dict[tuple[str, str, str], dict[str, Any]]) -> None:
        """Atomically swap the device lookup cache.

        Args:
            devices: ``{(location, cluster, device_name): {device_id, device_type, ...}}``
        """
        new_dict = dict(devices)
        self._device_lookup = new_dict
        self._ready = True
        logger.info(f"Updated device_lookup cache: {len(new_dict)} devices")

    # ------------------------------------------------------------------
    # Climate period support (unchanged)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Non-light schedule methods (unchanged)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Photoperiod (lights-on window)
    # ------------------------------------------------------------------

    def is_in_photoperiod(self, location: str, cluster: str, current_time: datetime) -> bool:
        """True if current time is in the room's sun (lights-on) window.

        Reads from ``self._mode_params[(location, cluster)]`` cache.
        The photoperiod is ``[day_start, night_start)``.

        Handles overnight wrap: if ``day_start > night_start``, the photoperiod
        spans midnight (e.g., day_start=17:00, night_start=11:00 means lights on
        from 17:00 to 11:00 next day).

        **Failsafe:** If no mode_params exist for ``(location, cluster)``, returns
        ``True`` so lights go to 10% + relay ON (NOT darkness). The CRITICAL alarm
        fires from T9 (AlarmManager).

        Args:
            location: Room name
            cluster: Cluster name
            current_time: Current time

        Returns:
            True if in photoperiod (or failsafe when mode_params missing).
        """
        params = self._mode_params.get((location, cluster))
        if params is None:
            # Failsafe: treat as in-photoperiod so lights go to 10% + relay ON
            logger.warning(
                f"is_in_photoperiod: no mode_params for {location}/{cluster} "
                f"- returning True (failsafe: 10% + relay ON, NOT darkness)"
            )
            return True

        day_start = self._parse_time(params.get("day_start"))
        night_start = self._parse_time(params.get("night_start"))

        if not day_start or not night_start:
            logger.warning(
                f"is_in_photoperiod: invalid day_start/night_start for "
                f"{location}/{cluster} - returning True (failsafe)"
            )
            return True

        current_time_obj = current_time.time()

        if day_start > night_start:
            # Overnight photoperiod: e.g., 17:00 to 11:00 next day
            # In photoperiod if current >= day_start OR current < night_start
            return current_time_obj >= day_start or current_time_obj < night_start
        else:
            # Normal photoperiod: e.g., 06:00 to 18:00
            # In photoperiod if day_start <= current < night_start
            return day_start <= current_time_obj < night_start

    # ------------------------------------------------------------------
    # Light intensity calculation
    # ------------------------------------------------------------------

    def get_schedule_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime | None = None,
        current_intensity: float | None = None,
    ) -> float:
        """Get target intensity for a device from caches, with ramp calculation.

        Evaluation order:
        1. **Light programs** (priority-based, cycle mode, overnight wrap).
           If a program matches, its intensity (with ramps) is returned.
        2. **Photoperiod intensity**: if ``is_in_photoperiod()`` is True,
           look up ``self._light_intensities[(device_id, mode_id)]`` and apply
           the full ramp logic (ramp-up, ramp-down, mid-ramp recalc, steady).
           If the intensity row is missing -> 10.0 (hardcoded safety default).
           If mode_params is missing -> 10.0 (failsafe).
        3. If not in photoperiod -> 0.0 (moon).

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
            current_intensity: Current light intensity (0-100%), used as ramp start point

        Returns:
            Target intensity (0-100%) as a float.
        """
        if current_time is None:
            current_time = datetime.now(tz=LOCAL_TZ)

        # 1. Check light programs first (priority-based)
        program_intensity = self._evaluate_light_programs(
            location, cluster, device_name, current_time, current_intensity
        )
        if program_intensity is not None:
            return program_intensity

        # 2. Check photoperiod
        is_sun = self.is_in_photoperiod(location, cluster, current_time)
        if not is_sun:
            return 0.0

        # 3. Look up target intensity from cache
        params = self._mode_params.get((location, cluster))
        if params is None:
            # Failsafe: mode_params missing -> 10% + relay ON
            return MINIMUM_LIGHT_INTENSITY

        active_mode_id = params.get("mode_id")
        if active_mode_id is None:
            logger.warning(
                f"get_schedule_intensity: no mode_id in mode_params for "
                f"{location}/{cluster} - returning 10.0 (failsafe)"
            )
            return MINIMUM_LIGHT_INTENSITY

        device_info = self._device_lookup.get((location, cluster, device_name))
        if device_info is None:
            logger.warning(
                f"get_schedule_intensity: device not in lookup for "
                f"{location}/{cluster}/{device_name} - returning 10.0 (failsafe)"
            )
            return MINIMUM_LIGHT_INTENSITY

        device_id = device_info.get("device_id")
        if device_id is None:
            return MINIMUM_LIGHT_INTENSITY

        target_intensity = self._light_intensities.get((device_id, active_mode_id))
        if target_intensity is None:
            # Hardcoded safety default: 10% when no intensity row exists
            logger.debug(
                f"get_schedule_intensity: no light_target_intensity for "
                f"device_id={device_id}, mode_id={active_mode_id} - returning 10.0"
            )
            return MINIMUM_LIGHT_INTENSITY

        # Parse target to float
        try:
            target_intensity = float(target_intensity)
        except (ValueError, TypeError) as e:
            logger.error(
                f"Failed to parse target_intensity {target_intensity}: {e}",
                exc_info=True,
            )
            return MINIMUM_LIGHT_INTENSITY

        # 4. Apply full ramp logic
        ramp_up_duration = params.get("ramp_up", 0) or 0
        ramp_down_duration = params.get("ramp_down", 0) or 0

        day_start = self._parse_time(params.get("day_start"))
        night_start = self._parse_time(params.get("night_start"))

        if not day_start or not night_start:
            # No valid times -> return target directly (no ramps)
            if (location, cluster, device_name) in self._light_ramp_state:
                del self._light_ramp_state[(location, cluster, device_name)]
            return max(0.0, min(100.0, target_intensity))

        start_datetime, end_datetime = self._compute_start_end_datetimes(
            day_start, night_start, current_time
        )

        ramp_key = (location, cluster, device_name)
        return self._compute_ramped_intensity(
            ramp_key=ramp_key,
            target_intensity=target_intensity,
            ramp_up_duration=float(ramp_up_duration),
            ramp_down_duration=float(ramp_down_duration),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            current_time=current_time,
            current_intensity=current_intensity,
            device_name=device_name,
            location=location,
            cluster=cluster,
        )

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

        nominal_intensity: float = 0.0
        ramp_progress: float | None = None

        # Check if a light program is active
        matching_program = self._find_matching_program(location, cluster, device_name, current_time)
        if matching_program is not None:
            nominal_intensity = float(matching_program.get("target_intensity", 0.0))
            program_id = matching_program.get("id")
            ramp_key = (location, cluster, device_name, program_id)
            ramp_progress = self._get_ramp_progress(ramp_key, current_time)
        else:
            # Photoperiod path
            is_sun = self.is_in_photoperiod(location, cluster, current_time)
            if is_sun:
                params = self._mode_params.get((location, cluster))
                if params is not None and params.get("mode_id") is not None:
                    device_info = self._device_lookup.get((location, cluster, device_name))
                    if device_info and device_info.get("device_id") is not None:
                        device_id = device_info["device_id"]
                        active_mode_id = params["mode_id"]
                        target = self._light_intensities.get((device_id, active_mode_id))
                        if target is not None:
                            try:
                                nominal_intensity = float(target)
                            except (ValueError, TypeError):
                                nominal_intensity = MINIMUM_LIGHT_INTENSITY
                        else:
                            nominal_intensity = MINIMUM_LIGHT_INTENSITY
                    else:
                        nominal_intensity = MINIMUM_LIGHT_INTENSITY
                else:
                    nominal_intensity = MINIMUM_LIGHT_INTENSITY
            else:
                nominal_intensity = 0.0

            # Check photoperiod ramp state
            ramp_key = (location, cluster, device_name)
            ramp_progress = self._get_ramp_progress(ramp_key, current_time)

        return {
            "effective_intensity": effective_intensity,
            "nominal_intensity": nominal_intensity,
            "ramp_progress": ramp_progress,
        }

    # ------------------------------------------------------------------
    # Internal helpers: ramp logic
    # ------------------------------------------------------------------

    def _compute_start_end_datetimes(
        self, start_time: time, end_time: time, current_time: datetime
    ) -> tuple[datetime, datetime]:
        """Compute start and end datetimes for a time window, handling overnight wrap.

        Args:
            start_time: Window start as a ``time`` object
            end_time: Window end as a ``time`` object
            current_time: Current datetime (may be tz-aware or naive)

        Returns:
            Tuple of (start_datetime, end_datetime) aligned to the correct day.
        """
        schedule_tz = current_time.tzinfo
        current_time_obj = current_time.time()

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
            start_datetime = datetime.combine(current_time.date(), start_time, tzinfo=schedule_tz)
            end_datetime = datetime.combine(current_time.date(), end_time, tzinfo=schedule_tz)
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

        return start_datetime, end_datetime

    def _compute_ramped_intensity(
        self,
        *,
        ramp_key: tuple[Any, ...],
        target_intensity: float,
        ramp_up_duration: float,
        ramp_down_duration: float,
        start_datetime: datetime,
        end_datetime: datetime,
        current_time: datetime,
        current_intensity: float | None,
        device_name: str,
        location: str,
        cluster: str,
    ) -> float:
        """Compute intensity with full ramp logic.

        Preserves the exact ramp logic from the original ``get_schedule_intensity()``:
        - **Ramp-up**: from ``MINIMUM_LIGHT_INTENSITY`` to ``target_intensity`` over
          ``ramp_up_duration`` minutes from window start.
        - **Ramp-down**: from ``target_intensity`` to
          ``min(MINIMUM_LIGHT_INTENSITY, target_intensity)`` over ``ramp_down_duration``
          minutes before window end.
        - **Steady state**: return ``target_intensity`` directly.
        - **Mid-ramp target change recalculation**: when ``target_intensity`` differs
          from ``ramp_state["target_intensity"]``, the ramp recalculates from the
          current effective intensity to the new target within the remaining window.

        Args:
            ramp_key: State key for ``self._light_ramp_state``.
            target_intensity: Target intensity (0-100%).
            ramp_up_duration: Ramp-up duration in minutes.
            ramp_down_duration: Ramp-down duration in minutes.
            start_datetime: Window start datetime.
            end_datetime: Window end datetime.
            current_time: Current datetime.
            current_intensity: Current light intensity (for ramp resume).
            device_name: Device name (for logging).
            location: Location (for logging).
            cluster: Cluster (for logging).

        Returns:
            Computed intensity (0-100%) as a float.
        """
        time_since_start = (current_time - start_datetime).total_seconds() / 60.0
        time_until_end = (end_datetime - current_time).total_seconds() / 60.0

        # --- RAMP UP ---
        if ramp_up_duration > 0 and time_since_start < ramp_up_duration:
            # Ramp up period: Always ramp from 10% (minimum) to target
            # Initialize ramp state if missing - calculate expected intensity from time
            if ramp_key not in self._light_ramp_state:
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
                    f"{current_effective:.1f}% -> {target_intensity:.1f}% over "
                    f"{remaining_time:.1f}min (speeds up if needed)"
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

        # --- RAMP DOWN ---
        elif ramp_down_duration > 0 and time_until_end < ramp_down_duration:
            # Allow 0% at moon; 10% minimum during sun
            effective_minimum = min(
                MINIMUM_LIGHT_INTENSITY,
                target_intensity if target_intensity is not None else MINIMUM_LIGHT_INTENSITY,
            )

            if ramp_key not in self._light_ramp_state:
                # Calculate where we SHOULD be based on elapsed time (handles restarts)
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
                    f"{current_effective:.1f}% -> {ramp_state['target_intensity']:.1f}% "
                    f"over {remaining_time:.1f}min"
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

            effective_minimum_val = min(MINIMUM_LIGHT_INTENSITY, target_intensity)
            return max(effective_minimum_val, min(intensity, target_intensity))

        # --- STEADY STATE ---
        else:
            # Steady state - return target directly (not in ramp up or ramp down)
            if ramp_key in self._light_ramp_state:
                del self._light_ramp_state[ramp_key]
            clamped_intensity = max(0.0, min(100.0, target_intensity))
            logger.debug(
                f"Steady state intensity for {device_name} ({location}/{cluster}): "
                f"{clamped_intensity:.1f}% (target: {target_intensity:.1f}%)"
            )
            return clamped_intensity

    def _get_ramp_progress(self, ramp_key: tuple[Any, ...], current_time: datetime) -> float | None:
        """Get ramp progress (0.0-1.0) for a ramp state key, or None if not ramping."""
        if ramp_key not in self._light_ramp_state:
            return None
        ramp_state = self._light_ramp_state[ramp_key]
        ramp_duration = ramp_state.get("ramp_duration") or 0.0
        if ramp_duration <= 0:
            return None
        elapsed = (current_time - ramp_state["ramp_start_timestamp"]).total_seconds() / 60.0
        progress = min(max(elapsed / ramp_duration, 0.0), 1.0)
        if progress >= 1.0:
            return None  # Ramp complete
        return progress

    # ------------------------------------------------------------------
    # Internal helpers: light programs
    # ------------------------------------------------------------------

    def _find_matching_program(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime,
    ) -> dict[str, Any] | None:
        """Find the highest-priority matching light program for a device.

        Filters by:
        - device_id matches OR device_id is None (room-level)
        - mode_id matches active mode OR mode_id is None (all modes)
        - current_time in [start_time, end_time) (handles overnight wrap)
        - day_of_week matches or is None

        Sorts by priority DESC, then created_at ASC (oldest first for ties).

        Returns:
            The matching program dict, or None if no program matches.
        """
        programs = self._light_programs_by_room.get((location, cluster), [])
        if not programs:
            return None

        # Get device_id and active mode_id for filtering
        device_info = self._device_lookup.get((location, cluster, device_name))
        device_id = device_info.get("device_id") if device_info else None

        params = self._mode_params.get((location, cluster))
        active_mode_id = params.get("mode_id") if params else None

        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()

        matching: list[dict[str, Any]] = []
        for prog in programs:
            if not prog.get("enabled", True):
                continue

            # Device filter: device_id matches or is None (room-level)
            prog_device_id = prog.get("device_id")
            if prog_device_id is not None and device_id is not None and prog_device_id != device_id:
                continue

            # Mode filter: mode_id matches or is None (all modes)
            prog_mode_id = prog.get("mode_id")
            if (
                prog_mode_id is not None
                and active_mode_id is not None
                and prog_mode_id != active_mode_id
            ):
                continue

            # Day of week filter
            day_of_week = prog.get("day_of_week")
            if day_of_week is not None and day_of_week != current_weekday:
                continue

            # Time window filter (handle overnight wrap)
            start_time = self._parse_time(prog.get("start_time"))
            end_time = self._parse_time(prog.get("end_time"))
            if not start_time or not end_time:
                continue

            if start_time > end_time:
                in_range = current_time_obj >= start_time or current_time_obj < end_time
            else:
                in_range = start_time <= current_time_obj < end_time

            if not in_range:
                continue

            matching.append(prog)

        if not matching:
            return None

        # Sort by priority DESC, created_at ASC (oldest first for ties)
        matching.sort(key=lambda p: (-p.get("priority", 0), p.get("created_at") or datetime.min))

        return matching[0]

    def _evaluate_light_programs(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: datetime,
        current_intensity: float | None,
    ) -> float | None:
        """Evaluate light programs for a device. Returns intensity if a program matches.

        Returns None if no program matches (caller should fall through to photoperiod).
        """
        prog = self._find_matching_program(location, cluster, device_name, current_time)
        if prog is None:
            return None

        if prog.get("cycle_enabled"):
            return self._evaluate_cycle_program(prog, current_time, device_name, location, cluster)
        else:
            return self._evaluate_static_program(
                prog, current_time, current_intensity, device_name, location, cluster
            )

    def _evaluate_cycle_program(
        self,
        prog: dict[str, Any],
        current_time: datetime,
        device_name: str,
        location: str,
        cluster: str,
    ) -> float:
        """Evaluate a cycle-mode program (on/off pulsing within the window).

        Calculates the on/off phase based on elapsed time since program start.
        Returns ``target_intensity`` during on-phase, ``0.0`` during off-phase.
        """
        on_seconds = prog.get("cycle_on_seconds") or 0
        off_seconds = prog.get("cycle_off_seconds") or 0
        if on_seconds <= 0:
            return 0.0

        start_time = self._parse_time(prog.get("start_time"))
        if not start_time:
            return 0.0

        # Calculate elapsed seconds since program start
        schedule_tz = current_time.tzinfo
        current_time_obj = current_time.time()

        if schedule_tz is None:
            start_datetime = datetime.combine(current_time.date(), start_time)
            if start_time > current_time_obj:
                # Program started yesterday (overnight)
                start_datetime = datetime.combine(
                    current_time.date() - timedelta(days=1), start_time
                )
        else:
            start_datetime = datetime.combine(current_time.date(), start_time, tzinfo=schedule_tz)
            if start_time > current_time_obj:
                start_datetime = datetime.combine(
                    current_time.date() - timedelta(days=1),
                    start_time,
                    tzinfo=schedule_tz,
                )

        elapsed_seconds = (current_time - start_datetime).total_seconds()
        if elapsed_seconds < 0:
            return 0.0

        cycle_total = on_seconds + off_seconds
        if cycle_total <= 0:
            return 0.0

        phase = elapsed_seconds % cycle_total
        if phase < on_seconds:
            return float(prog.get("target_intensity", 0.0))
        return 0.0

    def _evaluate_static_program(
        self,
        prog: dict[str, Any],
        current_time: datetime,
        current_intensity: float | None,
        device_name: str,
        location: str,
        cluster: str,
    ) -> float:
        """Evaluate a non-cycle program with ramp logic.

        Uses a SEPARATE ramp state key ``(location, cluster, device_name, program_id)``
        so program ramps do NOT share photoperiod ramp state.
        """
        try:
            target_intensity = float(prog.get("target_intensity", 0.0))
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse program target_intensity: {e}", exc_info=True)
            return 0.0

        ramp_up_duration = float(prog.get("ramp_up_minutes", 0) or 0)
        ramp_down_duration = float(prog.get("ramp_down_minutes", 0) or 0)

        start_time = self._parse_time(prog.get("start_time"))
        end_time = self._parse_time(prog.get("end_time"))
        if not start_time or not end_time:
            return target_intensity

        start_datetime, end_datetime = self._compute_start_end_datetimes(
            start_time, end_time, current_time
        )

        program_id = prog.get("id")
        # SEPARATE ramp state key: do NOT share photoperiod ramp state
        ramp_key = (location, cluster, device_name, program_id)

        return self._compute_ramped_intensity(
            ramp_key=ramp_key,
            target_intensity=target_intensity,
            ramp_up_duration=ramp_up_duration,
            ramp_down_duration=ramp_down_duration,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            current_time=current_time,
            current_intensity=current_intensity,
            device_name=device_name,
            location=location,
            cluster=cluster,
        )

    # ------------------------------------------------------------------
    # Internal helpers: device type lookup
    # ------------------------------------------------------------------

    def _is_light_device(self, location: str, cluster: str, device_name: str) -> bool:
        """Check if a device is a light based on the device_lookup cache.

        Returns False if the device is not in the lookup (falls back to
        schedule-based behavior for backward compatibility).
        """
        info = self._device_lookup.get((location, cluster, device_name))
        if info is None:
            return False
        return info.get("device_type") == "light"

    # ------------------------------------------------------------------
    # Utility methods (unchanged)
    # ------------------------------------------------------------------

    def _parse_time(self, time_str: str | None) -> time | None:
        """Parse time string to time object.

        Args:
            time_str: Time string in format "HH:MM" or "HH:MM:SS",
                or a ``time`` object (returned as-is)

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
        """Update schedules list (non-light DAY/NIGHT rows)."""
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
