"""Stateless scheduler-parity light schedule evaluation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import floor
from typing import Final
from zoneinfo import ZoneInfo

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.monitoring_models import Phase, Quality
from shared.room_light_authority import is_moon_authority_mode

LOCAL_TZ: Final = ZoneInfo("America/Toronto")
MINIMUM_LIGHT_INTENSITY: Final = 10.0
# Gate-boundary arithmetic tolerance in seconds; seconds-scale cycle formulas
# and microsecond sampling windows stay far below one cycle second.
_GATE_EPSILON_SECONDS: Final = 1e-9


@dataclass(frozen=True, slots=True)
class CycleWindow:
    """One wrapped light-program occurrence window that can gate on/off cycles."""

    start: datetime
    end: datetime
    on_seconds: float
    off_seconds: float


@dataclass(frozen=True, slots=True)
class SegmentBoundaryPlan:
    """Ordered canonical boundaries plus every clipped cycle-occurrence window."""

    boundaries: tuple[datetime, ...]
    cycle_windows: tuple[CycleWindow, ...]


def evaluate_intensity(
    snapshot: MonitoringSnapshot, light: Mapping[str, object], instant: datetime
) -> tuple[float, float, Quality]:
    """Return effective intensity, nominal target, and confidence without side effects."""
    if is_moon_authority_mode(_value(snapshot.active_mode, "mode_name")):
        return 0.0, 0.0, Quality.EXACT
    program = _matching(snapshot, light, instant)
    if program is not None:
        target = _number(program, "target_intensity", 0)
        return _cycle(program, instant, target), target, Quality.EXACT
    phase, quality = phase_at(snapshot, instant)
    if phase is Phase.MOON:
        return 0.0, 0.0, quality
    target, target_quality = _target(snapshot, light)
    if snapshot.mode_parameters is None:
        return MINIMUM_LIGHT_INTENSITY, MINIMUM_LIGHT_INTENSITY, Quality.UNAVAILABLE
    anchor = _scheduler_anchor(snapshot, str(light["device_name"]))
    return (
        _ramp(snapshot.mode_parameters, instant, target, anchor),
        target,
        min(
            (quality, target_quality, _ramp_quality(snapshot, str(light["device_name"]), instant)),
            key=_quality_rank,
        ),
    )


def phase_at(snapshot: MonitoringSnapshot, instant: datetime) -> tuple[Phase, Quality]:
    """Resolve room SUN/MOON state, with the visualization-only SUN fallback."""
    if snapshot.mode_parameters is None:
        return Phase.SUN, Quality.UNAVAILABLE
    day, night = _schedule_times(snapshot.mode_parameters)
    current = instant.astimezone(LOCAL_TZ).time()
    in_sun = current >= day or current < night if day > night else day <= current < night
    return (Phase.SUN if in_sun else Phase.MOON), Quality.EXACT


def cycle_change_count(snapshot: MonitoringSnapshot) -> int:
    """Count possible cycle transitions for bounded display rendering."""
    duration = (snapshot.range.end - snapshot.range.start).total_seconds()
    return sum(
        int(
            duration
            / max(1, _number(p, "cycle_on_seconds", 0) + _number(p, "cycle_off_seconds", 0))
        )
        * 2
        for p in snapshot.light_programs
        if bool(p.get("cycle_enabled"))
    )


def segment_boundary_plan(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    start: datetime,
    end: datetime,
) -> SegmentBoundaryPlan:
    """Ordered boundaries and cycle windows partitioning ``[start, end)``.

    Every instant where the scheduler rule can change value or shape is a
    boundary: local midnights (program ownership rolls), photoperiod day
    windows and their ramp ends, and each program occurrence window. Cycle
    gate instants are reported as windows so callers can budget them instead of
    materializing an unbounded list.
    """
    if is_moon_authority_mode(_value(snapshot.active_mode, "mode_name")):
        return SegmentBoundaryPlan((start.astimezone(UTC), end.astimezone(UTC)), ())
    instants: set[datetime] = {start.astimezone(UTC), end.astimezone(UTC)}
    cycles: list[CycleWindow] = []
    device_id = light.get("device_id")
    mode_id = _int_or_none(_value(snapshot.active_mode, "mode_id"))
    local_day = start.astimezone(LOCAL_TZ).date() - timedelta(days=1)
    final_local_day = end.astimezone(LOCAL_TZ).date()
    while local_day <= final_local_day:
        _add_boundary(instants, datetime.combine(local_day, time.min, LOCAL_TZ), start, end)
        if snapshot.mode_parameters is not None:
            parameters = snapshot.mode_parameters
            day_time, night_time = _schedule_times(parameters)
            day_start, day_end = _window(
                datetime.combine(local_day, day_time, LOCAL_TZ), day_time, night_time
            )
            up = _number(parameters, "light_ramp_up_minutes", _number(parameters, "ramp_up", 0))
            down = _number(
                parameters, "light_ramp_down_minutes", _number(parameters, "ramp_down", 0)
            )
            for instant in (
                day_start,
                day_start + timedelta(minutes=up) if up > 0 else day_start,
                day_end - timedelta(minutes=down) if down > 0 else day_end,
                day_end,
            ):
                _add_boundary(instants, instant, start, end)
        for program in snapshot.light_programs:
            if _program_mismatch(program, device_id, mode_id):
                continue
            occurrence = _occurrence_window(program, local_day)
            if occurrence is None:
                continue
            occurrence_start, occurrence_end = occurrence
            _add_boundary(instants, occurrence_start, start, end)
            _add_boundary(instants, occurrence_end, start, end)
            if bool(program.get("cycle_enabled")):
                on = _number(program, "cycle_on_seconds", 0)
                off = _number(program, "cycle_off_seconds", 0)
                if on > 0 and off > 0:
                    # Both phases must alternate; ``off == 0`` degrades to a
                    # constant target hold with no gate instants at all.
                    cycles.append(
                        CycleWindow(
                            start=occurrence_start,
                            end=occurrence_end,
                            on_seconds=on,
                            off_seconds=off,
                        )
                    )
        local_day += timedelta(days=1)
    return SegmentBoundaryPlan(tuple(sorted(instants)), tuple(cycles))


def phase_boundary_plan(
    snapshot: MonitoringSnapshot, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """Ordered boundaries where the room SUN/MOON phase can change."""
    instants: set[datetime] = {start.astimezone(UTC), end.astimezone(UTC)}
    local_day = start.astimezone(LOCAL_TZ).date() - timedelta(days=1)
    final_local_day = end.astimezone(LOCAL_TZ).date()
    while local_day <= final_local_day:
        _add_boundary(instants, datetime.combine(local_day, time.min, LOCAL_TZ), start, end)
        if snapshot.mode_parameters is not None:
            day_time, night_time = _schedule_times(snapshot.mode_parameters)
            for offset in (0, 1):
                anchor = local_day + timedelta(days=offset)
                _add_boundary(instants, datetime.combine(anchor, day_time, LOCAL_TZ), start, end)
                _add_boundary(instants, datetime.combine(anchor, night_time, LOCAL_TZ), start, end)
        local_day += timedelta(days=1)
    return tuple(sorted(instants))


def matched_program(
    snapshot: MonitoringSnapshot, light: Mapping[str, object], instant: datetime
) -> Mapping[str, object] | None:
    """The scheduler-parity program matching one light at ``instant``."""
    return _matching(snapshot, light, instant)


def cycle_gate_count(window: CycleWindow, *, start: datetime, end: datetime) -> int:
    """Gate instants strictly inside ``[start, end)`` for one cycle window, in O(1).

    Mirrors the ``_cycle`` modulo formula exactly: flips at
    ``start + k * period`` and ``start + k * period + on`` for ``k >= 0``. An
    interior flip exactly on the clipped begin edge is counted (the value
    changes at that boundary); the end edge is owned by the following interval.
    """
    span_pair = _clipped_span(window, start, end)
    if span_pair is None:
        return 0
    begin, finish = span_pair
    period = window.on_seconds + window.off_seconds
    if window.on_seconds <= 0 or window.off_seconds <= 0 or period <= 0:
        # A missing phase never alternates, so no gate can change the value.
        return 0
    prefix = (begin - window.start).total_seconds()
    span = (finish - window.start).total_seconds()
    # Strict interior parity: gates sitting exactly on the end edge belong to
    # the next interval's boundary and are already occurrence boundaries.
    return (
        _periodic_count(span, period)
        - _periodic_count(prefix, period)
        + _periodic_count(span - window.on_seconds, period)
        - _periodic_count(prefix - window.on_seconds, period)
    )


def cycle_gates(window: CycleWindow, *, start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Materialize the ordered cycle gate instants inside ``[start, end)``.

    Callers budget with :func:`cycle_gate_count` first so the materialized list
    stays bounded by the accepted forecast segment budget.
    """
    span_pair = _clipped_span(window, start, end)
    if span_pair is None:
        return ()
    begin, finish = span_pair
    period = window.on_seconds + window.off_seconds
    if window.on_seconds <= 0 or window.off_seconds <= 0 or period <= 0:
        # A missing phase never alternates, so no gate can change the value.
        return ()
    prefix = (begin - window.start).total_seconds()
    span = (finish - window.start).total_seconds()
    # The modulo pattern alternates a ``target`` hold for ``on`` seconds and an
    # ``off`` hold; enumerate both families with chronological cursors so a
    # degenerate zero-length phase cannot stall the alternation.
    hold_cursor = _first_k(prefix, period, 0.0)
    off_cursor = _first_k(prefix, period, window.on_seconds)
    gates: list[datetime] = []
    while True:
        hold = hold_cursor * period
        off = off_cursor * period + window.on_seconds
        hold_live = hold < span - _GATE_EPSILON_SECONDS
        off_live = off < span - _GATE_EPSILON_SECONDS
        if not hold_live and not off_live:
            break
        if hold_live and (not off_live or hold < off):
            hold_cursor += 1
            candidate = hold
        else:
            off_cursor += 1
            candidate = off
        if candidate > prefix - _GATE_EPSILON_SECONDS:
            gates.append(window.start + timedelta(seconds=candidate))
    return tuple(gates)


