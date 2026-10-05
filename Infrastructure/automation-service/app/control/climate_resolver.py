"""Climate Period Resolver - Handles period lookup and setpoint calculation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.control.scheduler import LOCAL_TZ
from shared.infra_logging import get_logger
from shared.redis_keys import climate_period_cache_key

if TYPE_CHECKING:
    from app.state import StateManager

logger = get_logger(__name__)


def _captured_profile_identity(
    active_profile: Mapping[str, Any] | None,
) -> tuple[int | None, int | None]:
    """Extract the exact captured (mode_id, submode_id), or unknown (None, None).

    A malformed identity is unknown — never coerced into the NULL base
    profile, which would make an unactivated prepared profile a broad
    fallback.
    """
    if active_profile is None:
        return None, None
    mode_id = active_profile.get("mode_id")
    submode_id = active_profile.get("submode_id")
    if not isinstance(mode_id, int):
        return None, None
    if submode_id is not None and not isinstance(submode_id, int):
        return None, None
    return mode_id, submode_id


def _normalized_hhmm(value: Any) -> str:
    """Normalize a stored clock value to its HH:MM form."""
    return str(value)[:5]


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
        *,
        active_profile: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Get active period and effective setpoints for one captured tick identity.

        The active profile is the captured tick identity from the installed
        registry snapshot. Unknown identity means no climate lookup and no
        cache reuse; an unactivated prepared profile is never a broad fallback.
        """
        if current_time.tzinfo is None:
            toronto_time = current_time.replace(tzinfo=LOCAL_TZ)
        else:
            toronto_time = current_time.astimezone(LOCAL_TZ)
        time_str = toronto_time.strftime("%H:%M")

        mode_id, submode_id = _captured_profile_identity(active_profile)

        active_period = await self._get_cached_climate_period(
            database, location, cluster, time_str, active_profile=active_profile
        )

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
                "period_start_time": active_period.get("start_time"),
                # Exact captured-profile identity for ramp retarget decisions;
                # the row's own IDs are replacement-row columns and must never
                # substitute the captured authority.
                "climate_identity": (
                    mode_id,
                    submode_id,
                    active_period.get("period_name"),
                    _normalized_hhmm(active_period.get("start_time")),
                    _normalized_hhmm(active_period.get("end_time")),
                ),
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
        *,
        active_profile: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Get the exact-profile climate period from cache or database (30s TTL).

        One cache entry per (location, cluster, mode_id, submode_id) profile
        key; the entry itself is exactly ``{time_str, period}`` because the
        key already carries the profile identity, so a cached value is only
        reused for the matching minute of the same profile.
        """
        mode_id, submode_id = _captured_profile_identity(active_profile)
        if mode_id is None:
            # Unknown captured identity: no climate lookup or cache reuse.
            return None

        cache_key = climate_period_cache_key(location, cluster, mode_id, submode_id)
        cached_entry: dict[str, Any] | None = None
        if self._state:
            cached_entry = await self._state.get(cache_key)
            if cached_entry is not None and cached_entry.get("time_str") == time_str:
                logger.debug(f"Cache hit for climate period: {location}/{cluster}/{time_str}")
                return cached_entry.get("period")
            # Minute mismatch falls through to a fresh lookup.

        try:
            active_period = await database.climate_periods_repo.get_active_period(
                location, cluster, time_str, mode_id=mode_id, submode_id=submode_id
            )
        except Exception as e:
            logger.info(f"Database error fetching climate period for {location}/{cluster}: {e}")
            return None

        if self._state and active_period is not None:
            await self._state.set(
                cache_key,
                {"time_str": time_str, "period": active_period},
                ttl=self._CLIMATE_PERIOD_TTL,
            )

        return active_period

    def calculate_is_sun(self, current_time: datetime, location: str, cluster: str) -> bool:
        """Calculate if current time is within photoperiod for a room/cluster."""
        return self.scheduler.is_in_photoperiod(location, cluster, current_time)
