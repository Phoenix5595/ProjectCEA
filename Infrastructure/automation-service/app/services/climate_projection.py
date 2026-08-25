"""Pure, clock-injected climate setpoint projection from one monitoring snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.repositories.monitoring_snapshot_types import FrozenRow, MonitoringSnapshot
from app.schemas.monitoring import ClimateTimelinePoint, ClimateTimelineSeries
from app.schemas.monitoring_models import (
    MonitoringWarning,
    Origin,
    ProjectionMetadata,
    Quality,
    TimelineProvenance,
)

LOCAL_TZ = ZoneInfo("America/Toronto")
_METRICS = ("heating", "cooling", "vpd", "co2")
_FIELDS = {
    "heating": "heating_setpoint",
    "cooling": "cooling_setpoint",
    "vpd": "vpd_setpoint",
    "co2": "co2_setpoint",
}
_THRESHOLDS = {"heating": 0.1, "cooling": 0.1, "vpd": 0.01, "co2": 10.0, "humidity": 1.0}


def project_climate_timelines(
    snapshot: MonitoringSnapshot, now: Callable[[], datetime]
) -> tuple[ClimateTimelineSeries, ...]:
    """Project every configured climate endpoint over ``snapshot.range`` without I/O."""
    current_time = now().astimezone(UTC)
    metadata = ProjectionMetadata(
        projection_revision=snapshot.projection_revision,
        anchor_fingerprint=snapshot.anchor_fingerprint,
        anchor_observed_at=snapshot.anchor_observed_at,
        anchor_quality=snapshot.anchor_quality,
        anchor_valid_until=snapshot.anchor_valid_until,
    )
    warnings = _warnings(snapshot)
    return tuple(
        ClimateTimelineSeries(
            name=metric,
            provenance=_provenance(Quality.ESTIMATED),
            projection=metadata,
            warnings=warnings,
            points=tuple(_points(snapshot, metric, current_time)),
        )
        for metric in _METRICS
    )


def _points(snapshot: MonitoringSnapshot, metric: str, now: datetime) -> list[ClimateTimelinePoint]:
    boundaries = _boundaries(snapshot, now)
    return [_point(snapshot, metric, instant, now) for instant in boundaries]


def _point(
    snapshot: MonitoringSnapshot, metric: str, instant: datetime, now: datetime
) -> ClimateTimelinePoint:
    mode = _mode_at(snapshot, instant)
    period = _period_at(snapshot.climate_periods, instant)
    configured_mode = snapshot.active_mode and mode == _mode_identity(snapshot.active_mode)
    target = _number(period, _FIELDS[metric]) if period is not None else None
    anchor = _active_anchor(snapshot.ramp_anchors, metric, now)
    if instant == now and anchor is not None:
        value = _interpolate(anchor, now)
        quality = snapshot.anchor_quality
        progress = _progress(anchor, now)
    elif period is None or not configured_mode or target is None:
        value, quality, progress = None, Quality.UNAVAILABLE, None
    else:
        value = _configured_value(snapshot.climate_periods, period, metric, instant)
        quality = (
            Quality.EXACT
            if instant != now or snapshot.anchor_quality is Quality.EXACT
            else Quality.ESTIMATED
        )
        progress = None
    return ClimateTimelinePoint(
        timestamp=instant,
        metric=metric,
        value=value,
        nominal_value=target,
        ramp_progress=progress,
        mode=None if mode is None else str(mode[0]),
        provenance=_provenance(quality),
    )


def _boundaries(snapshot: MonitoringSnapshot, now: datetime) -> tuple[datetime, ...]:
    start, end = snapshot.range.start.astimezone(UTC), snapshot.range.end.astimezone(UTC)
    boundaries = {start}
    if start < now < end:
        boundaries.add(now)
    for instant in _local_midnights(start, end):
        boundaries.add(instant)
    for period in snapshot.climate_periods:
        for instant in _period_instants(period, start, end):
            boundaries.add(instant)
    return tuple(sorted(boundaries))


def _local_midnights(start: datetime, end: datetime) -> tuple[datetime, ...]:
    local_day = start.astimezone(LOCAL_TZ).date()
    final_day = end.astimezone(LOCAL_TZ).date()
    instants: list[datetime] = []
    while local_day <= final_day:
        instant = datetime.combine(local_day, time.min, LOCAL_TZ).astimezone(UTC)
        if start < instant < end:
            instants.append(instant)
        local_day += timedelta(days=1)
    return tuple(instants)


def _period_instants(period: FrozenRow, start: datetime, end: datetime) -> tuple[datetime, ...]:
    clock = _clock(period.get("start_time"))
    local_day = start.astimezone(LOCAL_TZ).date() - timedelta(days=1)
    final_day = end.astimezone(LOCAL_TZ).date()
    instants: list[datetime] = []
    while local_day <= final_day:
        instant = datetime.combine(local_day, clock, LOCAL_TZ).astimezone(UTC)
        if start < instant < end:
            instants.append(instant)
        local_day += timedelta(days=1)
    return tuple(instants)


def _mode_at(
    snapshot: MonitoringSnapshot, instant: datetime
) -> tuple[int | None, int | None] | None:
    if snapshot.active_mode is None:
        return None
    active = _mode_identity(snapshot.active_mode)
    if snapshot.location != "Flower Room":
        return active
    local_date = instant.astimezone(LOCAL_TZ).date()
    event = _event_for(snapshot.calendar_events, local_date)
    if event is not None and _auto(event):
        target = (_int(event, "target_mode_id"), _int(event, "target_submode_id"))
        if target[0] is not None and target != active:
            return target
    last_plan_end = _last_plan_end(snapshot.calendar_events)
    if (
        event is None
        and last_plan_end is not None
        and local_date > last_plan_end
        and _mode_name(snapshot.active_mode) == "drying"
    ):
        return (_veg_mode_id(snapshot.calendar_events), None)
    return active


def _event_for(events: tuple[FrozenRow, ...], day: date) -> FrozenRow | None:
    matching = tuple(
        event
        for event in events
        if _date(event, "start_date") <= day <= _date(event, "end_date", "start_date")
    )
    return max(
        matching,
        key=lambda event: (_int(event, "phase_order") or 0, _int(event, "id") or 0),
        default=None,
    )


def _period_at(periods: tuple[FrozenRow, ...], instant: datetime) -> FrozenRow | None:
    if not periods:
        return None
    clock = instant.astimezone(LOCAL_TZ).time().replace(tzinfo=None)
    ordered = tuple(sorted(periods, key=lambda period: _clock(period.get("start_time"))))
    return next(
        (period for period in reversed(ordered) if _clock(period.get("start_time")) <= clock),
        ordered[-1],
    )


def _configured_value(
    periods: tuple[FrozenRow, ...], period: FrozenRow, metric: str, instant: datetime
) -> float | None:
    target = _number(period, _FIELDS[metric])
    if target is None:
        return None
    ordered = tuple(sorted(periods, key=lambda item: _clock(item.get("start_time"))))
    index = ordered.index(period)
    previous = ordered[index - 1]
    source = _number(previous, _FIELDS[metric])
    minutes = _number(period, "ramp_minutes") or 0.0
    if source is None or minutes <= 0 or abs(target - source) < _THRESHOLDS[metric]:
        return target
    start = datetime.combine(
        instant.astimezone(LOCAL_TZ).date(), _clock(period.get("start_time")), LOCAL_TZ
    ).astimezone(UTC)
    if instant < start or instant >= start + timedelta(minutes=minutes):
        return target
    return source + (target - source) * (instant - start).total_seconds() / (minutes * 60)


def _active_anchor(anchors: tuple[FrozenRow, ...], metric: str, now: datetime) -> FrozenRow | None:
    anchor = next(
        (item for item in anchors if item.get("setpoint_type", item.get("metric")) == metric), None
    )
    if anchor is None:
        return None
    start = anchor.get("start_time")
    duration = _number(anchor, "duration_minutes")
    if not isinstance(start, datetime) or duration is None:
        return None
    return (
        anchor
        if start.astimezone(UTC) <= now < start.astimezone(UTC) + timedelta(minutes=duration)
        else None
    )


def _interpolate(anchor: FrozenRow, now: datetime) -> float:
    start, target = _number(anchor, "start_value"), _number(anchor, "target_value")
    duration, started = _number(anchor, "duration_minutes"), anchor["start_time"]
    assert (
        start is not None
        and target is not None
        and duration is not None
        and isinstance(started, datetime)
    )
    progress = min((now - started.astimezone(UTC)).total_seconds() / (duration * 60), 1.0)
    return start + (target - start) * progress


def _progress(anchor: FrozenRow, now: datetime) -> float:
    duration, started = _number(anchor, "duration_minutes"), anchor["start_time"]
    assert duration is not None and isinstance(started, datetime)
    return min((now - started.astimezone(UTC)).total_seconds() / (duration * 60), 1.0)


def _warnings(snapshot: MonitoringSnapshot) -> tuple[MonitoringWarning, ...]:
    if snapshot.location == "Flower Room":
        return (
            MonitoringWarning(
                code="calendar_scheduler_availability",
                detail="Calendar transitions are applied by a 60-second runtime scheduler.",
            ),
        )
    return ()


def _provenance(quality: Quality) -> TimelineProvenance:
    return TimelineProvenance(origin=Origin.PROJECTED, quality=quality, is_aggregated=False)


def _clock(value: str | time | None) -> time:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    return time.fromisoformat(value or "00:00")


def _number(row: Mapping[str, object], field: str) -> float | None:
    value = row.get(field)
    return float(value) if isinstance(value, int | float) else None


def _int(row: Mapping[str, object], field: str) -> int | None:
    value = row.get(field)
    return value if isinstance(value, int) else None


def _date(row: Mapping[str, object], field: str, fallback: str | None = None) -> date:
    value = row.get(field) or (row.get(fallback) if fallback else None)
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _mode_identity(row: Mapping[str, object]) -> tuple[int | None, int | None]:
    return (_int(row, "mode_id"), _int(row, "submode_id"))


def _mode_name(row: Mapping[str, object]) -> str | None:
    value = row.get("mode_name")
    return value if isinstance(value, str) else None


def _auto(event: Mapping[str, object]) -> bool:
    value = event.get("auto_mode_transition")
    return value is not False


def _last_plan_end(events: tuple[FrozenRow, ...]) -> date | None:
    return max((_date(event, "end_date", "start_date") for event in events), default=None)


def _veg_mode_id(events: tuple[FrozenRow, ...]) -> int | None:
    event = next((event for event in events if event.get("target_mode_name") == "veg"), None)
    return None if event is None else _int(event, "target_mode_id")
