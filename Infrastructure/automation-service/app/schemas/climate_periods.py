"""Climate period schemas."""

from __future__ import annotations

from pydantic import BaseModel


class PeriodInput(BaseModel):
    """Input model for a climate period."""

    period_name: str
    start_time: str
    end_time: str
    ramp_minutes: int = 0
    heating_setpoint: float | None = None
    cooling_setpoint: float | None = None
    vpd_setpoint: float | None = None
    co2_setpoint: int | None = None
    details: str | None = None


class PeriodsSaveRequest(BaseModel):
    """Request model for saving climate periods."""

    periods: list[PeriodInput]
    mode_id: int
    submode_id: int | None = None


class MigrationRequest(BaseModel):
    """Request model for climate period migration."""

    mode_name: str = "Flower"
    submode_name: str | None = None
    dry_run: bool = True
