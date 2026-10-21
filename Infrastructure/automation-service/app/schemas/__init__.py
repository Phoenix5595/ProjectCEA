"""Central schema exports for automation service."""

from __future__ import annotations

from app.schemas.alarms import AlarmAcknowledgeRequest
from app.schemas.climate_periods import (
    MigrationRequest,
    PeriodInput,
    PeriodsSaveRequest,
)
from app.schemas.device import (
    DeviceControlRequest,
    DeviceModeRequest,
)
from app.schemas.lights import (
    ScheduleTimeControl,
    TargetIntensityControl,
)
from app.schemas.pid import (
    AutotuneStatusResponse,
    PIDModeResponse,
    PIDModeUpdate,
    PIDParameterUpdate,
)
from app.schemas.room_modes import (
    ActiveModeResponse,
    FlowerSubmode,
    ModeParameters,
    RoomMode,
    RoomModeWithParams,
    SetModeRequest,
    UpdateParametersRequest,
)
from app.schemas.schedules import (
    ClimateScheduleCreate,
    ClimateScheduleSetpoint,
    RoomScheduleCreate,
    ScheduleCreate,
    ScheduleUpdate,
)

__all__ = [
    # Device schemas
    "DeviceControlRequest",
    "DeviceModeRequest",
    # Light schemas
    "TargetIntensityControl",
    "ScheduleTimeControl",
    # PID schemas
    "PIDParameterUpdate",
    "PIDModeUpdate",
    "PIDModeResponse",
    "AutotuneStatusResponse",
    # Room mode schemas
    "RoomMode",
    "FlowerSubmode",
    "ActiveModeResponse",
    "ModeParameters",
    "RoomModeWithParams",
    "SetModeRequest",
    "UpdateParametersRequest",
    # Climate period schemas
    "PeriodInput",
    "PeriodsSaveRequest",
    "MigrationRequest",
    # Alarm schemas
    "AlarmAcknowledgeRequest",
    # Schedule schemas
    "ScheduleCreate",
    "ScheduleUpdate",
    "RoomScheduleCreate",
    "ClimateScheduleSetpoint",
    "ClimateScheduleCreate",
]
