"""Light intensity calculation from caches with ramp logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Minimum light intensity (10%) - lowest setting at which lights emit light
MINIMUM_LIGHT_INTENSITY = 10.0


class LightIntensityMixin:
    """Mixin for photoperiod-based light intensity lookup and ramp application."""

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
            from app.control.scheduler import LOCAL_TZ

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
            from app.control.scheduler import LOCAL_TZ

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
