from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.control.decision_event_policy import DecisionEventPolicy, DecisionObservation
from app.control.device_control_context import build_initial_control_context
from app.control.pid_controller_manager import PIDControllerManager


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit_nowait(self, event) -> None:
        self.events.append(event)


class FailingSink:
    def emit_nowait(self, event) -> None:
        raise RuntimeError("sink unavailable")


def observation(
    timestamp: datetime,
    output_percent: float,
    *,
    sensor_value: float | None = 20.0,
    setpoint: float | None = 22.0,
    mode: str = "auto",
    controller: str = "pid",
) -> DecisionObservation:
    return DecisionObservation(
        location="Flower Room",
        cluster="main",
        device_name="heater-1",
        timestamp=timestamp,
        controller=controller,
        sensor_value=sensor_value,
        effective_setpoint=setpoint,
        error=(setpoint - sensor_value)
        if setpoint is not None and sensor_value is not None
        else None,
        output_percent=output_percent,
        control_mode=mode,
        reason_code="pid.calculated",
        reason_text="PID calculated control output",
    )


def test_numerical_adjustment_emits_at_exact_output_and_time_thresholds() -> None:
    # Given: a silent startup baseline and a recording sink
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0))

    # When: output changes at the exact value/time boundaries
    policy.observe(observation(started_at + timedelta(seconds=29.99), 25.0))
    policy.observe(observation(started_at + timedelta(seconds=30), 24.99))
    policy.observe(observation(started_at + timedelta(seconds=30), 25.0))

    # Then: only the >= 5 point and >= 30 second observation emits
    assert [event.event_type for event in sink.events] == ["control.adjusted"]
    assert sink.events[0].payload.output_percent == 25.0


def test_lifecycle_events_emit_immediately_and_startup_baseline_is_silent() -> None:
    # Given: a policy with a normal output baseline
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0))

    # When: saturation, recovery, a setpoint change, a mode change, and missing input occur
    policy.observe(observation(started_at + timedelta(seconds=1), 100.0))
    policy.observe(observation(started_at + timedelta(seconds=2), 20.0))
    policy.observe(observation(started_at + timedelta(seconds=3), 20.0, setpoint=23.0))
    policy.observe(observation(started_at + timedelta(seconds=4), 20.0, mode="failsafe"))
    policy.observe(
        observation(started_at + timedelta(seconds=5), 20.0, sensor_value=None, mode="failsafe")
    )

    # Then: lifecycle events are immediate and context is preserved
    assert [event.event_type for event in sink.events] == [
        "control.saturated",
        "control.recovered",
        "control.setpoint_changed",
        "control.failsafe_entered",
        "control.input_missing",
    ]
    assert sink.events[-1].payload.sensor_value is None
    assert sink.events[-1].reason_code == "control.input_missing"


def test_sink_failure_does_not_change_policy_or_returned_observation() -> None:
    # Given: a sink that raises when control emits an event
    policy = DecisionEventPolicy(FailingSink())
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    baseline = observation(started_at, 20.0)
    adjusted = observation(started_at + timedelta(seconds=30), 25.0)
    policy.observe(baseline)

    # When: an eligible numerical adjustment is observed
    result = policy.observe(adjusted)

    # Then: the observation remains unchanged despite the sink failure
    assert result is adjusted


def test_float_artifact_setpoint_does_not_emit_a_lifecycle_event() -> None:
    # Given: a normal decision baseline with the decimal setpoint 0.3.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=0.3))

    # When: the equivalent IEEE-754 artifact is observed by the same controller.
    policy.observe(observation(started_at + timedelta(seconds=1), 20.0, setpoint=0.1 + 0.2))

    # Then: no setpoint lifecycle transition is emitted.
    assert [event.event_type for event in sink.events] == []


def test_setpoint_change_above_meaningful_threshold_emits_a_lifecycle_event() -> None:
    # Given: a normal decision baseline.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=22.0))

    # When: the effective setpoint changes by more than the unknown-type
    # fallback threshold (0.01 absolute).
    policy.observe(observation(started_at + timedelta(seconds=1), 20.0, setpoint=22.02))

    # Then: the changed setpoint emits exactly one lifecycle transition.
    assert [event.event_type for event in sink.events] == ["control.setpoint_changed"]
    assert sink.events[0].payload.effective_setpoint == 22.02


