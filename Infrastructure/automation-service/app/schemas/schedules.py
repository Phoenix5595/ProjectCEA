"""Schedules schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    """Request model for creating a schedule."""

    name: str
    location: str
    cluster: str
    device_name: str
    day_of_week: int | None = None  # 0-6 or None for daily
    start_time: str  # "HH:MM" format
    end_time: str  # "HH:MM" format
    enabled: bool = True
    mode: str | None = None  # DAY, NIGHT, TRANSITION
    target_intensity: float | None = None  # 0-100% for light ramp schedules
    ramp_up_duration: int | None = None  # Minutes to ramp up (0 = instant)
    ramp_down_duration: int | None = None  # Minutes to ramp down (0 = instant)


class ScheduleUpdate(BaseModel):
    """Request model for updating a schedule."""

    name: str | None = None
    day_of_week: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    enabled: bool | None = None
    mode: str | None = None  # DAY, NIGHT, TRANSITION
    target_intensity: float | None = None  # 0-100% for light ramp schedules
    ramp_up_duration: int | None = None  # Minutes to ramp up (0 = instant)
    ramp_down_duration: int | None = None  # Minutes to ramp down (0 = instant)
    expected_version: str | None = None  # ISO format timestamp for optimistic locking


class RoomScheduleCreate(BaseModel):
    """Request model for creating a room schedule."""

    day_start_time: str
    day_end_time: str
    night_start_time: str
    night_end_time: str
    ramp_up_duration: int | None = None
    ramp_down_duration: int | None = None


class ClimateScheduleSetpoint(BaseModel):
    """Setpoint values for climate schedule."""

    temperature: float | None = None
    humidity: float | None = None
    vpd: float | None = None
    co2: float | None = None


class ClimateScheduleCreate(BaseModel):
    """Climate schedule (legacy - fields ignored).

    Note: Climate control now uses climate_periods table for all timing and setpoints.
    This schema is kept for API compatibility but fields are deprecated.
    """

    day_start_time: str
    day_end_time: str
    pre_day_duration: int = 0  # Deprecated - ignored
    pre_night_duration: int = 0  # Deprecated - ignored