def _ramp(
    values: Mapping[str, object],
    instant: datetime,
    target: float,
    anchor: Mapping[str, object] | None,
) -> float:
    day, night = _schedule_times(values)
    local = instant.astimezone(LOCAL_TZ)
    start, end = _window(local, day, night)
    since, remaining = (local - start).total_seconds() / 60, (end - local).total_seconds() / 60
    up = _number(values, "light_ramp_up_minutes", _number(values, "ramp_up", 0))
    down = _number(values, "light_ramp_down_minutes", _number(values, "ramp_down", 0))
    if up > 0 and since < up:
        if (
            seeded := _seeded_value(anchor, instant, start + timedelta(minutes=up), target)
        ) is not None:
            return seeded
        return MINIMUM_LIGHT_INTENSITY + (target - MINIMUM_LIGHT_INTENSITY) * max(0, since / up)
    if down > 0 and remaining < down:
        minimum = min(MINIMUM_LIGHT_INTENSITY, target)
        if (seeded := _seeded_value(anchor, instant, end, minimum)) is not None:
            return seeded
        return target + (minimum - target) * max(0, (down - remaining) / down)
    return max(0.0, min(100.0, target))


def _scheduler_anchor(
    snapshot: MonitoringSnapshot, device_name: str
) -> Mapping[str, object] | None:
    anchor = next(
        (
            row
            for row in snapshot.effective_setpoint_predecessors
            if row.get("device_name") == device_name
        ),
        None,
    )
    intensity = (
        None
        if anchor is None
        else anchor.get("effective_light_intensity", anchor.get("effective_intensity"))
    )
    if (
        anchor is None
        or anchor.get("authority") not in {"AUTO", "auto", "scheduler"}
        or anchor.get("runtime_snapshot_identity") != snapshot.runtime_snapshot_version
        or not isinstance(anchor.get("timestamp"), datetime)
        or not isinstance(intensity, int | float)
    ):
        return None
    return anchor


