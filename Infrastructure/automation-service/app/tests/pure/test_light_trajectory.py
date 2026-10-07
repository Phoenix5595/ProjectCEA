"""Canonical per-light forecast segments and complete publication roundtrips."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.monitoring_publication.projection import (
    ProjectionPublicationAction,
    ProjectionPublicationDependencies,
)
from app.monitoring_publication.rich import project_saved_trajectory
from app.repositories.climate_timeline_snapshot import (
    ClimateSchedule,
    ClimateScheduleSnapshot,
    ClimateScheduleSlice,
    TimelineWindow,
)
from app.repositories.monitoring_snapshot_builder import MonitoringSnapshotRequest
from app.repositories.monitoring_snapshot_types import MonitoringSnapshot, frozen, frozen_rows
from app.schemas.climate_timeline import (
    LinearTrajectorySegment,
    RichTrajectoryEnvelope,
    StepTrajectorySegment,
)
from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
)
from app.services.future_projection import project_future_intervals
from app.services.light_projection import light_series_id
from app.services.light_projection_evaluator import CycleWindow, cycle_gate_count, cycle_gates
from app.services.light_trajectory import project_light_segments
from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSnapshot,
    PersistenceCursor,
    PersistenceState,
    PublicationVersion,
)

_NOW = datetime(2026, 1, 5, 12, tzinfo=UTC)  # fixed 07:00 Toronto projection instant


def _snapshot(
    *,
    start: datetime,
    end: datetime,
    mode_name: str | None = "Flower",
    parameters: Mapping[str, object] | None = None,
    missing_parameters: bool = False,
    targets: Sequence[Mapping[str, object]] | None = None,
    programs: Sequence[Mapping[str, object]] | None = None,
    predecessors: Sequence[Mapping[str, object]] | None = None,
    calendar_events: Sequence[Mapping[str, object]] = (),
    lights: Sequence[Mapping[str, object]] | None = None,
    location: str = "Veg Room",
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        range=MonitoringRange.from_absolute(start, end),
        location=location,
        cluster="main",
        active_mode=None if mode_name is None else frozen({"mode_id": 1, "mode_name": mode_name}),
        calendar_events=frozen_rows(calendar_events),
        calendar_applications=(),
        climate_periods=(),
        mode_parameters=(
            None
            if missing_parameters
            else frozen(
                parameters
                if parameters is not None
                else {
                    "mode_id": 1,
                    "day_start_time": "06:00",
                    "night_start_time": "18:00",
                    "light_ramp_up_minutes": 60,
                    "light_ramp_down_minutes": 60,
                }
            )
        ),
        light_targets=frozen_rows(
            targets if targets is not None else [{"device_id": 7, "target_intensity": 50.0}]
        ),
        light_programs=frozen_rows(programs or []),
        expected_lights=frozen_rows(
            lights if lights is not None else [{"device_id": 7, "device_name": "light_1"}]
        ),
        effective_setpoint_predecessors=frozen_rows(predecessors or ()),
        ramp_anchors=(),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(("configuration", 5),),
        projection_revision=ProjectionRevision("0000009"),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=end,
        runtime_snapshot_version=RuntimeSnapshotVersion(9),
        config_version=ConfigVersion(5),
    )


def _by_metric(segments: tuple, metric: str) -> tuple:
    """The segments of one metric in publication order."""
    return tuple(segment for segment in segments if segment.metric == metric)


def test_basic_and_rich_forecasts_share_one_light_namespace() -> None:
    # Given: one authoritative snapshot published through both channels.
    snapshot = _snapshot(
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
        targets=[{"device_id": 7, "target_intensity": 50.0}],
        lights=[{"device_id": 7, "device_name": "light_1"}],
    )

    # When: the legacy basic rows and the rich canonical segments are projected.
    basic_identifiers = {
        point.series_id.value
        for projection in project_future_intervals(snapshot)
        for point in projection.series
        if point.series_id.value.startswith("light.intensity.")
    }
    rich_identifiers = {
        segment.metric
        for segment in project_light_segments(snapshot, "0000000").segments
        if segment.metric.startswith("light.intensity.")
    }

    # Then: one light namespace identity serves both publications.
    assert basic_identifiers == rich_identifiers == {"light.intensity.light_1"}
    assert light_series_id("light_1") == "light.intensity.light_1"


def test_seeded_sunrise_ramp_has_exact_scheduler_endpoints() -> None:
    # Given: a sunrise already halfway seeded by an auto scheduler predecessor.
    start = datetime(2026, 1, 5, 11, 30, tzinfo=UTC)  # 06:30 Toronto
    predecessor = {
        "device_name": "light_1",
        "effective_light_intensity": 35,
        "timestamp": datetime(2026, 1, 5, 11, 0, tzinfo=UTC),
        "runtime_snapshot_identity": 9,
        "authority": "auto",
    }
    result = project_light_segments(
        _snapshot(
            start=start,
            end=start + timedelta(minutes=45),
            predecessors=[predecessor],
        ),
        "0000000",
    )

    # When: the canonical conversion runs.
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: one linear seeded-ramp segment and one held step, both estimated.
    ramp, hold = light[0], light[1]
    assert isinstance(ramp, LinearTrajectorySegment)
    assert ramp.start == start
    assert ramp.end == datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    assert ramp.start_value == pytest.approx(42.5)
    assert ramp.end_value == pytest.approx(50.0, abs=1e-6)
    assert ramp.trajectory_kind == "effective"
    assert ramp.quality == "estimated"
    assert hold == StepTrajectorySegment(
        start=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 12, 15, 0, tzinfo=UTC),
        metric="light.intensity.light_1",
        unit="%",
        trajectory_kind="effective",
        quality="estimated",
        source=ramp.source,
        shape="step",
        value=50.0,
    )


def test_steady_sun_holds_one_canonical_step() -> None:
    # Given: a midday window wholly inside the photoperiod hold.
    start = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # 09:00 Toronto
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(minutes=30)), "0000000"
    )

    # When: the conversion runs.
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: a single merged step carries the configured target on the shared identity.
    assert len(light) == 1
    assert isinstance(light[0], StepTrajectorySegment)
    assert light[0].value == 50.0
    assert light[0].start == start and light[0].end == start + timedelta(minutes=30)
    assert light[0].source.period.label == "Photoperiod"


def test_sunset_ramp_descends_and_moon_flips_without_invented_jumps() -> None:
    # Given: a window covering the hold, the down ramp, and the moon tail.
    start = datetime(2026, 1, 5, 21, 30, tzinfo=UTC)  # 16:30 Toronto
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=2)), "0000000"
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: hold, ramp, and OFF segments stay in scheduler order with exact endpoints.
    assert [segment.shape for segment in light] == ["step", "linear", "step"]
    hold, ramp, moon = light
    assert hold.value == 50.0
    assert ramp.start == datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    assert ramp.end == datetime(2026, 1, 5, 23, 0, tzinfo=UTC)
    assert ramp.start_value == 50.0
    assert ramp.end_value == pytest.approx(10.0, abs=1e-6)
    assert moon.value == 0.0


def test_moon_authority_mode_forces_off_step() -> None:
    # Given: the drying room mode forces schedule-light authority to moon.
    start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=1), mode_name="Drying"),
        "0000000",
    )

    # When: the canonical conversion runs.
    segments = result.segments

    # Then: the light coverage is exactly one zero step and the phase stays wall-clock.
    assert [s.shape for s in segments] == ["step", "step"]
    assert segments[1].metric == "light.intensity.light_1"
    assert segments[1].value == 0.0
    assert segments[0].metric == "light.photoperiod"
    assert segments[0].value == 1.0


def test_program_override_window_publishes_its_period_identity() -> None:
    # Given: a nightly override program with its own target intensity.
    program = {
        "id": 3,
        "device_id": 7,
        "name": "Late boost",
        "program_type": "override",
        "start_time": "19:00",
        "end_time": "20:00",
        "target_intensity": 70,
        "priority": 5,
        "enabled": True,
    }
    start = datetime(2026, 1, 5, 23, 30, tzinfo=UTC)  # 18:30 Toronto
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=2), programs=[program]),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: the program window holds its target with the program's identity.
    assert [s.value for s in light] == [0.0, 70.0, 0.0]
    assert light[1].source.period.label == "Late boost"
    assert light[0].source.period.label == "Photoperiod"


def test_short_programmed_cycles_publish_alternating_steps() -> None:
    # Given: a DLC-style one-minute on/off supplemental program.
    program = {
        "id": 4,
        "device_id": 7,
        "name": "DLC",
        "program_type": "supplemental",
        "start_time": "00:00",
        "end_time": "23:00",
        "target_intensity": 80,
        "cycle_enabled": True,
        "cycle_on_seconds": 60,
        "cycle_off_seconds": 60,
        "priority": 5,
        "enabled": True,
    }
    start = datetime(2026, 1, 5, 5, 0, tzinfo=UTC)  # 00:00 Toronto
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(minutes=30), programs=[program]),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: every gate is its own step segment with the program identity.
    assert len(light) == 30
    assert all(isinstance(segment, StepTrajectorySegment) for segment in light)
    expected = [80.0 if index % 2 == 0 else 0.0 for index in range(30)]
    assert [segment.value for segment in light] == expected
    assert {segment.source.period.period_id for segment in light} == {"4"}
    assert {segment.trajectory_kind for segment in light} == {"effective"}


def test_cycle_budget_closes_remaining_coverage_explicitly() -> None:
    # Given: a one-second cycle across a full day window.
    program = {
        "id": 4,
        "device_id": 7,
        "name": "Hard cycle",
        "program_type": "supplemental",
        "start_time": "00:00",
        "end_time": "22:00",
        "target_intensity": 80,
        "cycle_enabled": True,
        "cycle_on_seconds": 1,
        "cycle_off_seconds": 1,
        "priority": 5,
        "enabled": True,
    }
    start = datetime(2026, 1, 5, 5, 0, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(days=1), programs=[program]),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")
    unavailable = [segment for segment in light if segment.shape == "unavailable"]

    # Then: unrepresented coverage is explicit, warned, and no ramp is fabricated.
    assert unavailable, "cycle budget overflow must produce explicit unavailable segments"
    assert all("cycle" in segment.reason for segment in unavailable)
    assert any(warning.code == "light_cycle_budget_exceeded" for warning in result.warnings)
    assert _by_metric(result.segments, "light.photoperiod"), "other metrics keep canonical coverage"


def test_missing_active_mode_is_unavailable_for_every_light_series() -> None:
    # Given: no active room authority.
    start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=1), mode_name=None),
        "0000000",
    )

    # Then: photoperiod and the light stay unavailable with the mode warning.
    assert {s.shape for s in result.segments} == {"unavailable"}
    assert any(warning.code == "light_mode_unavailable" for warning in result.warnings)


def test_missing_light_target_stays_unavailable() -> None:
    # Given: no target row for the configured light.
    start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=1), targets=[]),
        "0000000",
    )

    # Then: the photoperiod still follows the wall clock while the light is a gap.
    photoperiod = _by_metric(result.segments, "light.photoperiod")[0]
    light = _by_metric(result.segments, "light.intensity.light_1")[0]
    assert photoperiod.shape == "step" and photoperiod.value == 1.0
    assert light.shape == "unavailable"
    assert "target" in light.reason
    assert any(warning.code == "light_target_unavailable" for warning in result.warnings)


def test_missing_mode_parameters_are_unavailable_for_lights_and_phase() -> None:
    # Given: no photoperiod parameters and no light program.
    start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(start=start, end=start + timedelta(hours=1), missing_parameters=True),
        "0000000",
    )

    # Then: explicit unavailable coverage replaces the fail-safe estimate.
    assert {s.shape for s in result.segments} == {"unavailable"}
    assert any(warning.code == "light_schedule_unavailable" for warning in result.warnings)


_MOON_EVENT: Mapping[str, object] = {
    "id": 9,
    "location": "Flower Room",
    "cluster": "main",
    "start_date": "2026-01-06",
    "end_date": "2026-01-06",
    "auto_mode_transition": True,
    "phase_order": 1,
    "target_mode_id": 2,
    "target_submode_id": None,
    "target_mode_name": "drying",
    "destination_configured": True,
}


def test_running_light_profile_wins_over_an_already_applied_calendar_day() -> None:
    start = datetime(2026, 1, 5, 14, tzinfo=UTC)
    previous_event = dict(_MOON_EVENT, start_date="2026-01-05", end_date="2026-01-05")
    result = project_light_segments(
        _snapshot(
            start=start,
            end=start + timedelta(hours=1),
            location="Flower Room",
            calendar_events=[previous_event],
        ),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    assert len(light) == 1
    assert light[0].shape == "step"
    assert light[0].value == 50.0
    assert light[0].source.mode == "1"
    assert light[0].quality == "estimated"


def test_known_calendar_transition_stops_unresolvable_light_authority() -> None:
    # Given: a Flower-room calendar transition tomorrow into a non-moon destination.
    flower_event = dict(_MOON_EVENT, target_mode_name="veg", target_mode_id=3)
    start = datetime(2026, 1, 5, 20, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(
            start=start,
            end=start + timedelta(days=1),
            location="Flower Room",
            calendar_events=[flower_event],
        ),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: the executing profile keeps canonical coverage and tomorrow stays a gap.
    switch_probe = next(segment for segment in light if segment.shape == "unavailable")
    assert switch_probe.start == datetime(2026, 1, 6, 5, 0, tzinfo=UTC)
    assert "frozen light authority" in switch_probe.reason
    assert any(warning.code == "light_identity_unresolved" for warning in result.warnings)
    photoperiod = _by_metric(result.segments, "light.photoperiod")
    assert any(
        segment.shape == "unavailable" and segment.start == switch_probe.start
        for segment in photoperiod
    )


def test_moon_destination_day_keeps_frozen_zero_light_authority() -> None:
    # Given: a Flower-room calendar transition into drying tomorrow.
    start = datetime(2026, 1, 5, 20, tzinfo=UTC)
    result = project_light_segments(
        _snapshot(
            start=start,
            end=start + timedelta(days=1),
            location="Flower Room",
            calendar_events=[_MOON_EVENT],
        ),
        "0000000",
    )
    light = _by_metric(result.segments, "light.intensity.light_1")

    # Then: the moon destination day publishes one zero step while phase stays a gap.
    destination = [
        segment for segment in light if segment.start == datetime(2026, 1, 6, 5, 0, tzinfo=UTC)
    ]
    assert len(destination) == 1
    assert destination[0].value == 0.0
    assert destination[0].source.period.label == "Moon authority"
    assert any(warning.code == "light_phase_unresolved" for warning in result.warnings)
    photoperiod = _by_metric(result.segments, "light.photoperiod")
    assert any(
        segment.shape == "unavailable" and segment.start >= datetime(2026, 1, 6, 5, 0, tzinfo=UTC)
        for segment in photoperiod
    )


def test_same_value_lights_keep_separate_device_identities() -> None:
    # Given: two physical lights configured to the same target.
    start = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
    snapshot = _snapshot(
        start=start,
        end=start + timedelta(minutes=30),
        targets=[
            {"device_id": 7, "target_intensity": 50.0},
            {"device_id": 8, "target_intensity": 50},
        ],
        lights=[
            {"device_id": 7, "device_name": "light_1"},
            {"device_id": 8, "device_name": "light_2"},
        ],
    )
    result = project_light_segments(snapshot, "0000000")

    # Then: each device keeps one deterministic identity even at equal values.
    identities = {
        segment.metric
        for segment in result.segments
        if segment.metric.startswith("light.intensity.")
    }
    assert identities == {"light.intensity.light_1", "light.intensity.light_2"}
    values_one = [
        segment.value for segment in _by_metric(result.segments, "light.intensity.light_1")
    ]
    values_two = [
        segment.value for segment in _by_metric(result.segments, "light.intensity.light_2")
    ]
    assert values_one == values_two == [50.0]


def test_cycle_gate_count_matches_materialized_gates() -> None:
    # Given: an asymmetric cycle window.
    window = CycleWindow(
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
        on_seconds=180.0,
        off_seconds=60.0,
    )

    # When: the closed form counts and the materialized list enumerates.
    count = cycle_gate_count(window, start=window.start, end=window.end)
    gates = cycle_gates(window, start=window.start, end=window.end)

    # Then: the two forms agree gate for gate and stay chronological.
    assert count == len(gates) == 30
    stamps = [gate.timestamp() for gate in gates]
    assert stamps == sorted(stamps)


def test_cycle_gate_count_skips_unalternating_windows() -> None:
    # Given: a cycle with no off phase (the scheduler holds the target constant).
    window = CycleWindow(
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 16, 0, tzinfo=UTC),
        on_seconds=120.0,
        off_seconds=0.0,
    )

    # Then: no gates can change the value.
    assert cycle_gate_count(window, start=window.start, end=window.end) == 0
    assert cycle_gates(window, start=window.start, end=window.end) == ()


class _SnapshotBuilder:
    """Fake monitor snapshot source returning one frozen snapshot."""

    def __init__(self, snapshot: MonitoringSnapshot) -> None:
        self.snapshot = snapshot

    async def build(self, request: MonitoringSnapshotRequest) -> MonitoringSnapshot:
        return self.snapshot


class _RichSnapshotBuilder:
    """Fake saved-trajectory authority source."""

    def __init__(self, snapshot: ClimateScheduleSnapshot) -> None:
        self.snapshot = snapshot

    async def build_saved(
        self, location: str, cluster: str, window: TimelineWindow
    ) -> ClimateScheduleSnapshot:
        return self.snapshot


class _PublicationWriter:
    """Fake Redis writer recording the paired publication calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def write_future(self, location: str, projections: tuple) -> bool:
        self.calls.append(("future", projections))
        return True

    def write_complete(
        self, location: str, projections: tuple, trajectory: RichTrajectoryEnvelope
    ) -> bool:
        self.calls.append(("complete", projections, trajectory))
        return True


