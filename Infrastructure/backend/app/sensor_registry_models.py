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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """Assign an RS-485 probe to a Flower bed. The room is derived from the
    chosen bed rack, never accepted from the body."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rs485"]
    bed: Literal["Front Bed", "Back Bed"]


AssignmentRequest = Annotated[
    CanAssignmentRequest | Rs485AssignmentRequest, Field(discriminator="kind")
]


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
    bed: str


AssignmentView = CanAssignmentView | Rs485AssignmentView


# ---------------------------------------------------------------------------
# Registry records
# ---------------------------------------------------------------------------


class SensorRegistryRecord(BaseModel):
    """One physical sensor unit (CAN node or RS-485 probe)."""

    registry_id: int
    bus: RegistryBus
    hardware_address: int
    display_name: str
    status: Literal["assigned", "unassigned"]
    first_seen: datetime
    last_seen: datetime
    assignment: AssignmentView | None = None


class SensorRegistryListResponse(BaseModel):
    records: list[SensorRegistryRecord]
    unassigned_count: int


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
