"""Legacy-compatible contracts for read-only sensor monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import ClassVar, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from shared.cluster_topology import known_rooms, sensor_url_clusters_for
from monitoring_service.sensor_intervals import (
    NICE_INTERVAL_SECONDS as _NICE_INTERVAL_SECONDS,
    derive_interval_seconds as _derive_interval_seconds,
    resolve_interval_seconds as _resolve_interval_seconds,
)

MINIMUM_RANGE: Final = timedelta(seconds=1)
MAXIMUM_RANGE: Final = timedelta(days=7)
RAW_TIER_LIMIT: Final = timedelta(hours=1)
ONE_MINUTE_TIER_LIMIT: Final = timedelta(hours=6)
NICE_INTERVAL_SECONDS: Final[tuple[int, ...]] = _NICE_INTERVAL_SECONDS
_MONITORING_ROOMS: Final = frozenset({"Flower Room", "Veg Room"})
_DATETIME: Final = TypeAdapter(datetime)


class Room(StrEnum):
    """Rooms supported by the sensor monitoring API."""

    FLOWER = "Flower Room"
    VEG = "Veg Room"


class Node(StrEnum):
    """Canonical URL nodes used by monitored rooms."""

    FRONT = "front"
    BACK = "back"
    MAIN = "main"


class UnitFamily(StrEnum):
    """Grafana-compatible sensor unit families."""

    CELSIUS = "celsius"
    PERCENT = "percent"
    KPA = "kpa"
    PPM = "ppm"
    HPA = "hpa"
    MM = "mm"


class Tier(StrEnum):
    """Storage resolution selected from the request duration."""

    RAW = "raw"
    ONE_MINUTE = "1min"
    FIVE_MINUTES = "5min"


class StddevQuality(StrEnum):
    """How precisely the selected statistics source can report sample deviation."""

    EXACT = "exact"
    APPROXIMATE = "approximate"


class MonitoringError(Exception):
    """A monitoring failure translated to a stable HTTP response."""

    status_code: ClassVar[int]

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class MonitoringValidationError(MonitoringError):
    """A recognized monitoring request violates the contract."""

    status_code: ClassVar[int] = 400


class MonitoringRangeError(MonitoringValidationError):
    """A monitoring interval violates its duration or timestamp contract."""


class MonitoringNotFoundError(MonitoringError):
    """The requested room does not exist in the canonical topology."""

    status_code: ClassVar[int] = 404


class MonitoringUnavailableError(MonitoringError):
    """A required read model is unavailable or lacks safe coverage."""

    status_code: ClassVar[int] = 503


class FrozenMonitoringModel(BaseModel):
    """Immutable, strict API model base."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")


def _validate_duration(duration: timedelta) -> None:
    if duration <= timedelta(0):
        raise MonitoringRangeError("range end must be later than range start")
    if duration < MINIMUM_RANGE:
        raise MonitoringRangeError("range duration must be at least 1 second")
    if duration > MAXIMUM_RANGE:
        raise MonitoringRangeError("range duration must not exceed 7 days")


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


def compute_tier(duration: timedelta) -> Tier:
    """Select the legacy-compatible tier for a valid duration."""
    _validate_duration(duration)
    if duration <= RAW_TIER_LIMIT:
        return Tier.RAW
    if duration <= ONE_MINUTE_TIER_LIMIT:
        return Tier.ONE_MINUTE
    return Tier.FIVE_MINUTES