def _scheduled_climate_snapshot() -> ClimateScheduleSnapshot:
    """One saved climate schedule so the climate rich path returns an envelope."""
    window = TimelineWindow.rolling(_NOW)
    period = frozen(
        {
            "id": "p1",
            "period_name": "Day",
            "start_time": "06:00",
            "end_time": "18:00",
            "ramp_minutes": 30,
            "heating_setpoint": 22.0,
            "cooling_setpoint": 28.0,
            "vpd_setpoint": 0.9,
            "co2_setpoint": 800,
        }
    )
    schedule = ClimateSchedule(
        mode=frozen({"mode_id": 1}),
        parameters=frozen({"mode_id": 1}),
        periods=(period,),
    )
    return ClimateScheduleSnapshot(
        window=window,
        slices=(ClimateScheduleSlice(window.start, window.end, schedule),),
    )


def _empty_climate_snapshot() -> ClimateScheduleSnapshot:
    """One saved climate slice without period rows so the rich path returns None."""
    window = TimelineWindow.rolling(_NOW)
    schedule = ClimateSchedule(
        mode=frozen({"mode_id": 1}),
        parameters=frozen({"mode_id": 1}),
        periods=(),
    )
    return ClimateScheduleSnapshot(
        window=window,
        slices=(ClimateScheduleSlice(window.start, window.end, schedule),),
    )


