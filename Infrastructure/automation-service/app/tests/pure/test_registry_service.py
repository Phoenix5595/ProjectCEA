from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.models.device_registry import (
    Device,
    DeviceCreate,
    DeviceUpdate,
    LightDevice,
)
from app.services.device_registry_service import (
    DeviceRegistryService,
    RegistryConflictError,
    SafeOutputError,
)


class _Connection:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def fetch(self, query: str, *_args: int) -> list[dict[str, int]]:
        if "mode_parameters" in query:
            return [{"mode_id": 1}]
        return []

    async def execute(self, query: str, *_args: object) -> str:
        self.commands.append(query)
        return "UPDATE 1"


class _MutationRegistry:
    def __init__(self) -> None:
        self.connection = _Connection()
        self.commits = 0

    async def mutate(self, mutation):
        result = await mutation(self.connection)
        self.commits += 1
        return result


class _Relay:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.commands: list[tuple[int, int]] = []

    async def set_channel_state(self, channel: int, state: int) -> bool:
        self.commands.append((channel, state))
        return self.succeeds

    def is_channel_observed_off(self, _channel: int) -> bool:
        return self.succeeds

    async def command_channel_off_and_observe(self, channel: int) -> bool:
        await self.set_channel_state(channel, 0)
        return self.is_channel_observed_off(channel)


class _Dfr:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.commands: list[tuple[int, int, float]] = []

    def set_intensity(self, board_id: int, channel: int, intensity: float) -> bool:
        self.commands.append((board_id, channel, intensity))
        return self.succeeds

    def get_intensity(self, _board_id: int, _channel: int) -> float:
        return 0.0 if self.succeeds else 10.0


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            1: {
                "device_id": 1,
                "device_type": "heating",
                "channel": 2,
                "display_name": "Heat",
                "device_name": "heating_v_1",
                "location": "Veg Room",
                "cluster": "main",
                "pid_enabled": False,
                "interlock_with": [],
                "pid_setpoints": {},
            },
            2: {
                "device_id": 2,
                "device_type": "exhaust",
                "channel": 5,
                "display_name": "Exhaust",
                "device_name": "exhaust_v_1",
                "location": "Veg Room",
                "cluster": "main",
                "pid_enabled": False,
                "interlock_with": [],
                "pid_setpoints": {},
            },
            3: {
                "device_id": 3,
                "device_type": "light",
                "channel": 7,
                "display_name": "Light",
                "device_name": "light_v_1",
                "location": "Veg Room",
                "cluster": "main",
                "board_id": 0,
                "dimming_board_id": 0,
                "dimming_channel": 0,
                "dimming_enabled": True,
                "dimming_type": "dfr0971",
                "safety_level": 0,
                "per_room_index": 1,
            },
        }
        self.deleted: list[int] = []

    def _model(self, row: dict[str, Any]) -> Device | LightDevice:
        if row["device_type"] == "light":
            return LightDevice(
                device_id=row["device_id"],
                device_type="light",
                board_id=row["dimming_board_id"],
                dimming_channel=row["dimming_channel"],
                relay_channel=row["channel"],
                display_name=row["display_name"],
                device_name=row["device_name"],
                location=row["location"],
                per_room_index=row["per_room_index"],
            )
        return Device(
            device_id=row["device_id"],
            device_type=row["device_type"],
            channel=row["channel"],
            display_name=row["display_name"],
            device_name=row["device_name"],
            location=row["location"],
            pid_enabled=row["pid_enabled"],
            interlock_with=row["interlock_with"],
            pid_setpoints=row["pid_setpoints"],
        )

    async def get_all_devices_flat(self):
        return [self._model(row) for row in self.rows.values()]

    async def get_device_for_update(self, _connection, device_id: int):
        row = self.rows.get(device_id)
        return dict(row) if row else None

    async def find_relay_owner_for_update(self, _connection, channel: int, exclude_device_id=None):
        for row in self.rows.values():
            if row["channel"] == channel and row["device_id"] != exclude_device_id:
                return dict(row)
        return None

    async def assert_dfr_free(
        self, _connection, board_id: int, channel: int, exclude_device_id=None
    ):
        for row in self.rows.values():
            if (
                row["device_type"] == "light"
                and row["dimming_board_id"] == board_id
                and row["dimming_channel"] == channel
                and row["device_id"] != exclude_device_id
            ):
                return dict(row)
        return None

    async def create_device_locked(self, _connection, create: DeviceCreate):
        row = {
            "device_id": 4,
            "device_type": create.device_type,
            "channel": create.channel,
            "display_name": create.display_name,
            "device_name": "heating_v_2",
            "location": create.room,
            "cluster": "main",
            "pid_enabled": create.pid_enabled,
            "interlock_with": create.interlock_with,
            "pid_setpoints": create.pid_setpoints,
        }
        self.rows[4] = row
        return self._model(row)

    async def create_light_locked(self, _connection, **kwargs):
        row = {
            "device_id": 4,
            "device_type": "light",
            "channel": kwargs["relay_channel"],
            "display_name": kwargs["display_name"],
            "device_name": "light_v_2",
            "location": kwargs["room"],
            "cluster": "main",
            "dimming_board_id": kwargs["board_id"],
            "dimming_channel": kwargs["dimming_channel"],
            "dimming_enabled": True,
            "dimming_type": "dfr0971",
            "safety_level": 0,
            "per_room_index": kwargs["per_room_index"],
        }
        self.rows[4] = row
        return self._model(row)

    async def clear_relay_binding(self, _connection, device_id: int):
        self.rows[device_id]["channel"] = None

    async def assign_relay_steal(
        self, connection, device_id: int, channel: int, displaced_device_id: int
    ):
        await self.clear_relay_binding(connection, displaced_device_id)
        self.rows[device_id]["channel"] = channel

    async def update_device_locked(self, _connection, device_id: int, update: DeviceUpdate):
        self.rows[device_id].update(update.model_dump(exclude_unset=True))
        return self._model(self.rows[device_id])

    async def update_light_locked(
        self, _connection: Any, device_id: int, fields: dict[str, Any]
    ) -> LightDevice:
        mapping = {"relay_channel": "channel", "room": "location"}
        self.rows[device_id].update(
            {mapping[key] if key in mapping else key: value for key, value in fields.items()}
        )
        updated = self._model(self.rows[device_id])
        assert isinstance(updated, LightDevice)
        return updated

    async def delete_current_state_locked(self, _connection, _device):
        return None

    async def delete_device_dependents_locked(self, _connection, _device_id: int):
        return None

    async def delete_device_locked(self, _connection, device_id: int):
        self.deleted.append(device_id)
        del self.rows[device_id]
        return True

    async def delete_light_locked(self, connection, device_id: int):
        return await self.delete_device_locked(connection, device_id)


