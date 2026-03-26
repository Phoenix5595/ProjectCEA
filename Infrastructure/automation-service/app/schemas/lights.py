"""Light dimming control schemas."""

from __future__ import annotations

from pydantic import BaseModel


class IntensityControl(BaseModel):
    """Request model for intensity control."""

    intensity: float  # 0-100%
    duration: float | None = None  # Optional ramp duration (for future use)


class VoltageControl(BaseModel):
    """Request model for voltage control."""

    voltage: float  # 0-10V
    duration: float | None = None  # Optional ramp duration (for future use)


class TargetIntensityControl(BaseModel):
    """Request model for setting target light intensity."""

    target_intensity: float


class ScheduleTimeControl(BaseModel):
    """Request model for updating light schedule times."""

    start_time: str
    end_time: str
