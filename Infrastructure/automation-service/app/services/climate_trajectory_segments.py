"""Scheduled segment construction and shared trajectory value operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from app.schemas.climate_timeline import (
    LinearTrajectorySegment,
    SegmentSource,
    StepTrajectorySegment,
    TrajectorySegment,
    UnavailableTrajectorySegment,
)

from .climate_trajectory_types import ClimatePeriodTrajectory, MetricTarget, TrajectoryRequest


def scheduled_segments(request: TrajectoryRequest, metric: str) -> tuple[TrajectorySegment, ...]:
    """Build scheduled segments at period, ramp, and photoperiod boundaries."""
    unit = metric_unit(request.periods, metric)
    segments: list[TrajectorySegment] = []
    cursor = request.window.start
    previous_end: datetime | None = None
    previous_value: float | None = None
    source = request.periods[0].source
    for period in sorted(request.periods, key=lambda item: item.start):
        start = max(period.start.astimezone(UTC), cursor)
        end = min(period.end.astimezone(UTC), request.window.end)
        if end <= start:
            continue
        if cursor < start:
            segments.append(
                unavailable(cursor, start, metric, unit, source, "schedule coverage is unavailable")
            )
            previous_end, previous_value = None, None
        target = target_for(period, metric)
        if target is None:
            segments.append(
                unavailable(start, end, metric, unit, period.source, "setpoint is unavailable")
            )
            previous_end, previous_value, cursor = end, None, end
            continue
        initial = (
            previous_value if previous_end == start and previous_value is not None else target.value
        )
        ramp_end = min(start + timedelta(minutes=period.ramp_minutes), end)
        if initial != target.value and ramp_end > start:
            segments.append(
                linear(
                    start, ramp_end, metric, unit, period.source, "scheduled", initial, target.value
                )
            )
            final = interpolate(
                initial,
                target.value,
                start,
                start + timedelta(minutes=period.ramp_minutes),
                ramp_end,
            )
            if ramp_end < end:
                segments.append(
                    step(ramp_end, end, metric, unit, period.source, "scheduled", target.value)
                )
                final = target.value
        else:
            segments.append(
                step(start, end, metric, unit, period.source, "scheduled", target.value)
            )
            final = target.value
        previous_end, previous_value, cursor, source = end, final, end, period.source
    if cursor < request.window.end:
        segments.append(
            unavailable(
                cursor, request.window.end, metric, unit, source, "schedule coverage is unavailable"
            )
        )
    return tuple(
        slice_segment(segment, start, end, "scheduled")
        for segment in segments
        for start, end in split_at_boundaries(segment, request.photoperiod_boundaries)
    )


def target_for(period: ClimatePeriodTrajectory, metric: str) -> MetricTarget | None:
    return next((target for target in period.targets if target.metric == metric), None)


def target_at(
    periods: tuple[ClimatePeriodTrajectory, ...], metric: str, instant: datetime
) -> MetricTarget | None:
    period = next((item for item in periods if item.start <= instant < item.end), None)
    return None if period is None else target_for(period, metric)


def ramp_minutes_at(
    periods: tuple[ClimatePeriodTrajectory, ...], metric: str, instant: datetime
) -> float:
    period = next(
        (
            item
            for item in periods
            if item.start <= instant < item.end and target_for(item, metric) is not None
        ),
        None,
    )
    return 0.0 if period is None else period.ramp_minutes


def metric_unit(periods: tuple[ClimatePeriodTrajectory, ...], metric: str) -> str:
    target = next(
        target for period in periods if (target := target_for(period, metric)) is not None
    )
    return target.unit


def linear(
    start: datetime,
    end: datetime,
    metric: str,
    unit: str,
    source: SegmentSource,
    kind: Literal["scheduled", "effective"],
    start_value: float,
    end_value: float,
) -> LinearTrajectorySegment:
    return LinearTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=unit,
        trajectory_kind=kind,
        quality="exact",
        source=source,
        shape="linear",
        start_value=start_value,
        end_value=end_value,
    )


def step(
    start: datetime,
    end: datetime,
    metric: str,
    unit: str,
    source: SegmentSource,
    kind: Literal["scheduled", "effective"],
    value: float,
) -> StepTrajectorySegment:
    return StepTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=unit,
        trajectory_kind=kind,
        quality="exact",
        source=source,
        shape="step",
        value=value,
    )


def unavailable(
    start: datetime, end: datetime, metric: str, unit: str, source: SegmentSource, reason: str
) -> UnavailableTrajectorySegment:
    return UnavailableTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=unit,
        trajectory_kind="scheduled",
        quality="unavailable",
        source=source,
        shape="unavailable",
        reason=reason,
    )


def interpolate(
    start_value: float, end_value: float, start: datetime, end: datetime, instant: datetime
) -> float:
    return (
        start_value
        + (end_value - start_value)
        * (instant - start).total_seconds()
        / (end - start).total_seconds()
    )


def slice_segment(
    segment: TrajectorySegment,
    start: datetime,
    end: datetime,
    kind: Literal["scheduled", "effective"],
) -> TrajectorySegment:
    match segment:
        case StepTrajectorySegment():
            return segment.model_copy(update={"start": start, "end": end, "trajectory_kind": kind})
        case LinearTrajectorySegment():
            start_value = interpolate(
                segment.start_value, segment.end_value, segment.start, segment.end, start
            )
            end_value = interpolate(
                segment.start_value, segment.end_value, segment.start, segment.end, end
            )
            return segment.model_copy(
                update={
                    "start": start,
                    "end": end,
                    "trajectory_kind": kind,
                    "start_value": start_value,
                    "end_value": end_value,
                }
            )
        case UnavailableTrajectorySegment():
            return segment.model_copy(update={"start": start, "end": end, "trajectory_kind": kind})


def split_at_boundaries(
    segment: TrajectorySegment, boundaries: tuple[datetime, ...]
) -> tuple[tuple[datetime, datetime], ...]:
    instants = (
        segment.start,
        *(boundary for boundary in boundaries if segment.start < boundary < segment.end),
        segment.end,
    )
    return tuple(zip(instants, instants[1:], strict=False))
