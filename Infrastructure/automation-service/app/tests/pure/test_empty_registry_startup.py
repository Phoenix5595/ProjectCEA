from __future__ import annotations

from typing import Any

from fastapi import FastAPI
import pytest

from app import container as container_module
from app.bootstrap import lifespan_manager
from app.container import ServiceContainer
from app.hardware.safe_outputs import SafeOutputError
from app.models.device_registry import LightDevice
from app.repositories.devices.projection import RegistryProjection


class _FakeRedis:
    def __init__(self) -> None:
        self.redis_enabled = False
        self.redis_client = None
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


class _FakeDeviceRepository:
    async def get_registry_projection(self) -> RegistryProjection:
        return RegistryProjection(flat=(), hierarchy={})

    async def get_all_as_hierarchy(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        return (await self.get_registry_projection()).hierarchy

    async def get_lights_by_room(self, _room: str) -> list[Any]:
        return []


class _FakeLightDeviceRepository(_FakeDeviceRepository):
    def __init__(self) -> None:
        self.light = LightDevice(
            device_id=42,
            display_name="Assigned light",
            device_name="light_v_1",
            location="Veg Room",
            board_id=1,
            dimming_channel=0,
            relay_channel=None,
            per_room_index=1,
        )

    async def get_registry_projection(self) -> RegistryProjection:
        return RegistryProjection(
            flat=(self.light,),
            hierarchy={
                "Veg Room": {
                    "main": {
                        self.light.device_name: {
                            "device_id": self.light.device_id,
                            "device_type": "light",
                            "dimming_enabled": True,
                            "dimming_type": "dfr0971",
                            "dimming_board_id": self.light.board_id,
                            "dimming_channel": self.light.dimming_channel,
                        }
                    }
                }
            },
        )

    async def get_lights_by_room(self, room: str) -> list[LightDevice]:
        return [self.light] if room == self.light.location else []

    async def get_latest_light_intensity(
        self, _location: str, _cluster: str, _device_name: str
    ) -> float:
        return 37.0


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


class _FakeRoomModeRepository:
    async def get_active_mode(self, _location: str, _cluster: str) -> None:
        return None


class _FakePool:
    pass


class _FakeDatabaseManager:
    def __init__(self) -> None:
        self._pool = None
        self._automation_redis = _FakeRedis()
        self.device_repo = _FakeDeviceRepository()
        self.light_target_intensity_repo = _FakeLightTargetIntensityRepository()
        self.light_programs_repo = _FakeLightProgramsRepository()
        self.schedule_repo = _FakeScheduleRepository()
        self.control_action_repo = _FakeControlActionRepository()
        self.room_mode_repo = _FakeRoomModeRepository()
        self.climate_periods_repo: Any = None
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self._pool = _FakePool()
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
                "dfr0971_boards": [
                    {"board_id": 0, "i2c_address": 0x88, "name": "Fake 0"},
                    {"board_id": 1, "i2c_address": 0x89, "name": "Fake 1"},
                    {"board_id": 2, "i2c_address": 0x90, "name": "Fake 2"},
                ],
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
        self.safety_commands: list[tuple[int, int, float]] = []
        self.__class__.instances.append(self)

    def add_board(self, _board_id: int, _address: int, _name: str) -> bool:
        return True

    def set_safety_level(self, board_id: int, channel: int, level: float) -> bool:
        self.safety_commands.append((board_id, channel, level))
        return True

    def set_intensity(self, board_id: int, channel: int, intensity: float, **_kwargs: Any) -> bool:
        self.commands.append((board_id, channel, intensity))
        return True

    def list_boards(self) -> list[dict[str, int]]:
        return [{"board_id": 0}, {"board_id": 1}, {"board_id": 2}]

    def get_intensity(self, _board_id: int, _channel: int) -> float:
        return 0.0


class _FakeBackgroundTasks:
    events: list[str] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        self.__class__.events.append("background-started")

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_empty_registry_startup_installs_empty_snapshot_without_energizing_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeMCP23017Driver.instances.clear()
    _FakeDFR0971Manager.instances.clear()
    _FakeBackgroundTasks.events.clear()
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
        assert _FakeDFR0971Manager.instances[0].commands == [
            (0, 0, 0.0),
            (0, 1, 0.0),
            (1, 0, 0.0),
            (1, 1, 0.0),
            (2, 0, 0.0),
            (2, 1, 0.0),
        ]
        assert _FakeDFR0971Manager.instances[0].safety_commands == []


@pytest.mark.asyncio
async def test_startup_loads_strict_snapshot_before_hardware_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an empty strict registry and a hardware initializer that records its precondition
    observed_hierarchy: list[dict[str, dict[str, dict[str, dict[str, Any]]]]] = []

    async def initialize_hardware_after_snapshot(container: Any) -> None:
        observed_hierarchy.append(dict(container.runtime_device_registry.snapshot.hierarchy))
        container.mcp23017 = _FakeMCP23017Driver()
        container.dfr0971_manager = _FakeDFR0971Manager()

    _FakeMCP23017Driver.instances.clear()
    _FakeDFR0971Manager.instances.clear()
    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_init_hardware", initialize_hardware_after_snapshot
    )
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When: the service starts through its normal lifespan
    container = ServiceContainer()
    async with lifespan_manager(FastAPI(), container):
        pass

    # Then: hardware initialization sees the already-installed strict empty snapshot
    assert observed_hierarchy == [{}]


