from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from app.automation.rules_engine import RulesEngine
from app.control.control_engine import ControlEngine
from app.control.decision_event_policy import DecisionEventPolicy, DecisionObservation
from app.control.device_controller import DeviceController
from app.control.hardware_batch import HardwareBatchExecutor
from app.control.pid_controller_manager import PIDControllerManager
from app.control.relay_manager import RelayManager
from relay_snapshot_fakes import Interlocks, Mcp, Registry


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control_mode", "expected_controller", "expected_output", "expected_reason_code"),
    [
        ("on_off", "on_off", 1.0, "on_off.binary_transition"),
        ("auto_pid", "auto_pid", 0.65, "auto_pid.binary_transition"),
        ("pid", "pid", 0.65, "pid.binary_transition"),
    ],
)
async def test_successful_pid_modes_bind_the_exact_decision_to_context(
    control_mode: str,
    expected_controller: str,
    expected_output: float,
    expected_reason_code: str,
) -> None:
    policy = MagicMock()
    manager = PIDControllerManager(MagicMock(), policy)
    manager.get_control_mode_info = AsyncMock(
        return_value={
            "control_mode": control_mode,
            "hysteresis_high": 1.0,
            "hysteresis_low": 0.5,
        }
    )
    controller = MagicMock()
    controller.kp = 1.0
    controller.ki = 0.0
    controller.kd = 0.0
    controller.calculate.return_value = expected_output
    manager.get_pid_controller = AsyncMock(return_value=controller)
    manager._process_autotune_control = AsyncMock(return_value=expected_output)

    context = {
        "effective_heating_setpoint": 22.0,
        "previous_climate_mode": {("Veg Room", "main"): "Veg"},
    }
    observed_at = datetime(2026, 9, 1, tzinfo=UTC)

    output = await manager.process_pid_control(
        "Veg Room",
        "main",
        "heater",
        {"device_type": "heating"},
        {"air_temperature": 20.0},
        observed_at,
        context,
        current_mode="Veg",
    )

    observation = context["pid_decision_observation"]
    assert isinstance(observation, DecisionObservation)
    assert observation.controller == expected_controller
    assert observation.sensor_value == 20.0
    assert observation.effective_setpoint == 22.0
    assert observation.output_percent == expected_output * 100.0
    assert output == expected_output
    policy.observe.assert_called_once_with(observation)
    reason = DeviceController._build_pid_relay_reason(
        observation, "heating", expected_output, 1, 0, 0.6
    )
    assert reason is not None
    assert reason.reason_code == expected_reason_code


@pytest.mark.asyncio
async def test_missing_pid_input_does_not_leave_a_decision_in_context() -> None:
    manager = PIDControllerManager(MagicMock(), MagicMock())
    manager.get_control_mode_info = AsyncMock(return_value={"control_mode": "on_off"})
    context = {
        "effective_heating_setpoint": 22.0,
        "pid_decision_observation": object(),
    }

    output = await manager.process_pid_control(
        "Veg Room",
        "main",
        "heater",
        {"device_type": "heating"},
        {},
        datetime(2026, 9, 1, tzinfo=UTC),
        context,
    )

    assert output is None
    assert "pid_decision_observation" not in context


@pytest.mark.asyncio
async def test_pid_relay_causes_cover_direct_and_queued_hysteresis_transitions() -> None:
    sink = RecordingSink()
    policy = DecisionEventPolicy(sink)
    mcp: Any = Mcp([True, True])
    relay_manager: Any = RelayManager(mcp, Registry(), Interlocks(), event_sink=sink)
    pid_manager = PIDControllerManager(MagicMock(), policy)
    pid_manager.get_control_mode_info = AsyncMock(return_value={"control_mode": "pid"})
    pid_controller = MagicMock()
    pid_controller.kp = 1.0
    pid_controller.ki = 0.0
    pid_controller.kd = 0.0
    pid_controller.calculate.side_effect = [0.65, 0.35, 0.55]
    pid_manager.get_pid_controller = AsyncMock(return_value=pid_controller)
    device_controller = DeviceController(relay_manager, MagicMock(), policy)
    device_controller._last_binary_state[("Veg Room", "main", "heater")] = 0
    device_info = {"device_type": "heating", "channel": 2, "control_mode": "auto"}
    started_at = datetime(2026, 9, 1, tzinfo=UTC)

    async def process_tick(tick: int, *, queued: bool) -> None:
        context = {
            "effective_heating_setpoint": 22.0,
            "previous_climate_mode": {("Veg Room", "main"): "Veg"},
        }
        current_time = started_at + timedelta(seconds=tick)
        output = await pid_manager.process_pid_control(
            "Veg Room",
            "main",
            "heater",
            device_info,
            {"air_temperature": 20.0},
            current_time,
            context,
            current_mode="Veg",
        )
        assert output is not None
        context["pid_output"] = output
        assert isinstance(context["pid_decision_observation"], DecisionObservation)
        executor = HardwareBatchExecutor() if queued else None
        await device_controller.process_device(
            "Veg Room",
            "main",
            "heater",
            device_info,
            {"air_temperature": 20.0},
            current_time,
            context,
            batch_executor=executor,
        )
        if executor is not None:
            result = await executor.execute()
            assert result.failure_count == 0

    await process_tick(0, queued=False)
    await process_tick(1, queued=True)
    await process_tick(2, queued=False)
    await device_controller.process_device(
        "Veg Room",
        "main",
        "heater",
        {**device_info, "control_mode": "manual"},
        {},
        started_at + timedelta(seconds=3),
        {},
    )

    relay_events = [event for event in sink.events if event.event_type.startswith("relay.")]
    commanded = [event for event in relay_events if event.event_type == "relay.commanded"]
    assert mcp.writes == [(2, True), (2, False)]
    assert [event.reason_code for event in commanded] == [
        "pid.binary_transition",
        "pid.binary_transition",
    ]
    assert [event.reason_text for event in commanded] == [
        "PID heating relay ON: sensor 20°C; target 22°C; output 65% crossed >60% ON threshold.",
        "PID heating relay OFF: sensor 20°C; target 22°C; output 35% crossed <40% OFF threshold.",
    ]
    assert len(relay_events) == 2
