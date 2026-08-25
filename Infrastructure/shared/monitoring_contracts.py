"""Authoritative, service-neutral monitoring publication contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Literal, NewType, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)
from typing_extensions import override

ConfigVersion = NewType("ConfigVersion", int)
ProjectionRevision = NewType("ProjectionRevision", str)


@dataclass(frozen=True, slots=True)
class MonitoringContractViolation(ValueError):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


class MonitoringContract(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class Quality(StrEnum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class PersistenceState(StrEnum):
    PENDING = "pending"
    PERSISTED = "persisted"
    FAILED = "failed"


class PhotoperiodPhase(StrEnum):
    SUN = "SUN"
    MOON = "MOON"
    UNKNOWN = "UNKNOWN"


class PublicationVersion(MonitoringContract):
    contract_version: Literal[1]
    config_version: ConfigVersion = Field(ge=1)
    revision: ProjectionRevision = Field(pattern=r"^[0-9a-f]{7,64}$")


class SemanticSeriesId(MonitoringContract):
    value: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class TimedMonitoringFact(MonitoringContract):
    observed_at: AwareDatetime
    valid_until: AwareDatetime

    @field_validator("observed_at", "valid_until")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_validity_window(self) -> Self:
        if self.valid_until < self.observed_at:
            raise MonitoringContractViolation("valid_until must not precede observed_at")
        return self


class CurrentSeriesPoint(TimedMonitoringFact):
    series_id: SemanticSeriesId
    value: FiniteFloat | None
    quality: Quality

    @model_validator(mode="after")
    def validate_value_quality(self) -> Self:
        match self.quality:  # noexcuse: # noqa: MATCH_OK
            case Quality.EXACT | Quality.ESTIMATED:
                if self.value is None:
                    raise MonitoringContractViolation("exact and estimated facts require a value")
            case Quality.UNAVAILABLE:
                if self.value is not None:
                    raise MonitoringContractViolation("unavailable facts must not carry a value")
        return self


class ProjectionSeriesPoint(MonitoringContract):
    series_id: SemanticSeriesId
    value: FiniteFloat | None
    quality: Quality
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @field_validator("valid_from", "valid_until")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_projection_state(self) -> Self:
        if self.valid_until < self.valid_from:
            raise MonitoringContractViolation("valid_until must not precede valid_from")
        match self.quality:  # noexcuse: # noqa: MATCH_OK
            case Quality.ESTIMATED:
                if self.value is None:
                    raise MonitoringContractViolation("estimated projections require a value")
            case Quality.UNAVAILABLE:
                if self.value is not None:
                    raise MonitoringContractViolation(
                        "unavailable projections must not carry a value"
                    )
            case Quality.EXACT:
                raise MonitoringContractViolation(
                    "future projections must be estimated or unavailable"
                )
        return self


class Photoperiod(TimedMonitoringFact):
    phase: PhotoperiodPhase
    quality: Quality

    @model_validator(mode="after")
    def validate_phase_quality(self) -> Self:
        match self.phase:  # noexcuse: # noqa: MATCH_OK
            case PhotoperiodPhase.UNKNOWN:
                match self.quality:  # noexcuse: # noqa: MATCH_OK
                    case Quality.UNAVAILABLE:
                        return self
                    case Quality.EXACT | Quality.ESTIMATED:
                        raise MonitoringContractViolation("unknown photoperiod must be unavailable")
            case PhotoperiodPhase.SUN | PhotoperiodPhase.MOON:
                match self.quality:  # noexcuse: # noqa: MATCH_OK
                    case Quality.EXACT | Quality.ESTIMATED:
                        return self
                    case Quality.UNAVAILABLE:
                        raise MonitoringContractViolation(
                            "known photoperiod must be exact or estimated"
                        )


class PersistenceCursor(MonitoringContract):
    state: PersistenceState
    cursor: str | None = Field(default=None, min_length=1)
    persisted_at: AwareDatetime | None = None
    error: str | None = Field(default=None, min_length=1)

    @field_validator("persisted_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_persistence_state(self) -> Self:
        match self.state:  # noexcuse: # noqa: MATCH_OK
            case PersistenceState.PERSISTED:
                if self.cursor is None or self.persisted_at is None or self.error is not None:
                    raise MonitoringContractViolation(
                        "persisted cursor requires cursor and persisted_at without error"
                    )
            case PersistenceState.PENDING:
                if (
                    self.cursor is not None
                    or self.persisted_at is not None
                    or self.error is not None
                ):
                    raise MonitoringContractViolation(
                        "pending cursor must not carry persistence details"
                    )
            case PersistenceState.FAILED:
                if self.cursor is not None or self.persisted_at is not None or self.error is None:
                    raise MonitoringContractViolation(
                        "failed cursor requires error without persisted details"
                    )
        return self


class CurrentSnapshot(MonitoringContract):
    version: PublicationVersion
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    series: tuple[CurrentSeriesPoint, ...]
    photoperiod: Photoperiod | None
    persistence: PersistenceCursor

    @field_validator("observed_at", "valid_until")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.valid_until < self.observed_at:
            raise MonitoringContractViolation("valid_until must not precede observed_at")
        if len({point.series_id.value for point in self.series}) != len(self.series):
            raise MonitoringContractViolation("current snapshot series identifiers must be unique")
        return self


class FutureProjection(MonitoringContract):
    version: PublicationVersion
    generated_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    series: tuple[ProjectionSeriesPoint, ...]

    @field_validator("generated_at", "valid_from", "valid_until")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.valid_until < self.valid_from:
            raise MonitoringContractViolation("valid_until must not precede valid_from")
        if len({point.series_id.value for point in self.series}) != len(self.series):
            raise MonitoringContractViolation("future projection series identifiers must be unique")
        return self


class MonitoringPublication(MonitoringContract):
    current: CurrentSnapshot
    future: tuple[FutureProjection, ...]

    @model_validator(mode="after")
    def validate_versions(self) -> Self:
        if any(projection.version != self.current.version for projection in self.future):
            raise MonitoringContractViolation(
                "current snapshot and future projections must share one version"
            )
        validate_projection_timeline(self.future)
        return self


def validate_projection_timeline(
    projections: tuple[FutureProjection, ...],
) -> tuple[FutureProjection, ...]:
    """Return projections validated as one ordered, non-overlapping, single-version timeline."""
    previous: FutureProjection | None = None
    for projection in projections:
        if previous is not None:
            if projection.version != previous.version:
                raise MonitoringContractViolation(
                    "projection timeline intervals must share one version"
                )
            if projection.valid_from < previous.valid_until:
                raise MonitoringContractViolation(
                    "projection timeline intervals must be ordered and non-overlapping"
                )
        previous = projection
    return projections
