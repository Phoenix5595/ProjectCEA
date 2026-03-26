"""Climate schedule endpoints (legacy - maintained for API compatibility).

Note: The climate control system now uses climate_periods table for all setpoint
and ramp management. These endpoints are kept for frontend compatibility but
no longer manage pre_day/pre_night durations.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.database import DatabaseManager
from app.schemas.schedules import ClimateScheduleCreate
from shared.infra_logging import get_logger

from .base import get_database

logger = get_logger(__name__)

router = APIRouter()


@router.get("/api/climate-schedule/{location}/{cluster}")
async def get_climate_schedule(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get climate schedule for a location/cluster (legacy endpoint).

    Args:
        location: Location name
        cluster: Cluster name

    Returns:
        Dict with day_start_time, day_end_time. pre_day/pre_night fields are
        deprecated and always return 0 - climate control now uses climate_periods.
    """
    light_schedule = await database.schedule_repo.get_room_light_schedule(location, cluster)
    if not light_schedule:
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "pre_day_duration": 0,
            "pre_night_duration": 0,
        }

    return {
        "day_start_time": light_schedule.get("day_start_time"),
        "day_end_time": light_schedule.get("day_end_time"),
        "pre_day_duration": 0,  # Deprecated - climate_periods now handles all timing
        "pre_night_duration": 0,  # Deprecated - climate_periods now handles all timing
    }


@router.post("/api/climate-schedule/{location}/{cluster}")
async def save_climate_schedule(
    location: str,
    cluster: str,
    schedule: ClimateScheduleCreate,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Save climate schedule (legacy endpoint - no-op).

    This endpoint is kept for API compatibility. The climate control system now
    uses climate_periods table for all timing and setpoint management.
    pre_day_duration and pre_night_duration are deprecated and ignored.

    Args:
        location: Location name
        cluster: Cluster name
        schedule: Climate schedule data (deprecated fields ignored)

    Returns:
        Success response
    """
    # Legacy validation removed - climate_periods now handles all timing
    logger.debug(f"Legacy climate schedule save called for {location}/{cluster} - ignoring")
    return {"success": True, "location": location, "cluster": cluster}