def _monitor_snapshot() -> MonitoringSnapshot:
    """One monitor authority snapshot matching the publication window."""
    start = _NOW
    end = start + timedelta(hours=24)
    return MonitoringSnapshot(
        range=MonitoringRange.from_absolute(start, end),
        location="Veg Room",
        cluster="main",
        active_mode=frozen({"mode_id": 1, "mode_name": "Flower"}),
        calendar_events=(),
        calendar_applications=(),
        climate_periods=(),
        mode_parameters=frozen(
            {
                "mode_id": 1,
                "day_start_time": "06:00",
                "night_start_time": "18:00",
                "light_ramp_up_minutes": 60,
                "light_ramp_down_minutes": 60,
            }
        ),
        light_targets=frozen_rows([{"device_id": 7, "target_intensity": 50.0}]),
        light_programs=frozen_rows([]),
        expected_lights=frozen_rows([{"device_id": 7, "device_name": "light_1"}]),
        effective_setpoint_predecessors=frozen_rows(
            [
                {
                    "device_name": "light_1",
                    "effective_light_intensity": 35,
                    "timestamp": start,
                    "runtime_snapshot_identity": 9,
                    "authority": "auto",
                }
            ]
        ),
        ramp_anchors=(),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(("configuration", 5),),
        projection_revision=ProjectionRevision("0000009"),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=end,
        runtime_snapshot_version=RuntimeSnapshotVersion(9),
        config_version=ConfigVersion(5),
    )


