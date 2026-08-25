"""Pure, immutable light schedule projection for monitoring timelines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.monitoring import LightTimelinePoint, LightTimelineSeries
from app.schemas.monitoring_models import (
    AggregationMetadata,
    Origin,
    Phase,
    ProjectionMetadata,
    Quality,
    TimelineProvenance,
)

from .light_projection_evaluator import cycle_change_count, evaluate_intensity, phase_at

LOCAL_TZ: Final = ZoneInfo("America/Toronto")
DISPLAY_GRID_POINTS: Final = 1_008


@dataclass(frozen=True, slots=True)
class PhotoperiodInterval:
    """One half-open UTC room photoperiod interval with immutable provenance."""

    start: datetime
    end: datetime
    phase: Phase
    provenance: TimelineProvenance


@dataclass(frozen=True, slots=True)
class IntensityBucket:
    """Display-safe aggregate of high-frequency cycle output."""

    start: datetime
    end: datetime
    average: float
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class LightProjection:
    """Projected per-light timelines and room photoperiod intervals."""

    lights: tuple[LightTimelineSeries, ...]
    photoperiod: tuple[PhotoperiodInterval, ...]
    buckets: tuple[IntensityBucket, ...] = ()


def project_lights(snapshot: MonitoringSnapshot, *, now: datetime) -> LightProjection:
    """Project immutable scheduler inputs over the snapshot's UTC half-open range."""
    clock = now.astimezone(UTC)
    times = _transition_times(snapshot, clock)
    photoperiod = _photoperiod_intervals(snapshot, times)
    aggregate = cycle_change_count(snapshot) > DISPLAY_GRID_POINTS
    lights, buckets = (
        zip(
            *(
                _project_light(snapshot, light, times, clock, aggregate)
                for light in snapshot.expected_lights
            ),
            strict=False,
        )
        if snapshot.expected_lights
        else ((), ())
    )
    return LightProjection(
        lights=lights,
        photoperiod=photoperiod,
        buckets=tuple(bucket for group in buckets for bucket in group),
    )


def _project_light(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    times: tuple[datetime, ...],
    now: datetime,
    aggregate: bool,
) -> tuple[LightTimelineSeries, tuple[IntensityBucket, ...]]:
    device_name = str(light["device_name"])
    if aggregate:
        return _aggregate_light(snapshot, light, device_name, now)
    points = tuple(_point(snapshot, light, device_name, instant) for instant in times)
    quality = min((point.provenance.quality for point in points), key=_quality_rank)
    return _timeline(snapshot, device_name, points, quality), ()


def _aggregate_light(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    device_name: str,
    now: datetime,
) -> tuple[LightTimelineSeries, tuple[IntensityBucket, ...]]:
    width = (snapshot.range.end - snapshot.range.start) / DISPLAY_GRID_POINTS
    buckets = tuple(
        _bucket(snapshot, light, instant, min(instant + width, snapshot.range.end))
        for instant in (
            snapshot.range.start + width * index for index in range(DISPLAY_GRID_POINTS)
        )
        if instant < snapshot.range.end
    )
    provenance = TimelineProvenance(
        origin=Origin.PROJECTED, quality=Quality.ESTIMATED, is_aggregated=True
    )
    points = tuple(
        LightTimelinePoint(
            timestamp=bucket.start,
            value=bucket.average,
            nominal_value=bucket.maximum,
            device_name=device_name,
            provenance=provenance,
            aggregation=AggregationMetadata(
                interval_seconds=max(1, round((bucket.end - bucket.start).total_seconds())),
                sample_count=2,
            ),
        )
        for bucket in buckets
    )
    del now
    return _timeline(snapshot, device_name, points, Quality.ESTIMATED, aggregated=True), buckets


def _bucket(
    snapshot: MonitoringSnapshot, light: Mapping[str, object], start: datetime, end: datetime
) -> IntensityBucket:
    first = evaluate_intensity(snapshot, light, start)[0]
    last = evaluate_intensity(snapshot, light, end - timedelta(microseconds=1))[0]
    return IntensityBucket(
        start=start,
        end=end,
        average=(first + last) / 2,
        minimum=min(first, last),
        maximum=max(first, last),
    )


def _timeline(
    snapshot: MonitoringSnapshot,
    device_name: str,
    points: tuple[LightTimelinePoint, ...],
    quality: Quality,
    *,
    aggregated: bool = False,
) -> LightTimelineSeries:
    provenance = TimelineProvenance(
        origin=Origin.PROJECTED, quality=quality, is_aggregated=aggregated
    )
    projection = ProjectionMetadata(
        projection_revision=snapshot.projection_revision,
        anchor_fingerprint=snapshot.anchor_fingerprint,
        anchor_observed_at=snapshot.anchor_observed_at,
        anchor_quality=quality,
        anchor_valid_until=snapshot.anchor_valid_until,
    )
    return LightTimelineSeries(
        name=device_name, provenance=provenance, projection=projection, points=points
    )


def _point(
    snapshot: MonitoringSnapshot, light: Mapping[str, object], device_name: str, instant: datetime
) -> LightTimelinePoint:
    value, nominal, quality = evaluate_intensity(snapshot, light, instant)
    return LightTimelinePoint(
        timestamp=instant,
        value=value,
        nominal_value=nominal,
        device_name=device_name,
        provenance=TimelineProvenance(origin=Origin.PROJECTED, quality=quality),
    )


def _transition_times(snapshot: MonitoringSnapshot, now: datetime) -> tuple[datetime, ...]:
    times = {snapshot.range.start, now}
    for offset in range(-1, (snapshot.range.end.date() - snapshot.range.start.date()).days + 2):
        local_day = snapshot.range.start.astimezone(LOCAL_TZ).date() + timedelta(days=offset)
        for schedule in (snapshot.mode_parameters, *snapshot.light_programs):
            if schedule is not None:
                for value in (
                    _value(schedule, "day_start_time") or _value(schedule, "start_time"),
                    _value(schedule, "night_start_time") or _value(schedule, "end_time"),
                ):
                    if value is not None:
                        times.add(
                            datetime.combine(local_day, _time(value), LOCAL_TZ).astimezone(UTC)
                        )
    return tuple(
        sorted(instant for instant in times if snapshot.range.start <= instant < snapshot.range.end)
    )


def _photoperiod_intervals(
    snapshot: MonitoringSnapshot, times: tuple[datetime, ...]
) -> tuple[PhotoperiodInterval, ...]:
    boundaries = (*times, snapshot.range.end)
    return tuple(
        PhotoperiodInterval(
            start=start,
            end=end,
            phase=phase_at(snapshot, start)[0],
            provenance=TimelineProvenance(
                origin=Origin.PROJECTED, quality=phase_at(snapshot, start)[1]
            ),
        )
        for start, end in zip(boundaries, boundaries[1:], strict=False)
        if start < end
    )


def _value(values: Mapping[str, object] | None, key: str) -> object | None:
    return None if values is None else values.get(key)


def _time(value: object | None) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value or "00:00"))


def _quality_rank(quality: Quality) -> int:
    return {Quality.EXACT: 2, Quality.ESTIMATED: 1, Quality.UNAVAILABLE: 0}[quality]
