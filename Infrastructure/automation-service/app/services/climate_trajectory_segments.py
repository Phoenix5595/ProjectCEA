"""Scheduled segment construction and shared trajectory value operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from app.control.ramp_policy import DEFAULT_RAMP_SKIP_THRESHOLD, RAMP_SKIP_THRESHOLDS
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
    previous_value: float | None = None
    previous_profile: tuple[str, str | None] | None = None
    previous_occurrence: tuple[object, ...] | None = None
    previous_ramp_initial: float | None = None
    source = request.periods[0].source
    threshold = RAMP_SKIP_THRESHOLDS.get(metric, DEFAULT_RAMP_SKIP_THRESHOLD)
    for period in sorted(request.periods, key=lambda item: item.start):
        start = max(period.start.astimezone(UTC), cursor)
        end = min(period.end.astimezone(UTC), request.window.end)
        if end <= start:
            continue
        profile = _profile_identity(period.source)
        if profile != previous_profile:
            previous_value = None
            previous_occurrence = None
            previous_ramp_initial = None
        if cursor < start:
            segments.append(
                unavailable(cursor, start, metric, unit, source, "schedule coverage is unavailable")
            )
            previous_value = None
            previous_occurrence = None
            previous_ramp_initial = None
        target = target_for(period, metric)
        if target is None:
            segments.append(
                unavailable(start, end, metric, unit, period.source, "setpoint is unavailable")
            )
            cursor, source, previous_profile = end, period.source, profile
            previous_value = None
            previous_occurrence = None
            previous_ramp_initial = None
            continue

        origin = (period.ramp_start or period.start).astimezone(UTC)
        ramp_end = origin + timedelta(minutes=period.ramp_minutes)
        occurrence = (
            profile,
            period.source.period.period_id,
            period.source.config_revision,
            origin,
            target.metric,
            target.unit,
            target.value,
        )
        continuing = previous_occurrence == occurrence and cursor == start
        if continuing and previous_ramp_initial is not None:
            initial = previous_ramp_initial
        elif origin == start and previous_value is not None:
            initial = previous_value
        else:
            predecessor = _previous_target_for(period, metric)
            initial = target.value if predecessor is None else predecessor.value

        should_ramp = (
            period.ramp_minutes > 0
            and start < ramp_end
            and abs(initial - target.value) >= threshold
        )
        if should_ramp:
            if continuing and previous_value is not None:
                start_value = previous_value
            elif origin < start:
                start_value = interpolate(initial, target.value, origin, ramp_end, start)
            else:
                start_value = initial
            visible_end = min(ramp_end, end)
            end_value = interpolate(start_value, target.value, start, ramp_end, visible_end)
            segments.append(
                linear(
                    start,
                    visible_end,
                    metric,
                    unit,
                    period.source,
                    "scheduled",
                    start_value,
                    end_value,
                )
            )
            final = end_value
            if visible_end < end:
                segments.append(
                    step(visible_end, end, metric, unit, period.source, "scheduled", target.value)
                )
                final = target.value
            if ramp_end > end:
                previous_occurrence = occurrence
                previous_ramp_initial = initial
            else:
                previous_occurrence = None
                previous_ramp_initial = None
        else:
            segments.append(
                step(start, end, metric, unit, period.source, "scheduled", target.value)
            )
            final = target.value
            previous_occurrence = None
            previous_ramp_initial = None
        previous_value, cursor, source, previous_profile = final, end, period.source, profile
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


def _profile_identity(source: SegmentSource) -> tuple[str, str | None]:
    return source.mode, source.submode


def _previous_target_for(period: ClimatePeriodTrajectory, metric: str) -> MetricTarget | None:
    return next((target for target in period.previous_targets if target.metric == metric), None)


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