def resolve_tier(duration: timedelta, max_points: int | None) -> Tier:
    """Select the tier honoring the budget without ever losing legacy density.

    Legacy requests without a budget keep the duration-only policy exactly.
    Budgeted requests may upgrade granularity (e.g., 24 h served from the
    1-minute model) but never fall coarser than the legacy choice.
    """
    _validate_duration(duration)
    if max_points is None:
        return compute_tier(duration)
    seconds = duration.total_seconds()
    minutes = seconds / 60
    smallest_raw_interval = -(-seconds // max_points)
    if duration <= timedelta(hours=6):
        if smallest_raw_interval < 60:
            return Tier.RAW
        if minutes <= max_points:
            return Tier.ONE_MINUTE
        return Tier.RAW
    if minutes <= max_points:
        return Tier.ONE_MINUTE
    return Tier.FIVE_MINUTES


def source_bucket_seconds(tier: Tier) -> int:
    """Return the legacy source bucket width for a selected sensor tier."""
    match tier:
        case Tier.RAW:
            return 1
        case Tier.ONE_MINUTE:
            return 60
        case Tier.FIVE_MINUTES:
            return 300


def resolve_interval_seconds(
    duration_seconds: int, max_points: int, source_bucket_seconds: int
) -> int:
    """Expose the sensor repository's applied ladder-snapped bucket policy."""
    return _resolve_interval_seconds(duration_seconds, max_points, source_bucket_seconds)


def derive_interval_seconds(
    duration: timedelta, source_bucket_seconds: int, max_points: int | None
) -> int | None:
    """Expose the optional budget's applied read interval for response metadata."""
    return _derive_interval_seconds(duration, source_bucket_seconds, max_points)


class MonitoringRange(FrozenMonitoringModel):
    """An aware-UTC half-open interval ``[start, end)``."""

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        _validate_duration(self.duration)
        return self

    @property
    def duration(self) -> timedelta:
        """Return the validated duration."""
        return self.end - self.start

    @classmethod
    def from_now(cls, duration: timedelta) -> Self:
        """Build a range ending at the current UTC instant."""
        end = datetime.now(UTC)
        return cls.from_absolute(end - duration, end)

    @classmethod
    def from_absolute(cls, start: datetime | str, end: datetime | str) -> Self:
        """Parse absolute timestamps outside FastAPI's default 422 path."""
        return cls(start=_parse_utc_timestamp(start, "start"), end=_parse_utc_timestamp(end, "end"))


class SeriesPoint(FrozenMonitoringModel):
    """Average and envelope at one UTC timestamp."""

    timestamp: AwareDatetime
    average: FiniteFloat
    minimum: FiniteFloat
    maximum: FiniteFloat
    sample_count: NonNegativeInt

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class RoomMetadata(FrozenMonitoringModel):
    """The canonical room and its ordered sensor nodes."""

    room: Room
    nodes: tuple[Node, ...]


def resolve_room_metadata(room: str) -> RoomMetadata:
    """Resolve the feature-scoped room with legacy 400/404 behavior."""
    if room not in known_rooms():
        raise MonitoringNotFoundError(f"unknown room: {room}")
    if room not in _MONITORING_ROOMS:
        raise MonitoringValidationError(f"room is outside monitoring feature scope: {room}")
    return RoomMetadata(
        room=Room(room), nodes=tuple(Node(node) for node in sensor_url_clusters_for(room))
    )


class SensorSeries(FrozenMonitoringModel):
    """One named, node-scoped sensor series."""

    sensor: str = Field(min_length=1)
    node: Node
    unit_family: UnitFamily
    unit: str = Field(min_length=1)
    points: tuple[SeriesPoint, ...]
    point_count: NonNegativeInt = 0
    sample_count_total: NonNegativeInt = 0

    @model_validator(mode="after")
    def compute_counts(self) -> Self:
        """Populate additive counts from the serialized points."""
        object.__setattr__(self, "point_count", len(self.points))
        object.__setattr__(
            self, "sample_count_total", sum(point.sample_count for point in self.points)
        )
        return self


class SensorStatistics(FrozenMonitoringModel):
    """Exact summary statistics for one sensor."""

    sensor: str = Field(min_length=1)
    node: Node
    minimum: FiniteFloat
    maximum: FiniteFloat
    average: FiniteFloat
    stddev_samp: FiniteFloat = Field(ge=0)
    sample_count: NonNegativeInt
    stddev_quality: StddevQuality = Field(default="exact")


class MonitoringMetadata(FrozenMonitoringModel):
    """Tier, range, generation, and topology details for a response."""

    generated_at: AwareDatetime
    tier: Tier
    range: MonitoringRange
    room: RoomMetadata
    requested_max_points: int | None = None
    interval_seconds: int | None = None

    @field_validator("generated_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_selected_tier(self) -> Self:
        selected = resolve_tier(self.range.duration, self.requested_max_points)
        if self.tier is not selected:
            raise MonitoringValidationError(
                f"tier {self.tier} does not match range-selected tier {selected}"
            )
        return self


class MonitoringResponse(FrozenMonitoringModel):
    """The legacy-compatible range or statistics response."""

    metadata: MonitoringMetadata
    series: tuple[SensorSeries, ...]
    statistics: tuple[SensorStatistics, ...]


class LiveSensorValue(FrozenMonitoringModel):
    """One current Redis-published sensor value."""

    sensor: str
    value: FiniteFloat
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
