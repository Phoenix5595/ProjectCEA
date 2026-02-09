"""Service for building and loading schedule state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Pool

    from app.redis_client import AutomationRedisClient
    from app.repositories.schedules import ScheduleRepository
    from app.repositories.setpoints import SetpointRepository

logger = get_logger(__name__)

SETPOINT_MODES = ("DAY", "NIGHT", "PRE_DAY", "PRE_NIGHT")


async def build_schedule_state(
    schedule_repo: ScheduleRepository,
    setpoint_repo: SetpointRepository,
    location: str,
    cluster: str,
) -> dict[str, Any]:
    """Build complete schedule state from database following canonical schema.

    Args:
        schedule_repo: Schedule repository
        setpoint_repo: Setpoint repository
        location: Location name
        cluster: Cluster name

    Returns:
        Complete schedule state matching canonical schema
    """
    # Get room schedule
    room_schedule = await schedule_repo.get_room_schedule(location, cluster) or {}

    # Get climate schedule
    climate_schedule = await schedule_repo.get_climate_schedule(location, cluster)

    # Get setpoints for all modes
    setpoints = {}
    for mode in SETPOINT_MODES:
        setpoint_data = await setpoint_repo.get_setpoint(location, cluster, mode)
        if setpoint_data:
            setpoints[mode] = {
                "heating_setpoint": setpoint_data.get("heating_setpoint"),
                "cooling_setpoint": setpoint_data.get("cooling_setpoint"),
                "humidity": setpoint_data.get("humidity"),
                "co2": setpoint_data.get("co2"),
                "vpd": setpoint_data.get("vpd"),
                "ramp_in_duration": setpoint_data.get("ramp_in_duration", 0) or 0,
            }
        else:
            setpoints[mode] = {}

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
            "pre_day_duration": climate_schedule.get("pre_day_duration", 0)
            if climate_schedule
            else 0,
            "pre_night_duration": climate_schedule.get("pre_night_duration", 0)
            if climate_schedule
            else 0,
        },
        "setpoints": setpoints,
        "lights": lights,
    }

    return schedule_state


async def load_schedule_state_to_redis(
    pool: Pool,
    redis_client: AutomationRedisClient,
    schedule_repo: ScheduleRepository,
    setpoint_repo: SetpointRepository,
) -> None:
    """Load all schedule state from database to Redis following canonical schema.

    Queries all room schedules, climate schedules, setpoints (including PRE_DAY and PRE_NIGHT),
    and light schedules from DB, groups by location/cluster, and writes to Redis state.
    Called on service startup to populate Redis with current schedule configuration.
    """
    if not redis_client or not getattr(redis_client, "redis_enabled", False):
        logger.warning("Redis not enabled, skipping schedule state load")
        return

    try:
        async with pool.acquire() as conn:
            # Get all unique location/cluster pairs
            rows = await conn.fetch("""
                SELECT DISTINCT location, cluster
                FROM schedules
                UNION
                SELECT DISTINCT location, cluster
                FROM setpoints
            """)

            locations_loaded = []

            for row in rows:
                location = row["location"]
                cluster = row["cluster"]

                try:
                    schedule_state = await build_schedule_state(
                        schedule_repo, setpoint_repo, location, cluster
                    )

                    # Write to Redis
                    redis_client.write_schedule_state(location, cluster, schedule_state)
                    locations_loaded.append(f"{location}/{cluster}")
                    logger.info(f"Loaded schedule state to Redis for {location}/{cluster}")
                except Exception as e:
                    logger.warning(f"Failed to load schedule state for {location}/{cluster}: {e}")

            if locations_loaded:
                logger.info(
                    f"Loaded schedule state to Redis for {len(locations_loaded)} locations: {', '.join(locations_loaded)}"
                )
            else:
                logger.info("No schedule state to load (no locations found in database)")
    except Exception as e:
        logger.error(f"Error loading schedule state to Redis: {e}", exc_info=True)