def _service(repository: Any, relay: _Relay | None = None, dfr: _Dfr | None = None):
    registry: Any = _MutationRegistry()
    relay_manager: Any = relay or _Relay()
    dfr_manager: Any = dfr
    return DeviceRegistryService(repository, registry, relay_manager, dfr_manager), registry


@pytest.mark.asyncio
async def test_create_update_delete_and_unbind_commit_once_each() -> None:
    repository = _Repository()
    service, registry = _service(repository)
    created = await service.create_device(
        DeviceCreate(device_type="heater", room="Veg Room", display_name="Heat 2")
    )
    updated = await service.update_device(1, DeviceUpdate(display_name="Warm"))
    unbound = await service.unbind_relay(1)
    deleted = await service.delete_device(2)
    assert created.device is not None
    assert updated.device is not None and updated.device.display_name == "Warm"
    assert unbound.device is not None and repository.rows[1]["channel"] is None
    assert deleted.device is None and repository.deleted == [2]
    assert registry.commits == 4


@pytest.mark.asyncio
async def test_relay_conflict_requires_confirmation_then_safely_steals() -> None:
    repository = _Repository()
    relay = _Relay()
    service, registry = _service(repository, relay)
    with pytest.raises(RegistryConflictError):
        await service.update_device(1, DeviceUpdate(channel=5))
    result = await service.update_device(1, DeviceUpdate(channel=5), confirmed_relay_steal=True)
    assert result.displaced_device_id == 2
    assert repository.rows[2]["channel"] is None
    assert repository.rows[1]["channel"] == 5
    assert relay.commands == [(2, 0), (5, 0)]
    assert registry.commits == 1


