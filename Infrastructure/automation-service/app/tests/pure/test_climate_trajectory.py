from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schemas.climate_timeline import (
    LinearTrajectorySegment,
    SegmentSource,
    StepTrajectorySegment,
    UnavailableTrajectorySegment,
    UtcWindow,
)
from app.services.climate_trajectory import (
    ClimatePeriodTrajectory,
    MetricTarget,
    RuntimeOverride,
    TrajectoryRequest,
    project_trajectories,
)


def _source(period_id: str) -> SegmentSource:
    return SegmentSource.model_validate(
        {
            "mode": "flower",
            "period": {"period_id": period_id, "label": f"Period {period_id}"},
            "config_revision": "revision",
        }
    )


def _request(
    periods: tuple[ClimatePeriodTrajectory, ...],
    *,
    overrides: tuple[RuntimeOverride, ...] = (),
    photoperiod_boundaries: tuple[datetime, ...] = (),
) -> TrajectoryRequest:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return TrajectoryRequest(
        window=UtcWindow(start=start, end=start + timedelta(hours=2), timezone="America/Toronto"),
        periods=periods,
        overrides=overrides,
        photoperiod_boundaries=photoperiod_boundaries,
    )


def _period(
    period_id: str,
    start: datetime,
    end: datetime,
    target: float | None,
    ramp_minutes: float = 0,
) -> ClimatePeriodTrajectory:
    targets = () if target is None else (MetricTarget(metric="heating", unit="C", value=target),)
    return ClimatePeriodTrajectory(
        start=start, end=end, source=_source(period_id), targets=targets, ramp_minutes=ramp_minutes
    )


def test_scheduled_ramp_has_exact_midpoint_endpoint_and_photoperiod_clip() -> None:
    # Given: a 22-to-20 thirty-minute period transition and a photoperiod boundary at its midpoint.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("day", start + timedelta(minutes=30), start + timedelta(hours=2), 20, 30),
        ),
        photoperiod_boundaries=(start + timedelta(minutes=45),),
    )

    # When: the immutable schedule is projected.
    result = project_trajectories(request)

    # Then: linear endpoints retain the exact 21 midpoint and the target at the half-open end.
    scheduled = [segment for segment in result.segments if segment.trajectory_kind == "scheduled"]
    ramp = next(segment for segment in scheduled if isinstance(segment, LinearTrajectorySegment))
    assert (ramp.start, ramp.end, ramp.start_value, ramp.end_value) == (
        start + timedelta(minutes=30),
        start + timedelta(minutes=45),
        22.0,
        21.0,
    )
    assert isinstance(scheduled[2], LinearTrajectorySegment)
    assert scheduled[2].end_value == 20.0
    assert isinstance(scheduled[3], StepTrajectorySegment)
    assert scheduled[3].value == 20.0


def test_interrupted_ramp_starts_replacement_at_prior_boundary_value() -> None:
    # Given: a ramp interrupted ten minutes after it starts.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("dawn", start + timedelta(minutes=30), start + timedelta(minutes=45), 20, 30),
            _period("day", start + timedelta(minutes=45), start + timedelta(hours=2), 24, 30),
        )
    )

    # When: the schedule is projected without a stateful ramp manager.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the replacement begins at 21 rather than jumping diagonally from 22 or 20.
    replacement = next(
        segment
        for segment in scheduled
        if isinstance(segment, LinearTrajectorySegment) and segment.source.period.period_id == "day"
    )
    assert (replacement.start_value, replacement.end_value) == (21.0, 24.0)


def test_unavailable_period_gap_is_never_joined_by_a_diagonal() -> None:
    # Given: two configured periods separated by an unconfigured half-hour.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("day", start + timedelta(hours=1), start + timedelta(hours=2), 20),
        )
    )

    # When: the schedule is projected.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the uncovered range is explicitly unavailable, not a connected line.
    gap = next(
        segment for segment in scheduled if isinstance(segment, UnavailableTrajectorySegment)
    )
    assert (gap.start, gap.end, gap.reason) == (
        start + timedelta(minutes=30),
        start + timedelta(hours=1),
        "schedule coverage is unavailable",
    )


