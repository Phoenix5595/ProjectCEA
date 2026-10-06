"""Canonical per-light future trajectory segments from one immutable snapshot.

The forecast reuses the active scheduler-parity evaluator over the frozen
monitoring snapshot that publication already built. It never extrapolates from
recorded history, never merges a stale climate-only rich envelope with other
light rows, and renders unsupported or contradictory authority as explicit
unavailable coverage instead of fabricated continuity.

Segment conventions (shared with the frontend canonical rich conversion):

- metric ``light.intensity.<device_name>`` for every physical light plus the
  room-level ``light.photoperiod`` phase series,
- ``trajectory_kind`` is ``effective`` for light coverage (the publication is
  anchored to the executing scheduler snapshot), never duplicated as scheduled,
- value segments carry quality ``estimated`` (future predictions make no
  dimmer-readback claim); unsupported windows are explicitly ``unavailable``,
- the per-device cycle budget bounds materialized gate transitions; unrepresented
  cycle coverage becomes an explicit unavailable gap with a warning instead of a
  silently simplified pattern,
- known calendar/profile identity switches stop the executing profile's light
  authority; moon-authority destinations keep full frozen authority from the
  mode name alone, every other destination stays explicitly unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.climate_timeline import (
    LinearTrajectorySegment,
    PeriodIdentity,
    SegmentSource,
    StepTrajectorySegment,
    TimelineWarning,
    TrajectorySegment,
    UnavailableTrajectorySegment,
)
from app.schemas.monitoring_models import Phase, Quality
from app.services.climate_projection import known_moon_destination_day, mode_identity_at
from app.services.light_projection import light_series_id
from app.services.light_projection_evaluator import (
    cycle_gate_count,
    cycle_gates,
    evaluate_intensity,
    matched_program,
    phase_at,
    phase_boundary_plan,
    segment_boundary_plan,
)

LOCAL_TZ: Final = ZoneInfo("America/Toronto")
_LIGHT_INTENSITY_UNIT: Final = "%"
_PHOTOPERIOD_SERIES: Final = "light.photoperiod"
_MAX_LIGHT_CYCLE_SEGMENTS_PER_DEVICE: Final = 1024
_WINDOW_MARGIN: Final = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class LightTrajectorySegments:
    """Canonical light forecast segments plus the honest coverage warnings."""

    segments: tuple[TrajectorySegment, ...]
    warnings: tuple[TimelineWarning, ...]


@dataclass(frozen=True, slots=True)
class _Authority:
    """Per-local-day forecast authority status for the publication window."""

    missing_mode: bool
    missing_parameters: bool
    moon_days: frozenset[date]
    resolvable_days: frozenset[date]


def project_light_segments(
    snapshot: MonitoringSnapshot, config_revision: str
) -> LightTrajectorySegments:
    """Convert one immutable monitor snapshot into per-light forecast segments."""
    start, end = snapshot.range.start.astimezone(UTC), snapshot.range.end.astimezone(UTC)
    authority = _authority_window(snapshot, start, end)
    warnings: list[TimelineWarning] = []
    metric_groups: list[tuple[TrajectorySegment, ...]] = [
        _photoperiod_segments(snapshot, start, end, config_revision, authority, warnings)
    ]
    metric_groups.extend(
        _light_segments(snapshot, light, start, end, config_revision, authority, warnings)
        for light in snapshot.expected_lights
    )
    return LightTrajectorySegments(
        _compact(tuple(piece for group in metric_groups for piece in group)),
        _unique_warnings(warnings),
    )


def _unique_warnings(warnings: list[TimelineWarning]) -> tuple[TimelineWarning, ...]:
    """Collapse repeated identical warnings while keeping first-seen order."""
    unique: list[TimelineWarning] = []
    seen: set[tuple[str, str]] = set()
    for warning in warnings:
        key = warning.code, warning.detail
        if key not in seen:
            seen.add(key)
            unique.append(warning)
    return tuple(unique)


def _authority_window(snapshot: MonitoringSnapshot, start: datetime, end: datetime) -> _Authority:
    """Resolve per-local-day coverage: executing profile, moon destination, unknown.

    A known calendar/profile identity change stops the executing profile's
    forecast. Moon-authority destinations stay fully governed by their mode
    name, so only those days remain forecastable after a switch; every other
    future identity publishes explicit unavailable coverage.
    """
    if snapshot.active_mode is None:
        return _Authority(
            missing_mode=True,
            missing_parameters=snapshot.mode_parameters is None,
            moon_days=frozenset(),
            resolvable_days=frozenset(),
        )
    missing_parameters = snapshot.mode_parameters is None
    identity = _active_identity(snapshot)
    first_day = start.astimezone(LOCAL_TZ).date()
    final_day = (end - _WINDOW_MARGIN).astimezone(LOCAL_TZ).date()
    moon_days: set[date] = set()
    resolvable: set[date] = set()
    day = first_day
    while day <= final_day:
        if _day_identity(snapshot, day) == identity:
            resolvable.add(day)
        elif known_moon_destination_day(snapshot, day):
            # The moon destination is fully governed by its frozen mode name.
            moon_days.add(day)
            resolvable.add(day)
        day += timedelta(days=1)
    return _Authority(
        missing_mode=False,
        missing_parameters=missing_parameters,
        moon_days=frozenset(moon_days),
        resolvable_days=frozenset(resolvable),
    )


def _active_identity(snapshot: MonitoringSnapshot) -> tuple[int | None, int | None]:
    """The executing profile identity of the immutable snapshot."""

    def _int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    active = snapshot.active_mode
    if active is None:
        return (None, None)
    return (_int(active.get("mode_id")), _int(active.get("submode_id")))


def _day_identity(snapshot: MonitoringSnapshot, day: date) -> tuple[int | None, int | None]:
    """The profile identity the snapshot applies across one local day."""
    # A current-day calendar entry may already have been applied and then
    # overridden. The executing snapshot owns today; calendar destinations
    # become projection authority only at future day boundaries.
    if day == snapshot.range.start.astimezone(LOCAL_TZ).date():
        return _active_identity(snapshot)
    identity = mode_identity_at(
        snapshot, datetime.combine(day, time(12, 0), LOCAL_TZ).astimezone(UTC)
    )
    return identity if identity is not None else (None, None)


def _photoperiod_segments(
    snapshot: MonitoringSnapshot,
    start: datetime,
    end: datetime,
    config_revision: str,
    authority: _Authority,
    warnings: list[TimelineWarning],
) -> tuple[TrajectorySegment, ...]:
    """Room SUN/MOON phase segments following the monitor's canonical phase."""
    if authority.missing_mode:
        warnings.append(_missing_mode_warning())
        return (
            _unavailable(
                start,
                end,
                _PHOTOPERIOD_SERIES,
                _photoperiod_source(config_revision),
                "active room mode authority is unavailable",
            ),
        )
    if authority.missing_parameters:
        warnings.append(_missing_schedule_warning())
        return (
            _unavailable(
                start,
                end,
                _PHOTOPERIOD_SERIES,
                _photoperiod_source(config_revision),
                "room photoperiod schedule is unavailable",
            ),
        )
    local_day = start.astimezone(LOCAL_TZ).date()
    final_day = (end - _WINDOW_MARGIN).astimezone(LOCAL_TZ).date()
    source = _photoperiod_source(config_revision)
    segments: list[TrajectorySegment] = []
    while local_day <= final_day:
        window = _day_window(start, end, local_day)
        if window is not None:
            segments.extend(
                _photoperiod_day(snapshot, window[0], window[1], source, authority, warnings)
            )
        local_day += timedelta(days=1)
    return tuple(segments)