@pytest.mark.asyncio
async def test_empty_registry_startup_fails_when_mcp_off_readback_is_not_all_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _McpWithOnReadback(_FakeMCP23017Driver):
        def sample_all_channels(self) -> tuple[bool, ...]:
            return (True,) + (False,) * 15

    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(container_module, "MCP23017Driver", _McpWithOnReadback)
    monkeypatch.setattr(container_module, "DFR0971Manager", _FakeDFR0971Manager)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When/Then: startup refuses readiness after an unsafe MCP readback
    with pytest.raises(SafeOutputError, match="sixteen OFF"):
        await ServiceContainer().initialize()


@pytest.mark.asyncio
async def test_empty_registry_startup_fails_when_mcp_rejects_off_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _McpWithFailedOffCommand(_FakeMCP23017Driver):
        def all_off(self) -> bool:
            self.commands.append("off")
            return False

    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(container_module, "MCP23017Driver", _McpWithFailedOffCommand)
    monkeypatch.setattr(container_module, "DFR0971Manager", _FakeDFR0971Manager)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When/Then: startup refuses readiness after an MCP OFF command failure
    with pytest.raises(SafeOutputError, match="rejected the all-off command"):
        await ServiceContainer().initialize()


@pytest.mark.asyncio
async def test_empty_registry_startup_fails_when_dfr_zero_is_not_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DfrWithFailedZero(_FakeDFR0971Manager):
        def set_intensity(
            self, board_id: int, channel: int, intensity: float, **_kwargs: Any
        ) -> bool:
            self.commands.append((board_id, channel, intensity))
            return (board_id, channel) != (2, 1)

    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(container_module, "MCP23017Driver", _FakeMCP23017Driver)
    monkeypatch.setattr(container_module, "DFR0971Manager", _DfrWithFailedZero)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When/Then: startup refuses readiness after a DFR zero command failure
    with pytest.raises(SafeOutputError, match="DFR0971 zero command"):
        await ServiceContainer().initialize()


@pytest.mark.asyncio
async def test_empty_registry_startup_fails_when_dfr_cache_is_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DfrWithNonzeroCommandCache(_FakeDFR0971Manager):
        def get_intensity(self, _board_id: int, _channel: int) -> float:
            return 1.0

    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _FakeDatabaseManager)
    monkeypatch.setattr(container_module, "MCP23017Driver", _FakeMCP23017Driver)
    monkeypatch.setattr(container_module, "DFR0971Manager", _DfrWithNonzeroCommandCache)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When/Then: startup refuses readiness when the DFR commanded-value cache remains nonzero
    with pytest.raises(SafeOutputError, match="DFR0971 zero command"):
        await ServiceContainer().initialize()


@pytest.mark.asyncio
async def test_assigned_light_slot_is_restored_without_startup_zero_before_background_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DatabaseWithAssignedLight(_FakeDatabaseManager):
        def __init__(self) -> None:
            super().__init__()
            self.device_repo = _FakeLightDeviceRepository()

    _FakeDFR0971Manager.instances.clear()
    _FakeBackgroundTasks.events.clear()
    monkeypatch.setattr(container_module, "ConfigLoader", _FakeConfigLoader)
    monkeypatch.setattr(container_module, "DatabaseManager", _DatabaseWithAssignedLight)
    monkeypatch.setattr(container_module, "MCP23017Driver", _FakeMCP23017Driver)
    monkeypatch.setattr(container_module, "DFR0971Manager", _FakeDFR0971Manager)
    monkeypatch.setattr(container_module, "AutomationRedisClient", _FakeRedis)
    monkeypatch.setattr(container_module, "BackgroundTasks", _FakeBackgroundTasks)
    monkeypatch.setattr(
        container_module.ServiceContainer, "_write_restart_hash_sidecar", lambda _self: None
    )
    monkeypatch.setattr(container_module, "force_all_outputs_safe", lambda *_args: None)

    # When: a startup snapshot assigns board 1 channel 0 to a light
    container = ServiceContainer()
    async with lifespan_manager(FastAPI(), container):
        dfr = _FakeDFR0971Manager.instances[0]

        # Then: only unassigned slots are zeroed and the assigned light restores before AUTO starts
        assert (1, 0, 0.0) not in dfr.commands
        assert dfr.commands == [
            (0, 0, 0.0),
            (0, 1, 0.0),
            (1, 1, 0.0),
            (2, 0, 0.0),
            (2, 1, 0.0),
            (1, 0, 37.0),
        ]
        assert _FakeBackgroundTasks.events == ["background-started"]
