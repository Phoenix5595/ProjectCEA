"""Climate Period Resolver - Handles period lookup and setpoint calculation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ClimatePeriodResolver:
    """Resolves climate period and calculates effective setpoints."""

    def __init__(self, scheduler: Any, setpoint_manager: Any):
        """Initialize climate period resolver.

        Args:
            scheduler: Scheduler instance for time-based operations
            setpoint_manager: SetpointManager for setpoint calculations
        """
        self.scheduler = scheduler
        self.setpoint_manager = setpoint_manager

    async def resolve_period(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        database: Any,
    ) -> dict[str, Any]:
        """Get active period and effective setpoints.

        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current timestamp
            database: Database manager for climate period queries

        Returns:
            Dict with period info, setpoint_data, effective_data, and current_period_name
        """
        from zoneinfo import ZoneInfo

        # Get current time in America/Toronto timezone (HH:MM format)
        toronto_time = datetime.now(ZoneInfo("America/Toronto"))
        time_str = toronto_time.strftime("%H:%M")

        # Get light schedule for is_sun determination
        light_schedule = None
        try:
            light_schedule = await database.schedule_repo.get_room_light_schedule(location, cluster)
        except Exception as e:
            logger.info(f"Database error fetching light schedule for {location}/{cluster}: {e}")

        # Get active climate period from database
        active_period = await database.climate_periods_repo.get_active_period(
            location, cluster, time_str
        )

        # Track current period name
        current_period_name: str = (
            active_period.get("period_name", "NO_PERIOD") if active_period else "NO_PERIOD"
        )

        # Build setpoint_data from period fields
        setpoint_data: dict[str, Any] | None = None
        if active_period:
            ramp_minutes = active_period.get("ramp_minutes", 0) or 0

            setpoint_data = {
                "heating_setpoint": active_period.get("heating_setpoint"),
                "cooling_setpoint": active_period.get("cooling_setpoint"),
                "vpd": active_period.get("vpd_setpoint"),
                "co2": active_period.get("co2_setpoint"),
                "humidity": None,  # VPD cascade derives humidity
                "ramp_in_duration": ramp_minutes,
            }

            logger.debug(
                f"Retrieved climate period for {location}/{cluster} at {time_str}: "
                + f"period={current_period_name}, "
                + f"heating={setpoint_data.get('heating_setpoint')}, "
                + f"cooling={setpoint_data.get('cooling_setpoint')}, "
                + f"ramp_minutes={ramp_minutes}"
            )

        return {
            "active_period": active_period,
            "current_period_name": current_period_name,
            "setpoint_data": setpoint_data,
            "light_schedule": light_schedule,
            "time_str": time_str,
        }

    def calculate_is_sun(
        self, light_schedule: dict[str, Any] | None, current_time: datetime
    ) -> bool:
        """Calculate if current time is within sun window.

        Args:
            light_schedule: Light schedule dict with day_start_time and day_end_time
            current_time: Current timestamp

        Returns:
            True if within sun window, False otherwise
        """
        if not light_schedule or not isinstance(light_schedule, dict):
            return False

        sun_start = light_schedule.get("day_start_time")
        sun_end = light_schedule.get("day_end_time")

        if not sun_start or not sun_end:
            return False

        try:
            from .scheduler import is_time_in_range

            parts_s = sun_start.split(":")
            parts_e = sun_end.split(":")
            sun_start_min = int(parts_s[0]) * 60 + int(parts_s[1])
            sun_end_min = int(parts_e[0]) * 60 + int(parts_e[1])
            current_min = current_time.hour * 60 + current_time.minute
            return is_time_in_range(current_min, sun_start_min, sun_end_min)
        except (ValueError, IndexError):
            return False
