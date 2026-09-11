from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from typing import Final

from app.repositories.climate_timeline_snapshot import ClimateSchedule, ClimateScheduleSnapshot
from app.repositories.monitoring_snapshot_types import FrozenRow
from app.schemas.climate_timeline import PeriodIdentity, SegmentSource, UtcWindow
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


def project_saved_trajectory(snapshot: ClimateScheduleSnapshot, room: str, config_revision: str):
    periods = tuple(
        period
        for schedule_slice in snapshot.slices
        if schedule_slice.schedule is not None
        for period in _slice_periods(
            schedule_slice.start, schedule_slice.end, schedule_slice.schedule, config_revision
        )
    )
    if not periods:
        return None
    return project_trajectories(
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


def _slice_periods(
    start: datetime, end: datetime, schedule: ClimateSchedule, revision: str
) -> Iterator[ClimatePeriodTrajectory]:
    mode = schedule.mode
    for row in schedule.periods:
        start_time = _value(row, "start_time")
        end_time = _value(row, "end_time")
        period_start = _at(start, start_time)
        period_end = _at(start, end_time)
        if period_end <= period_start:
            period_end += timedelta(days=1)
        clipped_start, clipped_end = max(start, period_start), min(end, period_end)
        if clipped_end <= clipped_start:
            continue
        targets: list[MetricTarget] = []
        for metric, key, unit in _METRICS:
            value = _value(row, key)
            if isinstance(value, int | float):
                targets.append(MetricTarget(metric, unit, float(value)))
        mode_id = _value(mode, "mode_id")
        submode_id = _value(mode, "submode_id")
        period_id = _value(row, "id")
        period_name = _value(row, "period_name")
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
            tuple(targets),
            float(ramp_minutes) if isinstance(ramp_minutes, int | float) else 0.0,
        )


def _at(day: datetime, value: object) -> datetime:
    parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    return datetime.combine(day.astimezone(UTC).date(), parsed, tzinfo=UTC)


def _value(row: FrozenRow, key: str) -> object:
    value: object = row.get(key)
    return value
