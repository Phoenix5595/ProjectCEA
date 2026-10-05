from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.mutation_context import MutationRequestContext
from app.repositories.room_modes import RoomModeRepository
from app.routes import room_modes
from app.services.mode_transition_service import ModeTransitionService


class _BlockedPool:
    def acquire(self):
        raise AssertionError("Rejected room mode must not access persistence")


class _Sink:
    def emit_nowait(self, event):
        raise AssertionError("Rejected or unchanged mode must not emit a mutation")


def _app(database):
    app = FastAPI()
    app.include_router(room_modes.router)
    app.dependency_overrides[room_modes.get_database] = lambda: database
    app.dependency_overrides[room_modes.get_config] = lambda: SimpleNamespace()
    app.dependency_overrides[room_modes.get_relay_manager] = lambda: SimpleNamespace()
    app.dependency_overrides[room_modes.get_dfr0971_manager] = lambda: None
    app.dependency_overrides[room_modes.get_mutation_request_context] = (
        lambda: MutationRequestContext.create()
    )
    app.dependency_overrides[room_modes.get_mutation_event_sink] = lambda: _Sink()
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"mode_name": "flower"},
        {"mode_name": "drying"},
        {"mode_name": "sleep"},
        {"mode_name": "veg", "submode_name": "bulk"},
    ],
)
async def test_veg_room_api_rejects_non_veg_modes_before_persistence(payload):
    pool: Any = _BlockedPool()
    database = SimpleNamespace(room_mode_repo=RoomModeRepository(pool), pool=pool)
    app = _app(database)
    app.dependency_overrides[room_modes.get_mode_transition_service] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/room-modes/room/Veg%20Room/main/mode", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("setter", ["set_active_mode", "set_mode_on_connection"])
async def test_veg_room_persistence_rejects_disallowed_mode_even_without_http(setter):
    pool: Any = _Pool()
    repository = RoomModeRepository(pool)
    with pytest.raises(ValueError):
        if setter == "set_active_mode":
            await repository.set_active_mode("Veg Room", "main", "sleep")
        else:
            await repository.set_mode_on_connection(_Connection(pool), "Veg Room", "main", 4, None)
    assert pool.writes == []


class _Connection:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def transaction(self):
        return self

    async def fetchrow(self, query, *args):
        if "FROM room_active_mode" in query:
            return dict(self.pool.active)
        if "FROM room_modes" in query:
            key = args[0]
            return next(
                (
                    dict(mode)
                    for mode in self.pool.modes
                    if mode["id"] == key or mode["name"] == key
                ),
                None,
            )
        if "FROM mode_parameters" in query:
            return {
                "day_start_time": "17:00", "night_start_time": "11:00",
                "light_ramp_up_minutes": 15, "light_ramp_down_minutes": 15,
            }
        raise AssertionError(f"Unexpected read: {query}")

    async def fetch(self, query, *args):
        if "FROM room_modes" in query:
            return [dict(mode) for mode in self.pool.modes]
        if "FROM room_active_mode" in query:
            return []
        raise AssertionError(f"Unexpected read: {query}")

    async def execute(self, query, *args):
        if "pg_advisory_xact_lock" in query:
            return "SELECT 1"
        self.pool.writes.append(query)
        if "INSERT INTO room_active_mode" in query:
            location, cluster, mode_id, submode_id = args
            mode = next(mode for mode in self.pool.modes if mode["id"] == mode_id)
            self.pool.active = {
                "location": location,
                "cluster": cluster,
                "mode_id": mode_id,
                "mode_name": mode["name"],
                "submode_id": submode_id,
                "submode_name": None,
            }
        elif "INSERT INTO mode_transition_history" not in query:
            raise AssertionError(f"Unexpected mutation: {query}")
        return "INSERT 1"


class _Pool:
    def __init__(self, mode="veg", location="Veg Room"):
        self.modes = [
            {"id": 1, "name": "veg", "is_constant": False, "photoperiod_hours": 18},
            {"id": 2, "name": "flower", "is_constant": False, "photoperiod_hours": 12},
            {"id": 3, "name": "drying", "is_constant": True, "photoperiod_hours": 0},
            {"id": 4, "name": "sleep", "is_constant": True, "photoperiod_hours": 0},
        ]
        mode_id = next(item["id"] for item in self.modes if item["name"] == mode)
        self.active = {
            "location": location,
            "cluster": "main",
            "mode_id": mode_id,
            "mode_name": mode,
            "submode_id": None,
            "submode_name": None,
        }
        self.writes = []

    def acquire(self):
        return _Connection(self)


class _Registry:
    """In-memory mutate boundary invoking the mutation inside the pool transaction."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self.snapshot = SimpleNamespace(version=9)

    async def mutate(self, mutation):
        async with self._pool.acquire() as connection, connection.transaction():
            return await mutation(connection)


class _ScheduleService:
    """Behavioral fake for the two activation seam methods."""

    def __init__(self) -> None:
        self.synced: list[tuple[int, Any]] = []

    async def sync_on_connection(
        self, _conn: Any, _location: str, _cluster: str, mode_id: int, submode_id: Any,
        *, parameters: Any = None,
    ) -> dict[str, int]:
        self.synced.append((mode_id, submode_id))
        return {"schedules_created": 2, "devices_configured": 3}

    async def merged_scheduler_schedules(self) -> list[dict[str, Any]]:
        return []


class _RecordingSink:
    def emit_nowait(self, event):
        pass


@pytest.mark.asyncio
async def test_veg_room_can_return_from_legacy_invalid_mode_to_veg(monkeypatch):
    pool: Any = _Pool(mode="sleep")
    database: Any = SimpleNamespace(
        pool=pool,
        room_mode_repo=RoomModeRepository(pool),
        config_repo=SimpleNamespace(log_config_version=AsyncMock(return_value=5)),
    )
    transition = ModeTransitionService(database, _ScheduleService(), _Registry(pool), None)
    monkeypatch.setattr(room_modes, "broadcast_mode_update", AsyncMock())
    app = _app(database)
    app.dependency_overrides[room_modes.get_mode_transition_service] = lambda: transition
    app.dependency_overrides[room_modes.get_mutation_event_sink] = lambda: _RecordingSink()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/room-modes/room/Veg%20Room/main/mode", json={"mode_name": "veg"}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode_name"] == "veg"
    assert body["config_revision"] == "0000005"
    assert body["runtime_ready"] is True
    assert pool.active["mode_name"] == "veg"
    assert pool.active["submode_id"] is None


@pytest.mark.asyncio
async def test_transition_service_cannot_activate_sleep_for_veg_room():
    pool: Any = _Pool()
    database: Any = SimpleNamespace(pool=pool, config_repo=SimpleNamespace())
    transition = ModeTransitionService(database, _ScheduleService(), _Registry(pool), None)
    result = await transition.execute_mode_transition("Veg Room", "main", 4, None, "system")
    assert result["success"] is False
    assert result["error_code"] == "invalid_profile_identity"
    assert pool.active["mode_name"] == "veg"
    assert pool.writes == []


@pytest.mark.asyncio
async def test_flower_room_keeps_its_other_modes():
    pool: Any = _Pool(location="Flower Room")
    repository = RoomModeRepository(pool)
    assert await repository.set_active_mode("Flower Room", "main", "drying") is True
    assert pool.active["mode_name"] == "drying"
