"""Pure tests for automation-state decision context and history cadence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
from typing import Any

import pytest

import app.control.control_engine as control_engine_module
from app.control.control_engine import ControlEngine
from app.control.decision_event_policy import DecisionObservation
from app.control.device_processor import DeviceProcessor
from app.repositories.control_actions import ControlActionRepository
from shared.replay_buffer import ReplayBuffer

T0 = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)
KEY = ("Flower Room", "main", "heater_f_1")
SNAPSHOT = SimpleNamespace(hierarchy={KEY[0]: {KEY[1]: {KEY[2]: {}}}})


class _Clock:
    now_value = T0

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        assert tz is UTC
        return cls.now_value


class _FakeRepository:
    def __init__(self, outcomes: list[bool | Exception] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.batches: list[list[dict[str, Any]]] = []

    async def log_automation_state_batch(self, records: list[dict[str, Any]]) -> bool:
        self.batches.append([dict(record) for record in records])
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeRelayManager:
    def get_device_state(self, location: str, cluster: str, device_name: str) -> int | None:
        return 0

    def get_device_mode(self, location: str, cluster: str, device_name: str) -> str:
        return "auto"


class _FakeDeviceController:
    async def process_device(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakePidControllerManager:
    def __init__(self) -> None:
        self.controller = SimpleNamespace(kp=25.0, ki=0.02, kd=0.0)
        self.controller_lookups = 0

    async def process_pid_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: dict[str, float | None],
        current_time: datetime,
        context: dict[str, Any],
        current_mode: str | None,
    ) -> float:
        context["pid_decision_observation"] = DecisionObservation(
            location=location,
            cluster=cluster,
            device_name=device_name,
            timestamp=current_time,
            controller="pid",
            sensor_value=20.0,
            effective_setpoint=22.0,
            error=2.0,
            output_percent=37.5,
            control_mode="pid",
            reason_code="control.pid_decision",
            reason_text="PID calculated control output",
            device_type="heating",
        )
        return 0.375

    async def get_pid_controller(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> Any:
        self.controller_lookups += 1
        return self.controller


def _engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    context: dict[str, Any] | None = None,
    outcomes: list[bool | Exception] | None = None,
) -> tuple[ControlEngine, _FakeRepository]:
    _Clock.now_value = T0
    monkeypatch.setattr(control_engine_module, "datetime", _Clock)
    repository = _FakeRepository(outcomes)
    engine = object.__new__(ControlEngine)
    engine._automation_log_context = {KEY: context or {}}
    engine._automation_context = {KEY: {"live_only": "unchanged"}}
    engine._last_logged = {}
    engine._automation_state_pending = ReplayBuffer(2_000, name="automation_state")
    engine.database = SimpleNamespace(control_action_repo=repository)
    engine.relay_manager = _FakeRelayManager()
    return engine, repository


@pytest.mark.asyncio
async def test_first_tick_writes_and_unchanged_same_slot_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, repository = _engine(monkeypatch, context={"control_reason": "none"})

    await engine._log_automation_state(SNAPSHOT)
    await engine._log_automation_state(SNAPSHOT)

    assert len(repository.batches) == 1
    timestamp = repository.batches[0][0]["time"]
    assert timestamp == T0
    assert timestamp.tzinfo is UTC
    assert engine._automation_context == {KEY: {"live_only": "unchanged"}}


@pytest.mark.asyncio
async def test_decision_field_change_writes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, repository = _engine(monkeypatch, context={"control_reason": "none"})

    await engine._log_automation_state(SNAPSHOT)
    engine._automation_log_context[KEY]["control_reason"] = "control.changed"
    await engine._log_automation_state(SNAPSHOT)

    assert len(repository.batches) == 2
    assert repository.batches[1][0]["control_reason"] == "control.changed"


@pytest.mark.asyncio
async def test_heartbeat_slot_change_writes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, repository = _engine(monkeypatch, context={"control_reason": "none"})

    await engine._log_automation_state(SNAPSHOT)
    _Clock.now_value = T0 + timedelta(seconds=30)
    await engine._log_automation_state(SNAPSHOT)

    assert len(repository.batches) == 2


@pytest.mark.asyncio
async def test_failed_batch_remains_buffered_and_flushes_oldest_first_with_original_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, repository = _engine(
        monkeypatch,
        context={"pid_output": 0.2, "control_reason": "none"},
        outcomes=[True, OSError("disposable transient database outage"), True],
    )

    await engine._log_automation_state(SNAPSHOT)
    _Clock.now_value = T0 + timedelta(seconds=30)
    engine._automation_log_context[KEY]["pid_output"] = 0.8
    await engine._log_automation_state(SNAPSHOT)

    assert len(engine._automation_state_pending) == 1
    assert engine._automation_state_pending.peek(1)[0]["pid_output"] == 0.8
    assert engine._last_logged[KEY][0][2] == 0.8

    _Clock.now_value = T0 + timedelta(seconds=60)
    engine._automation_log_context[KEY]["pid_output"] = 0.9
    await engine._log_automation_state(SNAPSHOT)

    assert len(repository.batches) == 3
    assert [record["pid_output"] for record in repository.batches[2]] == [0.8, 0.9]
    assert [record["time"] for record in repository.batches[2]] == [
        T0 + timedelta(seconds=30),
        T0 + timedelta(seconds=60),
    ]
    assert len(engine._automation_state_pending) == 0


@pytest.mark.asyncio
async def test_permanent_error_drops_failed_batch_and_flushes_later_state_rows(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, repository = _engine(
        monkeypatch,
        outcomes=[ValueError("disposable invalid row"), True],
    )
    records = [
        {
            "time": T0 + timedelta(seconds=index),
            "location": KEY[0],
            "cluster": KEY[1],
            "device_name": KEY[2],
            "device_state": 0,
            "device_mode": "auto",
        }
        for index in range(501)
    ]
    assert engine._automation_state_pending.offer(records) == 501

    with caplog.at_level(logging.ERROR, logger="app.control.control_engine"):
        await engine._log_automation_state(SimpleNamespace(hierarchy={}))

    assert [len(batch) for batch in repository.batches] == [500, 1]
    assert repository.batches[1][0]["time"] == T0 + timedelta(seconds=500)
    assert len(engine._automation_state_pending) == 0
    assert any("Dropped 500 automation-state rows" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_control_action_repository_propagates_batch_write_errors() -> None:
    class _FailingConnection:
        async def __aenter__(self) -> _FailingConnection:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def copy_records_to_table(self, *_args: Any, **_kwargs: Any) -> None:
            raise ValueError("permanent database error")

    repository = ControlActionRepository(SimpleNamespace(acquire=lambda: _FailingConnection()))

    with pytest.raises(ValueError, match="permanent database error"):
        await repository.log_automation_state_batch(
            [
                {
                    "time": T0,
                    "location": KEY[0],
                    "cluster": KEY[1],
                    "device_name": KEY[2],
                    "device_state": 0,
                    "device_mode": "auto",
                }
            ]
        )


@pytest.mark.asyncio
async def test_device_processing_context_flows_into_history_not_live_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, repository = _engine(monkeypatch)
    pid_manager = _FakePidControllerManager()
    processor = DeviceProcessor(
        device_controller=_FakeDeviceController(),
        database=SimpleNamespace(_automation_redis=None),
        pid_controller_manager=pid_manager,
    )
    log_context: dict[tuple[str, str, str], dict[str, Any]] = {}

    await processor.process_devices(
        KEY[0],
        KEY[1],
        {KEY[2]: {"device_type": "heating", "control_mode": "auto"}},
        {"dry_bulb": 20.0},
        T0,
        {"effective_heating_setpoint": 22.0},
        "veg",
        log_context=log_context,
    )
    engine._automation_log_context = log_context
    await engine._log_automation_state(SNAPSHOT)

    assert pid_manager.controller_lookups == 1
    assert log_context[KEY] == {
        "pid_output": 0.375,
        "duty_cycle_percent": 37.5,
        "control_reason": "control.pid_decision",
        "pid_kp": 25.0,
        "pid_ki": 0.02,
        "pid_kd": 0.0,
    }
    record = repository.batches[0][0]
    assert record["pid_output"] == 0.375
    assert record["duty_cycle_percent"] == 37.5
    assert record["control_reason"] == "control.pid_decision"
    assert (record["pid_kp"], record["pid_ki"], record["pid_kd"]) == (25.0, 0.02, 0.0)
    assert engine._automation_context == {KEY: {"live_only": "unchanged"}}
