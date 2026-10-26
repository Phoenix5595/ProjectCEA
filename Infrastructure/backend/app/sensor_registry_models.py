"""Strict Pydantic contracts for the physical sensor registry and soil API.

These models are the single source of truth for the
``/api/sensors/registry*`` and ``/api/sensors/soil/*`` surfaces. They are
deliberately separate from ``app.models`` (the loose legacy sensor models)
so every field is validated and the assignment shape is a discriminated
union — the frontend mirrors them with Zod under
``src/features/soil/api/``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RegistryBus = Literal["can", "rs485"]
CAN_LOCATIONS = ("front", "back", "main")
RS485_BEDS = ("Front Bed", "Back Bed")
SOIL_METRICS = ("temperature", "water_content", "ec", "ph")


# ---------------------------------------------------------------------------
# Assignment request bodies (discriminated by `kind`)
# ---------------------------------------------------------------------------


class CanAssignmentRequest(BaseModel):
    """Assign a CAN node to a controlled room position."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["can"]
    room: str
    location_in_room: Literal["front", "back", "main"]

    @field_validator("room")
    @classmethod
    def _room_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("room must not be blank")
        return value


class Rs485AssignmentRequest(BaseModel):
    """Place an RS-485 probe (R9): free position in a Flower bed, or
    room-level in Veg Room."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rs485"]
    room: Literal["Flower Room", "Veg Room"]
    bed: Literal["Front Bed", "Back Bed"] | None = None
    x_frac: float | None = Field(default=None, ge=0, le=1)
    y_frac: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _placement_shape(self) -> Rs485AssignmentRequest:
        spot = (self.bed, self.x_frac, self.y_frac)
        if self.room == "Flower Room" and any(value is None for value in spot):
            raise ValueError("Flower Room placement requires bed, x_frac and y_frac")
        if self.room == "Veg Room" and any(value is not None for value in spot):
            raise ValueError(
                "Veg Room placement is room-level; bed, x_frac and y_frac are not allowed"
            )
        return self


AssignmentRequest = Annotated[
    CanAssignmentRequest | Rs485AssignmentRequest, Field(discriminator="kind")
]


class SameIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["same"]
    target_registry_id: int = Field(gt=0)


class NewIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["new"]


IdentityRequest = SameIdentityRequest | NewIdentityRequest

# ---------------------------------------------------------------------------
# Assignment snapshots returned inside registry records
# ---------------------------------------------------------------------------


class CanAssignmentView(BaseModel):
    kind: Literal["can"]
    room: str
    location_in_room: Literal["front", "back", "main"]


class Rs485AssignmentView(BaseModel):
    kind: Literal["rs485"]
    room: str
    bed: str | None
    x_frac: float | None
    y_frac: float | None


AssignmentView = CanAssignmentView | Rs485AssignmentView


# ---------------------------------------------------------------------------
# Registry records
# ---------------------------------------------------------------------------


class SensorRegistryRecord(BaseModel):
    """One physical sensor unit (CAN node or RS-485 probe)."""

    registry_id: int
    bus: RegistryBus
    hardware_address: int | None
    sensor_uid: UUID
    identity_status: Literal["confirmed", "pending", "detached"]
    pending_candidate_registry_id: int | None
    pending_since: datetime | None
    display_name: str
    status: Literal["assigned", "unassigned"]
    first_seen: datetime
    last_seen: datetime
    assignment: AssignmentView | None = None


class SensorRegistryListResponse(BaseModel):
    records: list[SensorRegistryRecord]
    unassigned_count: int


# ---------------------------------------------------------------------------
# RS-485 bus status, beds and heatmap
# ---------------------------------------------------------------------------

SOIL_HEATMAP_STALE_HOURS = 72
SOIL_HEATMAP_WINDOW_MINUTES = 10


class SoilBusStatus(BaseModel):
    state: Literal["ok", "power_cycle_required", "no_free_address", "collision", "unknown"]
    address: int | None
    at: datetime | None


class SoilBed(BaseModel):
    rack_id: int
    name: str
    width_ft: float
    length_ft: float


class SoilBedsResponse(BaseModel):
    beds: list[SoilBed]


class SoilHeatmapPoint(BaseModel):
    registry_id: int
    hardware_address: int
    x_frac: float
    y_frac: float
    value: float
    active: bool
    removed_at: datetime | None


class SoilHeatmapBed(SoilBed):
    points: list[SoilHeatmapPoint]


class SoilHeatmapResponse(BaseModel):
    metric: Literal["temperature", "water_content", "ec", "ph"]
    stale_after_hours: int
    beds: list[SoilHeatmapBed]


# ---------------------------------------------------------------------------
# Soil live
# ---------------------------------------------------------------------------


class SoilMetricValue(BaseModel):
    value: float
    unit: str
    observed_at: datetime
    age_seconds: float


class SoilProbeLive(BaseModel):
    registry_id: int
    hardware_address: int
    display_name: str
    bed: str
    last_seen: datetime
    metrics: dict[Literal["temperature", "water_content", "ec", "ph"], SoilMetricValue | None]


class SoilLiveResponse(BaseModel):
    generated_at: datetime
    probes: list[SoilProbeLive]


# ---------------------------------------------------------------------------
# Soil history
# ---------------------------------------------------------------------------


class SoilHistoryPoint(BaseModel):
    """One truthful envelope bucket, anchored to its half-open interval."""

    bucket_start: datetime
    average: float | None
    minimum: float | None
    maximum: float | None
    sample_count: int


class SoilMetricHistory(BaseModel):
    registry_id: int
    hardware_address: int
    display_name: str
    bed: str
    metric: Literal["temperature", "water_content", "ec", "ph"]
    unit: str
    points: list[SoilHistoryPoint]


class SoilHistoryResponse(BaseModel):
    start: datetime
    end: datetime
    max_points: int
    tier: str
    bucket_seconds: int
    series: list[SoilMetricHistory]


# ---------------------------------------------------------------------------
# Validation bounds shared by routes and tests
# ---------------------------------------------------------------------------

SOIL_HISTORY_MIN_RANGE_SECONDS = 5 * 60
SOIL_HISTORY_MAX_RANGE_SECONDS = 7 * 24 * 3600
SOIL_HISTORY_MIN_POINTS = 100
SOIL_HISTORY_MAX_POINTS = 5000
