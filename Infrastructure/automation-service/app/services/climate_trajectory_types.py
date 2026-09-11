"""Immutable authority values for pure climate trajectory evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas.climate_timeline import SegmentSource, UtcWindow


@dataclass(frozen=True, slots=True)
class MetricTarget:
    """One configured climate target with its display unit."""

    metric: str
    unit: str
    value: float


@dataclass(frozen=True, slots=True)
class ClimatePeriodTrajectory:
    """One resolved saved climate period over an absolute UTC interval."""

    start: datetime
    end: datetime
    source: SegmentSource
    targets: tuple[MetricTarget, ...]
    ramp_minutes: float = 0.0


@dataclass(frozen=True, slots=True)
class RuntimeOverride:
    """A runtime target override; a missing end is explicitly indefinite."""

    metric: str
    value: float
    start: datetime
    end: datetime | None


@dataclass(frozen=True, slots=True)
class TrajectoryRequest:
    """Immutable resolved authority for one finite rich-trajectory window."""

    window: UtcWindow
    periods: tuple[ClimatePeriodTrajectory, ...]
    overrides: tuple[RuntimeOverride, ...] = ()
    photoperiod_boundaries: tuple[datetime, ...] = ()
    room: str = "Flower Room"
