from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import anyio

from app.automation.rules_engine import RulesEngine
from app.control.control_engine import ControlEngine
from app.control.decision_event_policy import DecisionEventPolicy
from app.control.device_controller import DeviceController


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit_nowait(self, event) -> None:
        self.events.append(event)


class AlwaysActiveScheduler:
    def is_schedule_active(self, location, cluster, device_name, current_time):
        return True, 7


def test_rule_match_emits_rule_and_schedule_context() -> None:
    # Given: one active rule and a recording operational-event sink
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    engine = RulesEngine(
        [
            {
                "id": 42,
                "location": "Flower Room",
                "cluster": "main",
                "action_device": "heater-1",
                "action_state": 1,
                "condition_sensor": "temperature",
                "condition_operator": "<",
                "condition_value": 20.0,
                "schedule_id": 7,
            }
        ],
        AlwaysActiveScheduler(),
        event_policy=policy,
    )

    # When: the rule condition is met
    result = engine.evaluate(
        "Flower Room",
        "main",
        {"temperature": 19.0},
        datetime(2026, 9, 1, tzinfo=UTC),
    )

    # Then: the decision and its causal identifiers are emitted
    assert result == ("heater-1", 1, 42)
    assert [event.event_type for event in sink.events] == ["control.rule_matched"]
    assert sink.events[0].reason_code == "control.rule_matched"
    assert sink.events[0].reason_text == "Rule 42 matched under schedule 7"
    assert engine._event_policy is policy


def test_control_engine_startup_injects_one_policy_into_decision_producers() -> None:
    # Given: one shared decision policy at the composition boundary.
    policy = DecisionEventPolicy()
    rules_engine = RulesEngine([], MagicMock(), event_policy=policy)
    config = MagicMock()
    config.get_control_config.return_value = {}
    database = MagicMock()
    database._automation_redis = MagicMock()

    # When: the control engine composes its controller-specific producers.
    engine = ControlEngine(
        relay_manager=MagicMock(),
        database=database,
        config=config,
        scheduler=MagicMock(),
        rules_engine=rules_engine,
        runtime_device_registry=MagicMock(),
        event_policy=policy,
    )

    # Then: every decision producer retains the exact same policy object.
    assert rules_engine._event_policy is policy
    assert engine.decision_event_policy is policy
    assert engine.pid_controller_manager._event_policy is policy
    assert engine.device_controller._event_policy is policy


def test_manual_control_reports_only_mode_transitions_across_repeated_ticks() -> None:
    # Given: one manual device processed for sixty identical control ticks.
    sink = RecordingSink()
    controller = DeviceController(MagicMock(), MagicMock(), DecisionEventPolicy(sink))
    manual_device = {"control_mode": "manual", "device_type": "heating", "device_id": 1}
    started_at = datetime(2026, 9, 1, tzinfo=UTC)

    for tick in range(60):
        anyio.run(
            controller.process_device,
            "Flower Room",
            "main",
            "heater-1",
            manual_device,
            {},
            started_at + timedelta(seconds=tick),
            {},
        )

    # When: the registry replaces the identity, then the device leaves and re-enters manual mode.
    anyio.run(
        controller.process_device,
        "Flower Room",
        "main",
        "heater-1",
        {"control_mode": "manual", "device_type": "heating", "device_id": 2},
        {},
        started_at.replace(minute=1),
        {},
    )
    anyio.run(
        controller.process_device,
        "Flower Room",
        "main",
        "heater-1",
        {"control_mode": "auto", "device_type": "unknown", "device_id": 1},
        {},
        started_at.replace(minute=1, second=1),
        {},
    )
    anyio.run(
        controller.process_device,
        "Flower Room",
        "main",
        "heater-1",
        manual_device,
        {},
        started_at.replace(minute=1, second=2),
        {},
    )

    # Then: unchanged ticks stay quiet, replacement and re-entry report once each.
    manual_events = [event for event in sink.events if event.payload.controller == "device"]
    assert [event.event_type for event in manual_events] == [
        "control.mode_changed",
        "control.mode_changed",
        "control.mode_changed",
    ]
    controller.relay_manager.set_device_state.assert_not_called()


def test_sensorless_flower_light_ticks_skip_numerical_events_and_apply_outputs() -> None:
    # Given: a scheduled Flower light with no mapped sensor input.
    sink = RecordingSink()
    controller = DeviceController(MagicMock(), MagicMock(), DecisionEventPolicy(sink))
    controller._apply_control_output = AsyncMock()
    light = {
        "control_mode": "auto",
        "device_type": "light",
        "dimming_enabled": True,
        "dimming_type": "dfr0971",
    }
    started_at = datetime(2026, 9, 1, tzinfo=UTC)

    async def process_ticks() -> None:
        # When: two consecutive scheduled light decisions are processed.
        for timestamp in (started_at, started_at + timedelta(seconds=1)):
            await controller.process_device(
                "Flower Room",
                "main",
                "light_f_1",
                light,
                {},
                timestamp,
                {"light_intensity": 0.42},
            )

    anyio.run(process_ticks)

    # Then: both calculated outputs cross the apply boundary unchanged and stay event-quiet:
    #       rule-driven lights have no mapped sensor by design, so missing inputs are never reported.
    assert controller._apply_control_output.await_args_list[0].args[4] == 0.42
    assert controller._apply_control_output.await_args_list[1].args[4] == 0.42
    assert [event.event_type for event in sink.events] == []