def _photoperiod_day(
    snapshot: MonitoringSnapshot,
    start: datetime,
    end: datetime,
    source: SegmentSource,
    authority: _Authority,
    warnings: list[TimelineWarning],
) -> tuple[TrajectorySegment, ...]:
    """The photoperiod coverage of one local day under the resolved authority."""
    local_day = start.astimezone(LOCAL_TZ).date()
    if local_day in authority.moon_days:
        warnings.append(
            TimelineWarning(
                code="light_phase_unresolved",
                detail=(
                    "transitioned local day switches into a moon mode; the "
                    "destination photoperiod stays explicitly unavailable"
                ),
            )
        )
        return (
            _unavailable(
                start,
                end,
                _PHOTOPERIOD_SERIES,
                source,
                "transitioned local day has no frozen photoperiod authority",
            ),
        )
    if local_day not in authority.resolvable_days:
        warnings.append(_identity_warning())
        return (
            _unavailable(
                start,
                end,
                _PHOTOPERIOD_SERIES,
                source,
                "transitioned local day has no frozen photoperiod authority",
            ),
        )
    segments: list[TrajectorySegment] = []
    boundaries = phase_boundary_plan(snapshot, start, end)
    for lower, upper in zip(boundaries, boundaries[1:], strict=False):
        phase_start, quality = phase_at(snapshot, lower)
        phase_end, end_quality = phase_at(snapshot, upper - _WINDOW_MARGIN)
        if quality is Quality.UNAVAILABLE or end_quality is Quality.UNAVAILABLE:
            segments.append(
                _unavailable(
                    lower,
                    upper,
                    _PHOTOPERIOD_SERIES,
                    source,
                    "room photoperiod schedule is unavailable",
                )
            )
            continue
        start_value = 1.0 if phase_start is Phase.SUN else 0.0
        end_value = 1.0 if phase_end is Phase.SUN else 0.0
        if start_value == end_value:
            segments.append(_step(lower, upper, _PHOTOPERIOD_SERIES, start_value, source))
        else:
            segments.append(
                _linear(lower, upper, _PHOTOPERIOD_SERIES, start_value, end_value, source)
            )
    return tuple(segments)


