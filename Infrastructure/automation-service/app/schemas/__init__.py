"""Central schema exports for automation service."""

from __future__ import annotations

from app.schemas.alarms import AlarmAcknowledgeRequest
from app.schemas.climate_periods import (
    MigrationRequest,
    PeriodInput,
    PeriodsSaveRequest,
)
from app.schemas.device import (
    DeviceConfigUpdate,
    DeviceControlRequest,
    DeviceMappingUpdate,
    DeviceModeRequest,
)
from app.schemas.flags import FlagResponse, FlagUpdateRequest
from app.schemas.lights import (
    IntensityControl,
    ScheduleTimeControl,
    TargetIntensityControl,
    VoltageControl,
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
from app.schemas.rules import RuleCreate, RuleToggle, RuleUpdate
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
    "DeviceMappingUpdate",
    "DeviceConfigUpdate",
    # Light schemas
    "IntensityControl",
    "VoltageControl",
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
    # Flag schemas
    "FlagUpdateRequest",
    "FlagResponse",
    # Rule schemas
    "RuleCreate",
    "RuleUpdate",
    "RuleToggle",
    # Alarm schemas
    "AlarmAcknowledgeRequest",
    # Schedule schemas
    "ScheduleCreate",
    "ScheduleUpdate",
    "RoomScheduleCreate",
    "ClimateScheduleSetpoint",
    "ClimateScheduleCreate",
]
