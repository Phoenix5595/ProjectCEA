"""Service for building and loading schedule state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Pool

    from app.redis_client import AutomationRedisClient
    from app.repositories.climate_periods import ClimatePeriodRepository
    from app.repositories.schedules import ScheduleRepository

logger = get_logger(__name__)


async def build_schedule_state(
    schedule_repo: ScheduleRepository,
    climate_periods_repo: ClimatePeriodRepository,
    location: str,
    cluster: str,
) -> dict[str, Any]:
    """Build complete schedule state from database following canonical schema.

    Args:
        schedule_repo: Schedule repository
        climate_periods_repo: Climate period repository
        location: Location name
        cluster: Cluster name

    Returns:
        Complete schedule state matching canonical schema
    """
    # Get room schedule
    room_schedule = await schedule_repo.get_room_schedule(location, cluster) or {}

    # Get climate periods for setpoints (climate_periods replaced legacy climate schedule)
    periods = await climate_periods_repo.get_periods(location, cluster)
    periods_data = []
    for period in periods:
        periods_data.append(
            {
                "period_name": period.get("period_name"),
                "start_time": str(period.get("start_time")) if period.get("start_time") else None,
                "end_time": str(period.get("end_time")) if period.get("end_time") else None,
                "ramp_minutes": period.get("ramp_minutes", 0) or 0,
                "heating_setpoint": period.get("heating_setpoint"),
                "cooling_setpoint": period.get("cooling_setpoint"),
                "vpd_setpoint": period.get("vpd_setpoint"),
                "co2_setpoint": period.get("co2_setpoint"),
            }
        )

    # Get light schedules to extract target_intensity
    all_schedules = await schedule_repo.get_schedules(location, cluster)
    lights = {}
    for sched in all_schedules:
        device_name = sched.get("device_name", "")
        if (
            device_name.startswith("light_")
            and sched.get("mode") in ("SUN", "DAY")
            and sched.get("enabled")
        ):
            target_intensity = sched.get("target_intensity")
            if target_intensity is not None:
                lights[device_name] = {"target_intensity": float(target_intensity)}

    # Build schedule state structure
    schedule_state = {
        "room": {
            "day_start_time": room_schedule.get("day_start_time", "06:00"),
            "day_end_time": room_schedule.get("day_end_time", "20:00"),
            "night_start_time": room_schedule.get("night_start_time", "20:00"),
            "night_end_time": room_schedule.get("night_end_time", "06:00"),
            "ramp_up_duration": room_schedule.get("ramp_up_duration", 30) or 30,
            "ramp_down_duration": room_schedule.get("ramp_down_duration", 15) or 15,
        },
        "climate": {
            # Legacy pre_day/pre_night fields removed - system now uses climate_periods
        },
        "periods": periods_data,
        "lights": lights,
    }

    return schedule_state


async def load_schedule_state_to_redis(
    pool: Pool,
    redis_client: AutomationRedisClient,
    schedule_repo: ScheduleRepository,
    climate_periods_repo: ClimatePeriodRepository,
) -> None:
    """DEPRECATED: Schedule state Redis keys are dead code (T10 cleanup).

    Previously loaded schedule state from DB to Redis. The SchedulesMixin
    that consumed these keys has been removed. This function is kept as a
    no-op to avoid breaking startup callers; it logs once and returns.
    """
    logger.info(
        "load_schedule_state_to_redis is deprecated and does nothing "
        "(SchedulesMixin removed in T10)"
    )
