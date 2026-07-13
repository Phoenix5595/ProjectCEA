"""Light program evaluation (supplemental, override, cycle, static)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class LightProgramsMixin:
    """Mixin for light program matching and evaluation."""

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
