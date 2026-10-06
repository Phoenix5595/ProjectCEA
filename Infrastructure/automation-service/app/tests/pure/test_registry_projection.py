from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import time as dt_time
from typing import Any, Protocol

import pytest

from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.repositories.devices import DeviceRepository
from app.repositories.devices.projection import RegistryProjection
from app.repositories.room_modes import ActiveModeProjection, RoomModeRepository


class _RowsConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetch(self, _query: str) -> list[dict[str, Any]]:
        return self.rows


class _RowsPool:
    def __init__(self, connection: _RowsConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _registry_row(device_id: int, device_name: str) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "location": "Veg Room",
        "cluster": "main",
        "device_name": device_name,
        "display_name": "Veg Heater",
        "device_type": "heating",
        "channel": 2,
        "dimming_enabled": None,
        "dimming_type": None,
        "dimming_board_id": None,
        "dimming_channel": None,
        "safety_level": None,
        "pid_enabled": False,
        "interlock_with": [],
        "pid_setpoints": {},
        "per_room_index": None,
        "created_at": None,
        "updated_at": None,
    }


def _light_row(device_id: int, device_name: str) -> dict[str, Any]:
    return {
        **_registry_row(device_id, device_name),
        "device_type": "light",
        "channel": 4,
        "dimming_enabled": True,
        "dimming_type": "dfr0971",
        "dimming_board_id": 1,
        "dimming_channel": 0,
        "safety_level": 0,
        "per_room_index": 1,
    }


@pytest.mark.asyncio
async def test_legacy_row_is_included_in_flat_and_hierarchy_loaders() -> None:
    valid_row = _registry_row(1, "heating_v_1")
    invalid_legacy_row = _registry_row(2, "legacy heater")
    invalid_legacy_row["channel"] = 3
    repository = DeviceRepository(_RowsPool(_RowsConnection([valid_row, invalid_legacy_row])))

    flat = await repository.get_all_devices_flat()
    hierarchy = await repository.get_all_as_hierarchy()

    assert [device.device_id for device in flat] == [1, 2]
    assert set(hierarchy["Veg Room"]["main"]) == {"heating_v_1", "legacy heater"}


@pytest.mark.asyncio
async def test_projection_has_identical_flat_and_hierarchy_identities() -> None:
    valid_device = _registry_row(1, "heating_v_1")
    valid_light = _light_row(2, "light_v_1")
    invalid_legacy_row = _registry_row(3, "legacy heater")
    invalid_legacy_row["channel"] = 5
    repository = DeviceRepository(
        _RowsPool(_RowsConnection([valid_device, valid_light, invalid_legacy_row]))
    )

    projection = await repository.get_registry_projection()

    flat_ids = {device.device_id for device in projection.flat}
    hierarchy_ids = {
        device_info["device_id"]
        for clusters in projection.hierarchy.values()
        for devices in clusters.values()
        for device_info in devices.values()
    }

    assert flat_ids == {1, 2, 3}
    assert hierarchy_ids == flat_ids
    assert "legacy heater" in projection.hierarchy["Veg Room"]["main"]


class _FailingRowsConnection:
    async def fetch(self, _query: str) -> list[dict[str, Any]]:
        raise ConnectionError("database unavailable")


@pytest.mark.asyncio
async def test_registry_query_error_raises_instead_of_returning_empty_projection() -> None:
    failing_connection: Any = _FailingRowsConnection()
    repository = DeviceRepository(_RowsPool(failing_connection))

    with pytest.raises(ConnectionError, match="database unavailable"):
        await repository.get_all_devices_flat()


class _ProjectionRepository:
    def __init__(self, hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]]) -> None:
        self.hierarchy = hierarchy
        self.error: Exception | None = None

    async def get_registry_projection(self, connection: Any | None = None) -> RegistryProjection:
        del connection
        if self.error is not None:
            raise self.error
        return RegistryProjection(flat=(), hierarchy=self.hierarchy)