def _seeded_value(
    anchor: Mapping[str, object] | None, instant: datetime, end: datetime, target: float
) -> float | None:
    if anchor is None:
        return None
    started_raw = anchor.get("timestamp")
    if not isinstance(started_raw, datetime):
        return None
    started = started_raw.astimezone(LOCAL_TZ)
    initial = anchor.get("effective_light_intensity", anchor.get("effective_intensity"))
    if not isinstance(initial, int | float) or not started <= instant <= end or end <= started:
        return None
    progress = (instant - started).total_seconds() / (end - started).total_seconds()
    return float(initial) + (target - float(initial)) * progress


def _matching(
    snapshot: MonitoringSnapshot, light: Mapping[str, object], instant: datetime
) -> Mapping[str, object] | None:
    local, device_id, mode_id = (
        instant.astimezone(LOCAL_TZ),
        light.get("device_id"),
        _value(snapshot.active_mode, "mode_id"),
    )
    matches = tuple(
        p
        for p in snapshot.light_programs
        if bool(p.get("enabled", True))
        and (p.get("device_id") is None or device_id is None or p.get("device_id") == device_id)
        and (p.get("mode_id") is None or mode_id is None or p.get("mode_id") == mode_id)
        and (p.get("day_of_week") is None or p.get("day_of_week") == local.weekday())
        and _in_window(local.time(), *_program_times(p))
    )
    return (
        min(
            matches, key=lambda p: (-_number(p, "priority", 0), p.get("created_at") or datetime.min)
        )
        if matches
        else None
    )


