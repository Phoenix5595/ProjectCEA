"""Pure, clock-injected climate setpoint projection from one monitoring snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.control.ramp_policy import DEFAULT_RAMP_SKIP_THRESHOLD, RAMP_SKIP_THRESHOLDS
from app.repositories.monitoring_snapshot_types import FrozenRow, MonitoringSnapshot
from app.schemas.monitoring import ClimateTimelinePoint, ClimateTimelineSeries
from app.schemas.monitoring_models import (
    MonitoringWarning,
    Origin,
    ProjectionMetadata,
    Quality,
    TimelineProvenance,
)
from shared.room_light_authority import is_moon_authority_mode

LOCAL_TZ = ZoneInfo("America/Toronto")
_METRICS = ("heating", "cooling", "vpd", "co2")
_FIELDS = {
    "heating": "heating_setpoint",
    "cooling": "cooling_setpoint",
    "vpd": "vpd_setpoint",
    "co2": "co2_setpoint",
}


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
    index = _period_index(snapshot.climate_periods)
    boundaries = _boundaries(snapshot, current_time, index)
    return tuple(
        ClimateTimelineSeries(
            name=metric,
            provenance=_provenance(Quality.ESTIMATED),
            projection=metadata,
            warnings=warnings,
            points=tuple(_points(snapshot, metric, current_time, index, boundaries)),
        )
        for metric in _METRICS
    )


def profile_targets_at(snapshot: MonitoringSnapshot, now: datetime) -> dict[str, float | None]:
    """Exact configured targets of the executing profile at ``now``; gaps stay absent."""
    if snapshot.active_mode is None:
        return {}
    index = _period_index(snapshot.climate_periods)
    period = _period_at(index.get(_mode_identity(snapshot.active_mode), ()), now.astimezone(UTC))
    if period is None:
        return {}
    return {metric: _number(period, _FIELDS[metric]) for metric in _METRICS}


def _points(
    snapshot: MonitoringSnapshot,
    metric: str,
    now: datetime,
    index: Mapping[tuple[int | None, int | None], tuple[FrozenRow, ...]],
    boundaries: tuple[datetime, ...],
) -> list[ClimateTimelinePoint]:
    return [_point(snapshot, metric, instant, now, index) for instant in boundaries]


def _point(
    snapshot: MonitoringSnapshot,
    metric: str,
    instant: datetime,
    now: datetime,
    index: Mapping[tuple[int | None, int | None], tuple[FrozenRow, ...]],
) -> ClimateTimelinePoint:
    mode = mode_identity_at(snapshot, instant)
    periods = index.get(mode, ()) if mode is not None else ()
    period = _period_at(periods, instant)
    target = _number(period, _FIELDS[metric]) if period is not None else None
    anchor = _compatible_anchor(snapshot.ramp_anchors, metric, mode)
    live = _anchor_value(anchor, instant)
    if live is not None:
        value = live
        quality = snapshot.anchor_quality if instant == now else Quality.ESTIMATED
        progress = _progress(anchor, instant) if instant == now else None
    elif period is None or target is None:
        value, quality, progress = None, Quality.UNAVAILABLE, None
    else:
        value = _configured_value(periods, period, metric, instant)
        if _anchor_holds_target(anchor, periods, period, metric, instant):
            value = _number(anchor, "target_value")
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
        mode=None if mode is None or mode[0] is None else str(mode[0]),
        provenance=_provenance(quality),
    )


def _boundaries(
    snapshot: MonitoringSnapshot,
    now: datetime,
    index: Mapping[tuple[int | None, int | None], tuple[FrozenRow, ...]],
) -> tuple[datetime, ...]:
    """Shared boundaries from the profiles authoritative in this window's local slices."""
    start, end = snapshot.range.start.astimezone(UTC), snapshot.range.end.astimezone(UTC)
    boundaries = {start}
    if start < now < end:
        boundaries.add(now)
    boundaries.update(_local_midnights(start, end))
    for identity in _authoritative_identities(snapshot, start, end):
        for period in index.get(identity, ()):
            boundaries.update(_period_interval_instants(period, start, end))
    for anchor in snapshot.ramp_anchors:
        boundaries.update(_anchor_instants(anchor, start, end))
    return tuple(sorted(boundaries))