class _RegistryProjectionProvider(Protocol):
    async def get_registry_projection(
        self, connection: Any | None = None
    ) -> RegistryProjection: ...


class _EmptyProjectionRepository:
    async def get_all_intensities(self) -> dict[tuple[int, int], float]:
        return {}

    async def get_all_programs(self) -> list[dict[str, Any]]:
        return []

    async def get_active_mode_projection(
        self,
        room_clusters: Sequence[tuple[str, str]],
        conn: Any | None = None,
    ) -> ActiveModeProjection:
        del room_clusters, conn
        return ActiveModeProjection(active_modes={}, mode_parameters={})


class _RuntimeDatabase:
    def __init__(
        self,
        device_repo: _RegistryProjectionProvider,
        room_mode_repo: Any | None = None,
    ) -> None:
        self.device_repo = device_repo
        self.light_target_intensity_repo = _EmptyProjectionRepository()
        self.light_programs_repo = _EmptyProjectionRepository()
        self.room_mode_repo = (
            room_mode_repo if room_mode_repo is not None else _EmptyProjectionRepository()
        )
        self.pool = _TransactionPool()

    async def _get_pool(self) -> _TransactionPool:
        return self.pool


class _TransactionConnection:
    async def execute(self, _query: str, *_args: int) -> str:
        return "SELECT 1"

    async def fetch(self, _query: str, *_args: object) -> list[dict[str, Any]]:
        return []

    @asynccontextmanager
    async def transaction(self):
        yield


class _TransactionPool:
    def __init__(self) -> None:
        self.connection = _TransactionConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
async def test_startup_query_error_prevents_snapshot_installation() -> None:
    device_repo = _ProjectionRepository({})
    device_repo.error = ConnectionError("database unavailable")
    database: Any = _RuntimeDatabase(device_repo)
    registry: Any = RuntimeDeviceRegistry(database)

    with pytest.raises(ConnectionError, match="database unavailable"):
        await registry.load_startup()

    with pytest.raises(RuntimeError, match="not installed"):
        _ = registry.snapshot


@pytest.mark.asyncio
async def test_reload_query_error_retains_installed_snapshot_identity_and_version() -> None:
    device_repo = _ProjectionRepository({})
    database: Any = _RuntimeDatabase(device_repo)
    registry: Any = RuntimeDeviceRegistry(database)
    installed = await registry.load_startup()
    device_repo.error = ConnectionError("database unavailable")

    with pytest.raises(ConnectionError, match="database unavailable"):
        await registry.reload_after_commit()

    assert registry.snapshot is installed
    assert registry.snapshot.version == installed.version


@pytest.mark.asyncio
async def test_successful_zero_row_query_installs_a_ready_empty_snapshot() -> None:
    database: Any = _RuntimeDatabase(_ProjectionRepository({}))
    registry: Any = RuntimeDeviceRegistry(database)

    snapshot = await registry.load_startup()

    assert snapshot.hierarchy == {}
    assert snapshot.by_device == {}
    assert snapshot.by_channel == {}


@pytest.mark.asyncio
async def test_runtime_snapshot_includes_legacy_rows_like_api_reads() -> None:
    valid_row = _registry_row(1, "heating_v_1")
    invalid_legacy_row = _registry_row(2, "legacy heater")
    invalid_legacy_row["channel"] = 3
    repository = DeviceRepository(_RowsPool(_RowsConnection([valid_row, invalid_legacy_row])))
    database: Any = _RuntimeDatabase(repository)
    registry: Any = RuntimeDeviceRegistry(database)

    snapshot = await registry.load_startup()

    assert set(snapshot.hierarchy["Veg Room"]["main"]) == {"heating_v_1", "legacy heater"}


@pytest.mark.asyncio
async def test_mutation_projection_error_retains_installed_snapshot_identity_and_version() -> None:
    device_repo = _ProjectionRepository({})
    database: Any = _RuntimeDatabase(device_repo)
    registry: Any = RuntimeDeviceRegistry(database)
    installed = await registry.load_startup()
    device_repo.error = ConnectionError("database unavailable")

    async def mutation(_connection: Any) -> None:
        return None

    with pytest.raises(ConnectionError, match="database unavailable"):
        await registry.mutate(mutation)

    assert registry.snapshot is installed
    assert registry.snapshot.version == installed.version