def _cycle(program: Mapping[str, object], instant: datetime, target: float) -> float:
    if not bool(program.get("cycle_enabled")):
        return target
    on, off = _number(program, "cycle_on_seconds", 0), _number(program, "cycle_off_seconds", 0)
    if on <= 0 or on + off <= 0:
        return 0.0
    start, _ = _window(instant.astimezone(LOCAL_TZ), *_program_times(program))
    return (
        target if (instant.astimezone(LOCAL_TZ) - start).total_seconds() % (on + off) < on else 0.0
    )


def _ramp_quality(snapshot: MonitoringSnapshot, device_name: str, instant: datetime) -> Quality:
    if not _is_ramp(snapshot, instant):
        return Quality.EXACT
    anchor = next(
        (
            row
            for row in snapshot.effective_setpoint_predecessors
            if row.get("device_name") == device_name
        ),
        None,
    )
    if anchor is None or anchor.get("authority") not in {"AUTO", "auto", "scheduler"}:
        return Quality.ESTIMATED
    return (
        Quality.EXACT
        if anchor.get("runtime_snapshot_identity") == snapshot.runtime_snapshot_version
        and isinstance(anchor.get("timestamp"), datetime)
        else Quality.ESTIMATED
    )


def _is_ramp(snapshot: MonitoringSnapshot, instant: datetime) -> bool:
    if snapshot.mode_parameters is None or phase_at(snapshot, instant)[0] is Phase.MOON:
        return False
    day, night = _schedule_times(snapshot.mode_parameters)
    start, end = _window(instant.astimezone(LOCAL_TZ), day, night)
    since, remaining = (
        (instant.astimezone(LOCAL_TZ) - start).total_seconds() / 60,
        (end - instant.astimezone(LOCAL_TZ)).total_seconds() / 60,
    )
    up, down = (
        _number(snapshot.mode_parameters, "light_ramp_up_minutes", 0),
        _number(snapshot.mode_parameters, "light_ramp_down_minutes", 0),
    )
    return (up > 0 and since < up) or (down > 0 and remaining < down)


def _target(snapshot: MonitoringSnapshot, light: Mapping[str, object]) -> tuple[float, Quality]:
    target = next(
        (row for row in snapshot.light_targets if row.get("device_id") == light.get("device_id")),
        None,
    )
    return (
        (MINIMUM_LIGHT_INTENSITY, Quality.ESTIMATED)
        if target is None
        else (_number(target, "target_intensity", MINIMUM_LIGHT_INTENSITY), Quality.EXACT)
    )


