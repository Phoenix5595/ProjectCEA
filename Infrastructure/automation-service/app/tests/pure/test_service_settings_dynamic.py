"""Runtime consumers read refreshed automation service settings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.background_tasks.control_loop as control_loop_module
from app.config import ConfigLoader
from app.control.pid_controller import PIDController
from app.control.pid_controller_manager import PIDControllerManager
from shared.service_settings import ServiceSettings

YAML_CONFIG = """\
hardware: {}
control:
  update_interval: 1
  last_good_hold_period: 30
  binary_hysteresis: 0.1
  pid_defaults:
    heater: {kp: 5.0, ki: 0.1, kd: 0.0}
  pid_limits:
    heater:
      kp_min: 0.0
      kp_max: 20.0
      ki_min: 0.0
      ki_max: 1.0
      kd_min: 0.0
      kd_max: 1.0
  safety_limits:
    min_temperature: 10.0
    max_temperature: 35.0
    min_humidity: 30.0
    max_humidity: 90.0
    min_co2: 400.0
    max_co2: 2000.0
  default_setpoints:
    Flower Room: {main: {heating_setpoint: 25.0}}
"""


@pytest.mark.asyncio
async def test_shared_refresh_updates_sync_config_accessors(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "automation_config.yaml"
    config_path.write_text(YAML_CONFIG)
    config = ConfigLoader(
        str(config_path), settings_cache_path=tmp_path / "automation-settings.json"
    )
    initial = {
        "update_interval": 3,
        "last_good_hold_period": 45,
        "binary_hysteresis": 0.3,
        "pid_defaults": {"heater": {"kp": 8.0, "ki": 0.2, "kd": 0.0}},
        "pid_limits": {
            "heater": {
                "kp_min": 0.0,
                "kp_max": 30.0,
                "ki_min": 0.0,
                "ki_max": 1.0,
                "kd_min": 0.0,
                "kd_max": 1.0,
            }
        },
        "safety_limits": {
            "min_temperature": 8.0,
            "max_temperature": 36.0,
            "min_humidity": 25.0,
            "max_humidity": 92.0,
            "min_co2": 350.0,
            "max_co2": 2100.0,
        },
    }

    async def load_settings(_pool, service, **kwargs):
        assert service == "automation"
        return dict(initial)

    async def run_refresh(_pool, service, on_change, **kwargs):
        assert service == "automation"
        changed = dict(initial, update_interval=4, binary_hysteresis=0.4)
        on_change(changed)

    monkeypatch.setattr(ServiceSettings, "load", load_settings)
    monkeypatch.setattr(ServiceSettings, "run_refresh", run_refresh)
    await config.load_service_settings(object())

    assert config.get_update_interval() == 3
    assert config.get("control.update_interval") == 3
    assert config.get_last_good_hold_period() == 45
    assert config.get_binary_hysteresis() == 0.3
    assert config.get_pid_params_for_device("heating") == {"kp": 8.0, "ki": 0.2, "kd": 0.0}
    assert config.get("control.pid_limits") == initial["pid_limits"]
    assert config.get_safety_limits() == initial["safety_limits"]
    assert "default_setpoints" not in config.get_control_config()
    assert config.get("control.default_setpoints") is None
    assert not hasattr(config, "get_default_setpoints")

    await config.run_service_settings_refresh()

    assert config.get_update_interval() == 4
    assert config.get("control.update_interval") == 4
    assert config.get_binary_hysteresis() == 0.4


@pytest.mark.asyncio
async def test_cached_pid_controller_tracks_live_service_defaults():
    class Config:
        gains = {"kp": 8.0, "ki": 0.2, "kd": 0.0}

        def get_pid_params_for_device(self, _device_type):
            return dict(self.gains)

    manager = PIDControllerManager.__new__(PIDControllerManager)
    manager.config = Config()
    key = ("Flower Room", "main", "Heater", "heating")
    controller = PIDController(1.0, 0.1, 0.0)
    manager._pid_controllers = {key: controller}
    manager._settings_default_controllers = {key}
    manager._get_cached_pid_parameters = AsyncMock(
        side_effect=[
            {"source": "settings_default"},
            {"source": "settings_default"},
            {"kp": 4.0, "ki": 0.4, "kd": 0.2, "source": "database"},
        ]
    )

    assert await manager.get_pid_controller(*key) is controller
    assert (controller.kp, controller.ki, controller.kd) == (8.0, 0.2, 0.0)

    manager.config.gains = {"kp": 9.0, "ki": 0.3, "kd": 0.1}
    assert await manager.get_pid_controller(*key) is controller
    assert (controller.kp, controller.ki, controller.kd) == (9.0, 0.3, 0.1)

    assert await manager.get_pid_controller(*key) is controller
    assert (controller.kp, controller.ki, controller.kd) == (4.0, 0.4, 0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_control_loop_uses_latest_interval_for_normal_and_error_sleep(monkeypatch, failure):
    class Config:
        interval = 2

        def get_update_interval(self):
            return self.interval

    class Loop(control_loop_module.ControlLoopMixin):
        def __init__(self):
            self.config = Config()
            self.control_engine = SimpleNamespace(config=self.config, run_control_loop=self.tick)
            self.database = SimpleNamespace(_db_connected=True)
            self._scheduler_ready = SimpleNamespace(is_set=lambda: True)
            self._running = True
            self._last_control_error_time = 0.0
            self._consecutive_control_errors = 0
            self._last_tick_completed_at: float | None = None
            self._scheduled_interval: int | None = None

        async def tick(self):
            self.config.interval = 4
            if failure:
                raise RuntimeError("tick failed")

        async def _maybe_run_calendar_mode_scheduler(self):
            return None

        async def _record_control_success(self):
            self._running = False

        async def _record_control_failure(self, reason):
            return None

    loop = Loop()
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        loop._running = False

    monkeypatch.setattr(
        control_loop_module,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError),
    )
    monkeypatch.setattr(
        control_loop_module,
        "time",
        SimpleNamespace(monotonic=lambda: 10.0),
    )

    await loop._control_loop()

    assert sleeps == [4.0]
    assert loop._scheduled_interval == (None if failure else 4)
