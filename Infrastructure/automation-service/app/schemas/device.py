"""Device control and configuration schemas."""

from __future__ import annotations

from pydantic import BaseModel


class DeviceControlRequest(BaseModel):
    """Request model for manual device control."""

    state: int  # 0 = OFF, 1 = ON
    reason: str | None = "Manual override"
    duration_seconds: int | None = None


class DeviceModeRequest(BaseModel):
    """Request model for setting device control mode."""

    mode: str  # 'manual', 'auto', 'scheduled'
