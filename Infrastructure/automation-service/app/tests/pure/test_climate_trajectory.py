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


def _source(
    period_id: str, *, mode: str = "flower", submode: str | None = None
) -> SegmentSource:
    return SegmentSource.model_validate(
        {
            "mode": mode,
            "submode": submode,
            "period": {"period_id": period_id, "label": f"Period {period_id}"},
            "config_revision": "revision",
        }
    )


def _request(
    periods: tuple[ClimatePeriodTrajectory, ...],
    *,
    overrides: tuple[RuntimeOverride, ...] = (),
    photoperiod_boundaries: tuple[datetime, ...] = (),
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> TrajectoryRequest:
    start = window_start or datetime(2026, 1, 1, tzinfo=UTC)
    end = window_end or start + timedelta(hours=2)
    return TrajectoryRequest(
        window=UtcWindow(start=start, end=end, timezone="America/Toronto"),
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
    *,
    source: SegmentSource | None = None,
    ramp_start: datetime | None = None,
    previous_targets: tuple[MetricTarget, ...] = (),
) -> ClimatePeriodTrajectory:
    targets = () if target is None else (MetricTarget(metric="heating", unit="C", value=target),)
    return ClimatePeriodTrajectory(
        start=start,
        end=end,
        source=_source(period_id) if source is None else source,
        targets=targets,
        ramp_minutes=ramp_minutes,
        ramp_start=ramp_start,
        previous_targets=previous_targets,
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


def test_coverage_gap_breaks_prior_known_ramp_seed() -> None:
    # Given: two same-profile periods separated by an unconfigured half-hour.
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

    # Then: the gap stays unavailable and a stale pre-gap value cannot seed the later ramp.
    gap = next(
        segment for segment in scheduled if isinstance(segment, UnavailableTrajectorySegment)
    )
    day = next(segment for segment in scheduled if segment.source.period.period_id == "day")
    assert (gap.start, gap.end) == (start + timedelta(minutes=30), start + timedelta(hours=1))
    assert isinstance(day, StepTrajectorySegment)
    assert day.value == 20.0


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


def test_missing_metric_period_clears_a_stale_same_profile_ramp_seed() -> None:
    # Given: a known metric, an unavailable metric period, then a ramped target with no predecessor.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("known", start, start + timedelta(minutes=30), 22.0),
            _period("unset", start + timedelta(minutes=30), start + timedelta(hours=1), None),
            _period("day", start + timedelta(hours=1), start + timedelta(hours=2), 20.0, 30),
        )
    )

    # When: the scheduled projection is evaluated.
    scheduled = tuple(
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    )

    # Then: NULL remains a gap and the post-gap target steps instead of reusing 22 C.
    unavailable = next(
        segment
        for segment in scheduled
        if isinstance(segment, UnavailableTrajectorySegment)
        and segment.source.period.period_id == "unset"
    )
    day = next(segment for segment in scheduled if segment.source.period.period_id == "day")
    assert unavailable.reason == "setpoint is unavailable"
    assert isinstance(day, StepTrajectorySegment)
    assert day.value == 20.0


def test_gap_after_truncated_ramp_uses_profile_predecessor_seed() -> None:
    # Given: a ramp cut short, a gap, then a new period with its same-profile predecessor target.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    request = _request(
        (
            _period("night", start, start + timedelta(minutes=30), 22),
            _period("dawn", start + timedelta(minutes=30), start + timedelta(minutes=45), 20, 30),
            _period(
                "day",
                start + timedelta(hours=1),
                start + timedelta(hours=2),
                24,
                30,
                previous_targets=(MetricTarget("heating", "C", 20.0),),
            ),
        )
    )

    # When: the schedule is projected.
    scheduled = [
        segment
        for segment in project_trajectories(request).segments
        if segment.trajectory_kind == "scheduled"
    ]

    # Then: the stale in-flight value 21 C is discarded in favor of the configured 20 C seed.
    replacement = next(
        segment
        for segment in scheduled
        if isinstance(segment, LinearTrajectorySegment) and segment.source.period.period_id == "day"
    )
    assert (replacement.start_value, replacement.end_value) == (20.0, 24.0)