@pytest.mark.asyncio
async def test_dfr_conflict_does_not_change_light() -> None:
    repository = _Repository()
    repository.rows[4] = {
        **repository.rows[3],
        "device_id": 4,
        "device_name": "light_v_2",
        "dimming_board_id": 1,
        "dimming_channel": 1,
        "per_room_index": 2,
    }
    service, registry = _service(repository, dfr=_Dfr())
    with pytest.raises(RegistryConflictError):
        await service.move_dfr(4, 0, 0)
    assert repository.rows[4]["dimming_board_id"] == 1
    assert registry.commits == 0


@pytest.mark.asyncio
async def test_safe_output_failure_aborts_before_commit() -> None:
    repository = _Repository()
    service, registry = _service(repository, _Relay(succeeds=False))
    with pytest.raises(SafeOutputError):
        await service.unbind_relay(1)
    assert repository.rows[1]["channel"] == 2
    assert registry.commits == 0


@pytest.mark.asyncio
async def test_light_move_and_delete_zero_old_dfr_and_turn_off_relay() -> None:
    repository = _Repository()
    relay = _Relay()
    dfr = _Dfr()
    service, registry = _service(repository, relay, dfr)
    moved = await service.move_dfr(3, 1, 1)
    deleted = await service.delete_light(3)
    assert moved.device is not None
    assert deleted.device is None
    assert dfr.commands == [(0, 0, 0.0), (1, 1, 0.0)]
    assert relay.commands == [(7, 0)]
    assert registry.commits == 2


class _Pool:
    def __init__(self, connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class _TransactionConnection:
    def __init__(self, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit

    async def execute(self, _query: str, *_args: int) -> str:
        return "SELECT 1"

    @asynccontextmanager
    async def transaction(self):
        yield
        if self.fail_commit:
            raise RuntimeError("commit failed")


class _Database:
    def __init__(self, connection) -> None:
        self.pool = _Pool(connection)

    async def _get_pool(self):
        return self.pool


def _snapshot(version: int) -> RuntimeDeviceSnapshot:
    return RuntimeDeviceSnapshot.create(
        version=version, hierarchy={}, mode_parameters={}, light_intensities={}, light_programs=[]
    )


@pytest.mark.asyncio
async def test_commit_failure_retains_old_snapshot_after_successful_safe_output() -> None:
    database: Any = _Database(_TransactionConnection(fail_commit=True))
    registry: Any = RuntimeDeviceRegistry(database)
    registry._snapshot = _snapshot(1)

    async def build(connection: Any | None = None) -> RuntimeDeviceSnapshot:
        del connection
        return _snapshot(2)

    registry._build_snapshot = build

    async def mutation(_connection):
        return "written"

    with pytest.raises(RuntimeError, match="commit failed"):
        await registry.mutate(mutation)
    assert registry.snapshot.version == 1


@pytest.mark.asyncio
async def test_mutation_lock_prevents_post_commit_snapshot_reordering() -> None:
    database: Any = _Database(_TransactionConnection())
    registry: Any = RuntimeDeviceRegistry(database)
    snapshots = iter((_snapshot(1), _snapshot(2)))
    first_committed = asyncio.Event()
    allow_first_swap = asyncio.Event()

    async def build(connection: Any | None = None) -> RuntimeDeviceSnapshot:
        del connection
        return next(snapshots)

    async def delay(snapshot: RuntimeDeviceSnapshot):
        if snapshot.version == 1:
            first_committed.set()
            await allow_first_swap.wait()

    registry._build_snapshot = build
    registry._after_commit_before_install = delay

    async def mutation(_connection):
        return None

    first = asyncio.create_task(registry.mutate(mutation))
    await first_committed.wait()
    second = asyncio.create_task(registry.mutate(mutation))
    await asyncio.sleep(0)
    assert not second.done()
    allow_first_swap.set()
    await first
    await second
    assert registry.snapshot.version == 2
