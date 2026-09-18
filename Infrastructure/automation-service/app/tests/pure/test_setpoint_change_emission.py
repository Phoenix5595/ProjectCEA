"""Meaningful setpoint-change emission: per-device-type thresholds + from->to truth.

These tests characterize the verified 1e-9 spam bug (one 60-minute light ramp
emitted ~10,800 events): setpoint lifecycle changes must only emit once the
cumulative drift crosses a device-type-appropriate threshold, the baseline only
advances on emission (hysteresis), and every emission carries the value the set
drifted FROM in ``previous_setpoint``.
"""

from datetime import UTC, datetime, timedelta

from app.control.decision_event_policy import DecisionEventPolicy, DecisionObservation


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit_nowait(self, event) -> None:
        self.events.append(event)


def observation(
    timestamp: datetime,
    output_percent: float,
    *,
    sensor_value: float | None = 20.0,
    setpoint: float | None = 22.0,
    mode: str = "auto",
    controller: str = "pid",
    device_type: str = "",
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
        device_type=device_type,
    )


def test_light_ramp_emits_once_per_threshold_not_once_per_tick() -> None:
    # Given: a light drifting 0.0002 (fraction) per 1-second tick from 0.2.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=0.2, device_type="light"))

    # When: 100 ramp ticks are observed.
    for tick in range(1, 101):
        policy.observe(
            observation(
                started_at + timedelta(seconds=tick),
                20.0,
                setpoint=0.2 + 0.0002 * tick,
                device_type="light",
            )
        )

    # Then: zero emissions until the cumulative drift crosses 0.005, then
    # threshold-crossing emissions with previous_setpoint equal to the
    # drifted-from value (exact 0.005 boundaries land with float dust, so
    # 100 ticks yield 3 emissions at ticks 25/50/75).
    emissions = list(sink.events)
    assert len(emissions) == 3, len(emissions)
    first = emissions[0]
    assert first.event_type == "control.setpoint_changed"
    assert first.payload.effective_setpoint == 0.2 + 0.0002 * 25
    assert first.payload.previous_setpoint == 0.2
    # Baseline advanced only on emission: the next "from" is the last "to".
    assert emissions[1].payload.previous_setpoint == first.payload.effective_setpoint
    assert emissions[2].payload.previous_setpoint == emissions[1].payload.effective_setpoint


def test_light_ramp_produces_about_thirty_self_contained_events() -> None:
    # Given: a light ramp spanning 0.15 (750 ticks at 0.0002/s).
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=0.2, device_type="light"))

    # When: the whole ramp runs tick by tick.
    for tick in range(1, 751):
        policy.observe(
            observation(
                started_at + timedelta(seconds=tick),
                20.0,
                setpoint=0.2 + 0.0002 * tick,
                device_type="light",
            )
        )

    # Then: ~30 emissions (not one per tick), each self-contained with from->to.
    emissions = [e for e in sink.events if e.event_type == "control.setpoint_changed"]
    assert 29 <= len(emissions) <= 31, len(emissions)
    assert all(e.payload.previous_setpoint is not None for e in emissions)
    for before, after in zip(emissions, emissions[1:], strict=False):
        assert after.payload.previous_setpoint == before.payload.effective_setpoint


def test_instant_setpoint_jump_emits_immediately_with_previous_value() -> None:
    # Given: a light baseline at 0.2.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=0.2, device_type="light"))

    # When: an instant jump to 0.8 is observed one second later.
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, setpoint=0.8, device_type="light")
    )

    # Then: one immediate emission carrying the previous value.
    assert [event.event_type for event in sink.events] == ["control.setpoint_changed"]
    assert sink.events[0].payload.previous_setpoint == 0.2
    assert sink.events[0].payload.effective_setpoint == 0.8


def test_heating_step_emits_at_threshold_crossings() -> None:
    # Given: a heating device stepping 0.01 C per second (threshold 0.1).
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=22.0, device_type="heating"))

    # When: 30 ticks are observed.
    for tick in range(1, 31):
        policy.observe(
            observation(
                started_at + timedelta(seconds=tick),
                20.0,
                setpoint=22.0 + 0.01 * tick,
                device_type="heating",
            )
        )

    # Then: emissions only at 0.1 crossings; exact-boundary float dust lands
    # them at ticks 11 and 21, chained.
    emissions = list(sink.events)
    assert [e.event_type for e in emissions] == ["control.setpoint_changed"] * 2
    assert emissions[0].payload.previous_setpoint == 22.0
    assert emissions[1].payload.previous_setpoint == emissions[0].payload.effective_setpoint


def test_unknown_device_type_uses_conservative_fallback_threshold() -> None:
    # Given: an unmapped device type drifting 0.001 per second.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=50.0, device_type="humidifier"))

    # When: 15 sub-threshold ticks (cumulative 0.015 > 0.01) are observed.
    for tick in range(1, 16):
        policy.observe(
            observation(
                started_at + timedelta(seconds=tick),
                20.0,
                setpoint=50.0 + 0.001 * tick,
                device_type="humidifier",
            )
        )

    # Then: one emission at the 0.01 fallback crossing (tick 11 with float dust).
    emissions = list(sink.events)
    assert [e.event_type for e in emissions] == ["control.setpoint_changed"]
    assert emissions[0].payload.previous_setpoint == 50.0
    assert emissions[0].payload.effective_setpoint == 50.0 + 0.001 * 11


def test_first_ever_setpoint_appearance_carries_no_previous_setpoint() -> None:
    # Given: a controller that starts without an effective setpoint.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=None, device_type="heating"))

    # When: the effective setpoint appears (input recovery precedes the
    # nullable setpoint comparison by design).
    policy.observe(
        observation(started_at + timedelta(seconds=1), 20.0, setpoint=22.0, device_type="heating")
    )

    # Then: no fabricated from-value is attached; previous_setpoint is absent.
    assert all(event.payload.previous_setpoint is None for event in sink.events)
    assert [event.event_type for event in sink.events] == [
        "control.input_missing",
        "control.input_recovered",
    ]


def test_sensorless_rule_device_never_reports_input_missing() -> None:
    # Given/When: a rule-driven device with no mapped sensor ticks twice.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0, sensor_value=None, controller="rule"))
    policy.observe(
        observation(started_at + timedelta(seconds=1), 40.0, sensor_value=None, controller="rule")
    )

    # Then: no input_missing noise for sensorless controllers (7e8fc10 semantics).
    assert sink.events == []


def test_rapid_and_slow_output_thresholds_are_untouched() -> None:
    # Given: a light device with an output history.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 40.0, device_type="light"))

    # When: the output moves 6 points over 30 seconds, then 20 points more.
    policy.observe(observation(started_at + timedelta(seconds=30), 46.0, device_type="light"))
    policy.observe(observation(started_at + timedelta(seconds=55), 66.0, device_type="light"))

    # Then: the 5 pt / 30 s slow gate and the 20 pt rapid gate still fire.
    assert [event.event_type for event in sink.events] == [
        "control.adjusted",
        "control.rapid_adjusted",
    ]


def test_sub_threshold_float_artifact_stays_silent_for_light() -> None:
    # Given: a light baseline with the decimal setpoint 0.3.
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    policy.observe(observation(started_at, 20.0, setpoint=0.3, device_type="light"))

    # When: the equivalent IEEE-754 artifact is observed.
    policy.observe(
        observation(
            started_at + timedelta(seconds=1),
            20.0,
            setpoint=0.1 + 0.2,
            device_type="light",
        )
    )

    # Then: no setpoint lifecycle event is emitted.
    assert sink.events == []