def _dependencies(
    writer: _PublicationWriter,
    snapshot_builder: _SnapshotBuilder,
    rich_builder: _RichSnapshotBuilder,
    *,
    light_projector: Callable | None = None,
    rich_config_revision: Callable | None = None,
) -> ProjectionPublicationDependencies:
    return ProjectionPublicationDependencies(
        snapshot_builder=snapshot_builder,
        current_snapshot=_current_snapshot,
        writer=writer,
        projector=project_future_intervals,
        rich_snapshot_builder=rich_builder,
        rich_projector=_saved_projector,
        light_projector=light_projector,
        complete_writer=writer,
        rich_config_revision=rich_config_revision,
        now=lambda: _NOW,
    )


def _saved_projector(snapshot: ClimateScheduleSnapshot) -> RichTrajectoryEnvelope | None:
    return project_saved_trajectory(snapshot, "Veg Room", "0000000")


def _current_snapshot() -> CurrentSnapshot:
    """One compatible current authority snapshot for the publication validator."""
    return CurrentSnapshot(
        version=PublicationVersion(contract_version=1, config_version=5, revision="0000009"),
        observed_at=_NOW,
        valid_until=_NOW + timedelta(seconds=30),
        series=(),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def _canonical_light_projector():
    return lambda snapshot: project_light_segments(snapshot, "0000000")


@pytest.mark.asyncio
async def test_publication_roundtrip_carries_rich_climate_and_lights_together() -> None:
    # Given: an action with one saved climate envelope and canonical light segments.
    writer = _PublicationWriter()
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        _dependencies(
            writer,
            _SnapshotBuilder(_monitor_snapshot()),
            _RichSnapshotBuilder(_scheduled_climate_snapshot()),
            light_projector=_canonical_light_projector(),
        ),
    )

    # When: a complete publication is built.
    outcome = await action.publish()

    # Then: one complete write pairs the legacy intervals with a rich envelope.
    assert outcome is True
    assert [call[0] for call in writer.calls] == ["complete"]
    projections, trajectory = writer.calls[0][1], writer.calls[0][2]
    assert {"heating", "vpd", "light.photoperiod", "light.intensity.light_1"} <= {
        segment.metric for segment in trajectory.segments
    }
    lights = _by_metric(trajectory.segments, "light.intensity.light_1")
    assert lights
    assert all(segment.trajectory_kind == "effective" for segment in lights)
    assert all(segment.quality in {"estimated", "unavailable"} for segment in lights)
    assert trajectory.revision_scope == "saved"
    assert trajectory.base_config_revision == "0000000"
    assert trajectory.window.start == _NOW
    assert trajectory.window.end == _NOW + timedelta(hours=24)
    basic_light_rows = [
        point
        for projection in projections
        for point in projection.series
        if point.series_id.value == "light.intensity.light_1"
    ]
    assert basic_light_rows, "the legacy rows keep publishing while rich adds lights"


