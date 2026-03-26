"""PID parameter and control schemas."""

from __future__ import annotations

from pydantic import BaseModel


class PIDParameterUpdate(BaseModel):
    """Request model for PID parameter update."""

    kp: float | None = None
    ki: float | None = None
    kd: float | None = None
    source: str = "api"  # 'api', 'config'
    updated_by: str | None = None


class PIDModeUpdate(BaseModel):
    """Request model for PID control mode update."""

    mode: str  # 'auto_pid', 'pid', 'on_off'
    hysteresis_high: float | None = None
    hysteresis_low: float | None = None
    updated_by: str | None = None


class PIDModeResponse(BaseModel):
    """Response model for PID control mode."""

    device_type: str
    mode: str
    hysteresis_high: float
    hysteresis_low: float
    autotune_active: bool
    updated_at: str | None = None


class AutotuneStatusResponse(BaseModel):
    """Response model for autotune status."""

    device_type: str
    is_active: bool
    status: str  # 'idle', 'running', 'calculating', 'complete', 'error'
    cycles_completed: int
    estimated_remaining_cycles: int
    current_ku: float | None = None
    current_tu: float | None = None
    suggested_kp: float | None = None
    suggested_ki: float | None = None
    suggested_kd: float | None = None
    last_change_reason: str | None = None
