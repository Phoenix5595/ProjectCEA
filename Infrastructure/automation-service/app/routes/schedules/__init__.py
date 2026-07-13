"""Schedules route package - aggregates base and room schedule endpoints."""

from fastapi import APIRouter

from app.schemas.schedules import (
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
from .room import router as room_router
from .utils import (
    _build_schedule_state,
    _parse_time_str,
)

router = APIRouter(tags=["schedules"])
router.include_router(base_router)
router.include_router(room_router)

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
]
