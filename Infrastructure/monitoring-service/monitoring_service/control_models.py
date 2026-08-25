"""Immutable read contracts for recorded and published control monitoring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from shared.monitoring_contracts import CurrentSnapshot, FutureProjection, Quality


class ControlReadModel(BaseModel):
    """Strict immutable base for the monitoring-service control read surface."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class ControlHistoryRange(ControlReadModel):
    """One half-open, UTC-normalized recorded-control history interval."""

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end <= self.start:
            raise ControlReadValidationError("history end must be later than history start")
        return self


class ControlReadValidationError(ValueError):
    """A control monitoring request cannot form a valid read interval."""


class CurrentPublicationResponse(ControlReadModel):
    quality: Quality
    value: CurrentSnapshot | None


class ProjectionPublicationResponse(ControlReadModel):
    quality: Quality
    value: tuple[FutureProjection, ...] = ()


class ControlPublicationResponse(ControlReadModel):
    """Paired current and future publications read atomically from Redis."""

    current: CurrentPublicationResponse
    projection: ProjectionPublicationResponse


class TimelineProvenanceModel(ControlReadModel):
    origin: Literal["recorded", "derived", "projected"]
    quality: Quality
    is_aggregated: bool


class TimelineStepOut(ControlReadModel):
    timestamp: AwareDatetime
    value: float | None
    provenance: TimelineProvenanceModel


class TimelineLinearOut(ControlReadModel):
    start: AwareDatetime
    end: AwareDatetime
    start_value: float
    end_value: float
    provenance: TimelineProvenanceModel


class ClimateTimelinePointOut(ControlReadModel):
    timestamp: AwareDatetime
    value: float | None
    provenance: TimelineProvenanceModel
    metric: str
    nominal_value: float | None = None
    ramp_progress: float | None = None
    mode: str | None = None
    device_name: str | None = None


class LightTimelinePointOut(ControlReadModel):
    timestamp: AwareDatetime
    value: float | None
    provenance: TimelineProvenanceModel
    device_name: str
    nominal_value: float | None = None
    ramp_progress: float | None = None
    mode: str | None = None


class ClimateTimelineSeriesOut(ControlReadModel):
    name: str
    provenance: TimelineProvenanceModel
    warnings: tuple[str, ...] = ()
    points: tuple[ClimateTimelinePointOut, ...]
    steps: tuple[TimelineStepOut, ...] = ()
    linear: tuple[TimelineLinearOut, ...] = ()


class LightTimelineSeriesOut(ControlReadModel):
    name: str
    provenance: TimelineProvenanceModel
    warnings: tuple[str, ...] = ()
    points: tuple[LightTimelinePointOut, ...]
    steps: tuple[TimelineStepOut, ...] = ()
    linear: tuple[TimelineLinearOut, ...] = ()


class DeviceTimelinePointOut(ControlReadModel):
    timestamp: AwareDatetime
    provenance: TimelineProvenanceModel
    device_name: str
    device_state: float
    device_mode: str
    control_reason: str


class DeviceTimelineSeriesOut(ControlReadModel):
    name: str
    provenance: TimelineProvenanceModel
    warnings: tuple[str, ...] = ()
    points: tuple[DeviceTimelinePointOut, ...]


class PidTimelinePointOut(ControlReadModel):
    timestamp: AwareDatetime
    provenance: TimelineProvenanceModel
    device_name: str
    pid_output: float | None = None
    duty_cycle_percent: float | None = None


class PidTimelineSeriesOut(ControlReadModel):
    name: str
    provenance: TimelineProvenanceModel
    warnings: tuple[str, ...] = ()
    points: tuple[PidTimelinePointOut, ...]


class PhotoperiodTimelinePointOut(ControlReadModel):
    timestamp: AwareDatetime
    phase: Literal["SUN", "MOON", "UNKNOWN"]
    provenance: TimelineProvenanceModel
    mode_id: int | None = None
    submode_id: int | None = None
    runtime_snapshot_version: int | None = None


class ControlHistoryEnvelope(ControlReadModel):
    """Recorded control facts shaped for the monitoring chart pipeline."""

    range: ControlHistoryRange
    runtime_snapshot_version: int
    requested_max_points: int | None = None
    interval_seconds: int | None = None
    cursors: tuple[str, ...] = ()
    flush_health: tuple[str, ...] = ()
    climate: tuple[ClimateTimelineSeriesOut, ...] = ()
    lights: tuple[LightTimelineSeriesOut, ...] = ()
    devices: tuple[DeviceTimelineSeriesOut, ...] = ()
    pid: tuple[PidTimelineSeriesOut, ...] = ()
    photoperiod: tuple[PhotoperiodTimelinePointOut, ...] = ()
