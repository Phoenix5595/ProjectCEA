"""Restart authorization is exercised only with captured processes and timers."""

from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.events.mutation_context import MutationRequestContext
from app.routes import system_config

PROBE = ["sudo", "-n", "-l", "--", "systemctl", "restart", "automation-service.service"]
ACTION = ["sudo", "-n", "systemctl", "restart", "automation-service.service"]
SEED = '{"hash":"seed-hash","subset":{}}'


class Sink:
    def __init__(self):
        self.events = []

    def emit_nowait(self, event):
        self.events.append(event)


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    config_path = tmp_path / "automation_config.yaml"
    config_path.write_text("hardware:\n  i2c_bus: 1\ncontrol:\n  update_interval: 2\n")
    sidecar_path = tmp_path / "automation_config.restart_hash"
    sidecar_path.write_text(SEED)
    processes = SimpleNamespace(
        run=Mock(return_value=subprocess.CompletedProcess(PROBE, 0)),
        Popen=Mock(),
        DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    monkeypatch.setattr(system_config, "subprocess", processes)
    return SimpleNamespace(
        config=system_config.ConfigLoader(str(config_path)),
        sidecar=sidecar_path,
        processes=processes,
        sink=Sink(),
        scheduled=[],
    )


async def invoke(boundary, monkeypatch):
    def capture(delay, callback, *args):
        boundary.scheduled.append((delay, callback, args))
        # Persistence and event publication must precede any scheduled action.
        assert json.loads(boundary.sidecar.read_text())["hash"] != "seed-hash"
        assert len(boundary.sink.events) == 1

    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", capture)
    return await system_config.restart_service(
        config=boundary.config,
        context=MutationRequestContext(correlation_id=uuid4()),
        sink=boundary.sink,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["denied", "missing", "timeout"])
async def test_probe_failure_denies_without_mutation_or_scheduling(boundary, monkeypatch, failure):
    if failure == "denied":
        boundary.processes.run.return_value = subprocess.CompletedProcess(PROBE, 1)
    elif failure == "missing":
        boundary.processes.run.side_effect = FileNotFoundError("sudo absent")
    else:
        boundary.processes.run.side_effect = subprocess.TimeoutExpired(PROBE, 5)
    with pytest.raises(system_config.HTTPException) as caught:
        await invoke(boundary, monkeypatch)
    assert caught.value.status_code == 403
    assert boundary.sidecar.read_text() == SEED
    assert boundary.sink.events == []
    assert boundary.scheduled == []
    boundary.processes.run.assert_called_once_with(
        PROBE, capture_output=True, timeout=5, check=False
    )
    boundary.processes.Popen.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_restart_persists_before_one_deferred_action(boundary, monkeypatch):
    response = await invoke(boundary, monkeypatch)
    assert response == {
        "status": "restarting",
        "delay_seconds": 1,
        "command": "sudo -n systemctl restart automation-service.service",
    }
    assert len(boundary.scheduled) == 1
    delay, callback, args = boundary.scheduled[0]
    assert delay == 1.0
    assert json.loads(boundary.sidecar.read_text())["subset"] == {
        "hardware": {"i2c_bus": 1},
        "control": {
            "update_interval": 2,
            "safety_limits": {},
            "pid_limits": {},
            "last_good_hold_period": None,
            "binary_hysteresis": None,
        },
    }
    boundary.processes.Popen.assert_not_called()
    callback(*args)
    boundary.processes.Popen.assert_called_once_with(
        ACTION, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    boundary.processes.run.assert_called_once_with(
        PROBE, capture_output=True, timeout=5, check=False
    )


@pytest.mark.asyncio
async def test_sidecar_write_failure_does_not_publish_or_schedule(boundary, monkeypatch):
    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(system_config, "_write_sidecar", fail_write)
    with pytest.raises(OSError, match="disk full"):
        await invoke(boundary, monkeypatch)
    assert boundary.sidecar.read_text() == SEED
    assert boundary.sink.events == []
    assert boundary.scheduled == []
    boundary.processes.Popen.assert_not_called()


@pytest.mark.asyncio
async def test_deferred_spawn_error_is_logged_without_retry(boundary, monkeypatch, caplog):
    await invoke(boundary, monkeypatch)
    error = OSError("spawn blocked")
    boundary.processes.Popen.side_effect = error
    _, callback, args = boundary.scheduled[0]
    with caplog.at_level("ERROR"):
        callback(*args)
    boundary.processes.Popen.assert_called_once_with(
        ACTION, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)
    assert len(boundary.scheduled) == 1
