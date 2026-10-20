"""Bounded all-history overview read contracts shared by monitoring endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal, Self

from pydantic import (
    AwareDatetime,
    Field,
    FiniteFloat,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from monitoring_service.history_buckets import (
    MAX_REQUESTED_POINTS,
    MIN_REQUESTED_POINTS,
    FixedHistoryBucket,
    choose_history_bucket,
    timedelta_microseconds,
)
from monitoring_service.sensor_models import (
    FrozenMonitoringModel,
    MonitoringRangeError,
    MonitoringValidationError,
    Node,
    RoomMetadata,
    StddevQuality,
    UnitFamily,
)

MINIMUM_RANGE: Final[timedelta] = timedelta(seconds=1)
CONTROL_MINIMUM_RANGE: Final[timedelta] = timedelta(minutes=5)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_DATETIME: Final = TypeAdapter(datetime)


def _parse_utc_timestamp(value: datetime | str, field_name: str) -> datetime:
    try:
        parsed = _DATETIME.validate_python(value)
    except ValidationError as exc:
        raise MonitoringRangeError(
            f"{field_name} must be an ISO 8601 timestamp with a timezone offset"
        ) from exc
    if parsed.utcoffset() is None:
        raise MonitoringRangeError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(UTC)


def _ensure_ordered_points(
    points: Sequence[HistoryOverviewPoint | HistoryOverviewRelayPoint],
) -> None:
    previous: HistoryOverviewPoint | HistoryOverviewRelayPoint | None = None
    for point in points:
        if previous is not None and point.start < previous.end:
            raise MonitoringValidationError("overview points must be ordered and nonoverlapping")
        previous = point


def _ensure_points_within_range(
    points: Sequence[HistoryOverviewPoint | HistoryOverviewRelayPoint],
    overview_range: HistoryOverviewRange,
) -> None:
    for point in points:
        if point.start < overview_range.start or point.end > overview_range.end:
            raise MonitoringValidationError("overview points must stay inside the requested range")


def _ensure_bucket_alignment(
    points: Sequence[HistoryOverviewPoint | HistoryOverviewRelayPoint],
    bucket: FixedHistoryBucket,
    overview_range: HistoryOverviewRange,
) -> None:
    """Require full-width aligned interior buckets and edge-clipped partials."""
    step_us = bucket.seconds * 1_000_000
    for point in points:
        if point.partial:
            if point.start != overview_range.start and point.end != overview_range.end:
                raise MonitoringValidationError(
                    "only buckets clipped by a requested edge may be partial"
                )
            continue
        if timedelta_microseconds(point.start - _EPOCH) % step_us != 0:
            raise MonitoringValidationError("fixed overview buckets must align to the shared epoch")
        if timedelta_microseconds(point.end - point.start) != step_us:
            raise MonitoringValidationError(
                "fixed overview buckets must span exactly their bucket width"
            )


class HistoryOverviewRange(FrozenMonitoringModel):
    """An aware-UTC half-open overview interval with no arbitrary maximum."""

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        if self.duration <= timedelta(0):
            raise MonitoringRangeError("range end must be later than range start")
        if self.duration < MINIMUM_RANGE:
            raise MonitoringRangeError("range duration must be at least 1 second")
        return self

    @property
    def duration(self) -> timedelta:
        """Return the validated duration."""
        return self.end - self.start

    @classmethod
    def from_absolute(cls, start: datetime | str | None, end: datetime | str | None) -> Self:
        """Parse absolute timestamps outside FastAPI's default 422 path."""
        if start is None or end is None:
            raise MonitoringValidationError("start and end must be supplied together")
        return cls(start=_parse_utc_timestamp(start, "start"), end=_parse_utc_timestamp(end, "end"))

    def require_control_minimum(self) -> Self:
        """Reject ranges coarser readers treat as too short for control overviews."""
        if self.duration < CONTROL_MINIMUM_RANGE:
            raise MonitoringRangeError("control overview range duration must be at least 5 minutes")
        return self


