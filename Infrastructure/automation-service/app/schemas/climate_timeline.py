"""Strict versioned contracts for non-actuating climate trajectories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)


class ClimateTimelineModel(BaseModel):
    """Immutable trust-boundary base for rich climate timeline payloads."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class UtcWindow(ClimateTimelineModel):
    """One named timezone view over a UTC half-open interval ``[start, end)``."""

    start: AwareDatetime
    end: AwareDatetime
    timezone: str = Field(min_length=1)

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_half_open_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("window end must be later than window start")
        return self


class TimelinePreviewWindow(ClimateTimelineModel):
    """JSON request window retained as strings until the HTTP boundary parses it."""

    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    timezone: str = Field(min_length=1)


class PeriodIdentity(ClimateTimelineModel):
    """Stable schedule-period identity displayed with a trajectory segment."""

    period_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SegmentSource(ClimateTimelineModel):
    """Saved or draft schedule identity that produced one segment."""

    mode: str = Field(min_length=1)
    submode: str | None = None
    period: PeriodIdentity
    config_revision: str = Field(min_length=1)
    draft_revision: str | None = None


class TrajectorySegmentBase(ClimateTimelineModel):
    """Fields shared by all scheduled/effective trajectory segment shapes."""

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

    @model_validator(mode="after")
    def validate_half_open_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("segment end must be later than segment start")
        return self


class StepTrajectorySegment(TrajectorySegmentBase):
    """A finite value that holds throughout its UTC half-open interval."""

    shape: Literal["step"]
    value: FiniteFloat


class LinearTrajectorySegment(TrajectorySegmentBase):
    """A finite ramp evaluated between explicit UTC endpoints."""

    shape: Literal["linear"]
    start_value: FiniteFloat
    end_value: FiniteFloat


class UnavailableTrajectorySegment(TrajectorySegmentBase):
    """An explicit gap that must never be joined into an adjacent trajectory."""

    shape: Literal["unavailable"]
    reason: str = Field(min_length=1)


TrajectorySegment = Annotated[
    StepTrajectorySegment | LinearTrajectorySegment | UnavailableTrajectorySegment,
    Field(discriminator="shape"),
]


class TimelineWarning(ClimateTimelineModel):
    """One non-fatal reason a trajectory may be estimated or incomplete."""

    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class RichTrajectoryEnvelope(ClimateTimelineModel):
    """Versioned saved or draft trajectory envelope independent of legacy scalars."""

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
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_revision_aggregate(self) -> Self:
        match self.revision_scope:
            case "saved":
                if self.draft_revision is not None:
                    raise ValueError("saved trajectory cannot declare a draft revision")
            case "draft":
                if self.draft_revision is None:
                    raise ValueError("draft trajectory requires a draft revision")
        for segment in self.segments:
            if segment.source.config_revision != self.base_config_revision:
                raise ValueError("segment config revision must match envelope base revision")
            if segment.source.draft_revision != self.draft_revision:
                raise ValueError("segment draft revision must match envelope draft revision")
        return self


class TimelinePeriodDraft(ClimateTimelineModel):
    """One complete, non-persisted climate-period edit."""

    id: str = Field(min_length=1)
    period_name: str = Field(min_length=1)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    ramp_minutes: int = Field(ge=0)
    heating_setpoint: FiniteFloat | None = None
    cooling_setpoint: FiniteFloat | None = None
    vpd_setpoint: FiniteFloat | None = None
    co2_setpoint: int | None = None
    details: str = ""

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_clock_value(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value


class TimelinePhotoperiodDraft(ClimateTimelineModel):
    """Timeline-owned photoperiod values supplied with every draft preview."""

    day_start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    night_start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    ramp_up_minutes: int = Field(ge=0)
    ramp_down_minutes: int = Field(ge=0)

    @field_validator("day_start_time", "night_start_time")
    @classmethod
    def validate_clock_value(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value


class TimelinePreviewRequest(ClimateTimelineModel):
    """Strict complete draft input for a non-persisting trajectory preview."""

    request_id: str = Field(min_length=1)
    expected_config_revision: str = Field(min_length=1)
    draft_revision: int = Field(ge=0)
    mode_id: int
    submode_id: int | None = None
    window: TimelinePreviewWindow
    periods: Annotated[tuple[TimelinePeriodDraft, ...], BeforeValidator(tuple)] = Field(
        min_length=1
    )
    photoperiod: TimelinePhotoperiodDraft


class TimelinePreviewResponse(ClimateTimelineModel):
    """Preview result paired with the request identity that produced it."""

    request_id: str = Field(min_length=1)
    expected_config_revision: str = Field(min_length=1)
    draft_revision: int = Field(ge=0)
    trajectory: RichTrajectoryEnvelope


class TimelineSavedResponse(ClimateTimelineModel):
    """Saved timeline values and the saved rich trajectory for one window."""

    config_revision: str = Field(min_length=1)
    mode_id: int
    submode_id: int | None = None
    periods: Annotated[tuple[TimelinePeriodDraft, ...], BeforeValidator(tuple)] = Field(
        min_length=1
    )
    photoperiod: TimelinePhotoperiodDraft
    trajectory: RichTrajectoryEnvelope | None = None


class TimelineApplyRequest(ClimateTimelineModel):
    """A reviewed complete timeline aggregate authorized for one atomic replacement."""

    request_id: str = Field(min_length=1)
    expected_config_revision: str = Field(min_length=1)
    draft_revision: int = Field(ge=0)
    mode_id: int
    submode_id: int | None = None
    periods: Annotated[tuple[TimelinePeriodDraft, ...], BeforeValidator(tuple)] = Field(
        min_length=1
    )
    photoperiod: TimelinePhotoperiodDraft


class TimelineApplyResponse(ClimateTimelineModel):
    """Committed timeline values and the one revision produced by Apply."""

    request_id: str = Field(min_length=1)
    config_revision: str = Field(min_length=1)
    mode_id: int
    submode_id: int | None = None
    periods: Annotated[tuple[TimelinePeriodDraft, ...], BeforeValidator(tuple)] = Field(
        min_length=1
    )
    photoperiod: TimelinePhotoperiodDraft