def _light_segments(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    start: datetime,
    end: datetime,
    config_revision: str,
    authority: _Authority,
    warnings: list[TimelineWarning],
) -> tuple[TrajectorySegment, ...]:
    """Canonical coverage for one physical light across the forecast window."""
    series_id = light_series_id(str(light.get("device_name")))
    if authority.missing_mode:
        warnings.append(_missing_mode_warning())
        return (
            _device_unavailable(
                light, start, end, config_revision, "active room mode authority is unavailable"
            ),
        )
    if not any(row.get("device_id") == light.get("device_id") for row in snapshot.light_targets):
        warnings.append(
            TimelineWarning(
                code="light_target_unavailable",
                detail=f"light target authority is unavailable for {series_id}",
            )
        )
        return (
            _device_unavailable(
                light, start, end, config_revision, "light target authority is unavailable"
            ),
        )
    if authority.missing_parameters:
        warnings.append(_missing_schedule_warning())
        return (
            _device_unavailable(
                light,
                start,
                end,
                config_revision,
                "room light schedule authority is unavailable",
            ),
        )
    segments: list[TrajectorySegment] = []
    local_day = start.astimezone(LOCAL_TZ).date()
    final_day = (end - _WINDOW_MARGIN).astimezone(LOCAL_TZ).date()
    while local_day <= final_day:
        window = _day_window(start, end, local_day)
        if window is not None:
            segments.extend(
                _light_day_segments(
                    snapshot,
                    light,
                    series_id,
                    window[0],
                    window[1],
                    config_revision,
                    authority,
                    local_day,
                    warnings,
                )
            )
        local_day += timedelta(days=1)
    return tuple(segments)