class HistoryOverviewPoint(FrozenMonitoringModel):
    """One aligned bucket with nullable sampled or time-weighted statistics."""

    start: AwareDatetime
    end: AwareDatetime
    average: FiniteFloat | None = None
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    sample_count: NonNegativeInt
    partial: bool

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_sample_consistency(self) -> Self:
        if self.end <= self.start:
            raise MonitoringValidationError("overview point end must be later than its start")
        fields = (self.average, self.minimum, self.maximum)
        if self.sample_count == 0 and all(value is None for value in fields):
            return self
        if any(value is None for value in fields):
            raise MonitoringValidationError(
                "populated overview points must set average, minimum and maximum"
            )
        if self.minimum > self.average or self.average > self.maximum:
            raise MonitoringValidationError(
                "overview point minimum, average and maximum must be ordered"
            )
        return self


class HistoryOverviewSamplePoint(HistoryOverviewPoint):
    """Sample-backed sensor/light point with exact zero- and one-row semantics."""

    @model_validator(mode="after")
    def validate_sample_statistics(self) -> Self:
        if self.sample_count == 0 and any(
            value is not None for value in (self.average, self.minimum, self.maximum)
        ):
            raise MonitoringValidationError(
                "zero-count overview points must leave average, minimum and maximum unset"
            )
        if self.sample_count == 1 and not (
            self.minimum == self.average == self.maximum
        ):
            raise MonitoringValidationError(
                "single-sample overview points must set average, minimum and maximum equally"
            )
        return self


class HistoryOverviewRelayPoint(FrozenMonitoringModel):
    """One aligned relay bucket of recorded on/off/unknown lane durations."""

    start: AwareDatetime
    end: AwareDatetime
    on_seconds: FiniteFloat = Field(ge=0)
    off_seconds: FiniteFloat = Field(ge=0)
    unknown_seconds: FiniteFloat = Field(ge=0)
    coverage_complete: bool
    partial: bool

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_durations(self) -> Self:
        if self.end <= self.start:
            raise MonitoringValidationError("relay point end must be later than its start")
        elapsed_us = timedelta_microseconds(self.end - self.start)
        total_us = 0
        for field_name, seconds in (
            ("on", self.on_seconds),
            ("off", self.off_seconds),
            ("unknown", self.unknown_seconds),
        ):
            micros = Decimal(str(seconds)) * 1_000_000
            if micros != micros.to_integral_value():
                raise MonitoringValidationError(
                    f"relay point {field_name} duration must be a whole number of microseconds"
                )
            total_us += int(micros)
        if abs(total_us - elapsed_us) > 1:
            raise MonitoringValidationError(
                "relay point on, off and unknown durations must sum to the bucket duration"
                " within one microsecond"
            )
        if self.coverage_complete and self.unknown_seconds != 0:
            raise MonitoringValidationError("complete relay coverage cannot report unknown time")
        return self


class HistoryOverviewSensorSeries(FrozenMonitoringModel):
    """One named, node-scoped sensor overview series."""

    sensor: str = Field(min_length=1)
    node: Node
    unit: str = Field(min_length=1)
    unit_family: UnitFamily
    points: tuple[HistoryOverviewSamplePoint, ...]

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        _ensure_ordered_points(self.points)
        return self


class HistoryOverviewControlSeries(FrozenMonitoringModel):
    """One location-scoped control summary series keyed by recorded identity."""

    location: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    device_name: str = Field(min_length=1)
    points: tuple[HistoryOverviewSamplePoint, ...]

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        _ensure_ordered_points(self.points)
        return self


class HistoryOverviewRequestedPidSeries(FrozenMonitoringModel):
    """One location-scoped time-weighted PID series."""

    location: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    device_name: str = Field(min_length=1)
    points: tuple[HistoryOverviewPoint, ...]

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        _ensure_ordered_points(self.points)
        return self


class HistoryOverviewRelaySeries(FrozenMonitoringModel):
    """One actual recorded relay lane with honest duration buckets."""

    location: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    channel: int = Field(ge=0, le=15)
    device_id: int = Field(ge=0)
    device_name: str = Field(min_length=1)
    device_type: str = Field(min_length=1)
    points: tuple[HistoryOverviewRelayPoint, ...]

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        _ensure_ordered_points(self.points)
        return self