@pytest.mark.asyncio
async def test_publication_roundtrip_rewrites_light_revision_with_config_cursor() -> None:
    # Given: the publication is driven to the persistent config revision cursor.
    writer = _PublicationWriter()

    async def rich_revision() -> str:
        return "0000005"

    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        _dependencies(
            writer,
            _SnapshotBuilder(_monitor_snapshot()),
            _RichSnapshotBuilder(_scheduled_climate_snapshot()),
            light_projector=_canonical_light_projector(),
            rich_config_revision=rich_revision,
        ),
    )

    # When: the publication publishes with the revision fence aligned.
    outcome = await action.publish()

    # Then: every segment shares the authoritative revision and version.
    assert outcome is True
    projections, trajectory = writer.calls[0][1], writer.calls[0][2]
    revisions = {segment.source.config_revision for segment in trajectory.segments}
    assert revisions == {"0000005"}
    assert trajectory.base_config_revision == "0000005"
    assert {projection.version.config_version for projection in projections} == {5}


@pytest.mark.asyncio
async def test_light_only_forecast_publishes_when_climate_rich_returns_none() -> None:
    # Given: no saved climate schedule but valid light authority.
    writer = _PublicationWriter()
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        _dependencies(
            writer,
            _SnapshotBuilder(_monitor_snapshot()),
            _RichSnapshotBuilder(_empty_climate_snapshot()),
            light_projector=_canonical_light_projector(),
        ),
    )

    # When: the publication runs.
    outcome = await action.publish()

    # Then: a light-only saved envelope still reaches the complete write path.
    assert outcome is True
    assert [call[0] for call in writer.calls] == ["complete"]
    projections, trajectory = writer.calls[0][1], writer.calls[0][2]
    assert {
        segment.metric for segment in trajectory.segments if segment.metric.startswith("light")
    } == {"light.photoperiod", "light.intensity.light_1"}
    assert trajectory.base_config_revision == "0000000"
    assert trajectory.window.start == _NOW
    assert trajectory.window.end == _NOW + timedelta(hours=24)
    assert projections, "the legacy basic rows still publish alongside the light-only rich"


