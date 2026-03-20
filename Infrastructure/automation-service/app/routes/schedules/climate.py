"""Climate schedule endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import DatabaseManager
from shared.infra_logging import get_logger

from .base import get_database
from .utils import _parse_time_str

logger = get_logger(__name__)

router = APIRouter()


class ClimateScheduleCreate(BaseModel):
    """Climate schedule with pre-day/pre-night durations.

    Note: Setpoint data (heating, cooling, VPD, CO2) is managed via the climate_periods API.
    This endpoint manages schedule timing metadata only.
    """

    day_start_time: str
    day_end_time: str
    pre_day_duration: int
    pre_night_duration: int


@router.get("/api/climate-schedule/{location}/{cluster}")
async def get_climate_schedule(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get climate schedule for a location/cluster.

    Args:
        location: Location name
        cluster: Cluster name

    Returns:
        Dict with day_start_time, day_end_time, pre_day_duration, pre_night_duration
    """
    light_schedule = await database.schedule_repo.get_room_light_schedule(location, cluster)
    if not light_schedule:
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "pre_day_duration": 0,
            "pre_night_duration": 0,
        }

    climate_schedule = await database.schedule_repo.get_climate_schedule(location, cluster)

    return {
        "day_start_time": light_schedule.get("day_start_time"),
        "day_end_time": light_schedule.get("day_end_time"),
        "pre_day_duration": climate_schedule.get("pre_day_duration", 0) if climate_schedule else 0,
        "pre_night_duration": climate_schedule.get("pre_night_duration", 0)
        if climate_schedule
        else 0,
    }


@router.post("/api/climate-schedule/{location}/{cluster}")
async def save_climate_schedule(
    location: str,
    cluster: str,
    schedule: ClimateScheduleCreate,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Save climate schedule atomically.

    This endpoint saves schedule timing metadata (pre_day_duration, pre_night_duration).
    Setpoint data is managed via the climate_periods API.

    Args:
        location: Location name
        cluster: Cluster name
        schedule: Climate schedule data

    Returns:
        Success response
    """
    from app.control.scheduler import Scheduler

    scheduler = Scheduler([])
    is_valid, error_msg = scheduler.validate_climate_schedule_conflicts(
        schedule.day_start_time,
        schedule.day_end_time,
        schedule.pre_day_duration,
        schedule.pre_night_duration,
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    if schedule.pre_day_duration < 0 or schedule.pre_day_duration > 240:
        raise HTTPException(
            status_code=400, detail="pre_day_duration must be between 0 and 240 minutes"
        )

    if schedule.pre_night_duration < 0 or schedule.pre_night_duration > 240:
        raise HTTPException(
            status_code=400, detail="pre_night_duration must be between 0 and 240 minutes"
        )

    try:
        pool = await database._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
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

    except Exception as e:
        logger.error(f"Error saving climate schedule: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save climate schedule: {str(e)}"
        ) from e

    return {"success": True, "location": location, "cluster": cluster}
