from __future__ import annotations

from datetime import UTC, datetime

from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.scheduler import Scheduler
from app.events.operational_models import OperationalEvent


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


def test_light_ramp_emits_start_midpoint_completion_and_replacement() -> None:
    # Given: a one-hour photoperiod ramp for one light.
    sink = RecordingSink()
    scheduler = Scheduler([], event_sink=sink)
    scheduler.install_snapshot(_snapshot(80.0))

    # When: ticks begin at sun, skip the midpoint, then replace the target and complete.
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    )
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 40, tzinfo=UTC)
    )
    scheduler.install_snapshot(_snapshot(60.0))
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 45, tzinfo=UTC)
    )
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    )

    # Then: each lifecycle edge is emitted once with photoperiod context.
    assert [event.event_type for event in sink.events] == [
        "ramp.started",
        "ramp.midpoint_reached",
        "ramp.interrupted",
        "ramp.started",
        "ramp.completed",
    ]
    assert sink.events[0].payload.phase == "sunrise"
    assert sink.events[0].payload.duration_seconds == 3600
    assert sink.events[0].reason_code == "photoperiod"


def test_light_ramp_emits_failure_when_target_is_not_numeric() -> None:
    # Given: a photoperiod light with an invalid stored target.
    sink = RecordingSink()
    scheduler = Scheduler([], event_sink=sink)
    scheduler.install_snapshot(_snapshot("invalid"))

    # When: the scheduler resolves the target.
    intensity = scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    )

    # Then: the failsafe output remains unchanged and the failure is observable once.
    assert intensity == 10.0
    assert [event.event_type for event in sink.events] == ["ramp.failed"]


def test_light_ramp_failure_is_deduplicated_until_the_invalid_target_changes() -> None:
    # Given: a scheduler that repeatedly receives one invalid target.
    sink = RecordingSink()
    scheduler = Scheduler([], event_sink=sink)
    scheduler.install_snapshot(_snapshot("invalid"))

    # When: two ticks resolve it, then a new invalid target replaces it.
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    )
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 1, tzinfo=UTC)
    )
    scheduler.install_snapshot(_snapshot("still-invalid"))
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 8, 2, tzinfo=UTC)
    )

    # Then: failure is one semantic transition per distinct invalid target.
    assert [event.event_type for event in sink.events] == ["ramp.failed", "ramp.failed"]


def test_light_ramp_down_emits_sunset_lifecycle_and_target_replacement() -> None:
    # Given: a one-hour sunset ramp.
    sink = RecordingSink()
    scheduler = Scheduler([], event_sink=sink)
    scheduler.install_snapshot(_snapshot(80.0, ramp_down=60))

    # When: it starts, crosses halfway, receives a new target, then reaches moon.
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 19, 0, tzinfo=UTC)
    )
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 19, 31, tzinfo=UTC)
    )
    scheduler.install_snapshot(_snapshot(60.0, ramp_down=60))
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 19, 40, tzinfo=UTC)
    )
    scheduler.get_schedule_intensity(
        "Veg Room", "main", "light_v_1", datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    )

    # Then: sunset emits its start, midpoint, replacement, and completion only once.
    assert [event.event_type for event in sink.events] == [
        "ramp.started",
        "ramp.midpoint_reached",
        "ramp.interrupted",
        "ramp.started",
        "ramp.completed",
    ]
    assert sink.events[0].payload.phase == "sunset"


def _snapshot(target_intensity: float | str, *, ramp_down: int = 0) -> RuntimeDeviceSnapshot:
    return RuntimeDeviceSnapshot.create(
        version=1,
        hierarchy={"Veg Room": {"main": {"light_v_1": {"device_id": 42, "device_type": "light"}}}},
        mode_parameters={
            ("Veg Room", "main"): {
                "mode_id": 1,
                "day_start": "08:00",
                "night_start": "20:00",
                "ramp_up": 60,
                "ramp_down": ramp_down,
            }
        },
        light_intensities={(42, 1): target_intensity},
        light_programs=[],
    )