def test_clipped_mid_ramp_matches_sampling_the_full_recurrence() -> None:
    # Given: a two-hour ramp whose recurrence began before the clipped request window.
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    predecessor = (MetricTarget("heating", "C", 20.0),)
    full_period = _period(
        "day",
        origin,
        origin + timedelta(hours=2),
        24.0,
        120,
        ramp_start=origin,
        previous_targets=predecessor,
    )
    full = _request(
        (full_period,), window_start=origin, window_end=origin + timedelta(hours=2)
    )
    full_ramp = next(
        segment
        for segment in project_trajectories(full).segments
        if isinstance(segment, LinearTrajectorySegment)
        and segment.trajectory_kind == "scheduled"
    )

    # When: the same recurrence is evaluated only from thirty to ninety minutes after its origin.
    clipped_start, clipped_end = origin + timedelta(minutes=30), origin + timedelta(minutes=90)
    clipped_period = _period(
        "day",
        clipped_start,
        clipped_end,
        24.0,
        120,
        ramp_start=origin,
        previous_targets=predecessor,
    )
    clipped = _request(
        (clipped_period,), window_start=clipped_start, window_end=clipped_end
    )
    clipped_ramp = next(
        segment
        for segment in project_trajectories(clipped).segments
        if isinstance(segment, LinearTrajectorySegment)
        and segment.trajectory_kind == "scheduled"
    )

    # Then: clipping preserves the full-window values at both UTC endpoints.
    assert (clipped_ramp.start_value, clipped_ramp.end_value) == (
        21.0,
        23.0,
    )
    assert (clipped_ramp.start, clipped_ramp.end) == (clipped_start, clipped_end)
    assert (full_ramp.start_value, full_ramp.end_value) == (20.0, 24.0)


def test_ramp_threshold_skips_only_values_strictly_below_threshold() -> None:
    # Given: changes just below, exactly at, and just above the heating threshold.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for delta, should_ramp in ((0.099, False), (0.1, True), (0.101, True)):
        request = _request(
            (
                _period("night", start, start + timedelta(minutes=30), 20.0),
                _period(
                    "day",
                    start + timedelta(minutes=30),
                    start + timedelta(hours=2),
                    20.0 + delta,
                    10,
                ),
            )
        )

        # When: the shared ramp policy evaluates each change.
        day = next(
            segment
            for segment in project_trajectories(request).segments
            if segment.trajectory_kind == "scheduled"
            and segment.source.period.period_id == "day"
        )

        # Then: equality and larger changes ramp, while only the smaller change steps.
        assert isinstance(day, LinearTrajectorySegment) == should_ramp
        assert isinstance(day, StepTrajectorySegment) == (not should_ramp)


def test_previous_targets_never_carry_across_mode_or_submode_identity() -> None:
    # Given: a prior value under one exact profile followed by a ramped period under another.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for destination in (
        _source("new-mode", mode="2", submode="1"),
        _source("new-submode", mode="1", submode="2"),
    ):
        request = _request(
            (
                _period(
                    "night",
                    start,
                    start + timedelta(minutes=30),
                    20.0,
                    source=_source("night", mode="1", submode="1"),
                ),
                _period(
                    destination.period.period_id,
                    start + timedelta(minutes=30),
                    start + timedelta(hours=2),
                    24.0,
                    30,
                    source=destination,
                ),
            )
        )

        # When: schedules change mode identity or only submode identity.
        scheduled = tuple(
            segment
            for segment in project_trajectories(request).segments
            if segment.trajectory_kind == "scheduled"
        )
        destination_segments = tuple(
            segment
            for segment in scheduled
            if segment.source.period.period_id == destination.period.period_id
        )

        # Then: the destination has no invented start value from the prior profile.
        assert len(destination_segments) == 1
        assert isinstance(destination_segments[0], StepTrajectorySegment)
        assert destination_segments[0].value == 24.0
