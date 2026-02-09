"""Schedules route package - aggregates base, room, and climate schedule endpoints."""

from fastapi import APIRouter

from .base import (
    get_automation_redis,
    get_config,
    get_database,
    get_scheduler,
)
from .base import router as base_router
from .climate import ClimateScheduleCreate, ClimateScheduleSetpoint
from .climate import router as climate_router
from .models import RoomScheduleCreate, ScheduleCreate, ScheduleUpdate
from .room import router as room_router
from .utils import (
    SETPOINT_MODES,
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
    "SETPOINT_MODES",
    "ScheduleCreate",
    "ScheduleUpdate",
    "RoomScheduleCreate",
    "ClimateScheduleSetpoint",
    "ClimateScheduleCreate",
]
