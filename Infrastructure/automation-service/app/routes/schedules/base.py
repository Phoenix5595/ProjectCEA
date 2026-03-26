"""Base schedule CRUD endpoints and helpers."""

from __future__ import annotations

# Standard library imports
from typing import TYPE_CHECKING, Any

from asyncpg import Connection

# Third-party imports
from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from app.schemas.schedules import ScheduleCreate, ScheduleUpdate

# Local imports
from shared.infra_logging import get_logger

from .utils import (
    _build_schedule_state,
    _ensure_light_schedules_are_daily,
)

if TYPE_CHECKING:
    from app.control.scheduler import Scheduler

logger = get_logger(__name__)

router = APIRouter()


# Dependency stubs - will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_scheduler() -> Scheduler:
    """Dependency to get scheduler."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


def get_automation_redis() -> AutomationRedisClient | None:
    """Get automation Redis client."""
    from app.container import ServiceContainer

    return ServiceContainer().automation_redis


async def delete_schedules_bulk(
    database: DatabaseManager,
    location: str,
    cluster: str,
    device_filter: str | None = None,
    conn: Connection | None = None,
) -> int:
    """Delete schedules in bulk, optionally filtered by device name prefix."""
    schedules = await database.schedule_repo.get_schedules(location, cluster)
    if device_filter:
        schedule_ids = [
            s["id"] for s in schedules if s.get("device_name", "").startswith(device_filter)
        ]
    else:
        schedule_ids = [s["id"] for s in schedules]

    if not schedule_ids:
        return 0

    return await database.schedule_repo.delete_schedules_bulk(schedule_ids, conn)


@router.get("/api/schedules")
async def get_schedules(
    location: str | None = Query(None),
    cluster: str | None = Query(None),
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, Any]]:
    """List all schedules."""
    schedules = await database.schedule_repo.get_schedules(location, cluster)
    return schedules


@router.post("/api/schedules")
async def create_schedule(
    schedule: ScheduleCreate, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Create a new schedule."""
    if schedule.mode:
        valid_modes = ["DAY", "NIGHT", "TRANSITION", "SUN", "MOON"]
        if schedule.mode.upper() not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {schedule.mode}. Valid modes: {', '.join(valid_modes)}",
            )
        schedule.mode = schedule.mode.upper()

    if schedule.target_intensity is not None:
        if schedule.target_intensity < 0 or schedule.target_intensity > 100:
            raise HTTPException(
                status_code=400, detail="target_intensity must be between 0 and 100"
            )

    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_up_duration must be >= 0")
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_down_duration must be >= 0")

    _ensure_light_schedules_are_daily(
        schedule.mode, schedule.target_intensity, schedule.day_of_week
    )

    schedule_id = await database.schedule_repo.create_schedule(
        schedule.name,
        schedule.location,
        schedule.cluster,
        schedule.device_name,
        schedule.start_time,
        schedule.end_time,
        [schedule.day_of_week] if schedule.day_of_week is not None else None,
        schedule.enabled,
        schedule.mode or "light",
        schedule.target_intensity,
        schedule.ramp_up_duration,
        schedule.ramp_down_duration,
    )

    if not schedule_id:
        raise HTTPException(status_code=500, detail="Failed to create schedule")

    schedules = await database.schedule_repo.get_schedules(schedule.location, schedule.cluster)
    created = next((s for s in schedules if s["id"] == schedule_id), None)

    if not created:
        raise HTTPException(status_code=500, detail="Schedule created but not found")

    try:
        redis_client = get_automation_redis()
        if redis_client:
            schedule_state = await _build_schedule_state(
                database, schedule.location, schedule.cluster
            )
            redis_client.write_schedule_state(schedule.location, schedule.cluster, schedule_state)
            logger.info(f"Wrote schedule state to Redis for {schedule.location}/{schedule.cluster}")
    except Exception as e:
        logger.warning(f"Failed to write schedule state to Redis: {e}")

    return created


@router.put("/api/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    schedule: ScheduleUpdate,
    database: DatabaseManager = Depends(get_database),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    """Update a schedule."""
    if schedule.mode:
        valid_modes = ["DAY", "NIGHT", "TRANSITION", "SUN", "MOON"]
        if schedule.mode.upper() not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {schedule.mode}. Valid modes: {', '.join(valid_modes)}",
            )
        schedule.mode = schedule.mode.upper()

    if schedule.target_intensity is not None:
        if schedule.target_intensity < 0 or schedule.target_intensity > 100:
            raise HTTPException(
                status_code=400, detail="target_intensity must be between 0 and 100"
            )

    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_up_duration must be >= 0")
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_down_duration must be >= 0")

    all_schedules = await database.schedule_repo.get_schedules()
    existing = next((s for s in all_schedules if s["id"] == schedule_id), None)

    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")

    expected_version_dt = None
    if schedule.expected_version:
        try:
            from datetime import datetime

            expected_version_dt = datetime.fromisoformat(
                schedule.expected_version.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=400,
                detail="expected_version must be in ISO format (e.g., '2024-01-15T10:30:00Z')",
            ) from None

    final_mode = schedule.mode or existing.get("mode")
    final_target = (
        schedule.target_intensity
        if schedule.target_intensity is not None
        else existing.get("target_intensity")
    )
    final_day_of_week = (
        schedule.day_of_week if schedule.day_of_week is not None else existing.get("day_of_week")
    )
    _ensure_light_schedules_are_daily(final_mode, final_target, final_day_of_week)

    updated_record = await database.schedule_repo.update_schedule(
        schedule_id,
        name=schedule.name,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        day_of_week=[schedule.day_of_week] if schedule.day_of_week is not None else None,
        enabled=schedule.enabled,
        mode=schedule.mode,
        target_intensity=schedule.target_intensity,
        ramp_up_duration=schedule.ramp_up_duration,
        ramp_down_duration=schedule.ramp_down_duration,
        expected_version=expected_version_dt,
    )

    if not updated_record:
        raise HTTPException(status_code=500, detail="Failed to update schedule")

    schedules = await database.schedule_repo.get_schedules(
        existing["location"], existing["cluster"]
    )
    updated = next((s for s in schedules if s["id"] == schedule_id), None)

    if not updated:
        raise HTTPException(status_code=500, detail="Schedule updated but not found")

    if scheduler:
        all_schedules = await database.schedule_repo.get_schedules()
        scheduler.update_schedules(all_schedules)
        logger.info(f"Scheduler refreshed after schedule {schedule_id} update")

    try:
        from app.routes.websocket import broadcast_schedule_update

        await broadcast_schedule_update(schedule_id, updated)
    except Exception as e:
        logger.warning(f"Failed to broadcast schedule update: {e}")

    try:
        redis_client = get_automation_redis()
        if redis_client:
            schedule_state = await _build_schedule_state(
                database, existing["location"], existing["cluster"]
            )
            redis_client.write_schedule_state(
                existing["location"], existing["cluster"], schedule_state
            )
            logger.info(
                f"Wrote schedule state to Redis for {existing['location']}/{existing['cluster']}"
            )
    except Exception as e:
        logger.warning(f"Failed to write schedule state to Redis: {e}")

    return updated


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: int, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Delete a schedule."""
    success = await database.schedule_repo.delete_schedule(schedule_id)

    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")

    return {"id": schedule_id, "success": True}