def _schedule_times(values: Mapping[str, object]) -> tuple[time, time]:
    return _time(_value(values, "day_start_time") or _value(values, "day_start")), _time(
        _value(values, "night_start_time") or _value(values, "night_start")
    )


def _program_times(program: Mapping[str, object]) -> tuple[time, time]:
    return _time(program.get("start_time")), _time(program.get("end_time"))


def _window(current: datetime, start: time, end: time) -> tuple[datetime, datetime]:
    start_at, end_at = (
        datetime.combine(current.date(), start, LOCAL_TZ),
        datetime.combine(current.date(), end, LOCAL_TZ),
    )
    return (
        (
            (start_at, end_at + timedelta(days=1))
            if current.time() >= start
            else (start_at - timedelta(days=1), end_at)
        )
        if start > end
        else (start_at, end_at)
    )


def _in_window(current: time, start: time, end: time) -> bool:
    return current >= start or current < end if start > end else start <= current < end


def _value(values: Mapping[str, object] | None, key: str) -> object | None:
    return None if values is None else values.get(key)


def _number(values: Mapping[str, object], key: str, default: float) -> float:
    value = values.get(key)
    return float(value) if isinstance(value, int | float) else default


def _time(value: object | None) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value or "00:00"))


def _quality_rank(quality: Quality) -> int:
    return {Quality.EXACT: 2, Quality.ESTIMATED: 1, Quality.UNAVAILABLE: 0}[quality]


def _int_or_none(value: object | None) -> int | None:
    """Integer for row identity comparisons; booleans stay excluded."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _add_boundary(
    instants: set[datetime], instant: datetime, start: datetime, end: datetime
) -> None:
    """Record one in-range aware-UTC boundary candidate for the plan."""
    clipped = instant if instant.tzinfo is None else instant.astimezone(UTC)
    if start <= clipped < end:
        instants.add(clipped)


def _occurrence_window(
    program: Mapping[str, object], local_day: date
) -> tuple[datetime, datetime] | None:
    """One program occurrence window on ``local_day``; None when it never matches.

    Mirrors ``_matching`` plus ``_window`` semantics: a zero-length window never
    matches and ``end < start`` wraps to the following day.
    """
    start_time, end_time = _program_times(program)
    if start_time == end_time:
        return None
    start_at = datetime.combine(local_day, start_time, LOCAL_TZ)
    end_at = datetime.combine(
        local_day + timedelta(days=1) if end_time < start_time else local_day,
        end_time,
        LOCAL_TZ,
    )
    return start_at.astimezone(UTC), end_at.astimezone(UTC)


def _program_mismatch(
    program: Mapping[str, object], device_id: object | None, mode_id: int | None
) -> bool:
    """Whether a program can never match this light's device or active profile."""
    program_device = program.get("device_id")
    if device_id is not None and program_device is not None and program_device != device_id:
        return True
    program_mode = _int_or_none(program.get("mode_id"))
    return mode_id is not None and program_mode is not None and program_mode != mode_id


def _clipped_span(
    window: CycleWindow, start: datetime, end: datetime
) -> tuple[datetime, datetime] | None:
    """The intersection of one cycle window with ``[start, end)``, or None."""
    begin = max(window.start.astimezone(UTC), start.astimezone(UTC))
    finish = min(window.end.astimezone(UTC), end.astimezone(UTC))
    return None if finish <= begin else (begin, finish)


def _periodic_count(limit: float, period: float) -> int:
    """Count ``k >= 0`` with ``k * period`` strictly below ``limit``."""
    if limit <= _GATE_EPSILON_SECONDS:
        return 0
    return int((limit - _GATE_EPSILON_SECONDS) // period) + 1


def _first_k(prefix: float, period: float, offset: float) -> int:
    """First ``k >= 0`` with ``k * period + offset`` above ``prefix`` minus epsilon."""
    position = (prefix - offset - _GATE_EPSILON_SECONDS) / period
    return max(0, floor(position) + 1)
