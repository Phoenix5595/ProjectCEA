"""Pydantic schemas for calendar API."""

from __future__ import annotations

from datetime import date
import os
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, validator


class CalendarEventCreate(BaseModel):
    location: str
    cluster: str = "main"
    event_type: str = "planned_task"
    title: str
    start_date: date
    end_date: date | None = None
    all_day: bool = True
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None
    event_type: str | None = None
    location: str | None = None
    cluster: str | None = None
    metadata: dict[str, Any] | None = None


class FlowerGrowPlanRequest(BaseModel):
    idempotency_key: UUID
    crop_name: str = "Flower cycle"
    environment: str = "indoor"
    flower_end: date
    flower_weeks: int = Field(ge=1, le=52)
    include_pot_phases: bool = True
    clone_weeks: int = Field(default=3, ge=1, le=12)
    pot_weeks: int = Field(default=2, ge=1, le=12)
    bed_weeks: int = Field(default=2, ge=1, le=12)
    stretch_days: int = Field(default=21, ge=1, le=60)
    ripen_days: int = Field(default=21, ge=1, le=60)
    drying_days: int = Field(default=7, ge=1, le=14)
    auto_mode_transition: bool = True


class SyncConnectionCreate(BaseModel):
    display_name: str | None = None
    account_email: str | None = None
    caldav_base_url: str
    username: str
    app_password: str
    target_calendar_url: str


class SyncConnectionTest(BaseModel):
    caldav_base_url: str
    username: str
    app_password: str

    @validator("caldav_base_url")
    @classmethod
    def require_https(cls, v: str) -> str:
        if v.startswith("http://"):
            if os.getenv("CALDAV_ALLOW_HTTP_TEST", "").lower() != "true":
                raise ValueError(
                    "CALDAV connection test requires HTTPS. Set CALDAV_ALLOW_HTTP_TEST=true for dev only."
                )
        return v
