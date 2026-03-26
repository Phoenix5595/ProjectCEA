"""Schedules route package - aggregates base, room, and climate schedule endpoints."""

from fastapi import APIRouter

from app.schemas.schedules import (
    ClimateScheduleCreate,
    ClimateScheduleSetpoint,
    RoomScheduleCreate,
    ScheduleCreate,
    ScheduleUpdate,
)

from .base import (
    get_automation_redis,
    get_config,
    get_database,
    get_scheduler,
)
from .base import router as base_router
from .climate import router as climate_router
from .room import router as room_router
from .utils import (
    _build_schedule_state,
    _parse_time_str,
)

router = APIRouter(tags=["schedules"])
router.include_router(base_router)
router.include_router(room_router)
router.include_router(climate_router)

__all__ = [
    "router",
    "_build_schedule_state",
    "_parse_time_str",
    "get_database",
    "get_scheduler",
    "get_config",
    "get_automation_redis",
    "ScheduleCreate",
    "ScheduleUpdate",
    "RoomScheduleCreate",
    "ClimateScheduleSetpoint",
    "ClimateScheduleCreate",
]