@pytest.mark.asyncio
async def test_incompatible_light_revision_cannot_replace_the_publication() -> None:
    # Given: light segments carrying a revision foreign to the climate envelope.
    writer = _PublicationWriter()
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        _dependencies(
            writer,
            _SnapshotBuilder(_monitor_snapshot()),
            _RichSnapshotBuilder(_scheduled_climate_snapshot()),
            light_projector=lambda snapshot: project_light_segments(snapshot, "ccccccc"),
        ),
    )

    # When: the publication runs.
    outcome = await action.publish()

    # Then: incompatible authority publishes neither a mixed envelope nor a fallback.
    assert outcome is False
    assert writer.calls == []


@pytest.mark.asyncio
async def test_publication_without_light_projector_keeps_climate_only_rich() -> None:
    # Given: an action wired without the canonical light projector.
    writer = _PublicationWriter()
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        _dependencies(
            writer,
            _SnapshotBuilder(_monitor_snapshot()),
            _RichSnapshotBuilder(_scheduled_climate_snapshot()),
        ),
    )

    # When: the publication runs.
    outcome = await action.publish()

    # Then: the climate-only envelope publishes with no light metrics.
    assert outcome is True
    assert [call[0] for call in writer.calls] == ["complete"]
    trajectory = writer.calls[0][2]
    assert not any(segment.metric.startswith("light") for segment in trajectory.segments)
