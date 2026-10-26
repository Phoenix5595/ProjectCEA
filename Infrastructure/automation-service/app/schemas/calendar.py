"""Pydantic schemas for calendar API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, validator

from shared.nextcloud_dav import DavError, validate_dav_url


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
    grow_plan_id: UUID | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None
    event_type: str | None = None
    location: str | None = None
    cluster: str | None = None
    metadata: dict[str, Any] | None = None
    grow_plan_id: UUID | None = None


class GrowRevisionRequest(BaseModel):
    expected_revision: StrictInt = Field(ge=1)


class GrowAssociationRequest(BaseModel):
    grow_plan_id: UUID
    expected_revision: StrictInt = Field(ge=1)


class GrowLifecycleResponse(BaseModel):
    grow_plan_id: UUID
    crop_batch_id: int
    state: str
    calendar_revision: int
    original_drying_end: date | None
    first_closed_at: datetime | None
    closed_at: datetime | None
    anniversary_at: datetime | None
    dataset_id: UUID | None
    data_revision: int | None
    published_revision: int | None


class GrowClosePreviewResponse(BaseModel):
    lifecycle: GrowLifecycleResponse
    revision: int
    phase_windows: list[dict[str, Any]]
    drying_end: date | None
    affected_counts: dict[str, Any]
    gaps: list[dict[str, Any]]
    scope_unresolved: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    newly_included: dict[str, Any]
    released: dict[str, Any]
    unavailable: list[dict[str, Any]]


class FlowerGrowPlanRequest(BaseModel):
    idempotency_key: UUID
    crop_name: str = "Flower cycle"
    environment: str = "indoor"
    anchor: Literal["bed_veg_start", "flower_start", "ripen_end"]
    anchor_date: date
    bed_veg_start: date
    bed_veg_days: int = Field(default=14, ge=1, le=84)
    stretch_days: int = Field(default=21, ge=1, le=60)
    bulk_days: int = Field(default=21, ge=1, le=60)
    ripen_days: int = Field(default=21, ge=1, le=60)
    drying_days: int = Field(default=7, ge=1, le=14)
    pot_start: date | None = None
    clone_start: date | None = None
    auto_mode_transition: bool = True


class PendingModeChangeResponse(BaseModel):
    """The manual mode change the owner still has to trigger for Flower Room."""

    from_mode: str | None
    to_mode: str
    to_submode: str | None
    event_id: int | None
    since: date


class ExpectedHarvestResponse(BaseModel):
    """The last ripen day of the plan occupying the room."""

    harvest_date: date
    grow_plan_id: UUID
    event_id: int


class GrowPlanExtendRequest(BaseModel):
    event_id: StrictInt
    days: StrictInt = Field(default=7, ge=1, le=60)


class CalendarCurrentRunResponse(BaseModel):
    """Full room span of the unique canonical plan containing Toronto today."""

    location: str
    grow_plan_id: UUID
    start_date: date
    end_date: date


class FlowerCalendarModeTransitionUpdate(BaseModel):
    enabled: bool


class SyncConnectionCreate(BaseModel):
    display_name: str | None = None
    account_email: str | None = None
    caldav_base_url: str
    username: str
    app_password: str
    target_calendar_url: str

    @validator("caldav_base_url", "target_calendar_url")
    @classmethod
    def secure_urls(cls, value: str) -> str:
        try:
            return validate_dav_url(value)
        except DavError as error:
            raise ValueError(error.code) from error


class SyncReconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_generation: StrictInt = Field(ge=1)
    backup_confirmed: StrictBool


class SyncConnectionTest(BaseModel):
    caldav_base_url: str
    username: str
    app_password: str

    @validator("caldav_base_url")
    @classmethod
    def require_https(cls, v: str) -> str:
        try:
            return validate_dav_url(v)
        except DavError as error:
            raise ValueError(error.code) from error