class HistoryOverviewSensorStatistics(FrozenMonitoringModel):
    """Nullable range statistics honoring zero- and single-sample boundaries."""

    sensor: str = Field(min_length=1)
    node: Node
    sample_count: NonNegativeInt
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    average: FiniteFloat | None = None
    stddev_samp: FiniteFloat | None = Field(default=None, ge=0)
    stddev_quality: StddevQuality = Field(default="exact")

    @model_validator(mode="after")
    def validate_summary_consistency(self) -> Self:
        if self.sample_count == 0:
            if any(
                value is not None
                for value in (self.minimum, self.maximum, self.average, self.stddev_samp)
            ):
                raise MonitoringValidationError(
                    "zero-count overview statistics must leave minimum, maximum, average"
                    " and stddev_samp unset"
                )
            return self
        if self.minimum is None or self.maximum is None or self.average is None:
            raise MonitoringValidationError(
                "populated overview statistics must set minimum, maximum and average"
            )
        if self.minimum > self.maximum or not (self.minimum <= self.average <= self.maximum):
            raise MonitoringValidationError(
                "overview statistics minimum, average and maximum must be ordered"
            )
        if self.sample_count == 1:
            if not (self.minimum == self.average == self.maximum):
                raise MonitoringValidationError(
                    "single-sample overview statistics must set minimum, average and maximum"
                    " to the same value"
                )
            if self.stddev_samp != 0.0:
                raise MonitoringValidationError(
                    "single-sample overview statistics must report zero deviation"
                )
            return self
        if self.stddev_samp is None:
            raise MonitoringValidationError(
                "multi-sample overview statistics must report a nonnegative deviation"
            )
        return self


class HistoryOverviewMetadata(FrozenMonitoringModel):
    """Common bounded-overview response metadata."""

    generated_at: AwareDatetime
    range: HistoryOverviewRange
    room: RoomMetadata
    requested_max_points: int = Field(ge=MIN_REQUESTED_POINTS, le=MAX_REQUESTED_POINTS)
    bucket: FixedHistoryBucket = Field(discriminator="kind")
    source_coverage: Literal["ready", "unavailable"]
    all_aggregated: Literal[True] = True

    @field_validator("generated_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_bucket_selection(self) -> Self:
        selected = choose_history_bucket(
            self.range.start, self.range.end, self.requested_max_points
        )
        if selected != self.bucket:
            raise MonitoringValidationError(
                "overview bucket does not match the range-selected bucket"
            )
        return self


def _validate_overview_series(
    points: Sequence[HistoryOverviewPoint | HistoryOverviewRelayPoint],
    metadata: HistoryOverviewMetadata,
) -> None:
    if len(points) > metadata.requested_max_points:
        raise MonitoringValidationError("overview series exceeds the requested point budget")
    _ensure_points_within_range(points, metadata.range)
    _ensure_bucket_alignment(points, metadata.bucket, metadata.range)


class HistoryOverviewSensorResponse(FrozenMonitoringModel):
    """The bounded all-history sensor overview response."""

    metadata: HistoryOverviewMetadata
    series: tuple[HistoryOverviewSensorSeries, ...]
    statistics: tuple[HistoryOverviewSensorStatistics, ...]

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        for series in self.series:
            _validate_overview_series(series.points, self.metadata)
        return self


class HistoryOverviewControlResponse(FrozenMonitoringModel):
    """The bounded all-history control overview response."""

    metadata: HistoryOverviewMetadata
    lights: tuple[HistoryOverviewControlSeries, ...]
    requested_pid: tuple[HistoryOverviewRequestedPidSeries, ...]
    relays: tuple[HistoryOverviewRelaySeries, ...]

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        self.metadata.range.require_control_minimum()
        for series in (*self.lights, *self.requested_pid):
            _validate_overview_series(series.points, self.metadata)
        for relay_series in self.relays:
            _validate_overview_series(relay_series.points, self.metadata)
        return self
