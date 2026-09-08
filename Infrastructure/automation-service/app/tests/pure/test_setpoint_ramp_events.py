from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.control.setpoint_manager import RampManager
from app.events.operational_models import OperationalEvent


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


def test_setpoint_ramp_emits_start_skipped_midpoint_and_completion_once() -> None:
    # Given: a ten-minute heating ramp with an operational event sink.
    sink = RecordingSink()
    manager = RampManager(event_sink=sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)

    # When: one control tick jumps from below to above the midpoint, then reaches target.
    manager.start_ramp("Veg Room", "main", "heating", 20.0, 30.0, 10.0, started_at)
    manager.get_ramp_value("Veg Room", "main", "heating", 30.0, started_at + timedelta(minutes=4))
    manager.get_ramp_value("Veg Room", "main", "heating", 30.0, started_at + timedelta(minutes=6))
    manager.get_ramp_value("Veg Room", "main", "heating", 30.0, started_at + timedelta(minutes=10))

    # Then: semantic lifecycle transitions occur exactly once, without per-tick output events.
    assert [event.event_type for event in sink.events] == [
        "ramp.started",
        "ramp.midpoint_reached",
        "ramp.completed",
    ]
    assert sink.events[0].payload.start_value == 20.0
    assert sink.events[0].payload.target_value == 30.0
    assert sink.events[0].payload.duration_seconds == 600


def test_setpoint_ramp_replacement_cancellation_and_restore_do_not_replay_phases() -> None:
    # Given: an active ramp, then a restored ramp already beyond its midpoint.
    sink = RecordingSink()
    manager = RampManager(event_sink=sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    manager.start_ramp("Veg Room", "main", "co2", 400.0, 800.0, 10.0, started_at)
    manager.start_ramp(
        "Veg Room", "main", "co2", 420.0, 900.0, 10.0, started_at + timedelta(minutes=2)
    )
    manager.cancel_ramp("Veg Room", "main", "co2", started_at + timedelta(minutes=3))
    manager.restore_ramp(
        "Veg Room",
        "main",
        "heating",
        20.0,
        30.0,
        10.0,
        started_at,
        started_at + timedelta(minutes=7),
    )

    # When: the restored ramp completes.
    manager.get_ramp_value("Veg Room", "main", "heating", 30.0, started_at + timedelta(minutes=10))

    # Then: replacement/cancellation are visible, restoration emits completion only.
    assert [event.event_type for event in sink.events] == [
        "ramp.started",
        "ramp.interrupted",
        "ramp.started",
        "ramp.cancelled",
        "ramp.completed",
    ]


def test_zero_duration_setpoint_ramp_emits_an_instant_lifecycle() -> None:
    # Given: a zero-duration target transition.
    sink = RecordingSink()
    manager = RampManager(event_sink=sink)

    # When: the target is applied.
    manager.start_ramp(
        "Veg Room", "main", "heating", 20.0, 30.0, 0.0, datetime(2026, 9, 1, tzinfo=UTC)
    )

    # Then: it has a start and completion, but no midpoint or active state.
    assert [event.event_type for event in sink.events] == ["ramp.started", "ramp.completed"]
    assert manager.has_active_ramps("Veg Room", "main") is False


def test_setpoint_period_replacement_interrupts_before_starting_the_next_ramp() -> None:
    # Given: a climate-period ramp that is replaced before completion.
    sink = RecordingSink()
    manager = RampManager(event_sink=sink)
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    manager.start_ramp("Veg Room", "main", "heating", 20.0, 30.0, 10.0, started_at)

    # When: the period authority clears the old ramp before starting its replacement.
    manager.clear_ramps_for_room("Veg Room", "main")
    manager.start_ramp("Veg Room", "main", "heating", 25.0, 18.0, 10.0, started_at)

    # Then: the old ramp is interrupted rather than cancelled and the replacement starts.
    assert [event.event_type for event in sink.events] == [
        "ramp.started",
        "ramp.interrupted",
        "ramp.started",
    ]
