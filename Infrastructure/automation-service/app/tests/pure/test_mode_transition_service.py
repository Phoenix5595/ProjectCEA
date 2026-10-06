"""Contract-level activation transitions over real seams and in-memory state.

Every test drives the real :class:`ModeTransitionService` through the real
``RuntimeDeviceRegistry.mutate`` boundary with real repositories
(``RoomModeRepository``, ``ScheduleRepository``, ``ConfigRepository``,
``DeviceRepository``, the light repos and ``RoomScheduleService``). The only
fake is the asyncpg-shaped connection whose ``transaction()`` context manager
snapshots every persisted table and restores it on rollback, so rollback,
revision conflicts and post-commit publication failures are observed as
concrete state instead of forwarded mocks.

Faults are injected at the persistence layer (schedule insert, history insert,
snapshot projection read) or at the real post-commit seams (registry
publication hook, snapshot consumer, device-configuration read). A service
that swallowed a failure, committed before the failure, or bypassed the shared
transaction fails these tests.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time as dt_time
from typing import Any, final

import pytest

from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.repositories.config import ConfigRepository
from app.repositories.devices import DeviceRepository
from app.repositories.devices.projection import project_registry_rows
from app.repositories.light_programs import LightProgramsRepository
from app.repositories.light_target_intensity import LightTargetIntensityRepository
from app.repositories.room_modes import RoomModeRepository
from app.repositories.schedules import ScheduleRepository
from app.services.mode_transition_service import (
    ACTIVATION_RUNTIME_REFRESH_FAILED,
    ModeTransitionService,
)
from app.services.room_schedule_service import RoomScheduleService
from app.state import reset_state_manager

_FLOWER = "Flower Room"
_MAIN = "main"
_SEEDED_AT = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)


def _mode_rows() -> dict[int, dict[str, Any]]:
    return {
        1: {"id": 1, "name": "veg", "is_constant": False, "photoperiod_hours": 18},
        2: {"id": 2, "name": "flower", "is_constant": False, "photoperiod_hours": 12},
        3: {"id": 3, "name": "drying", "is_constant": True, "photoperiod_hours": 0},
    }


def _submode_rows() -> dict[int, dict[str, Any]]:
    return {
        1: {"id": 1, "name": "stretch", "week_start": 1, "week_end": 3},
        2: {"id": 2, "name": "bulk", "week_start": 4, "week_end": 6},
    }


def _registry_rows() -> list[dict[str, Any]]:
    """device_registry rows in the production SELECT column set."""
    common: dict[str, Any] = {
        "location": _FLOWER,
        "cluster": _MAIN,
        "safety_level": 0,
        "pid_enabled": False,
        "interlock_with": [],
        "pid_setpoints": {},
        "created_at": _SEEDED_AT,
        "updated_at": _SEEDED_AT,
    }
    return [
        {
            **common,
            "device_id": 11,
            "device_name": "heater",
            "display_name": "Heater",
            "device_type": "heater",
            "channel": None,
            "dimming_enabled": None,
            "dimming_type": None,
            "dimming_board_id": None,
            "dimming_channel": None,
            "per_room_index": None,
        },
        {
            **common,
            "device_id": 12,
            "device_name": "humidifier",
            "display_name": "Humidifier",
            "device_type": "humidifier",
            "channel": None,
            "dimming_enabled": None,
            "dimming_type": None,
            "dimming_board_id": None,
            "dimming_channel": None,
            "per_room_index": None,
        },
        {
            **common,
            "device_id": 13,
            "device_name": "light1",
            "display_name": "Light 1",
            "device_type": "light",
            "channel": 1,
            "dimming_enabled": True,
            "dimming_type": "dfr0971",
            "dimming_board_id": 0,
            "dimming_channel": 0,
            "per_room_index": 1,
        },
    ]


def _parameter_row(
    mode_id: int,
    submode_id: int | None,
    day_start: str,
    night_start: str,
    ramp_up: int,
    ramp_down: int,
) -> dict[str, Any]:
    return {
        "id": 40 + mode_id * 10 + (submode_id or 0),
        "location": _FLOWER,
        "cluster": _MAIN,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "day_start_time": dt_time(int(day_start[:2]), int(day_start[3:5])),
        "night_start_time": dt_time(int(night_start[:2]), int(night_start[3:5])),
        "light_ramp_up_minutes": ramp_up,
        "light_ramp_down_minutes": ramp_down,
        "main_light_intensity": 100,
        "supplemental_light_intensity": 0,
        "updated_at": _SEEDED_AT,
    }


def _schedule_row(row_id: int, device_name: str, mode: str, start: str, end: str) -> dict[str, Any]:
    return {
        "id": row_id,
        "name": f"{device_name} {mode}",
        "location": _FLOWER,
        "cluster": _MAIN,
        "device_name": device_name,
        "start_time": dt_time(int(start[:2]), int(start[3:5])),
        "end_time": dt_time(int(end[:2]), int(end[3:5])),
        "day_of_week": None,
        "enabled": True,
        "mode": mode,
        "target_intensity": None,
        "ramp_up_duration": None,
        "ramp_down_duration": None,
        "updated_at": _SEEDED_AT,
    }


@final
class _Store:
    """Persisted tables swapped as one committed unit by the transaction manager."""

    def __init__(self) -> None:
        self.modes: dict[int, dict[str, Any]] = {}
        self.submodes: dict[int, dict[str, Any]] = {}
        self.registry_rows: list[dict[str, Any]] = []
        self.active_modes: dict[tuple[str, str], dict[str, Any]] = {}
        self.parameters: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
        self.schedules: dict[int, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.config_versions: list[dict[str, Any]] = []
        self.light_target_intensity: list[dict[str, Any]] = []
        self.light_programs: list[dict[str, Any]] = []
        self.next_schedule_id = 105
        self.next_history_id = 1
        self.next_version_id = 8


def _seeded_store() -> _Store:
    """Flower Room/main on flower/stretch with one prior configuration revision."""
    store = _Store()
    store.modes = _mode_rows()
    store.submodes = _submode_rows()
    store.registry_rows = _registry_rows()
    store.active_modes = {
        (_FLOWER, _MAIN): {"mode_id": 2, "submode_id": 1},
        # Sibling cluster of the same location: exercised by desync detection.
        (_FLOWER, "side"): {"mode_id": 3, "submode_id": None},
    }
    store.parameters = {
        (_FLOWER, _MAIN, 2, 1): _parameter_row(2, 1, "06:00", "18:00", 15, 15),
        (_FLOWER, _MAIN, 2, 2): _parameter_row(2, 2, "05:00", "17:00", 10, 12),
        (_FLOWER, _MAIN, 1, None): _parameter_row(1, None, "06:00", "00:00", 20, 20),
        (_FLOWER, _MAIN, 3, None): _parameter_row(3, None, "00:00", "00:00", 0, 0),
    }
    store.schedules = {
        101: _schedule_row(101, "heater", "DAY", "06:00", "18:00"),
        102: _schedule_row(102, "heater", "NIGHT", "18:00", "06:00"),
        103: _schedule_row(103, "room_schedule", "DAY", "06:00", "18:00"),
        104: _schedule_row(104, "climate", "DAY", "06:00", "18:00"),
    }
    store.config_versions = [
        {
            "version_id": 7,
            "author": "system",
            "comment": "prior mode_parameters save",
            "config_type": "mode_parameters",
            "location": _FLOWER,
            "cluster": _MAIN,
            "changes": None,
        }
    ]
    return store


@final
class _MemoryState:
    """Committed persistence, transaction counters and injected fault flags."""

    def __init__(self) -> None:
        self.committed = _seeded_store()
        self.commits = 0
        self.rollbacks = 0
        self.transaction_begins = 0
        self.issued: list[str] = []
        self.fail_schedule_insert = False
        self.fail_history_insert = False
        self.fail_snapshot_projection = False
        self.fail_post_commit_config = False

    def persisted_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.committed.__dict__)


@final
class _MemoryConnection:
    """asyncpg-shaped connection over the in-memory store.

    Reads and writes inside ``transaction()`` hit a deep copy of the committed
    tables; a clean exit swaps that copy in as the new committed state and a
    raised exception drops it, restoring every persisted category.
    """

    def __init__(self, state: _MemoryState) -> None:
        self._state = state
        self._pending: _Store | None = None

    @property
    def _store(self) -> _Store:
        if self._pending is not None:
            return self._pending
        return self._state.committed

    @asynccontextmanager
    async def transaction(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
        self._state.transaction_begins += 1
        self._pending = copy.deepcopy(self._state.committed)
        try:
            yield
        except BaseException:
            self._pending = None
            self._state.rollbacks += 1
            raise
        self._state.committed = self._pending
        self._pending = None
        self._state.commits += 1

    async def execute(self, query: str, *args: Any) -> str:
        self._state.issued.append(query)
        sql = " ".join(query.split())
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if "DELETE FROM schedules WHERE id = ANY" in sql:
            removed = [row_id for row_id in args[0] if row_id in self._store.schedules]
            for row_id in removed:
                del self._store.schedules[row_id]
            return f"DELETE {len(removed)}"
        if "INSERT INTO room_active_mode" in sql:
            location, cluster, mode_id, submode_id = args
            self._store.active_modes[(location, cluster)] = {
                "mode_id": mode_id,
                "submode_id": submode_id,
            }
            return "INSERT 0 1"
        if "INSERT INTO mode_transition_history" in sql:
            if self._state.fail_history_insert:
                raise RuntimeError("history insert failed")
            (
                location,
                cluster,
                old_mode_id,
                old_submode_id,
                new_mode_id,
                new_submode_id,
                triggered_by,
                params_json,
            ) = args
            self._store.history.append(
                {
                    "id": self._store.next_history_id,
                    "location": location,
                    "cluster": cluster,
                    "old_mode_id": old_mode_id,
                    "old_submode_id": old_submode_id,
                    "new_mode_id": new_mode_id,
                    "new_submode_id": new_submode_id,
                    "triggered_by": triggered_by,
                    "parameters_synced": json.loads(params_json),
                    "success": True,
                }
            )
            self._store.next_history_id += 1
            return "INSERT 0 1"
        raise AssertionError(f"Unexpected execute: {sql}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self._state.issued.append(query)
        sql = " ".join(query.split())
        store = self._store
        if "FROM room_active_mode arm JOIN room_modes rm" in sql:
            active = store.active_modes.get((args[0], args[1]))
            if active is None or active["mode_id"] not in store.modes:
                return None
            mode = store.modes[active["mode_id"]]
            submode = (
                store.submodes.get(active["submode_id"])
                if active["submode_id"] is not None
                else None
            )
            return {
                "location": args[0],
                "cluster": args[1],
                "mode_name": mode["name"],
                "submode_name": submode["name"] if submode else None,
                "mode_id": active["mode_id"],
                "submode_id": active["submode_id"],
            }
        if "FROM room_active_mode" in sql:
            active = store.active_modes.get((args[0], args[1]))
            if active is None:
                return None
            return {"mode_id": active["mode_id"], "submode_id": active["submode_id"]}
        if "FROM room_modes WHERE id = $1" in sql:
            row = store.modes.get(args[0])
            return dict(row) if row else None
        if "FROM flower_submodes WHERE id = $1" in sql:
            row = store.submodes.get(args[0])
            return dict(row) if row else None
        if "FROM room_modes WHERE name = $1" in sql:
            row = next((m for m in store.modes.values() if m["name"] == args[0]), None)
            return dict(row) if row else None
        if "FROM flower_submodes WHERE name = $1" in sql:
            row = next((s for s in store.submodes.values() if s["name"] == args[0]), None)
            return dict(row) if row else None
        if "FROM mode_parameters" in sql:
            location, cluster, mode_id = args[0], args[1], args[2]
            if "IS NOT DISTINCT FROM" in sql or "COALESCE(submode_id" in sql:
                submode_id: int | None = args[3]
            elif "submode_id IS NULL" in sql:
                submode_id = None
            elif "submode_id = $4" in sql:
                submode_id = args[3]
            else:
                raise AssertionError(f"Unexpected mode_parameters filter: {sql}")
            row = store.parameters.get((location, cluster, mode_id, submode_id))
            return dict(row) if row else None
        if "INSERT INTO schedules" in sql:
            if self._state.fail_schedule_insert:
                raise RuntimeError("schedule insert failed")
            (
                name,
                location,
                cluster,
                device_name,
                start_time,
                end_time,
                day_of_week,
                enabled,
                mode,
                target_intensity,
                ramp_up_duration,
                ramp_down_duration,
            ) = args
            schedule_id = store.next_schedule_id
            store.next_schedule_id += 1
            store.schedules[schedule_id] = {
                "id": schedule_id,
                "name": name,
                "location": location,
                "cluster": cluster,
                "device_name": device_name,
                "start_time": start_time,
                "end_time": end_time,
                "day_of_week": day_of_week,
                "enabled": enabled,
                "mode": mode,
                "target_intensity": target_intensity,
                "ramp_up_duration": ramp_up_duration,
                "ramp_down_duration": ramp_down_duration,
                "updated_at": datetime.now(tz=UTC),
            }
            return {"id": schedule_id}
        if "INSERT INTO config_versions" in sql:
            author, comment, config_type, location, cluster, changes_json = args
            version_id = store.next_version_id
            store.next_version_id += 1
            store.config_versions.append(
                {
                    "version_id": version_id,
                    "author": author,
                    "comment": comment,
                    "config_type": config_type,
                    "location": location,
                    "cluster": cluster,
                    "changes": json.loads(changes_json) if changes_json else None,
                }
            )
            return {"version_id": version_id}
        raise AssertionError(f"Unexpected fetchrow: {sql}")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self._state.issued.append(query)
        sql = " ".join(query.split())
        store = self._store
        if "FROM requested req" in sql and "IS NOT DISTINCT FROM arm.submode_id" in sql:
            if self._state.fail_snapshot_projection:
                raise RuntimeError("database unavailable")
            # Real RoomModeRepository.get_active_mode_projection shape: one row
            # per requested room pair, active identity LEFT JOINed with its
            # exact NULL-safe parameter row.
            rows: list[dict[str, Any]] = []
            for index, (location, cluster) in enumerate(zip(args[0::2], args[1::2])):
                active = store.active_modes.get((location, cluster))
                mode = store.modes.get(active["mode_id"]) if active else None
                if active is None or mode is None:
                    rows.append(
                        {
                            "location": location,
                            "cluster": cluster,
                            "mode_id": None,
                            "submode_id": None,
                            "mode_name": None,
                            "submode_name": None,
                            "day_start_time": None,
                            "night_start_time": None,
                            "light_ramp_up_minutes": None,
                            "light_ramp_down_minutes": None,
                            "ord": index,
                        }
                    )
                    continue
                submode = (
                    store.submodes.get(active["submode_id"])
                    if active["submode_id"] is not None
                    else None
                )
                parameters = store.parameters.get(
                    (location, cluster, active["mode_id"], active["submode_id"])
                )
                rows.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "mode_id": active["mode_id"],
                        "submode_id": active["submode_id"],
                        "mode_name": mode["name"],
                        "submode_name": submode["name"] if submode else None,
                        "day_start_time": parameters["day_start_time"] if parameters else None,
                        "night_start_time": parameters["night_start_time"] if parameters else None,
                        "light_ramp_up_minutes": (
                            parameters["light_ramp_up_minutes"] if parameters else None
                        ),
                        "light_ramp_down_minutes": (
                            parameters["light_ramp_down_minutes"] if parameters else None
                        ),
                        "ord": index,
                    }
                )
            return rows
        if "FROM device_registry" in sql:
            if self._state.fail_snapshot_projection:
                raise RuntimeError("database unavailable")
            rows: list[dict[str, Any]] = []
            for row in store.registry_rows:
                device_schedules = sorted(
                    (
                        schedule
                        for schedule in store.schedules.values()
                        if schedule["location"] == row["location"]
                        and schedule["cluster"] == row["cluster"]
                        and schedule["device_name"] == row["device_name"]
                    ),
                    key=lambda schedule: (schedule["start_time"], schedule["id"]),
                )
                enriched = dict(row)
                enriched["inherited_schedule_count"] = len(device_schedules)
                enriched["inherited_schedule_summary"] = [
                    schedule["name"] for schedule in device_schedules
                ]
                rows.append(enriched)
            return sorted(
                rows, key=lambda row: (row["location"], row["cluster"], row["device_name"])
            )
        if "FROM schedules" in sql:
            if "device_name = 'room_schedule'" in sql:
                location, cluster = args
                return [
                    {"id": row_id}
                    for row_id, row in sorted(store.schedules.items())
                    if row["location"] == location
                    and row["cluster"] == cluster
                    and row["device_name"] == "room_schedule"
                ]
            if "ORDER BY start_time" in sql:
                rows = list(store.schedules.values())
                if "WHERE location = $1 AND cluster = $2" in sql:
                    location, cluster = args
                    rows = [
                        row
                        for row in rows
                        if row["location"] == location and row["cluster"] == cluster
                    ]
                elif "WHERE location = $1" in sql:
                    rows = [row for row in rows if row["location"] == args[0]]
                return sorted(rows, key=lambda row: (row["start_time"], row["id"]))
        if "FROM light_target_intensity" in sql:
            return [dict(row) for row in store.light_target_intensity]
        if "FROM light_programs" in sql:
            rows = store.light_programs
            if "enabled = TRUE" in sql:
                rows = [row for row in rows if row["enabled"]]
            return [dict(row) for row in rows]
        if "FROM room_active_mode" in sql and "cluster != " in sql:
            location, cluster = args
            return [
                {"cluster": key[1], "mode_id": row["mode_id"]}
                for key, row in store.active_modes.items()
                if key[0] == location and key[1] != cluster
            ]
        raise AssertionError(f"Unexpected fetch: {sql}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._state.issued.append(query)
        sql = " ".join(query.split())
        if "COALESCE(MAX(version_id), 0) FROM config_versions" in sql:
            return max(
                (row["version_id"] for row in self._store.config_versions),
                default=0,
            )
        raise AssertionError(f"Unexpected fetchval: {sql}")


@final
class _MemoryPool:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    def acquire(self):
        connection = _MemoryConnection(self._state)

        @asynccontextmanager
        async def _acquired() -> AsyncIterator[_MemoryConnection]:
            yield connection

        return _acquired()


@final
class _DeviceConfiguration:
    """Device hierarchy for schedule derivation, sourced from the registry rows."""

    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    async def get_devices(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        if self._state.fail_post_commit_config and self._state.commits > 0:
            raise RuntimeError("device configuration unavailable after commit")
        return project_registry_rows(self._state.committed.registry_rows).hierarchy


@final
class _ActivationDatabase(DatabaseManager):
    """Real DatabaseManager wired to the in-memory pool and real repositories."""

    def __init__(self, state: _MemoryState) -> None:
        super().__init__(
            {
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "user": "test",
                "password": "test",
            }
        )
        pool = _MemoryPool(state)
        self._state = state
        self._pool = pool  # type: ignore[assignment]
        self._db_connected = True
        self._device_repo = DeviceRepository(pool)
        self._schedule_repo = ScheduleRepository(pool)
        self._room_mode_repo = RoomModeRepository(pool)
        self._config_repo = ConfigRepository(pool)
        self._light_target_intensity_repo = LightTargetIntensityRepository(pool)
        self._light_programs_repo = LightProgramsRepository(pool)


@final
class _Harness:
    """Assembled transition stack over one in-memory state."""

    def __init__(self) -> None:
        self.state = _MemoryState()
        self.database = _ActivationDatabase(self.state)
        self.registry = RuntimeDeviceRegistry(self.database)
        self.schedule_service = RoomScheduleService(self.database, _DeviceConfiguration(self.state))
        self.scheduler = Scheduler([], None)
        self.scheduler._light_ramp_state = {
            (_FLOWER, _MAIN, "light1", None): {"target": 22},
            (_FLOWER, _MAIN, "light1", 5): {"target": 22},
            ("Veg Room", _MAIN, "light1", None): {"target": 18},
        }
        self.transition = ModeTransitionService(
            self.database,
            self.schedule_service,
            self.registry,
            self.scheduler,
        )
        self.startup_snapshot: RuntimeDeviceSnapshot | None = None

    async def start(self) -> None:
        self.startup_snapshot = await self.registry.load_startup()


async def _build_harness() -> _Harness:
    harness = _Harness()
    await harness.start()
    return harness


def _assert_no_persisted_write(state: _MemoryState) -> None:
    writes = [
        sql
        for sql in state.issued
        if any(op in sql for op in ("INSERT INTO", "UPDATE ", "DELETE FROM"))
    ]
    assert writes == [], f"unexpected persisted writes: {writes}"


@pytest.fixture(autouse=True)
def _fresh_cache_singletons() -> Iterator[None]:
    reset_state_manager()
    try:
        yield
    finally:
        reset_state_manager()


@pytest.mark.asyncio
async def test_full_mode_activation_commits_one_revision_replaces_schedules_and_clears_ramps() -> None:
    # Given: Flower/stretch is active with its own schedules and ramp state.
    harness = await _build_harness()
    state = harness.state

    # When: the base Veg profile is activated through the registry seam.
    result = await harness.transition.execute_mode_transition(
        _FLOWER, _MAIN, 1, None, "api", expected_config_revision="0000007"
    )

    # Then: one transaction commits the identity, one revision, history and schedules.
    assert result["success"] is True
    assert result["config_revision"] == "0000008"
    assert result["runtime_ready"] is True
    assert result["warning"] is None
    assert result.get("error_code") is None
    assert result["new_mode"]["location"] == _FLOWER
    assert result["new_mode"]["mode_id"] == 1
    assert result["new_mode"]["submode_id"] is None
    assert result["new_mode"]["mode_name"] == "veg"
    assert result["new_mode"]["is_constant"] is False
    assert result["old_mode"]["mode_name"] == "flower"
    assert result["old_mode"]["submode_name"] == "stretch"
    assert result["schedule_sync_result"] == {"schedules_created": 4, "devices_configured": 3}
    assert result["parameters"]["mode_id"] == 1
    assert result["parameters"]["day_start_time"] == dt_time(6, 0)
    assert state.commits == 1
    assert state.rollbacks == 0
    assert state.transaction_begins == 1

    committed = state.committed
    assert committed.active_modes[(_FLOWER, _MAIN)] == {"mode_id": 1, "submode_id": None}

    assert len(committed.history) == 1
    history_row = committed.history[0]
    assert history_row["old_mode_id"] == 2
    assert history_row["old_submode_id"] == 1
    assert history_row["new_mode_id"] == 1
    assert history_row["new_submode_id"] is None
    assert history_row["triggered_by"] == "api"
    assert history_row["parameters_synced"]["old_mode_name"] == "flower"
    assert history_row["parameters_synced"]["new_mode_name"] == "veg"
    assert history_row["success"] is True

    assert [row["version_id"] for row in committed.config_versions] == [7, 8]
    revision_row = committed.config_versions[-1]
    assert revision_row["config_type"] == "room_mode"
    assert revision_row["changes"] == {
        "old_mode_id": 2,
        "old_submode_id": 1,
        "new_mode_id": 1,
        "new_submode_id": None,
    }

    schedules = committed.schedules
    assert set(schedules) == {103, 104, 105, 106, 107, 108}
    assert schedules[103]["start_time"] == dt_time(6, 0)
    assert schedules[104]["device_name"] == "climate"
    heater_rows = [row for row in schedules.values() if row["device_name"] == "heater"]
    assert {
        (row["name"], row["mode"], row["start_time"], row["end_time"], row["ramp_up_duration"])
        for row in heater_rows
    } == {
        ("Heater - Day", "DAY", dt_time(6, 0), dt_time(0, 0), None),
        ("Heater - Night", "NIGHT", dt_time(0, 0), dt_time(6, 0), None),
    }
    assert not any(row["device_name"].startswith("light") for row in schedules.values())

    snapshot = harness.registry.snapshot
    assert isinstance(snapshot, RuntimeDeviceSnapshot)
    assert snapshot is not harness.startup_snapshot
    assert snapshot.version == harness.startup_snapshot.version + 1
    assert snapshot.mode_parameters[(_FLOWER, _MAIN)] == {
        "mode_id": 1,
        "day_start": "06:00",
        "night_start": "00:00",
        "ramp_up": 20,
        "ramp_down": 20,
    }
    assert snapshot.active_modes[(_FLOWER, _MAIN)] == {
        "mode_id": 1,
        "submode_id": None,
        "mode_name": "veg",
        "submode_name": None,
    }
    with pytest.raises(TypeError):
        snapshot.active_modes[(_FLOWER, _MAIN)] = {}  # type: ignore[index]
    assert set(snapshot.hierarchy[_FLOWER][_MAIN]) == {"heater", "humidifier", "light1"}
    assert snapshot.hierarchy[_FLOWER][_MAIN]["heater"]["device_type"] == "heater"
    with pytest.raises(TypeError):
        snapshot.mode_parameters[(_FLOWER, _MAIN)] = {}  # type: ignore[index]

    scheduler_ramps = harness.scheduler._light_ramp_state
    assert all(key[0] != _FLOWER for key in scheduler_ramps)
    assert ("Veg Room", _MAIN, "light1", None) in scheduler_ramps


@pytest.mark.asyncio
async def test_same_mode_submode_change_skips_schedule_replacement_and_ramp_reset() -> None:
    # Given: Flower/stretch is active and Flower/bulk has configured parameters.
    harness = await _build_harness()
    state = harness.state

    # When: only the flower submode changes, as the calendar does.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 2, 2, "system")

    # Then: no schedule rows are rewritten and light ramp state is retained.
    assert result["success"] is True
    assert result["schedule_sync_result"] == {"skipped": True, "reason": "submode_only_transition"}
    assert result["config_revision"] == "0000008"
    assert result["runtime_ready"] is True
    assert state.commits == 1
    assert state.rollbacks == 0

    committed = state.committed
    assert committed.active_modes[(_FLOWER, _MAIN)] == {"mode_id": 2, "submode_id": 2}
    assert [row["version_id"] for row in committed.config_versions] == [7, 8]
    assert committed.history[0]["old_mode_id"] == 2
    assert committed.history[0]["new_mode_id"] == 2
    assert committed.history[0]["parameters_synced"]["old_submode_name"] == "stretch"
    assert committed.history[0]["parameters_synced"]["new_submode_name"] == "bulk"

    assert set(committed.schedules) == {101, 102, 103, 104}
    assert committed.schedules[101]["start_time"] == dt_time(6, 0)

    snapshot = harness.registry.snapshot
    assert snapshot.version == harness.startup_snapshot.version + 1
    assert snapshot.mode_parameters[(_FLOWER, _MAIN)] == {
        "mode_id": 2,
        "day_start": "05:00",
        "night_start": "17:00",
        "ramp_up": 10,
        "ramp_down": 12,
    }
    assert snapshot.active_modes[(_FLOWER, _MAIN)] == {
        "mode_id": 2,
        "submode_id": 2,
        "mode_name": "flower",
        "submode_name": "bulk",
    }

    assert set(harness.scheduler._light_ramp_state) == {
        (_FLOWER, _MAIN, "light1", None),
        (_FLOWER, _MAIN, "light1", 5),
        ("Veg Room", _MAIN, "light1", None),
    }


@pytest.mark.asyncio
async def test_same_mode_submode_without_parameters_rejects_before_any_write() -> None:
    # Given: Flower/bulk was never configured with parameters.
    harness = await _build_harness()
    state = harness.state
    del state.committed.parameters[(_FLOWER, _MAIN, 2, 2)]
    before = state.persisted_snapshot()

    # When: the calendar targets the unconfigured flower/bulk profile.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 2, 2, "system")

    # Then: the activation is rejected with the typed configured-profile error
    # and the pre-write identity is rolled back before any commit.
    assert result["success"] is False
    assert result["error_code"] == "profile_not_configured"
    assert result["runtime_ready"] is False
    assert result["message"]
    assert state.commits == 0
    assert state.rollbacks == 1
    assert state.persisted_snapshot() == before
    assert harness.registry.snapshot is harness.startup_snapshot


@pytest.mark.asyncio
async def test_stale_expected_revision_rejects_without_any_write() -> None:
    # Given: the committed revision advanced since the request was built.
    harness = await _build_harness()
    state = harness.state
    before = state.persisted_snapshot()

    # When: the activation guards the stale expected revision.
    result = await harness.transition.execute_mode_transition(
        _FLOWER, _MAIN, 3, None, "api", expected_config_revision="0000003"
    )

    # Then: the conflict is reported and no persisted category changes.
    assert result["success"] is False
    assert result["error_code"] == "activation_revision_conflict"
    assert result["runtime_ready"] is False
    assert result["old_mode"] == {
        "location": _FLOWER,
        "cluster": _MAIN,
        "mode_name": "flower",
        "submode_name": "stretch",
        "mode_id": 2,
        "submode_id": 1,
    }
    assert state.commits == 0
    assert state.rollbacks == 1
    assert state.persisted_snapshot() == before
    _assert_no_persisted_write(state)
    assert harness.registry.snapshot is harness.startup_snapshot


@pytest.mark.asyncio
async def test_schedule_insert_failure_rolls_back_every_persisted_category() -> None:
    # Given: the derived schedule insertion fails on the activation connection.
    harness = await _build_harness()
    state = harness.state
    state.fail_schedule_insert = True
    before = state.persisted_snapshot()

    # When: a full mode change reaches the failing schedule boundary.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 1, None, "api")

    # Then: identity, history and revision all roll back together.
    assert result["success"] is False
    assert result["runtime_ready"] is False
    assert result["message"]
    assert state.commits == 0
    assert state.rollbacks == 1
    assert state.persisted_snapshot() == before
    assert harness.registry.snapshot is harness.startup_snapshot


@pytest.mark.asyncio
async def test_history_insert_failure_rolls_back_every_persisted_category() -> None:
    # Given: the transition history insert fails inside the activation transaction.
    harness = await _build_harness()
    state = harness.state
    state.fail_history_insert = True
    before = state.persisted_snapshot()

    # When: a full mode change reaches the failing history boundary.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 1, None, "api")

    # Then: the activation commits nothing and restores all persisted categories.
    assert result["success"] is False
    assert result["runtime_ready"] is False
    assert state.commits == 0
    assert state.rollbacks == 1
    assert state.persisted_snapshot() == before
    assert harness.registry.snapshot is harness.startup_snapshot


@pytest.mark.asyncio
async def test_pending_snapshot_build_failure_rolls_back_and_keeps_snapshot() -> None:
    # Given: the projection read behind the pending snapshot fails pre-commit.
    harness = await _build_harness()
    state = harness.state
    state.fail_snapshot_projection = True
    before = state.persisted_snapshot()

    # When: the activation reaches the pending snapshot build.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 1, None, "api")

    # Then: nothing commits and the installed snapshot stays untouched.
    assert result["success"] is False
    assert result["runtime_ready"] is False
    assert state.commits == 0
    assert state.rollbacks == 1
    assert state.persisted_snapshot() == before
    assert harness.registry.snapshot is harness.startup_snapshot


@pytest.mark.asyncio
async def test_post_commit_publication_hook_failure_reports_committed_activation() -> None:
    # Given: the registry publication hook fails after the transaction committed.
    harness = await _build_harness()
    state = harness.state

    async def failing_hook(_snapshot: RuntimeDeviceSnapshot) -> None:
        raise RuntimeError("subscriber crashed")

    harness.registry._after_commit_before_install = failing_hook  # type: ignore[method-assign]

    # When: the activation commits and then fails to publish its snapshot.
    result = await harness.transition.execute_mode_transition(
        _FLOWER, _MAIN, 1, None, "api", expected_config_revision="0000007"
    )

    # Then: the committed activation is reported with reduced runtime readiness.
    assert result["success"] is True
    assert result["runtime_ready"] is False
    assert result["warning"] == ACTIVATION_RUNTIME_REFRESH_FAILED
    assert result["config_revision"] == "0000008"
    assert result["new_mode"]["mode_name"] == "veg"
    assert state.commits == 1
    assert state.rollbacks == 0

    committed = state.committed
    assert committed.active_modes[(_FLOWER, _MAIN)] == {"mode_id": 1, "submode_id": None}
    assert len(committed.history) == 1
    assert [row["version_id"] for row in committed.config_versions] == [7, 8]
    assert committed.schedules[105]["name"] == "Heater - Day"

    assert harness.registry.snapshot is harness.startup_snapshot
    scheduler_ramps = harness.scheduler._light_ramp_state
    assert all(key[0] != _FLOWER for key in scheduler_ramps)
    assert ("Veg Room", _MAIN, "light1", None) in scheduler_ramps


@pytest.mark.asyncio
async def test_snapshot_consumer_failure_reports_committed_activation() -> None:
    # Given: a subscribed consumer accepts the startup snapshot but fails on the mutation install.
    harness = _Harness()

    @final
    class _FailingConsumer:
        def __init__(self) -> None:
            self.versions: list[int] = []

        def __call__(self, snapshot: RuntimeDeviceSnapshot) -> None:
            self.versions.append(snapshot.version)
            if snapshot.version >= 2:
                raise RuntimeError("consumer crashed on install")

    consumer = _FailingConsumer()
    harness.registry.subscribe(consumer)
    await harness.start()

    # When: the activation commits and the consumer rejects the pending install.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 1, None, "api")

    # Then: the committed result is reported with the explicit refresh warning.
    assert result["success"] is True
    assert result["runtime_ready"] is False
    assert result["warning"] == ACTIVATION_RUNTIME_REFRESH_FAILED
    assert result["config_revision"] == "0000008"
    assert consumer.versions == [harness.startup_snapshot.version, 2]
    assert harness.registry.snapshot is harness.startup_snapshot
    assert harness.state.commits == 1
    committed = harness.state.committed
    assert committed.active_modes[(_FLOWER, _MAIN)] == {"mode_id": 1, "submode_id": None}
    assert len(committed.history) == 1


@pytest.mark.asyncio
async def test_scheduler_refresh_failure_after_commit_warns_but_stays_committed() -> None:
    # Given: the device configuration read fails once the transaction has committed.
    harness = await _build_harness()
    stale = await harness.schedule_service.merged_scheduler_schedules()
    harness.scheduler.update_schedules(stale)
    harness.state.fail_post_commit_config = True

    # When: the activation commits and the post-commit scheduler refresh fails.
    result = await harness.transition.execute_mode_transition(_FLOWER, _MAIN, 1, None, "api")

    # Then: the activation is committed with the runtime refresh warning.
    assert result["success"] is True
    assert result["runtime_ready"] is False
    assert result["warning"] == ACTIVATION_RUNTIME_REFRESH_FAILED
    assert result["config_revision"] == "0000008"
    assert harness.state.commits == 1

    committed = harness.state.committed
    assert committed.active_modes[(_FLOWER, _MAIN)] == {"mode_id": 1, "submode_id": None}
    assert [row["version_id"] for row in committed.config_versions] == [7, 8]

    snapshot = harness.registry.snapshot
    assert snapshot is not harness.startup_snapshot
    assert snapshot.version == harness.startup_snapshot.version + 1

    # Ramp state clears before the failing refresh; schedules stay stale.
    assert all(key[0] != _FLOWER for key in harness.scheduler._light_ramp_state)
    assert harness.scheduler.schedules == stale