def test_shared_policy_emits_one_transition_for_one_logical_controller() -> None:
    # Given: one policy observes a controller's initial setpoint.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=22.0, controller="pid"))

    # When: the same logical controller repeats one setpoint transition.
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, setpoint=23.0, controller="pid")
    )
    policy.observe(
        observation(started_at + timedelta(seconds=2), 20.0, setpoint=23.0, controller="pid")
    )

    # Then: that transition is emitted once.
    assert [event.event_type for event in sink.events] == ["control.setpoint_changed"]


def test_distinct_controllers_keep_independent_setpoint_transitions() -> None:
    # Given: two controllers for the same device share one policy.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=22.0, controller="pid"))
    policy.observe(observation(started_at, 20.0, setpoint=22.0, controller="rule"))

    # When: each controller independently changes its effective setpoint.
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, setpoint=23.0, controller="pid")
    )
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, setpoint=23.0, controller="rule")
    )

    # Then: both controller-specific transitions are retained.
    assert [event.event_type for event in sink.events] == [
        "control.setpoint_changed",
        "control.setpoint_changed",
    ]


def test_input_loss_precedes_nullable_setpoint_comparison() -> None:
    # Given: an available-input decision baseline.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=22.0))

    # When: both control inputs become unavailable.
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, sensor_value=None, setpoint=None)
    )

    # Then: the input lifecycle event wins over a nullable setpoint transition.
    assert [event.event_type for event in sink.events] == ["control.input_missing"]


def test_input_recovery_precedes_nullable_setpoint_comparison() -> None:
    # Given: a decision baseline with unavailable control inputs.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, sensor_value=None, setpoint=None))

    # When: the same controller receives both inputs and its effective setpoint.
    policy.observe(observation(started_at + timedelta(seconds=1), 20.0, setpoint=22.0))

    # Then: input recovery wins over a nullable setpoint transition.
    assert [event.event_type for event in sink.events] == [
        "control.input_missing",
        "control.input_recovered",
    ]


def test_rapid_movement_uses_oldest_inclusive_sixty_second_sample_after_slow_event() -> None:
    # Given: samples at 40%, 46%, and 60% with the slow event at 30 seconds.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: the endpoint reaches 60% at 55 seconds.
    policy.observe(observation(started_at + timedelta(seconds=30), 46.0))
    policy.observe(observation(started_at + timedelta(seconds=55), 60.0))

    # Then: slow and rapid net movement both report without changing the observation.
    assert [event.event_type for event in sink.events] == [
        "control.adjusted",
        "control.rapid_adjusted",
    ]
    assert sink.events[-1].payload.output_percent == 60.0
    assert (
        sink.events[-1].reason_text
        == "Rapid control output movement; PID calculated control output"
    )


def test_rapid_anchor_prevents_duplicate_and_allows_later_endpoint_movement() -> None:
    # Given: a rapid movement has anchored at 60% at 55 seconds.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    for seconds, output in ((0, 40.0), (30, 46.0), (55, 60.0)):
        policy.observe(observation(started_at + timedelta(seconds=seconds), output))

    # When: the output repeats once, then moves 20 points beyond the rapid anchor.
    policy.observe(observation(started_at + timedelta(seconds=56), 60.0))
    policy.observe(observation(started_at + timedelta(seconds=57), 80.0))

    # Then: only the later endpoint movement creates a second rapid event.
    assert [event.event_type for event in sink.events] == [
        "control.adjusted",
        "control.rapid_adjusted",
        "control.rapid_adjusted",
    ]


def test_one_observation_emits_only_one_numerical_event_when_both_rules_are_eligible() -> None:
    # Given: a 40% initial command and no intervening numerical event.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: one command reaches both the slow and rapid thresholds at 30 seconds.
    policy.observe(observation(started_at + timedelta(seconds=30), 60.0))

    # Then: the rapid event wins the single numerical-event slot.
    assert [event.event_type for event in sink.events] == ["control.rapid_adjusted"]


def test_fixed_output_does_not_repeat_rapid_events() -> None:
    # Given: a rapid movement has already been reported.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))
    policy.observe(observation(started_at + timedelta(seconds=1), 60.0))

    # When: the same output remains fixed for later observations.
    for seconds in (2, 30, 60, 90):
        policy.observe(observation(started_at + timedelta(seconds=seconds), 60.0))

    # Then: no repeated numerical event is emitted for a fixed command.
    assert [event.event_type for event in sink.events] == ["control.rapid_adjusted"]


def test_rapid_movement_does_not_accumulate_oscillation() -> None:
    # Given: an output that reaches 60% and returns to its 50% endpoint.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 50.0))

    # When: the command oscillates before the numerical interval elapses.
    policy.observe(observation(started_at + timedelta(seconds=1), 60.0))
    policy.observe(observation(started_at + timedelta(seconds=2), 50.0))

    # Then: neither accumulated jitter nor an endpoint-zero delta emits.
    assert sink.events == []


