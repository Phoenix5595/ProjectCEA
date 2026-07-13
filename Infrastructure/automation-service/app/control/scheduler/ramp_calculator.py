"""Ramp calculation logic for light intensity transitions."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Minimum light intensity (10%) - lowest setting at which lights emit light
MINIMUM_LIGHT_INTENSITY = 10.0


class RampCalculatorMixin:
    """Mixin for ramp-up, ramp-down, and steady-state intensity calculations."""

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