def test_runtime_snapshot_rejects_untyped_hierarchy_values() -> None:
    snapshot = RuntimeDeviceSnapshot.create(
        version=1,
        hierarchy={},
        mode_parameters={},
        active_modes={},
        light_intensities={},
        light_programs=[],
    )

    assert snapshot.hierarchy == {}


# ---------------------------------------------------------------------------
# Batch ActiveModeProjection: operation counts and exact projection values
# ---------------------------------------------------------------------------


class _ProjectionRowConnection:
    """Counts reads; the canned rows stand in for the projection query result."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetch_calls = 0

    async def fetch(self, _query: str, *_args: object) -> list[dict[str, Any]]:
        self.fetch_calls += 1
        return self.rows


class _ProjectionPool:
    def __init__(self, connection: _ProjectionRowConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _projection_row(
    location: str,
    cluster: str,
    *,
    mode_id: int | None = None,
    submode_id: int | None = None,
    mode_name: str | None = None,
    submode_name: str | None = None,
    day_start_time: Any = None,
    night_start_time: Any = None,
    ramp_up: int | None = None,
    ramp_down: int | None = None,
) -> dict[str, Any]:
    return {
        "location": location,
        "cluster": cluster,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "mode_name": mode_name,
        "submode_name": submode_name,
        "day_start_time": day_start_time,
        "night_start_time": night_start_time,
        "light_ramp_up_minutes": ramp_up,
        "light_ramp_down_minutes": ramp_down,
        "ord": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("pair_count", [1, 4, 12], ids=["one-pair", "four-pairs", "twelve-pairs"])
async def test_active_mode_projection_is_one_read_for_configured_pairs(pair_count: int) -> None:
    pairs = [(f"Room {index}", "main") for index in range(1, pair_count + 1)]
    rows: list[dict[str, Any]] = []
    expected_active: dict[tuple[str, str], dict[str, Any]] = {}
    expected_parameters: dict[tuple[str, str], dict[str, Any]] = {}
    for index, pair in enumerate(pairs, start=1):
        rows.append(
            _projection_row(
                pair[0],
                pair[1],
                mode_id=index,
                submode_id=None,
                mode_name=f"mode {index}",
                submode_name=None,
                day_start_time=dt_time(6, index),
                night_start_time=dt_time(18, index % 60),
                ramp_up=15,
                ramp_down=15,
            )
        )
        expected_active[pair] = {
            "mode_id": index,
            "submode_id": None,
            "mode_name": f"mode {index}",
            "submode_name": None,
        }
        expected_parameters[pair] = {
            "mode_id": index,
            "day_start": f"06:{index:02d}",
            "night_start": f"18:{index % 60:02d}",
            "ramp_up": 15,
            "ramp_down": 15,
        }

    connection = _ProjectionRowConnection(rows)
    repository = RoomModeRepository(_ProjectionPool(connection))

    projection = await repository.get_active_mode_projection(pairs)

    # Operation-count proof: every configured nonempty hierarchy reads once.
    assert connection.fetch_calls == 1
    assert projection.active_modes == expected_active
    assert projection.mode_parameters == expected_parameters


@pytest.mark.asyncio
async def test_active_mode_projection_reads_once_on_caller_owned_connection() -> None:
    rows = [
        _projection_row(
            "Veg Room",
            "main",
            mode_id=2,
            submode_id=None,
            mode_name="veg",
            submode_name=None,
            day_start_time="06:00",
            night_start_time="18:00",
            ramp_up=15,
            ramp_down=15,
        )
    ]
    connection = _ProjectionRowConnection(rows)
    repository = RoomModeRepository(None)

    projection = await repository.get_active_mode_projection(
        [("Veg Room", "main")], conn=connection
    )

    assert connection.fetch_calls == 1
    assert projection.active_modes == {
        ("Veg Room", "main"): {
            "mode_id": 2,
            "submode_id": None,
            "mode_name": "veg",
            "submode_name": None,
        }
    }
    assert projection.mode_parameters == {
        ("Veg Room", "main"): {
            "mode_id": 2,
            "day_start": "06:00",
            "night_start": "18:00",
            "ramp_up": 15,
            "ramp_down": 15,
        }
    }


@pytest.mark.asyncio
async def test_empty_hierarchy_projection_performs_zero_reads() -> None:
    connection = _ProjectionRowConnection([])
    poolless_repository = RoomModeRepository(None)
    pooled_repository = RoomModeRepository(_ProjectionPool(connection))

    from_pool = await pooled_repository.get_active_mode_projection([])
    from_conn = await poolless_repository.get_active_mode_projection([], conn=connection)

    assert connection.fetch_calls == 0  # no SQL for an empty runtime hierarchy
    assert from_pool.active_modes == {}
    assert from_pool.mode_parameters == {}
    assert from_conn.active_modes == {}
    assert from_conn.mode_parameters == {}


@pytest.mark.asyncio
async def test_projection_keeps_identity_names_where_parameters_absent() -> None:
    rows = [
        # Flower submode identity with no stored parameter row: names survive,
        # no parameters entry is invented.
        _projection_row(
            "Flower Room",
            "main",
            mode_id=1,
            submode_id=5,
            mode_name="flower",
            submode_name="bulk",
        ),
        # NULL-base identity with parameters stored as TIME columns.
        _projection_row(
            "Veg Room",
            "main",
            mode_id=2,
            submode_id=None,
            mode_name="veg",
            submode_name=None,
            day_start_time=dt_time(6, 0),
            night_start_time=dt_time(18, 0),
            ramp_up=15,
            ramp_down=15,
        ),
        # Pair with no active row at all: neither map gains an entry.
        _projection_row("Flower Room", "dry"),
    ]
    connection = _ProjectionRowConnection(rows)
    repository = RoomModeRepository(None)

    projection = await repository.get_active_mode_projection(
        [("Flower Room", "main"), ("Veg Room", "main"), ("Flower Room", "dry")],
        conn=connection,
    )

    assert projection.active_modes == {
        ("Flower Room", "main"): {
            "mode_id": 1,
            "submode_id": 5,
            "mode_name": "flower",
            "submode_name": "bulk",
        },
        ("Veg Room", "main"): {
            "mode_id": 2,
            "submode_id": None,
            "mode_name": "veg",
            "submode_name": None,
        },
    }
    assert projection.mode_parameters == {
        ("Veg Room", "main"): {
            "mode_id": 2,
            "day_start": "06:00",
            "night_start": "18:00",
            "ramp_up": 15,
            "ramp_down": 15,
        }
    }


@pytest.mark.asyncio
async def test_projection_query_error_propagates_from_both_load_paths() -> None:
    class _FailingProjection:
        async def fetch(self, _query: str, *_args: object) -> list[dict[str, Any]]:
            raise ConnectionError("projection query failed")

    repository = RoomModeRepository(None)

    with pytest.raises(ConnectionError, match="projection query failed"):
        await repository.get_active_mode_projection(
            [("Veg Room", "main")], conn=_FailingProjection()
        )

    failing_pool_repository = RoomModeRepository(_ProjectionPool(_FailingProjection()))
    with pytest.raises(ConnectionError, match="projection query failed"):
        await failing_pool_repository.get_active_mode_projection([("Veg Room", "main")])


# ---------------------------------------------------------------------------
# Registry snapshot builds: one projection query per load path, no partial
# install on projection failure.
# ---------------------------------------------------------------------------


class _CountingRoomModeProjectionRepository:
    def __init__(self, projection: ActiveModeProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[tuple[tuple[str, str], ...], Any]] = []

    async def get_active_mode_projection(
        self,
        room_clusters: Sequence[tuple[str, str]],
        conn: Any | None = None,
    ) -> ActiveModeProjection:
        self.calls.append((tuple(room_clusters), conn))
        return self.projection


class _FailingRoomModeProjectionRepository:
    def __init__(self, projection: ActiveModeProjection) -> None:
        self.projection = projection
        self.fail = False
        self.calls: list[tuple[tuple[tuple[str, str], ...], Any]] = []

    async def get_active_mode_projection(
        self,
        room_clusters: Sequence[tuple[str, str]],
        conn: Any | None = None,
    ) -> ActiveModeProjection:
        self.calls.append((tuple(room_clusters), conn))
        if self.fail:
            raise ConnectionError("active mode projection failed")
        return self.projection


_PREPARED_PROJECTION = ActiveModeProjection(
    active_modes={
        ("Veg Room", "main"): {
            "mode_id": 2,
            "submode_id": None,
            "mode_name": "veg",
            "submode_name": None,
        }
    },
    mode_parameters={},
)


@pytest.mark.asyncio
async def test_registry_installs_projection_maps_with_one_query_per_load_path() -> None:
    device_repo = _ProjectionRepository({"Veg Room": {"main": {}}})
    room_mode_repo = _CountingRoomModeProjectionRepository(_PREPARED_PROJECTION)
    database: Any = _RuntimeDatabase(device_repo, room_mode_repo=room_mode_repo)
    registry: Any = RuntimeDeviceRegistry(database)

    installed = await registry.load_startup()

    standalone_pairs, standalone_conn = room_mode_repo.calls[0]
    assert installed.active_modes == _PREPARED_PROJECTION.active_modes
    assert installed.mode_parameters == _PREPARED_PROJECTION.mode_parameters
    assert standalone_pairs == (("Veg Room", "main"),)
    assert standalone_conn is None

    room_mode_repo.calls.clear()

    async def mutation(_connection: Any) -> str:
        return "written"

    committed = await registry.mutate(mutation)

    assert committed == "written"
    transaction_pairs, transaction_conn = room_mode_repo.calls[0]
    assert transaction_pairs == (("Veg Room", "main"),)
    assert transaction_conn is not None  # the mutation's own connection
    assert registry.snapshot.active_modes == _PREPARED_PROJECTION.active_modes
    assert registry.snapshot.version == installed.version + 1


@pytest.mark.asyncio
async def test_startup_projection_error_prevents_snapshot_installation() -> None:
    device_repo = _ProjectionRepository({"Veg Room": {"main": {}}})
    room_mode_repo = _FailingRoomModeProjectionRepository(_PREPARED_PROJECTION)
    room_mode_repo.fail = True
    database: Any = _RuntimeDatabase(device_repo, room_mode_repo)
    registry: Any = RuntimeDeviceRegistry(database)

    with pytest.raises(ConnectionError, match="active mode projection failed"):
        await registry.load_startup()

    with pytest.raises(RuntimeError, match="not installed"):
        _ = registry.snapshot


@pytest.mark.asyncio
async def test_mutation_projection_error_keeps_installed_snapshot_complete() -> None:
    device_repo = _ProjectionRepository({"Veg Room": {"main": {}}})
    room_mode_repo = _FailingRoomModeProjectionRepository(_PREPARED_PROJECTION)
    database: Any = _RuntimeDatabase(device_repo, room_mode_repo)
    registry: Any = RuntimeDeviceRegistry(database)
    installed = await registry.load_startup()

    room_mode_repo.fail = True

    async def mutation(_connection: Any) -> None:
        return None

    with pytest.raises(ConnectionError, match="active mode projection failed"):
        await registry.mutate(mutation)

    # Strict projection failure: no empty/partial snapshot is installed.
    assert registry.snapshot is installed
    assert registry.snapshot.version == installed.version
    assert registry.snapshot.active_modes == installed.active_modes
    assert registry.snapshot.mode_parameters == installed.mode_parameters
