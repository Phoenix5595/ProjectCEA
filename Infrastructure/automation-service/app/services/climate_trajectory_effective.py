"""Runtime override overlay for pure climate trajectories."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.schemas.climate_timeline import TrajectorySegment

from .climate_trajectory_segments import (
    interpolate,
    linear,
    metric_unit,
    ramp_minutes_at,
    slice_segment,
    step,
    target_at,
)
from .climate_trajectory_types import RuntimeOverride, TrajectoryRequest


def effective_segments(
    request: TrajectoryRequest, metric: str, scheduled: tuple[TrajectorySegment, ...]
) -> tuple[TrajectorySegment, ...]:
    """Overlay runtime overrides and their finite resumption ramps."""
    boundaries = {request.window.start, request.window.end}
    boundaries.update(
        boundary for segment in scheduled for boundary in (segment.start, segment.end)
    )
    for override in request.overrides:
        if override.metric != metric:
            continue
        boundaries.add(override.start)
        if override.end is not None:
            boundaries.add(override.end)
            boundaries.add(
                override.end
                + timedelta(minutes=ramp_minutes_at(request.periods, metric, override.end))
            )
    ordered = tuple(
        sorted(
            boundary
            for boundary in boundaries
            if request.window.start <= boundary <= request.window.end
        )
    )
    return tuple(
        effective_interval(request, metric, scheduled, start, end)
        for start, end in zip(ordered, ordered[1:], strict=False)
        if start < end
    )


def effective_interval(
    request: TrajectoryRequest,
    metric: str,
    scheduled: tuple[TrajectorySegment, ...],
    start: datetime,
    end: datetime,
) -> TrajectorySegment:
    base = next(segment for segment in scheduled if segment.start <= start < segment.end)
    override = active_override(request.overrides, metric, start)
    if override is not None:
        return step(
            start,
            end,
            metric,
            metric_unit(request.periods, metric),
            base.source,
            "effective",
            override.value,
        )
    expired = expired_override(request.overrides, metric, start)
    if expired is None or expired.end is None:
        return slice_segment(base, start, end, "effective")
    duration = ramp_minutes_at(request.periods, metric, expired.end)
    target = target_at(request.periods, metric, expired.end)
    resume_end = expired.end + timedelta(minutes=duration)
    if target is None or duration <= 0 or start >= resume_end:
        return slice_segment(base, start, end, "effective")
    return linear(
        start,
        end,
        metric,
        target.unit,
        base.source,
        "effective",
        interpolate(expired.value, target.value, expired.end, resume_end, start),
        interpolate(expired.value, target.value, expired.end, resume_end, end),
    )


def active_override(
    overrides: tuple[RuntimeOverride, ...], metric: str, instant: datetime
) -> RuntimeOverride | None:
    return max(
        (
            item
            for item in overrides
            if item.metric == metric
            and item.start <= instant
            and (item.end is None or instant < item.end)
        ),
        key=lambda item: item.start,
        default=None,
    )


def expired_override(
    overrides: tuple[RuntimeOverride, ...], metric: str, instant: datetime
) -> RuntimeOverride | None:
    return max(
        (
            item
            for item in overrides
            if item.metric == metric and item.end is not None and item.end <= instant
        ),
        key=lambda item: item.end or instant,
        default=None,
    )