def _light_day_segments(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    series_id: str,
    start: datetime,
    end: datetime,
    config_revision: str,
    authority: _Authority,
    local_day: date,
    warnings: list[TimelineWarning],
) -> tuple[TrajectorySegment, ...]:
    """Canonical coverage for one light inside a single local day."""
    if local_day in authority.moon_days:
        return (
            _step(
                start,
                end,
                series_id,
                0.0,
                _source(snapshot, start, config_revision, "moon_authority", "Moon authority"),
            ),
        )
    if local_day not in authority.resolvable_days:
        warnings.append(
            TimelineWarning(
                code="light_identity_unresolved",
                detail=(
                    f"{series_id} forecast stops at the known calendar transition; "
                    "the destination profile has no frozen light authority"
                ),
            )
        )
        return (
            _unavailable(
                start,
                end,
                series_id,
                _source(snapshot, start, config_revision, "transitioned", "Transitioned profile"),
                (
                    "known calendar transition beyond the executing profile has no "
                    "frozen light authority"
                ),
            ),
        )
    plan = segment_boundary_plan(snapshot, light, start, end)
    events: list[datetime] = list(plan.boundaries)
    missing_spans: tuple[tuple[datetime, datetime], ...] = ()
    remaining = _MAX_LIGHT_CYCLE_SEGMENTS_PER_DEVICE
    for window in plan.cycle_windows:
        count = cycle_gate_count(window, start=start, end=end)
        if count > remaining:
            missing_spans = (
                *missing_spans,
                (
                    max(window.start.astimezone(UTC), start),
                    min(window.end.astimezone(UTC), end),
                ),
            )
            continue
        remaining -= count
        events.extend(cycle_gates(window, start=start, end=end))
    if missing_spans:
        warnings.append(_cycle_budget_warning(series_id))
    ordered = tuple(sorted(set(events)))
    return tuple(
        _classify(snapshot, light, series_id, lower, upper, missing_spans, config_revision)
        for lower, upper in zip(ordered, ordered[1:], strict=False)
    )


def _classify(
    snapshot: MonitoringSnapshot,
    light: Mapping[str, object],
    series_id: str,
    lower: datetime,
    upper: datetime,
    missing_spans: tuple[tuple[datetime, datetime], ...],
    config_revision: str,
) -> TrajectorySegment:
    """Classify one canonical interval between adjacent scheduler boundaries."""
    if _missing_cycle_span(lower, upper, missing_spans):
        return _unavailable(
            lower,
            upper,
            series_id,
            _source(snapshot, lower, config_revision, "cycle_budget", "Cycle budget"),
            "light cycle transition budget exceeded",
        )
    program = matched_program(snapshot, light, lower)
    source = _matched_source(snapshot, lower, config_revision, program)
    value_start, _nominal, quality_start = evaluate_intensity(snapshot, light, lower)
    if quality_start is Quality.UNAVAILABLE:
        return _unavailable(
            lower, upper, series_id, source, "room light schedule authority is unavailable"
        )
    value_end, _nominal_end, quality_end = evaluate_intensity(
        snapshot, light, upper - _WINDOW_MARGIN
    )
    if quality_end is Quality.UNAVAILABLE:
        return _unavailable(
            lower, upper, series_id, source, "room light schedule authority is unavailable"
        )
    if value_start == value_end:
        return _step(lower, upper, series_id, value_start, source)
    return _linear(lower, upper, series_id, value_start, value_end, source)


def _missing_cycle_span(
    lower: datetime, upper: datetime, spans: tuple[tuple[datetime, datetime], ...]
) -> bool:
    """Whether any budgeted-out cycle span intersects the canonical interval."""
    return any(lower < end and start < upper for start, end in spans)


