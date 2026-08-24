"""Immutable provenance-aware control monitoring timeline contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .monitoring_models import (
    AggregationMetadata,
    FrozenMonitoringModel,
    MonitoringRange,
    MonitoringWarning,
    OpaqueCursorId,
    Origin,
    Phase,
    ProjectionMetadata,
    Quality,
    RuntimeSnapshotVersion,
    TimelineProvenance,
)
from .monitoring_models import AnchorFingerprint as _AnchorFingerprint
from .monitoring_models import ProjectionRevision as _ProjectionRevision

AnchorFingerprint = _AnchorFingerprint
ProjectionRevision = _ProjectionRevision


class TimelinePoint(FrozenMonitoringModel):
    """A scalar control value on the shared UTC monitoring grid."""

    timestamp: AwareDatetime
    value: float | None
    provenance: TimelineProvenance
    aggregation: AggregationMetadata | None = None

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class TimelineStep(FrozenMonitoringModel):
    """A value that holds from ``timestamp`` until the following point."""

    timestamp: AwareDatetime
    value: float | None
    provenance: TimelineProvenance

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class TimelineLinear(FrozenMonitoringModel):
    """A linear target segment with explicit UTC endpoints."""

    start: AwareDatetime
    end: AwareDatetime
    start_value: float
    end_value: float
    provenance: TimelineProvenance

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("linear segment end must be later than start")
        return self


class ClimateTimelinePoint(TimelinePoint):
    """Recorded or projected effective/nominal climate setpoint values."""

    metric: str = Field(min_length=1)
    nominal_value: float | None
    ramp_progress: float | None = Field(default=None, ge=0, le=1)
    mode: str | None = None
    device_name: str | None = None


class LightTimelinePoint(TimelinePoint):
    """Recorded or projected per-device effective/nominal light intensity."""

    device_name: str = Field(min_length=1)
    nominal_value: float | None
    ramp_progress: float | None = Field(default=None, ge=0, le=1)
    mode: str | None = None


class DeviceTimelinePoint(FrozenMonitoringModel):
    """Historical automation-state observation; future device state is never fabricated."""

    timestamp: AwareDatetime
    provenance: TimelineProvenance
    device_name: str = Field(min_length=1)
    device_state: int
    device_mode: str = Field(min_length=1)
    control_reason: str = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def reject_projected(self) -> Self:
        match self.provenance.origin:
            case Origin.PROJECTED:
                raise ValueError("device history cannot be projected")
            case Origin.RECORDED | Origin.DERIVED:
                return self


class PidTimelinePoint(FrozenMonitoringModel):
    """Historical PID output observation; null output stays null."""

    timestamp: AwareDatetime
    provenance: TimelineProvenance
    device_name: str = Field(min_length=1)
    pid_output: float | None = None
    duty_cycle_percent: float | None = Field(default=None, ge=0, le=100)

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def reject_projected(self) -> Self:
        match self.provenance.origin:
            case Origin.PROJECTED:
                raise ValueError("PID history cannot be projected")
            case Origin.RECORDED | Origin.DERIVED:
                return self


class PhotoperiodTimelinePoint(FrozenMonitoringModel):
    """A historical or projected room photoperiod phase transition."""

    timestamp: AwareDatetime
    phase: Phase
    provenance: TimelineProvenance
    mode_id: int | None = None
    submode_id: int | None = None
    runtime_snapshot_version: RuntimeSnapshotVersion | None = None

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_unknown_phase(self) -> Self:
        match self.phase:
            case Phase.UNKNOWN:
                if self.provenance.quality is not Quality.UNAVAILABLE:
                    raise ValueError("UNKNOWN photoperiod phase requires unavailable quality")
            case Phase.SUN | Phase.MOON:
                if self.provenance.quality is Quality.UNAVAILABLE:
                    raise ValueError("known photoperiod phase cannot be unavailable")
        return self


class ProjectableTimelineSeries(FrozenMonitoringModel):
    """Shared projection completeness invariant for climate and light timelines."""

    name: str = Field(min_length=1)
    provenance: TimelineProvenance
    projection: ProjectionMetadata | None = None
    warnings: tuple[MonitoringWarning, ...] = ()

    @model_validator(mode="after")
    def require_projection_metadata(self) -> Self:
        match self.provenance.origin:
            case Origin.PROJECTED:
                if self.projection is None:
                    raise ValueError("projected series requires projection metadata")
            case Origin.RECORDED | Origin.DERIVED:
                pass
        return self


class ClimateTimelineSeries(ProjectableTimelineSeries):
    """Climate target timeline with step and linear ramp representations."""

    points: tuple[ClimateTimelinePoint, ...]
    steps: tuple[TimelineStep, ...] = ()
    linear: tuple[TimelineLinear, ...] = ()


class LightTimelineSeries(ProjectableTimelineSeries):
    """Per-light target timeline with step and linear ramp representations."""

    points: tuple[LightTimelinePoint, ...]
    steps: tuple[TimelineStep, ...] = ()
    linear: tuple[TimelineLinear, ...] = ()


class DeviceTimelineSeries(FrozenMonitoringModel):
    """Historical device automation states only."""

    name: str = Field(min_length=1)
    points: tuple[DeviceTimelinePoint, ...]
    warnings: tuple[MonitoringWarning, ...] = ()


class PidTimelineSeries(FrozenMonitoringModel):
    """Historical PID observations only."""

    name: str = Field(min_length=1)
    points: tuple[PidTimelinePoint, ...]
    warnings: tuple[MonitoringWarning, ...] = ()


class SourceCursor(FrozenMonitoringModel):
    """Opaque per-source tail cursor and bounded-page state."""

    source: str = Field(min_length=1)
    cursor: OpaqueCursorId | None = None
    has_more: bool


class FlushHealth(FrozenMonitoringModel):
    """Persisted-history flush status, including rows dropped before storage."""

    source: str = Field(min_length=1)
    dropped_rows: int = Field(ge=0)
    last_flushed_at: AwareDatetime | None = None
    healthy: bool

    @field_validator("last_flushed_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class PhotoperiodHistorySink(Protocol):
    """Append-only photoperiod history capability accepting immutable events."""

    async def append(self, point: PhotoperiodTimelinePoint) -> None: ...


class MonitoringHealthProvider(Protocol):
    """Read-only capability exposing immutable flush-health observations."""

    def flush_health(self) -> tuple[FlushHealth, ...]: ...


class ControlMonitoringResponse(FrozenMonitoringModel):
    """Range or tail response containing immutable recorded and projected timelines."""

    range: MonitoringRange
    runtime_snapshot_version: RuntimeSnapshotVersion
    cursors: tuple[SourceCursor, ...]
    flush_health: tuple[FlushHealth, ...]
    climate: tuple[ClimateTimelineSeries, ...] = ()
    lights: tuple[LightTimelineSeries, ...] = ()
    devices: tuple[DeviceTimelineSeries, ...] = ()
    pid: tuple[PidTimelineSeries, ...] = ()
    photoperiod: tuple[PhotoperiodTimelinePoint, ...] = ()
