from datetime import UTC, datetime, timedelta

from app.control.decision_event_policy import DecisionEventPolicy, DecisionObservation


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
) -> DecisionObservation:
    return DecisionObservation(
        location="Flower Room",
        cluster="main",
        device_name="heater-1",
        timestamp=timestamp,
        controller="pid",
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