def _compact(segments: tuple[TrajectorySegment, ...]) -> tuple[TrajectorySegment, ...]:
    """Merge adjacent constant-step segments that share one source identity."""
    merged: list[TrajectorySegment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        if (
            isinstance(previous, StepTrajectorySegment)
            and isinstance(segment, StepTrajectorySegment)
            and previous.metric == segment.metric
            and previous.value == segment.value
            and previous.source == segment.source
        ):
            merged[-1] = previous.model_copy(update={"end": segment.end})
            continue
        merged.append(segment)
    return tuple(merged)


def _matched_source(
    snapshot: MonitoringSnapshot,
    instant: datetime,
    config_revision: str,
    program: Mapping[str, object] | None,
) -> SegmentSource:
    """Per-interval SegmentSource; program windows keep their schedule identity."""
    if program is not None:
        period_id = str(program.get("id") or "program")
        label = str(program.get("name") or program.get("program_type") or period_id)
        return _source(snapshot, instant, config_revision, period_id, label)
    return _source(snapshot, instant, config_revision, "photoperiod", "Photoperiod")


def _source(
    snapshot: MonitoringSnapshot,
    instant: datetime,
    config_revision: str,
    period_id: str,
    label: str,
) -> SegmentSource:
    """One SegmentSource with the snapshot-resolved profile identity."""
    mode, submode = _source_identity(_day_identity(snapshot, instant.astimezone(LOCAL_TZ).date()))
    return SegmentSource(
        mode=mode,
        submode=submode,
        period=PeriodIdentity(period_id=period_id, label=label),
        config_revision=config_revision,
    )


def _source_identity(
    identity: tuple[int | None, int | None],
) -> tuple[str, str | None]:
    """Mode/submode text for a resolved identity; missing authority stays marked."""
    mode, submode = identity
    return str(mode) if mode is not None else "unavailable", (
        None if submode is None else str(submode)
    )


def _photoperiod_source(config_revision: str) -> SegmentSource:
    """The room-level photoperiod segment source identity."""
    return SegmentSource(
        mode="unavailable",
        submode=None,
        period=PeriodIdentity(period_id="photoperiod", label="Photoperiod"),
        config_revision=config_revision,
    )


def _device_unavailable(
    light: Mapping[str, object],
    start: datetime,
    end: datetime,
    config_revision: str,
    reason: str,
) -> UnavailableTrajectorySegment:
    """One explicit whole-window unavailable gap for one physical light."""
    return _unavailable(
        start,
        end,
        light_series_id(str(light.get("device_name"))),
        SegmentSource(
            mode="unavailable",
            submode=None,
            period=PeriodIdentity(period_id="unavailable", label="Unavailable"),
            config_revision=config_revision,
        ),
        reason,
    )


def _missing_mode_warning() -> TimelineWarning:
    """The standardized missing-mode warning shared by photoperiod and devices."""
    return TimelineWarning(
        code="light_mode_unavailable",
        detail="active room mode is unavailable; light projection is unavailable",
    )


def _missing_schedule_warning() -> TimelineWarning:
    """The standardized missing photoperiod schedule warning."""
    return TimelineWarning(
        code="light_schedule_unavailable",
        detail="room photoperiod schedule is unavailable; light coverage stays unavailable",
    )


def _identity_warning() -> TimelineWarning:
    """The standardized transitioned-identity warning for photoperiod gaps."""
    return TimelineWarning(
        code="light_stage_switched",
        detail=(
            "known calendar/profile transition beyond the executing profile has "
            "no frozen photoperiod; coverage is unavailable"
        ),
    )


def _cycle_budget_warning(series_id: str) -> TimelineWarning:
    """The standardized cycle-budget warning for one device's unrepresented pack."""
    return TimelineWarning(
        code="light_cycle_budget_exceeded",
        detail=(
            f"{series_id} cycle patterns exceed the "
            f"{_MAX_LIGHT_CYCLE_SEGMENTS_PER_DEVICE}-segment forecast budget; "
            "unrepresented coverage stays unavailable"
        ),
    )


def _unavailable(
    start: datetime,
    end: datetime,
    metric: str,
    source: SegmentSource,
    reason: str,
) -> UnavailableTrajectorySegment:
    return UnavailableTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=_LIGHT_INTENSITY_UNIT,
        trajectory_kind="effective",
        quality="unavailable",
        source=source,
        shape="unavailable",
        reason=reason,
    )


def _step(
    start: datetime, end: datetime, metric: str, value: float, source: SegmentSource
) -> StepTrajectorySegment:
    return StepTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=_LIGHT_INTENSITY_UNIT,
        trajectory_kind="effective",
        quality="estimated",
        source=source,
        shape="step",
        value=value,
    )


def _linear(
    start: datetime,
    end: datetime,
    metric: str,
    start_value: float,
    end_value: float,
    source: SegmentSource,
) -> LinearTrajectorySegment:
    return LinearTrajectorySegment(
        start=start,
        end=end,
        metric=metric,
        unit=_LIGHT_INTENSITY_UNIT,
        trajectory_kind="effective",
        quality="estimated",
        source=source,
        shape="linear",
        start_value=start_value,
        end_value=end_value,
    )


def _day_window(
    start: datetime, end: datetime, local_day: date
) -> tuple[datetime, datetime] | None:
    """The canonical monitor window clipped to one Toronto local day."""
    window_start = max(start, _midnight_utc(local_day))
    window_end = min(end, _midnight_utc(local_day + timedelta(days=1)))
    return None if window_end <= window_start else (window_start, window_end)


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, LOCAL_TZ).astimezone(UTC)
