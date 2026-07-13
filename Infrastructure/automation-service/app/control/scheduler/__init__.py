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

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from shared.infra_logging import get_logger

from .light_intensity import LightIntensityMixin
from .light_programs import LightProgramsMixin
from .photoperiod import PhotoperiodMixin
from .ramp_calculator import RampCalculatorMixin
from .schedules import SchedulesMixin

# Timezone constant for consistent scheduling
LOCAL_TZ = ZoneInfo("America/Toronto")

# Minimum light intensity (10%) - lowest setting at which lights emit light
MINIMUM_LIGHT_INTENSITY = 10.0

logger = get_logger(__name__)


class Scheduler(
    PhotoperiodMixin,
    RampCalculatorMixin,
    LightProgramsMixin,
    SchedulesMixin,
    LightIntensityMixin,
):
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
