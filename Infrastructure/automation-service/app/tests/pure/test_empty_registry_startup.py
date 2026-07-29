from __future__ import annotations

from typing import Any

from fastapi import FastAPI
import pytest

from app import container as container_module
from app.bootstrap import lifespan_manager
from app.container import ServiceContainer


class _FakeRedis:
    def __init__(self) -> None:
        self.redis_enabled = False
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


class _FakeDeviceRepository:
    async def get_all_as_hierarchy(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        return {}

    async def get_lights_by_room(self, _room: str) -> list[Any]:
        return []


class _FakeLightTargetIntensityRepository:
    async def get_all_intensities(self) -> dict[tuple[int, int], float]:
        return {}


class _FakeLightProgramsRepository:
    async def get_all_programs(self) -> list[dict[str, Any]]:
        return []


class _FakeScheduleRepository:
    async def get_schedules(self) -> list[dict[str, Any]]:
        return []


class _FakeControlActionRepository:
    async def log_automation_state_batch(self, _records: list[dict[str, Any]]) -> None:
        return None


class _FakeDatabaseManager:
    def __init__(self) -> None:
        self._automation_redis = _FakeRedis()
        self.device_repo = _FakeDeviceRepository()
        self.light_target_intensity_repo = _FakeLightTargetIntensityRepository()
        self.light_programs_repo = _FakeLightProgramsRepository()
        self.schedule_repo = _FakeScheduleRepository()
        self.control_action_repo = _FakeControlActionRepository()
        self.climate_periods_repo: Any = None
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def load_schedule_state_to_redis(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _FakeConfigLoader:
    def __init__(self) -> None:
        self.config_path = "/tmp/empty-registry-automation-config.yaml"
        self._config = {
            "hardware": {
                "mcp_i2c_bus": 0,
                "dfr0971_i2c_bus": 1,
                "i2c_address": 0x27,
                "active_low": True,
                "dfr0971_boards": [{"board_id": 1, "i2c_address": 0x88, "name": "Fake"}],
            },
            "control": {"update_interval": 1},
            "interlocks": [],
            "sensors": {},
        }
        self._device_repo: _FakeDeviceRepository | None = None
        self._runtime_device_registry: Any = None

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_control_config(self) -> dict[str, float]:
        return {"binary_hysteresis": 0.1}

    def get_sensor_mapping(self) -> dict[str, Any]:
        return {}

    def get_update_interval(self) -> int:
        return 1

    async def get_devices(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        assert self._runtime_device_registry is not None
        return dict(self._runtime_device_registry.snapshot.hierarchy)

    def set_device_repo(self, device_repo: _FakeDeviceRepository) -> None:
        self._device_repo = device_repo

    def set_runtime_device_registry(self, runtime_device_registry: Any) -> None:
        self._runtime_device_registry = runtime_device_registry


class _FakeMCP23017Driver:
    instances: list[_FakeMCP23017Driver] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.active_low = True
        self.commands: list[str] = []
        self.__class__.instances.append(self)

    def probe(self) -> bool:
        return True

    def all_off(self) -> bool:
        self.commands.append("off")
        return True

    def sample_all_channels(self) -> tuple[bool, ...]:
        return (False,) * 16


class _FakeDFR0971Manager:
    instances: list[_FakeDFR0971Manager] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.commands: list[tuple[int, int, float]] = []
        self.__class__.instances.append(self)

    def add_board(self, _board_id: int, _address: int, _name: str) -> bool:
        return True

    def set_safety_level(self, _board_id: int, _channel: int, _level: float) -> bool:
        return True

    def set_intensity(self, board_id: int, channel: int, intensity: float, **_kwargs: Any) -> bool:
        self.commands.append((board_id, channel, intensity))
        return True

    def list_boards(self) -> list[dict[str, int]]:
        return [{"board_id": 1}]

    def get_intensity(self, _board_id: int, _channel: int) -> float:
        return 0.0


class _FakeBackgroundTasks:
    def __init__(self, **_kwargs: Any) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_empty_registry_startup_installs_empty_snapshot_without_energizing_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeMCP23017Driver.instances.clear()
    _FakeDFR0971Manager.instances.clear()
    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(container_module, "MCP23017Driver", _FakeMCP23017Driver)
    monkeypatch.setattr(container_module, "DFR0971Manager", _FakeDFR0971Manager)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    container = ServiceContainer()
    async with lifespan_manager(FastAPI(), container):
        snapshot = container.get_control_engine().runtime_device_registry.snapshot

        assert snapshot.hierarchy == {}
        assert snapshot.by_device == {}
        assert snapshot.by_channel == {}
        assert container.background_tasks is not None
        assert getattr(container.background_tasks, "started", False) is True

        await container.get_control_engine().run_control_loop()

        assert _FakeMCP23017Driver.instances[0].commands == ["off"]
        assert all(
            intensity == 0.0 for _, _, intensity in _FakeDFR0971Manager.instances[0].commands
        )
