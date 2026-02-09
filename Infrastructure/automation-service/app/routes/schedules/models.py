"""Pydantic models for schedule endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class ScheduleCreate(BaseModel):
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
    location: str
    cluster: str
    schedules: list[dict]


class ClimateScheduleSetpoint(BaseModel):
    temperature: float | None = None
    humidity: float | None = None
    vpd: float | None = None
    co2: float | None = None


class ClimateScheduleCreate(BaseModel):
    location: str
    cluster: str
    mode: str  # DAY, NIGHT, PRE_DAY, PRE_NIGHT
    start_time: str  # "HH:MM" format
    duration_minutes: int
    setpoints: ClimateScheduleSetpoint
