"""Climate Period Resolver - Handles period lookup and setpoint calculation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.control.scheduler import LOCAL_TZ
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from app.state import StateManager

logger = get_logger(__name__)


class ClimatePeriodResolver:
    """Resolves climate period and calculates effective setpoints."""

    # TTL constants for cache-aside pattern
    _LIGHT_SCHEDULE_TTL = 30.0
    _CLIMATE_PERIOD_TTL = 30.0

    def __init__(self, scheduler: Any, setpoint_manager: Any, state: StateManager | None = None):
        """Initialize climate period resolver.

        Args:
            scheduler: Scheduler instance for time-based operations
            setpoint_manager: SetpointManager for setpoint calculations
            state: StateManager for caching DB queries (<1ms reads vs 30-90ms DB)
        """
        self.scheduler = scheduler
        self.setpoint_manager = setpoint_manager
        self._state = state

    async def resolve_period(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        database: Any,
    ) -> dict[str, Any]:
        """Get active period and effective setpoints.

        Cache-aside pattern: check StateManager first, populate on miss.
        Caches: light schedule (30s), room mode (via StateManager default 300s),
        active climate period (30s).
        """
        # Wall clock for climate_periods lookup — align with control loop ``current_time``.
        if current_time.tzinfo is None:
            toronto_time = current_time.replace(tzinfo=LOCAL_TZ)
        else:
            toronto_time = current_time.astimezone(LOCAL_TZ)
        time_str = toronto_time.strftime("%H:%M")

        active_period = await self._get_cached_climate_period(database, location, cluster, time_str)

        light_schedule = await self._get_cached_light_schedule(database, location, cluster)
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

    async def _get_cached_light_schedule(
        self, database: Any, location: str, cluster: str
    ) -> Any | None:
        """Get light schedule from cache or database (30s TTL)."""
        cache_key = f"schedule:{location}:{cluster}"
        if self._state:
            cached_sched = await self._state.get(cache_key)
            if cached_sched is not None:
                logger.debug(f"Cache hit for light schedule: {location}/{cluster}")
                return cached_sched

        try:
            light_schedule = await database.schedule_repo.get_room_light_schedule(location, cluster)
        except Exception as e:
            logger.info(f"Database error fetching light schedule for {location}/{cluster}: {e}")
            return None

        if self._state and light_schedule is not None:
            await self._state.set(cache_key, light_schedule, ttl=self._LIGHT_SCHEDULE_TTL)

        return light_schedule

    async def _get_cached_climate_period(
        self,
        database: Any,
        location: str,
        cluster: str,
        time_str: str,
    ) -> dict[str, Any] | None:
        """Get active climate period from cache or database (30s TTL)."""
        mode_id = None
        submode_id = None
        try:
            active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
            if active_mode:
                mode_id = active_mode.get("mode_id")
                submode_id = active_mode.get("submode_id")
        except Exception as e:
            logger.error(
                f"Failed to get active mode for climate period {location}/{cluster}: {e}",
                exc_info=True,
            )

        cache_key = f"cache:climate_period:{location}:{cluster}:{time_str}"
        cached_period: dict[str, Any] | None = None
        if self._state and mode_id is not None:
            cached_period = await self._state.get(cache_key)
            if cached_period is not None:
                logger.debug(f"Cache hit for climate period: {location}/{cluster}/{time_str}")
                return cached_period

        try:
            active_period = await database.climate_periods_repo.get_active_period(
                location, cluster, time_str, mode_id=mode_id, submode_id=submode_id
            )
        except Exception as e:
            logger.info(f"Database error fetching climate period for {location}/{cluster}: {e}")
            return None

        if self._state and active_period is not None:
            await self._state.set(cache_key, active_period, ttl=self._CLIMATE_PERIOD_TTL)

        return active_period

    def calculate_is_sun(self, current_time: datetime, location: str, cluster: str) -> bool:
        """Calculate if current time is within photoperiod for a room/cluster."""
        return self.scheduler.is_in_photoperiod(location, cluster, current_time)