def test_runtime_override_expiry_and_indefinite_assumption_preserve_input() -> None:
    # Given: a known-expiry override followed by an indefinite override over an immutable request.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    periods = (
        _period("night", start, start + timedelta(minutes=30), 22),
        _period("day", start + timedelta(minutes=30), start + timedelta(hours=2), 20, 30),
    )
    overrides = (
        RuntimeOverride(
            "heating", 25, start + timedelta(minutes=40), start + timedelta(minutes=50)
        ),
        RuntimeOverride("heating", 23, start + timedelta(minutes=80), None),
    )
    request = _request(periods, overrides=overrides)

    # When: effective and scheduled trajectories are built.
    result = project_trajectories(request)

    # Then: expiry resumes a controller-style ramp, the indefinite hold is labeled, and inputs remain unchanged.
    effective = [segment for segment in result.segments if segment.trajectory_kind == "effective"]
    resumed = next(
        segment
        for segment in effective
        if isinstance(segment, LinearTrajectorySegment)
        and segment.start == start + timedelta(minutes=50)
    )
    assert resumed.start_value == 25.0
    assert (
        next(
            segment.end_value
            for segment in effective
            if isinstance(segment, LinearTrajectorySegment)
            and segment.end == start + timedelta(minutes=80)
        )
        == 20.0
    )
    hold = next(
        segment
        for segment in effective
        if isinstance(segment, StepTrajectorySegment) and segment.value == 23.0
    )
    assert hold.end == start + timedelta(hours=2)
    assert result.assumptions == ("assumes override remains active",)
    assert request.periods == periods
    assert request.overrides == overrides


def test_gap_between_periods_still_ramps_from_prior_value() -> None:
    # Given: two configured periods separated by an unconfigured half-hour.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("day", start + timedelta(hours=1), start + timedelta(hours=2), 20, 30),
        )
    )

    # When: the schedule is projected.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the gap stays explicitly unavailable while the day period ramps
    # from the schedule's last known value instead of stepping.
    gap = next(
        segment for segment in scheduled if isinstance(segment, UnavailableTrajectorySegment)
    )
    assert (gap.start, gap.end) == (start + timedelta(minutes=30), start + timedelta(hours=1))
    ramp = next(segment for segment in scheduled if isinstance(segment, LinearTrajectorySegment))
    assert (ramp.start, ramp.start_value, ramp.end_value) == (
        start + timedelta(hours=1),
        22.0,
        20.0,
    )


def test_unknown_prior_metric_steps_instead_of_ramping() -> None:
    # Given: a period without the metric followed by a ramped period.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("unset", start, start + timedelta(minutes=30), None),
            _period("day", start + timedelta(minutes=30), start + timedelta(hours=2), 20, 30),
        )
    )

    # When: the schedule is projected.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the unknown prior value cannot ramp, so the new value steps.
    assert not any(isinstance(segment, LinearTrajectorySegment) for segment in scheduled)
    step = next(segment for segment in scheduled if isinstance(segment, StepTrajectorySegment))
    assert step.value == 20.0


def test_truncated_ramp_carries_partial_value_across_gap() -> None:
    # Given: a ramp cut short by its period end, an unconfigured gap, then a
    # replacement period with its own ramp.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("dawn", start + timedelta(minutes=30), start + timedelta(minutes=45), 20, 30),
            _period("day", start + timedelta(hours=1), start + timedelta(hours=2), 24, 30),
        )
    )

    # When: the schedule is projected.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the replacement ramps from the interrupted ramp's value at its
    # boundary (21) rather than stepping from either endpoint value.
    replacement = next(
        segment
        for segment in scheduled
        if isinstance(segment, LinearTrajectorySegment) and segment.source.period.period_id == "day"
    )
    assert (replacement.start_value, replacement.end_value) == (21.0, 24.0)
