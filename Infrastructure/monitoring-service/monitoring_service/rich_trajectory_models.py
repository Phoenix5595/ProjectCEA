"""Read-only Pydantic contracts for saved climate trajectories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
)


class RichTrajectoryModel(BaseModel):
    """Immutable monitoring-service boundary model for rich trajectory data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class PeriodIdentity(RichTrajectoryModel):
    period_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SegmentSource(RichTrajectoryModel):
    mode: str = Field(min_length=1)
    submode: str | None = None
    period: PeriodIdentity
    config_revision: str = Field(min_length=1)
    draft_revision: str | None = None


class TrajectorySegmentBase(RichTrajectoryModel):
    start: AwareDatetime
    end: AwareDatetime
    metric: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    trajectory_kind: Literal["scheduled", "effective"]
    quality: Literal["exact", "estimated", "unavailable"]
    source: SegmentSource

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class StepTrajectorySegment(TrajectorySegmentBase):
    shape: Literal["step"]
    value: FiniteFloat


class LinearTrajectorySegment(TrajectorySegmentBase):
    shape: Literal["linear"]
    start_value: FiniteFloat
    end_value: FiniteFloat


class UnavailableTrajectorySegment(TrajectorySegmentBase):
    shape: Literal["unavailable"]
    reason: str = Field(min_length=1)


TrajectorySegment = Annotated[
    StepTrajectorySegment | LinearTrajectorySegment | UnavailableTrajectorySegment,
    Field(discriminator="shape"),
]


class TimelineWarning(RichTrajectoryModel):
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class UtcWindow(RichTrajectoryModel):
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = Field(min_length=1)

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class RichTrajectoryEnvelope(RichTrajectoryModel):
    contract_version: Literal[1]
    room: str = Field(min_length=1)
    generated_at: AwareDatetime
    window: UtcWindow
    revision_scope: Literal["saved", "draft"]
    base_config_revision: str = Field(min_length=1)
    draft_revision: str | None = None
    segments: Annotated[tuple[TrajectorySegment, ...], BeforeValidator(tuple)] = Field(min_length=1)
    assumptions: Annotated[tuple[str, ...], BeforeValidator(tuple)] = ()
    warnings: Annotated[tuple[TimelineWarning, ...], BeforeValidator(tuple)] = ()

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
