from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from app.repositories.climate_timeline_snapshot import (
    LOCAL_TZ as _LOCAL_TZ,
)
from app.repositories.climate_timeline_snapshot import (
    ClimateSchedule,
    ClimateScheduleSnapshot,
)
from app.repositories.monitoring_snapshot_types import FrozenRow
from app.schemas.climate_timeline import (
    PeriodIdentity,
    RichTrajectoryEnvelope,
    SegmentSource,
    TimelineWarning,
    UtcWindow,
)
from app.services.climate_trajectory import (
    ClimatePeriodTrajectory,
    MetricTarget,
    TrajectoryRequest,
    project_trajectories,
)

_METRICS: Final = (
    ("heating", "heating_setpoint", "C"),
    ("cooling", "cooling_setpoint", "C"),
    ("vpd", "vpd_setpoint", "kPa"),
    ("co2", "co2_setpoint", "ppm"),
)


def project_saved_trajectory(
    snapshot: ClimateScheduleSnapshot, room: str, config_revision: str
) -> RichTrajectoryEnvelope | None:
    dst_warnings: list[TimelineWarning] = []
    periods = tuple(
        sorted(
            (
                period
                for schedule_slice in snapshot.slices
                if schedule_slice.schedule is not None
                for period in _slice_periods(
                    schedule_slice.start,
                    schedule_slice.end,
                    schedule_slice.schedule,
                    config_revision,
                    dst_warnings,
                )
            ),
            key=lambda item: item.start,
        )
    )
    if not any(period.targets for period in periods):
        return None
    trajectory = project_trajectories(
        TrajectoryRequest(
            window=UtcWindow(
                start=snapshot.window.start,
                end=snapshot.window.end,
                timezone=snapshot.window.timezone,
            ),
            periods=periods,
            room=room,
        )
    )
    warnings = [
        TimelineWarning(code="calendar.transition_skipped", detail=schedule_slice.warning)
        for schedule_slice in snapshot.slices
        if schedule_slice.warning is not None
    ]
    warnings.extend(dst_warnings)
    unique_warnings: list[TimelineWarning] = []
    seen_warnings: set[tuple[str, str]] = set()
    for warning in warnings:
        key = warning.code, warning.detail
        if key not in seen_warnings:
            seen_warnings.add(key)
            unique_warnings.append(warning)
    return trajectory.model_copy(update={"warnings": tuple(unique_warnings)})


def _slice_periods(
    start: datetime,
    end: datetime,
    schedule: ClimateSchedule,
    revision: str,
    dst_warnings: list[TimelineWarning] | None = None,
) -> Iterator[ClimatePeriodTrajectory]:
    if end <= start:
        return
    rows = schedule.periods
    ordered_rows = sorted(rows, key=lambda row: _wall_time(_value(row, "start_time")))
    # The exact schedule identity owns its cyclic predecessor seed.
    previous_targets = (
        {
            id(row): _row_targets(ordered_rows[(index - 1) % len(ordered_rows)])
            for index, row in enumerate(ordered_rows)
        }
        if ordered_rows
        else {}
    )
    first_local_day = start.astimezone(_LOCAL_TZ).date() - timedelta(days=1)
    last_local_day = (end - timedelta(microseconds=1)).astimezone(_LOCAL_TZ).date()
    mode = schedule.mode
    local_day = first_local_day
    while local_day <= last_local_day:
        for row in rows:
            start_time = _wall_time(_value(row, "start_time"))
            end_time = _wall_time(_value(row, "end_time"))
            period_start, start_assumption = _resolve_local_wall_time(local_day, start_time)
            end_day = local_day + timedelta(days=1 if end_time <= start_time else 0)
            period_end, end_assumption = _resolve_local_wall_time(end_day, end_time)
            if period_end <= period_start:
                continue
            clipped_start, clipped_end = max(start, period_start), min(end, period_end)
            if clipped_end <= clipped_start:
                continue

            period_id = _value(row, "id")
            period_name = _value(row, "period_name")
            for assumption, boundary_day, wall_time in (
                (start_assumption, local_day, start_time),
                (end_assumption, end_day, end_time),
            ):
                if assumption is not None and dst_warnings is not None:
                    dst_warnings.append(
                        TimelineWarning(
                            code="profile.dst_assumption",
                            detail=(
                                f"{assumption}: {period_name} "
                                f"{boundary_day.isoformat()} {wall_time.strftime('%H:%M')}"
                            ),
                        )
                    )

            targets = _row_targets(row)
            mode_id = _value(mode, "mode_id")
            submode_id = _value(mode, "submode_id")
            ramp_minutes = _value(row, "ramp_minutes")
            source = SegmentSource(
                mode=str(mode_id),
                submode=None if submode_id is None else str(submode_id),
                period=PeriodIdentity(period_id=str(period_id), label=str(period_name)),
                config_revision=revision,
            )
            yield ClimatePeriodTrajectory(
                clipped_start,
                clipped_end,
                source,
                targets,
                float(ramp_minutes)
                if isinstance(ramp_minutes, int | float) and not isinstance(ramp_minutes, bool)
                else 0.0,
                ramp_start=period_start,
                previous_targets=previous_targets.get(id(row), ()),
            )
        local_day += timedelta(days=1)


def _resolve_local_wall_time(local_day: date, value: object) -> tuple[datetime, str | None]:
    """Resolve Toronto wall clocks by UTC round-trip, choosing fold-first/gap-forward."""
    wall_time = _wall_time(value)
    requested = datetime.combine(local_day, wall_time)
    valid: dict[datetime, None] = {}
    round_trips: list[tuple[datetime, datetime]] = []
    for fold in (0, 1):
        candidate = requested.replace(tzinfo=_LOCAL_TZ, fold=fold).astimezone(UTC)
        normalized = candidate.astimezone(_LOCAL_TZ).replace(tzinfo=None)
        round_trips.append((normalized, candidate))
        if normalized == requested:
            valid[candidate] = None
    if valid:
        instants = sorted(valid)
        return instants[0], "fold-first" if len(instants) > 1 else None
    forward = sorted(
        (normalized - requested, candidate)
        for normalized, candidate in round_trips
        if normalized > requested
    )
    if forward:
        return forward[0][1], "gap-forward"
    raise ValueError(f"cannot resolve Toronto wall time {requested.isoformat()}")


def _wall_time(value: object) -> time:
    parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    return time(parsed.hour, parsed.minute, parsed.second, parsed.microsecond)


def _row_targets(row: FrozenRow) -> tuple[MetricTarget, ...]:
    targets: list[MetricTarget] = []
    for metric, key, unit in _METRICS:
        value = _value(row, key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            targets.append(MetricTarget(metric, unit, float(value)))
    return tuple(targets)


def _value(row: FrozenRow, key: str) -> object:
    value: object = row.get(key)
    return value
