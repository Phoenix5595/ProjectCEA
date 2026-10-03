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
from app.routes.schedules import room as room_schedule
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
    async with AsyncClient(
        transport=ASGITransport(app=_app(database)), base_url="http://test"
    ) as client:
        response = await client.post("/api/room-modes/room/Veg%20Room/main/mode", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("setter", ["set_active_mode", "set_mode_with_transaction"])
async def test_veg_room_persistence_rejects_disallowed_mode_even_without_http(setter):
    pool: Any = _BlockedPool()
    repository = RoomModeRepository(pool)
    with pytest.raises(ValueError):
        await getattr(repository, setter)("Veg Room", "main", "sleep")


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
            return None
        raise AssertionError(f"Unexpected read: {query}")

    async def fetch(self, query, *args):
        if "FROM room_modes" in query:
            return [dict(mode) for mode in self.pool.modes]
        if "FROM room_active_mode" in query:
            return []
        raise AssertionError(f"Unexpected read: {query}")

    async def execute(self, query, *args):
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
            {"id": 1, "name": "veg", "is_constant": False},
            {"id": 2, "name": "flower", "is_constant": False},
            {"id": 3, "name": "drying", "is_constant": True},
            {"id": 4, "name": "sleep", "is_constant": True},
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


class _OfflineTransition(ModeTransitionService):
    async def _trigger_scheduler_refresh(self, location, cluster):
        return None

    def _clear_light_ramp_state(self, location, cluster):
        return None


class _RecordingSink:
    def emit_nowait(self, event):
        pass


@pytest.mark.asyncio
async def test_veg_room_can_return_from_legacy_invalid_mode_to_veg(monkeypatch):
    pool: Any = _Pool(mode="sleep")
    database: Any = SimpleNamespace(pool=pool, room_mode_repo=RoomModeRepository(pool))
    monkeypatch.setattr(room_modes, "ModeTransitionService", _OfflineTransition)
    monkeypatch.setattr(room_modes, "broadcast_mode_update", AsyncMock())
    monkeypatch.setattr(
        room_schedule, "sync_room_schedule_from_mode_parameters", AsyncMock(return_value={})
    )
    app = _app(database)
    app.dependency_overrides[room_modes.get_mutation_event_sink] = lambda: _RecordingSink()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/room-modes/room/Veg%20Room/main/mode", json={"mode_name": "veg"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["mode_name"] == "veg"
    assert pool.active["mode_name"] == "veg"
    assert pool.active["submode_id"] is None


@pytest.mark.asyncio
async def test_transition_service_cannot_activate_sleep_for_veg_room():
    pool: Any = _Pool()
    database: Any = SimpleNamespace(pool=pool)
    result = await _OfflineTransition(database).execute_mode_transition(
        "Veg Room", "main", 4, None, "system"
    )
    assert result["success"] is False
    assert pool.active["mode_name"] == "veg"
    assert pool.writes == []


@pytest.mark.asyncio
async def test_flower_room_keeps_its_other_modes():
    pool: Any = _Pool(location="Flower Room")
    repository = RoomModeRepository(pool)
    assert await repository.set_active_mode("Flower Room", "main", "drying") is True
    assert pool.active["mode_name"] == "drying"
