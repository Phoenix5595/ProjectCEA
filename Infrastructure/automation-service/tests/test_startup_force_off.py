"""Tests for explicit all-off fail-safe at automation-service startup.

The container MUST call `mcp23017.all_off()` immediately after
`_init_hardware()` returns successfully, and BEFORE
`restore_ramp_state_from_database()` and `restore_light_intensities()`.

This guarantees a clean OFF state across unclean reboots, regardless of
what the OLAT latches retained. It also runs in simulation mode so dev
workstation boots behave like production.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# These imports must come from automation-service's working dir so the
# `app` package resolves.
sys.path.insert(0, "/home/antoine/ProjectCEA/Infrastructure/automation-service")


def _build_mock_mcp23017() -> MagicMock:
    """Build a mock MCP23017Driver instance with the surface used by container."""
    inst = MagicMock(name="MCP23017Driver")
    inst.probe.return_value = True
    inst.all_off = MagicMock(name="all_off", return_value=True)
    inst.close = MagicMock(name="close")
    return inst


def _patched_mcp_class(mock_inst: MagicMock):
    """Return a stand-in class for MCP23017Driver that always returns mock_inst."""

    class _FakeMCP:
        def __init__(self, *args, **kwargs):
            # Capture init kwargs for inspection.
            self.init_kwargs = kwargs
            self.init_args = args
            # Delegate attribute access to mock_inst so all_off(), probe(), etc.
            # all hit the same mock recorder.
            self.__dict__.update(mock_inst.__dict__)
            self._mock = mock_inst

        def __getattr__(self, name):
            return getattr(mock_inst, name)

    return _FakeMCP


def _build_container_with_mocks(
    monkeypatch,
    *,
    simulation: bool = False,
    all_off_raises: bool = False,
):
    """Patch the heavy dependencies and return (container, mcp_mock, calls_log).

    `calls_log` is an ordered list of (event_name, args) capturing the order
    in which `all_off`, `restore_ramp_state_from_database`, and
    `restore_light_intensities` were called.
    """
    calls_log: list[tuple[str, Any]] = []

    # Build MCP mock
    mock_mcp = _build_mock_mcp23017()
    fake_mcp_class = _patched_mcp_class(mock_mcp)

    def _all_off_impl(*args, **kwargs):
        calls_log.append(("all_off", None))
        if all_off_raises:
            raise RuntimeError("I2C NACK on all_off")
        return True

    mock_mcp.all_off.side_effect = _all_off_impl

    # Patch MCP23017Driver in the container module
    monkeypatch.setattr("app.container.MCP23017Driver", fake_mcp_class)

    # Build a minimal config mock
    config_mock = MagicMock(name="ConfigLoader")
    config_mock.get_devices.return_value = {
        "Flower Room": {
            "main": {
                "light_1": {
                    "channel": 3,
                    "device_type": "light",
                    "dimming_enabled": True,
                    "dimming_type": "dfr0971",
                    "dimming_board_id": 2,
                    "dimming_channel": 0,
                }
            }
        }
    }
    config_mock.get.side_effect = lambda key, default=None: {
        "interlocks": [],
        "control": {"update_interval": 1, "last_good_hold_period": 30},
    }.get(key, default)
    config_mock.get_update_interval.return_value = 1
    monkeypatch.setattr("app.container.ConfigLoader", lambda *a, **kw: config_mock)

    # Build database mock
    db_mock = MagicMock(name="DatabaseManager")
    db_mock.initialize = AsyncMock(name="db.initialize")
    db_mock._automation_redis = MagicMock(name="automation_redis")
    db_mock._automation_redis.redis_enabled = False
    db_mock.load_schedule_state_to_redis = AsyncMock(name="load_schedule_state")
    db_mock.schedule_repo = MagicMock(name="schedule_repo")
    db_mock.schedule_repo.get_schedules = AsyncMock(return_value=[])
    db_mock.climate_periods_repo = MagicMock(name="climate_periods_repo")
    db_mock.close = AsyncMock(name="db.close")
    db_mock.device_repo = MagicMock(name="device_repo")
    db_mock.device_repo.get_latest_light_intensity = AsyncMock(return_value=None)
    monkeypatch.setattr("app.container.DatabaseManager", lambda *a, **kw: db_mock)

    # Build DFR0971 manager mock
    dfr_mock = MagicMock(name="DFR0971Manager")
    dfr_mock.add_board = MagicMock()
    dfr_mock.set_safety_level = MagicMock(return_value=True)
    dfr_mock.set_intensity = MagicMock(return_value=True)
    monkeypatch.setattr("app.container.DFR0971Manager", lambda *a, **kw: dfr_mock)

    # Patch InterlockManager, RelayManager, RulesEngine, AlarmManager to no-op
    monkeypatch.setattr("app.container.InterlockManager", MagicMock(name="InterlockManager"))
    monkeypatch.setattr("app.container.RelayManager", MagicMock(name="RelayManager"))

    sched_mock = MagicMock(name="Scheduler")
    sched_mock.set_climate_periods_repo = MagicMock()
    monkeypatch.setattr("app.container.Scheduler", lambda *a, **kw: sched_mock)

    monkeypatch.setattr("app.container.RulesEngine", MagicMock(name="RulesEngine"))
    monkeypatch.setattr("app.container.AlarmManager", MagicMock(name="AlarmManager"))

    # ControlEngine: we need to track when its restore_ramp_state_from_database is called
    control_engine_mock = MagicMock(name="ControlEngine")
    control_engine_mock.restore_ramp_state_from_database = AsyncMock(
        side_effect=lambda: calls_log.append(("restore_ramp", None))
    )
    monkeypatch.setattr("app.container.ControlEngine", lambda *a, **kw: control_engine_mock)

    # BackgroundTasks: don't actually start
    bg_mock = MagicMock(name="BackgroundTasks")
    bg_mock.start = AsyncMock()
    monkeypatch.setattr("app.container.BackgroundTasks", lambda *a, **kw: bg_mock)

    # Patch restore_light_intensities and set_safety_levels to record their call order
    from app.initialization import lighting as lighting_module

    async def fake_restore(*args, **kwargs):
        calls_log.append(("restore_lights", None))

    async def fake_safety(*args, **kwargs):
        calls_log.append(("set_safety", None))

    monkeypatch.setattr(lighting_module, "restore_light_intensities", fake_restore)
    monkeypatch.setattr(lighting_module, "set_safety_levels", fake_safety)

    # Side-effect (set on mock_mcp above) is intentionally NOT reassigned here:
    # _all_off_impl already records the call AND honors `all_off_raises`, which
    # is the path test_all_off_failure_does_not_crash_init must exercise.
    return config_mock, mock_mcp, calls_log, control_engine_mock


@pytest.mark.asyncio
async def test_all_off_runs_after_init_hardware_and_before_restore(monkeypatch):
    """all_off() must run between _init_hardware() and the restore functions."""
    from app.container import ServiceContainer

    config_mock, mcp_mock, calls, _ce = _build_container_with_mocks(monkeypatch)

    # Override hardware config: simulation=False but all_off is honored
    config_mock.get.side_effect = lambda key, default=None: {
        "interlocks": [],
        "control": {"update_interval": 1, "last_good_hold_period": 30},
        "hardware": {
            "simulation": False,
            "i2c_bus": 0,
            "mcp_i2c_bus": 0,
            "dfr0971_i2c_bus": 1,
            "i2c_address": 0x27,
            "active_low": True,
            "dfr0971_boards": [],
        },
    }.get(key, default)

    container = ServiceContainer()
    await container.initialize()

    # Find the positions in the call log
    event_names = [name for name, _ in calls]
    assert "all_off" in event_names, f"all_off was not called. events: {event_names}"
    assert "restore_ramp" in event_names
    assert "restore_lights" in event_names

    pos_all_off = event_names.index("all_off")
    pos_restore_ramp = event_names.index("restore_ramp")
    pos_restore_lights = event_names.index("restore_lights")

    assert pos_all_off < pos_restore_ramp, (
        f"all_off (pos {pos_all_off}) must run BEFORE "
        f"restore_ramp_state_from_database (pos {pos_restore_ramp}). "
        f"Order: {event_names}"
    )
    assert pos_all_off < pos_restore_lights, (
        f"all_off (pos {pos_all_off}) must run BEFORE "
        f"restore_light_intensities (pos {pos_restore_lights}). "
        f"Order: {event_names}"
    )


@pytest.mark.asyncio
async def test_all_off_runs_in_simulation_mode(monkeypatch):
    """all_off() must run even in simulation mode (no skip-on-simulation)."""
    from app.container import ServiceContainer

    config_mock, mcp_mock, calls, _ce = _build_container_with_mocks(monkeypatch)
    config_mock.get.side_effect = lambda key, default=None: {
        "interlocks": [],
        "control": {"update_interval": 1, "last_good_hold_period": 30},
        "hardware": {
            "simulation": True,
            "i2c_bus": 0,
            "mcp_i2c_bus": 0,
            "dfr0971_i2c_bus": 1,
            "i2c_address": 0x27,
            "active_low": True,
            "dfr0971_boards": [],
        },
    }.get(key, default)

    container = ServiceContainer()
    await container.initialize()

    event_names = [name for name, _ in calls]
    assert "all_off" in event_names, (
        f"all_off() MUST run in simulation mode too. events: {event_names}"
    )


@pytest.mark.asyncio
async def test_all_off_failure_does_not_crash_init(monkeypatch, caplog):
    """If all_off() raises, init continues and a WARNING is logged."""
    import logging

    from app.container import ServiceContainer

    config_mock, mcp_mock, calls, _ce = _build_container_with_mocks(
        monkeypatch, all_off_raises=True
    )
    config_mock.get.side_effect = lambda key, default=None: {
        "interlocks": [],
        "control": {"update_interval": 1, "last_good_hold_period": 30},
        "hardware": {
            "simulation": False,
            "i2c_bus": 0,
            "mcp_i2c_bus": 0,
            "dfr0971_i2c_bus": 1,
            "i2c_address": 0x27,
            "active_low": True,
            "dfr0971_boards": [],
        },
    }.get(key, default)

    container = ServiceContainer()

    with caplog.at_level(logging.WARNING, logger="app.container"):
        await container.initialize()

    # Init must complete despite the all_off failure
    assert container._initialized is True, "init must not abort on all_off failure"

    # WARNING log emitted
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "force-off" in m.lower() or "force off" in m.lower() or "all_off" in m.lower()
        for m in warning_msgs
    ), f"Expected a WARNING log on all_off failure, got: {warning_msgs}"


@pytest.mark.asyncio
async def test_all_off_success_logs_info(monkeypatch, caplog):
    """Successful all_off() must log at INFO level for operator visibility."""
    import logging

    from app.container import ServiceContainer

    config_mock, mcp_mock, calls, _ce = _build_container_with_mocks(monkeypatch)
    config_mock.get.side_effect = lambda key, default=None: {
        "interlocks": [],
        "control": {"update_interval": 1, "last_good_hold_period": 30},
        "hardware": {
            "simulation": False,
            "i2c_bus": 0,
            "mcp_i2c_bus": 0,
            "dfr0971_i2c_bus": 1,
            "i2c_address": 0x27,
            "active_low": True,
            "dfr0971_boards": [],
        },
    }.get(key, default)

    container = ServiceContainer()

    with caplog.at_level(logging.INFO, logger="app.container"):
        await container.initialize()

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "force-off" in m.lower() or "force off" in m.lower() or "all_off" in m.lower()
        for m in info_msgs
    ), f"Expected an INFO log when force-off runs, got: {info_msgs}"