def test_rapid_samples_expire_only_after_the_inclusive_sixty_second_boundary() -> None:
    # Given: a 20-point movement is initially anchored at time zero.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: the endpoint is exactly 60 seconds away, then one second older.
    policy.observe(observation(started_at + timedelta(seconds=60), 59.0))
    policy.observe(observation(started_at + timedelta(seconds=61), 60.0))

    # Then: the boundary sample remains eligible, but the expired sample cannot cause a rapid event.
    assert [event.event_type for event in sink.events] == ["control.adjusted"]


def test_numerical_samples_reset_on_input_loss_mode_change_and_backwards_clock() -> None:
    # Given: a policy with a movement history.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: input loss, mode change, and a backwards clock each interrupt the history.
    policy.observe(observation(started_at + timedelta(seconds=1), 50.0, sensor_value=None))
    policy.observe(observation(started_at + timedelta(seconds=2), 60.0, mode="failsafe"))
    policy.observe(observation(started_at - timedelta(seconds=1), 80.0, mode="auto"))
    policy.observe(observation(started_at + timedelta(seconds=59), 99.0, mode="auto"))

    # Then: no stale pre-transition sample produces a rapid event.
    assert [event.event_type for event in sink.events] == [
        "control.input_missing",
        "control.failsafe_entered",
        "control.adjusted",
    ]


def test_saturation_lifecycle_retains_pending_numerical_movement_for_next_observation() -> None:
    # Given: a pending slow movement and an unsaturated baseline.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: saturation takes the one event slot, followed by the eligible numerical observation.
    policy.observe(observation(started_at + timedelta(seconds=30), 100.0))
    policy.observe(observation(started_at + timedelta(seconds=31), 46.0))
    policy.observe(observation(started_at + timedelta(seconds=32), 46.0))

    # Then: saturation is reported first and the pending slow movement follows.
    assert [event.event_type for event in sink.events] == [
        "control.saturated",
        "control.recovered",
        "control.adjusted",
    ]


def test_effective_setpoint_changes_do_not_reset_numerical_samples() -> None:
    # Given: a sample history with a changing effective target.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))

    # When: the target changes and the output reaches the rapid endpoint.
    policy.observe(observation(started_at + timedelta(seconds=1), 41.0, setpoint=23.0))
    policy.observe(observation(started_at + timedelta(seconds=55), 60.0, setpoint=23.0))

    # Then: the original sample remains the endpoint-net comparison anchor.
    assert [event.event_type for event in sink.events] == [
        "control.setpoint_changed",
        "control.rapid_adjusted",
    ]


def test_distinct_device_identities_do_not_share_numerical_history() -> None:
    # Given: two observations with the same display name in distinct locations.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0))
    other = replace(observation(started_at + timedelta(seconds=1), 60.0), location="Back Room")
    policy.observe(other)

    # When: the second location later reaches the first location's rapid delta.
    policy.observe(
        replace(other, timestamp=started_at + timedelta(seconds=56), output_percent=66.0)
    )

    # Then: the first room's history does not make the second device rapid.
    assert [event.event_type for event in sink.events] == ["control.adjusted"]


def test_ramping_now_maps_each_pid_device_type_to_its_own_ramp_domain() -> None:
    # Given: a control context built from engine effective data with a heating
    # ramp and a VPD ramp mid-flight.
    context = build_initial_control_context(
        "Veg Room",
        "main",
        {
            "effective_heating_setpoint": 21.0,
            "ramp_progress_heating": 0.5,
            "ramp_progress_humidity": None,
            "ramp_progress_vpd": 0.25,
            "ramp_progress_cooling": None,
            "ramp_progress_co2": None,
        },
        current_mode="day",
        previous_climate_mode=None,
    )

    # Then: heating and the VPD-driven dehumidifier are ramping; the others are not.
    assert PIDControllerManager.ramping_now("heating", context) is True
    assert PIDControllerManager.ramping_now("cooling", context) is False
    assert PIDControllerManager.ramping_now("co2", context) is False
    assert PIDControllerManager.ramping_now("dehumidifier", context) is True
    assert PIDControllerManager.ramping_now("humidifier", context) is False

    # When: a humidity ramp becomes active.
    context["ramp_progress"]["humidity"] = 0.1

    # Then: the humidifier lane is marked as ramping without reading the VPD domain.
    assert PIDControllerManager.ramping_now("humidifier", context) is True
