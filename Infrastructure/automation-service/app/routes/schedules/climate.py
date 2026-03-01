"""Climate schedule endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.validation import validate_setpoint
from shared.infra_logging import get_logger

from .base import (
    get_automation_redis,
    get_config,
    get_database,
)
from .utils import (
    SETPOINT_MODES,
    _build_schedule_state,
    _parse_time_str,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

router = APIRouter()


class ClimateScheduleSetpoint(BaseModel):
    """Setpoint data for a specific mode."""

    heating_setpoint: float | None = None
    cooling_setpoint: float | None = None
    humidity: float | None = None
    co2: float | None = None
    vpd: float | None = None
    ramp_in_duration: int | None = None  # Minutes to ramp in when entering this mode (0 = instant)


class ClimateScheduleCreate(BaseModel):
    """Climate schedule with pre-day/pre-night durations and setpoints for all modes."""

    day_start_time: str  # "HH:MM" format (from light schedule)
    day_end_time: str  # "HH:MM" format (from light schedule)
    pre_day_duration: int  # Minutes before day starts
    pre_night_duration: int  # Minutes after night starts
    setpoints: dict[str, ClimateScheduleSetpoint]  # Keys: 'DAY', 'NIGHT', 'PRE_DAY', 'PRE_NIGHT'


@router.get("/api/climate-schedule/{location}/{cluster}")
async def get_climate_schedule(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get climate schedule and all setpoints for a location/cluster.

    Args:
        location: Location name
        cluster: Cluster name

    Returns:
        Dict with day_start_time, day_end_time, pre_day_duration, pre_night_duration,
        and setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)
    """
    # Get light schedule for day times
    light_schedule = await database.schedule_repo.get_room_light_schedule(location, cluster)
    if not light_schedule:
        # Return defaults if no light schedule found
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "pre_day_duration": 0,
            "pre_night_duration": 0,
            "setpoints": {"DAY": {}, "NIGHT": {}, "PRE_DAY": {}, "PRE_NIGHT": {}},
        }

    # Get climate schedule (pre-day/pre-night durations)
    climate_schedule = await database.schedule_repo.get_climate_schedule(location, cluster)

    # Get setpoints for all modes
    setpoints = {}
    for mode in SETPOINT_MODES:
        setpoint_data = await database.setpoint_repo.get_setpoint(location, cluster, mode)
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

    return {
        "day_start_time": light_schedule.get("day_start_time"),
        "day_end_time": light_schedule.get("day_end_time"),
        "pre_day_duration": climate_schedule.get("pre_day_duration", 0) if climate_schedule else 0,
        "pre_night_duration": climate_schedule.get("pre_night_duration", 0)
        if climate_schedule
        else 0,
        "setpoints": setpoints,
    }