def _authoritative_identities(
    snapshot: MonitoringSnapshot, start: datetime, end: datetime
) -> tuple[tuple[int | None, int | None], ...]:
    """Identities that can govern a Toronto local-day slice of the projection window."""
    if snapshot.active_mode is None:
        return ()
    active = _mode_identity(snapshot.active_mode)
    if snapshot.location != "Flower Room":
        return (active,)
    identities = {active}
    day = start.astimezone(LOCAL_TZ).date()
    final_day = end.astimezone(LOCAL_TZ).date()
    while day <= final_day:
        identity = mode_identity_at(
            snapshot, datetime.combine(day, time(12, 0), LOCAL_TZ).astimezone(UTC)
        )
        if identity is not None:
            identities.add(identity)
        day += timedelta(days=1)
    return tuple(sorted(identities, key=lambda item: (item[0] or 0, item[1] or 0)))


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


def _period_interval_instants(
    period: FrozenRow, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """Start, ramp end, end, and interior one-minute ramp ticks of each occurrence."""
    clock = _clock(period.get("start_time"))
    span = _span_minutes(clock, _clock(period.get("end_time")))
    minutes = _number(period, "ramp_minutes") or 0.0
    day = start.astimezone(LOCAL_TZ).date() - timedelta(days=1)
    final_day = end.astimezone(LOCAL_TZ).date()
    instants: list[datetime] = []
    while day <= final_day:
        start_instant = datetime.combine(day, clock, LOCAL_TZ).astimezone(UTC)
        for instant in (
            start_instant,
            start_instant + timedelta(minutes=span),
            start_instant + timedelta(minutes=minutes),
        ):
            if start < instant < end:
                instants.append(instant)
        if 0 < minutes < span:
            instants.extend(
                instant
                for offset in range(1, int(minutes))
                if start < (instant := start_instant + timedelta(minutes=offset)) < end
            )
        day += timedelta(days=1)
    return tuple(instants)


def _anchor_instants(anchor: FrozenRow, start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Live compatible ramp-anchor end plus interior one-minute ticks of its interval."""
    anchor_start = anchor.get("start_time")
    duration = _number(anchor, "duration_minutes")
    if not isinstance(anchor_start, datetime) or duration is None:
        return ()
    anchor_start = anchor_start.astimezone(UTC)
    anchor_end = anchor_start + timedelta(minutes=duration)
    instants = [instant for instant in (anchor_start, anchor_end) if start < instant < end]
    if duration > 0:
        tick = _next_minute(max(start, anchor_start))
        while tick < anchor_end:
            if start < tick < end:
                instants.append(tick)
            tick += timedelta(minutes=1)
    return tuple(instants)


def _next_minute(instant: datetime) -> datetime:
    instant = instant.astimezone(UTC)
    if instant.second or instant.microsecond:
        instant = instant.replace(second=0, microsecond=0) + timedelta(minutes=1)
    else:
        instant = instant + timedelta(minutes=1)
    return instant


def _span_minutes(start: time, end: time) -> int:
    """Minutes one occurrence covers: equal start/end is all-day, lower end is overnight."""
    start_mins, end_mins = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    if start_mins == end_mins:
        return 1440
    return end_mins - start_mins if end_mins > start_mins else 1440 + end_mins - start_mins


def _period_index(
    periods: tuple[FrozenRow, ...],
) -> dict[tuple[int | None, int | None], tuple[FrozenRow, ...]]:
    """Group period rows by exact (mode_id, submode_id) profile identity once."""
    grouped: dict[tuple[int | None, int | None], list[FrozenRow]] = {}
    for row in periods:
        mode_id = _int(row, "mode_id")
        if mode_id is None:
            # Unscoped/NULL-mode rows never substitute a known profile identity.
            continue
        grouped.setdefault((mode_id, _int(row, "submode_id")), []).append(row)
    return {
        identity: tuple(sorted(rows, key=lambda row: _clock(row.get("start_time"))))
        for identity, rows in grouped.items()
    }


def mode_identity_at(
    snapshot: MonitoringSnapshot, instant: datetime
) -> tuple[int | None, int | None] | None:
    """The (mode_id, submode_id) profile identity this snapshot applies at ``instant``.

    Flower-room calendar transitions only activate destinations with persisted
    mode parameters; a finished plan falls back to vegetative growth.
    """
    if snapshot.active_mode is None:
        return None
    active = _mode_identity(snapshot.active_mode)
    if snapshot.location != "Flower Room":
        return active
    local_date = instant.astimezone(LOCAL_TZ).date()
    event = _event_for(snapshot.calendar_events, local_date)
    if event is not None:
        target = (_int(event, "target_mode_id"), _int(event, "target_submode_id"))
        if (
            target[0] is not None
            and target != active
            and event.get("destination_configured") is True
        ):
            # Only a destination with persisted mode parameters is activation
            # authority; without them the calendar cannot activate it.
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


def known_moon_destination_day(snapshot: MonitoringSnapshot, day: date) -> bool:
    """Whether this local day switches Flower room lighting authority to a moon mode.

    Moon-authority destinations (drying/sleep) carry complete frozen light
    authority from the mode name alone, so their days stay forecastable;
    every other future identity has no frozen light inputs.
    """
    if snapshot.active_mode is None or snapshot.location != "Flower Room":
        return False
    event = _event_for(snapshot.calendar_events, day)
    if event is None:
        return False
    target = (_int(event, "target_mode_id"), _int(event, "target_submode_id"))
    if (
        target[0] is None
        or target == _mode_identity(snapshot.active_mode)
        or event.get("destination_configured") is not True
    ):
        return False
    name = event.get("target_mode_name")
    return isinstance(name, str) and is_moon_authority_mode(name)


def _event_for(events: tuple[FrozenRow, ...], day: date) -> FrozenRow | None:
    """Auto-transition event with the highest phase order covering ``day``.

    Matches ``CalendarRepository.get_active_flower_phase_event``: non-auto
    events are excluded before the phase-order winner is chosen.
    """
    matching = tuple(
        event
        for event in events
        if _date(event, "start_date") <= day <= _date(event, "end_date", "start_date")
    )
    auto = tuple(event for event in matching if _auto(event))
    return max(
        auto,
        key=lambda event: (_int(event, "phase_order") or 0, _int(event, "id") or 0),
        default=None,
    )


def _period_at(periods: tuple[FrozenRow, ...], instant: datetime) -> FrozenRow | None:
    """The period governing ``instant`` with repository half-open semantics.

    Equal start/end is all-day; an end before start wraps overnight; uncovered
    minutes are a gap and return None instead of holding the last-start row.
    """
    if not periods:
        return None
    ref = instant.astimezone(LOCAL_TZ).time().replace(tzinfo=None)
    ref_mins = ref.hour * 60 + ref.minute
    for period in sorted(periods, key=lambda period: _clock(period.get("start_time"))):
        start_mins, end_mins = _minutes(period.get("start_time")), _minutes(period.get("end_time"))
        if start_mins == end_mins:
            return period
        if start_mins < end_mins:
            if start_mins <= ref_mins < end_mins:
                return period
        elif ref_mins >= start_mins or ref_mins < end_mins:
            return period
    return None


def _minutes(value: str | time | None) -> int:
    clock = _clock(value)
    return clock.hour * 60 + clock.minute


def _period_start_instant(period: FrozenRow, instant: datetime) -> datetime:
    """The most recent local occurrence of the period start, previous day overnight."""
    local = instant.astimezone(LOCAL_TZ)
    start = _clock(period.get("start_time"))
    clock = local.time().replace(tzinfo=None)
    day = local.date() if clock >= start else local.date() - timedelta(days=1)
    return datetime.combine(day, start, LOCAL_TZ).astimezone(UTC)


def _configured_value(
    periods: tuple[FrozenRow, ...], period: FrozenRow, metric: str, instant: datetime
) -> float | None:
    target = _number(period, _FIELDS[metric])
    if target is None:
        return None
    ordered = tuple(sorted(periods, key=lambda item: _clock(item.get("start_time"))))
    position = ordered.index(period)
    source = _number(ordered[position - 1], _FIELDS[metric])
    minutes = _number(period, "ramp_minutes") or 0.0
    threshold = RAMP_SKIP_THRESHOLDS.get(metric, DEFAULT_RAMP_SKIP_THRESHOLD)
    if source is None or minutes <= 0 or abs(target - source) < threshold:
        return target
    start = _period_start_instant(period, instant)
    if instant < start or instant >= start + timedelta(minutes=minutes):
        return target
    return source + (target - source) * (instant - start).total_seconds() / (minutes * 60)


def _compatible_anchor(
    anchors: tuple[FrozenRow, ...], metric: str, mode: tuple[int | None, int | None] | None
) -> FrozenRow | None:
    """The ramp anchor of one metric whose profile identity governs ``instant``.

    Anchors never leak into another profile, a calendar destination, or a draft.
    """
    if mode is None:
        return None
    return next(
        (
            item
            for item in anchors
            if item.get("setpoint_type", item.get("metric")) == metric
            and _anchor_identity(item) == mode
        ),
        None,
    )


def _anchor_identity(item: Mapping[str, object]) -> tuple[int | None, int | None]:
    return (_int(item, "mode_id"), _int(item, "submode_id"))


def _anchor_value(anchor: FrozenRow | None, instant: datetime) -> float | None:
    """The anchor-interpolated value while the live ramp actually governs."""
    if anchor is None:
        return None
    start = anchor.get("start_time")
    duration = _number(anchor, "duration_minutes")
    if not isinstance(start, datetime) or duration is None:
        return None
    began = start.astimezone(UTC)
    if not began <= instant.astimezone(UTC) < began + timedelta(minutes=duration):
        return None
    return _interpolate(anchor, instant)


def _anchor_holds_target(
    anchor: FrozenRow | None,
    periods: tuple[FrozenRow, ...],
    period: FrozenRow,
    metric: str,
    instant: datetime,
) -> bool:
    """Whether an elapsed compatible anchor pins the executing period's ramp window.

    A no-ramp current period holds its actual nominal instead of inventing a
    scheduled ramp; after an in-flight ramp ends early its nominal holds until
    the next period boundary. The hold is scoped to the anchor's own local-day
    occurrence: a completed anchor never governs a later occurrence's ramp.
    """
    if anchor is None:
        return False
    target = _number(anchor, "target_value")
    if target is None or target != _number(period, _FIELDS[metric]):
        return False
    if _anchor_value(anchor, instant) is not None:
        return False
    anchor_start = anchor.get("start_time")
    if not isinstance(anchor_start, datetime) or anchor_start.astimezone(UTC) > instant:
        return False
    anchor_period = _period_at(periods, anchor_start)
    if anchor_period is None or anchor_period != period:
        return False
    clock = _clock(anchor_period.get("start_time"))
    occurrence_end = _period_start_instant(anchor_period, anchor_start) + timedelta(
        minutes=_span_minutes(clock, _clock(anchor_period.get("end_time")))
    )
    if instant >= occurrence_end:
        return False
    minutes = _number(period, "ramp_minutes") or 0.0
    if minutes <= 0:
        return False
    start = _period_start_instant(period, instant)
    return start <= instant < start + timedelta(minutes=minutes)


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
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
    event = next(
        (
            event
            for event in events
            if event.get("target_mode_name") == "veg"
            and event.get("destination_configured") is True
        ),
        None,
    )
    return None if event is None else _int(event, "target_mode_id")
