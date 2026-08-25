"""Stateless scheduler-parity light schedule evaluation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.monitoring_models import Phase, Quality
from shared.room_light_authority import is_moon_authority_mode

LOCAL_TZ: Final = ZoneInfo("America/Toronto")
MINIMUM_LIGHT_INTENSITY: Final = 10.0


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
