"""Shared strict primitives for automation monitoring contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, NewType, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.services.photoperiod_history_logger import PhotoperiodObservation

    from .monitoring import FlushHealth

ProjectionRevision = NewType("ProjectionRevision", str)
AnchorFingerprint = NewType("AnchorFingerprint", str)
RuntimeSnapshotVersion = NewType("RuntimeSnapshotVersion", int)
OpaqueCursorId = NewType("OpaqueCursorId", str)

MINIMUM_RANGE = timedelta(minutes=5)
MAXIMUM_RANGE = timedelta(days=7)


class Origin(StrEnum):
    """How a timeline value entered the response."""

    RECORDED = "recorded"
    DERIVED = "derived"
    PROJECTED = "projected"


class Quality(StrEnum):
    """Confidence in a timeline value independently of its origin."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class Phase(StrEnum):
    """The resolved photoperiod phase for a room."""

    SUN = "SUN"
    MOON = "MOON"
    UNKNOWN = "UNKNOWN"


class FrozenMonitoringModel(BaseModel):
    """Strict immutable base for monitoring request and response boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class PhotoperiodObservationSink(Protocol):
    """Non-blocking append capability for exact room photoperiod observations."""

    def enqueue_final_phase(
        self, observation: PhotoperiodObservation, *, force: bool = False
    ) -> None: ...


class PhotoperiodLoggerHealthProvider(Protocol):
    """Read-only health capability for the photoperiod history source."""

    def flush_health(self) -> tuple[FlushHealth, ...]: ...


class MonitoringRange(FrozenMonitoringModel):
    """Validated aware-UTC half-open monitoring interval ``[start, end)``."""

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        duration = self.end - self.start
        if not MINIMUM_RANGE <= duration <= MAXIMUM_RANGE:
            raise ValueError("range duration must be from 5 minutes through 7 days")
        return self

    @classmethod
    def from_absolute(cls, start: datetime, end: datetime) -> Self:
        """Create a range after the HTTP boundary has parsed aware timestamps."""
        return cls(start=start, end=end)


class TimelineProvenance(FrozenMonitoringModel):
    """Orthogonal source, confidence, and aggregation facts for timeline values."""

    origin: Origin
    quality: Quality
    is_aggregated: bool = False


class AggregationMetadata(FrozenMonitoringModel):
    """Source bucket information retained when a timeline series is aggregated."""

    interval_seconds: int = Field(gt=0)
    sample_count: int = Field(ge=0)


class ProjectionMetadata(FrozenMonitoringModel):
    """Stable provenance captured with every projected control series."""

    projection_revision: ProjectionRevision = Field(min_length=1)
    anchor_fingerprint: AnchorFingerprint = Field(min_length=1)
    anchor_observed_at: AwareDatetime
    anchor_quality: Quality
    anchor_valid_until: AwareDatetime

    @field_validator("anchor_observed_at", "anchor_valid_until")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_anchor_window(self) -> Self:
        if self.anchor_valid_until < self.anchor_observed_at:
            raise ValueError("anchor_valid_until must not precede anchor_observed_at")
        return self


class MonitoringWarning(FrozenMonitoringModel):
    """A non-fatal reason a control timeline is estimated or incomplete."""

    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