@router.post("/api/climate-schedule/{location}/{cluster}")
async def save_climate_schedule(
    location: str,
    cluster: str,
    schedule: ClimateScheduleCreate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Save climate schedule and setpoints atomically.

    This endpoint atomically saves:
    - Climate schedule (pre_day_duration, pre_night_duration)
    - Setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)

    Args:
        location: Location name
        cluster: Cluster name
        schedule: Climate schedule data

    Returns:
        Success response with warnings (if any)
    """
    from app.control.scheduler import Scheduler

    # Validate conflict rules using scheduler
    scheduler = Scheduler([])
    is_valid, error_msg = scheduler.validate_climate_schedule_conflicts(
        schedule.day_start_time,
        schedule.day_end_time,
        schedule.pre_day_duration,
        schedule.pre_night_duration,
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Validate durations
    if schedule.pre_day_duration < 0 or schedule.pre_day_duration > 240:
        raise HTTPException(
            status_code=400, detail="pre_day_duration must be between 0 and 240 minutes"
        )

    if schedule.pre_night_duration < 0 or schedule.pre_night_duration > 240:
        raise HTTPException(
            status_code=400, detail="pre_night_duration must be between 0 and 240 minutes"
        )

    warnings = []

    # Validate setpoints and check for VPD ramp warnings
    for mode, setpoint_data in schedule.setpoints.items():
        if mode not in SETPOINT_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode in setpoints: {mode}. Valid modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT",
            )

        # Validate ramp_in_duration
        ramp_in = setpoint_data.ramp_in_duration or 0
        if ramp_in < 0 or ramp_in > 240:
            raise HTTPException(
                status_code=400,
                detail=f"ramp_in_duration for {mode} must be between 0 and 240 minutes",
            )

        # VPD ramp warning
        if setpoint_data.vpd is not None and ramp_in > 15:
            warnings.append(
                f"VPD ramp_in_duration for {mode} is {ramp_in} minutes (>15 min). This may cause stomatal shock, humidity overshoot, or condensation events."
            )

        # Validate setpoint values if provided
        if setpoint_data.heating_setpoint is not None:
            is_valid, error = validate_setpoint(
                "temperature",
                setpoint_data.heating_setpoint,
                config,  # type: ignore
            )
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.heating_setpoint: {error}")

        if setpoint_data.cooling_setpoint is not None:
            is_valid, error = validate_setpoint(
                "temperature", setpoint_data.cooling_setpoint, config
            )
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.cooling_setpoint: {error}")

        if setpoint_data.humidity is not None:
            is_valid, error = validate_setpoint("humidity", setpoint_data.humidity, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.humidity: {error}")

        if setpoint_data.co2 is not None:
            is_valid, error = validate_setpoint("co2", setpoint_data.co2, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.co2: {error}")

        if setpoint_data.vpd is not None:
            is_valid, error = validate_setpoint("vpd", setpoint_data.vpd, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.vpd: {error}")

    # Atomic save: use database transaction
    try:
        pool = await database._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Save climate schedule (update or create schedule with pre_day_duration/pre_night_duration)
                # Find existing climate schedule or create new one
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND device_name = 'climate'
                      AND (pre_day_duration IS NOT NULL OR pre_night_duration IS NOT NULL)
                    ORDER BY id DESC
                    LIMIT 1
                """,
                    location,
                    cluster,
                )

                if existing:
                    # Update existing
                    await conn.execute(
                        """
                        UPDATE schedules
                        SET pre_day_duration = $1, pre_night_duration = $2
                        WHERE id = $3
                    """,
                        schedule.pre_day_duration,
                        schedule.pre_night_duration,
                        existing["id"],
                    )
                else:
                    # Create new (use a dummy device_name for climate schedules)
                    start_time_obj = _parse_time_str(schedule.day_start_time)
                    end_time_obj = _parse_time_str(schedule.day_end_time)

                    await conn.execute(
                        """
                        INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time, enabled, pre_day_duration, pre_night_duration)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                        f"Climate Schedule {location}/{cluster}",
                        location,
                        cluster,
                        "climate",
                        start_time_obj,
                        end_time_obj,
                        True,
                        schedule.pre_day_duration,
                        schedule.pre_night_duration,
                    )

                # Save setpoints for all modes
                for mode, setpoint_data in schedule.setpoints.items():
                    # Check if setpoint exists
                    existing_setpoint = await conn.fetchrow(
                        """
                        SELECT id FROM setpoints
                        WHERE location = $1 AND cluster = $2 AND mode = $3
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """,
                        location,
                        cluster,
                        mode,
                    )

                    # Build update dict
                    updates = {}
                    if setpoint_data.heating_setpoint is not None:
                        updates["heating_setpoint"] = setpoint_data.heating_setpoint
                    if setpoint_data.cooling_setpoint is not None:
                        updates["cooling_setpoint"] = setpoint_data.cooling_setpoint
                    if setpoint_data.humidity is not None:
                        updates["humidity"] = setpoint_data.humidity
                    if setpoint_data.co2 is not None:
                        updates["co2"] = setpoint_data.co2
                    if setpoint_data.vpd is not None:
                        updates["vpd"] = setpoint_data.vpd
                    if setpoint_data.ramp_in_duration is not None:
                        updates["ramp_in_duration"] = setpoint_data.ramp_in_duration

                    if updates:
                        if existing_setpoint:
                            # Update existing
                            set_clauses = [f"{k} = ${i + 1}" for i, k in enumerate(updates.keys())]
                            values = list(updates.values()) + [existing_setpoint["id"]]
                            await conn.execute(
                                f"""
                                UPDATE setpoints
                                SET {", ".join(set_clauses)}, updated_at = NOW()
                                WHERE id = ${len(updates) + 1}
                            """,
                                *values,
                            )
                        else:
                            # Insert new
                            columns = ["location", "cluster", "mode"] + list(updates.keys())
                            placeholders = [f"${i + 1}" for i in range(len(columns))]
                            values = [location, cluster, mode] + list(updates.values())
                            await conn.execute(
                                f"""
                                INSERT INTO setpoints ({", ".join(columns)}, updated_at)
                                VALUES ({", ".join(placeholders)}, NOW())
                            """,
                                *values,
                            )

    except Exception as e:
        logger.error(f"Error saving climate schedule: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save climate schedule: {str(e)}"
        ) from e

    # Broadcast update to all WebSocket clients
    try:
        from app.routes.websocket import broadcast_climate_schedule_update

        await broadcast_climate_schedule_update(
            location,
            cluster,
            {
                "day_start_time": schedule.day_start_time,
                "day_end_time": schedule.day_end_time,
                "pre_day_duration": schedule.pre_day_duration,
                "pre_night_duration": schedule.pre_night_duration,
                "setpoints": {
                    mode: setpoint.model_dump() for mode, setpoint in schedule.setpoints.items()
                },
            },
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast climate schedule update: {e}")

    # Write schedule state to Redis
    try:
        redis_client = get_automation_redis()
        if redis_client:
            schedule_state = await _build_schedule_state(database, location, cluster)
            redis_client.write_schedule_state(location, cluster, schedule_state)
            logger.info(f"Wrote schedule state to Redis for {location}/{cluster}")
    except Exception as e:
        logger.warning(f"Failed to write schedule state to Redis: {e}")

    return {"success": True, "location": location, "cluster": cluster, "warnings": warnings}
