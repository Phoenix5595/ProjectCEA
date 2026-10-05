from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.repositories.light_target_intensity import LightTargetIntensityRepository
from app.routes.lights import light_target
from app.schemas.lights import LightIntensityUpdate, TargetIntensityControl


class _Connection:
    def __init__(self, active: dict[str, Any] | None) -> None:
        self.active = active
        self.targets = {(42, 2): 40.0}
        self.pending: dict[tuple[int, int], float] = {}
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, int, float]] = []
        self.row_lock = asyncio.Lock()
        self.target_locked = asyncio.Event()
        self.transition_waiting: asyncio.Event | None = None
        self.in_transaction = False
        self.fail_write = False
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield self
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.targets.update(self.pending)
            self.commits += 1
        finally:
            self.pending.clear()
            self.in_transaction = False
            if self.row_lock.locked():
                self.row_lock.release()

    async def fetchrow(self, query: str, *args: Any):
        if "room_active_mode" in query:
            assert "FOR UPDATE OF arm" in query
            assert self.in_transaction
            assert args == ("Flower Room", "main")
            await self.row_lock.acquire()
            self.target_locked.set()
            return self.active
        assert "light_target_intensity" in query
        assert self.in_transaction and self.row_lock.locked()
        device_id, mode_id = args
        self.reads.append((device_id, mode_id))
        value = self.targets.get((device_id, mode_id))
        return {"target_intensity": value} if value is not None else None

    async def execute(self, query: str, *args: Any):
        assert "INSERT INTO light_target_intensity" in query
        assert self.in_transaction and self.row_lock.locked()
        if self.transition_waiting is not None:
            await self.transition_waiting.wait()
        if self.fail_write:
            raise RuntimeError("target persistence failed")
        device_id, mode_id, value = args
        self.writes.append((device_id, mode_id, value))
        self.pending[(device_id, mode_id)] = value
        return "INSERT 0 1"


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn
        self.acquisitions = 0
        self.acquired = False

    @asynccontextmanager
    async def acquire(self):
        assert not self.acquired, "target repository acquired a nested connection"
        self.acquired = True
        self.acquisitions += 1
        try:
            yield self.conn
        finally:
            self.acquired = False


class _Sink:
    def __init__(self, conn: _Connection, pool: _Pool) -> None:
        self.conn = conn
        self.pool = pool
        self.events: list[Any] = []

    def emit_nowait(self, event: Any) -> None:
        assert not self.conn.in_transaction and not self.pool.acquired
        assert self.conn.commits == 1
        self.events.append(event)


def _environment(active: dict[str, Any] | None):
    conn = _Connection(active)
    pool = _Pool(conn)
    legacy_reads: list[str] = []

    async def get_device_id(*_args):
        return 42

    async def get_device_type_by_id(*_args):
        return "light"

    async def get_light_by_id(*_args):
        return SimpleNamespace(location="Flower Room", cluster="main", device_name="light_1")

    async def get_active_mode(*_args):
        legacy_reads.append("active")
        return active

    async def get_mode_by_name(name):
        legacy_reads.append(name)
        return {"id": 1 if name == "veg" else 2}

    async def get_devices():
        return {"Flower Room": {"main": {"light_1": {"device_type": "light"}}}}

    database = SimpleNamespace(
        pool=pool,
        device_repo=SimpleNamespace(
            get_device_id=get_device_id,
            get_device_type_by_id=get_device_type_by_id,
            get_light_by_id=get_light_by_id,
        ),
        room_mode_repo=SimpleNamespace(
            get_active_mode=get_active_mode, get_mode_by_name=get_mode_by_name
        ),
        light_target_intensity_repo=LightTargetIntensityRepository(pool),
    )
    return conn, pool, database, SimpleNamespace(get_devices=get_devices), _Sink(conn, pool), legacy_reads


async def _request(endpoint, database, config, sink, expected_mode_id=2):
    context = MutationRequestContext.create()
    if endpoint == "room":
        return await light_target.set_target_intensity(
            "Flower Room", "main", "light_1",
            TargetIntensityControl(target_intensity=55.0, expected_mode_id=expected_mode_id),
            config, database, None, context, sink,
        )
    return await light_target.update_light_intensity(
        42, LightIntensityUpdate(target_intensity=55.0, expected_mode_id=expected_mode_id),
        database, None, context, sink,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["room", "device"])
@pytest.mark.parametrize("active", [None, {"mode_id": 3, "mode_name": "drying"}])
async def test_stale_or_missing_active_mode_never_reads_or_writes_any_target(
    monkeypatch, endpoint, active
):
    conn, pool, database, config, sink, legacy_reads = _environment(active)

    async def forbidden_publication(*_args):
        pytest.fail("a rejected target must not publish or refresh runtime")

    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", forbidden_publication)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", forbidden_publication)
    with pytest.raises(HTTPException) as error:
        await _request(endpoint, database, config, sink)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "light_target_mode_changed"
    assert conn.reads == [] and conn.writes == []
    assert conn.targets == {(42, 2): 40.0}
    assert legacy_reads == [] and sink.events == []
    assert conn.rollbacks == 1 and conn.commits == 0
    assert pool.acquisitions == 1 and not conn.row_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["room", "device"])
@pytest.mark.parametrize("submode_id", [None, 10, 20])
async def test_guard_accepts_same_mode_submode_and_commits_before_publication(
    monkeypatch, endpoint, submode_id
):
    active = {"mode_id": 2, "mode_name": "flower", "submode_id": submode_id}
    conn, pool, database, config, sink, legacy_reads = _environment(active)
    publications: list[str] = []

    async def refresh(*_args):
        assert not conn.in_transaction and not pool.acquired
        assert not conn.row_lock.locked()
        assert conn.targets[(42, 2)] == 55.0
        publications.append("runtime")

    async def publish(*_args):
        assert not conn.in_transaction and not pool.acquired
        publications.append("event")

    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", refresh)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", publish)
    result = await _request(endpoint, database, config, sink)
    assert result["success"] is True and result["mode_name"] == "flower"
    assert conn.reads == [(42, 2)] and conn.writes == [(42, 2, 55.0)]
    assert pool.acquisitions == 1 and legacy_reads == []
    assert conn.commits == 1 and conn.rollbacks == 0
    assert publications == ["runtime", "event"]
    assert len(sink.events) == 1
    assert sink.events[0].entity.entity_id == "42:2"
    assert sink.events[0].payload.changes[0].before == 40.0
    assert sink.events[0].payload.changes[0].after == 55.0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["room", "device"])
async def test_target_row_lock_is_released_before_waiting_for_registry_lock(monkeypatch, endpoint):
    conn, pool, database, config, sink, _ = _environment({"mode_id": 2, "mode_name": "flower"})
    registry_lock = asyncio.Lock()
    registry_owned = asyncio.Event()
    conn.transition_waiting = asyncio.Event()

    async def activate():
        async with registry_lock:
            registry_owned.set()
            await conn.target_locked.wait()
            conn.transition_waiting.set()
            async with conn.row_lock:
                assert not conn.in_transaction

    async def refresh(*_args):
        assert not conn.in_transaction and not pool.acquired
        async with registry_lock:
            pass

    async def publish(*_args):
        return None

    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", refresh)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", publish)
    activation = asyncio.create_task(activate())
    await registry_owned.wait()
    result, _ = await asyncio.wait_for(
        asyncio.gather(_request(endpoint, database, config, sink), activation), timeout=1
    )
    assert result["success"] is True and conn.commits == 1
    assert not conn.row_lock.locked() and not registry_lock.locked()


@pytest.mark.asyncio
async def test_supplied_connection_failure_rolls_back_and_never_emits(monkeypatch):
    conn, pool, database, config, sink, _ = _environment({"mode_id": 2, "mode_name": "flower"})
    conn.fail_write = True

    async def forbidden_publication(*_args):
        pytest.fail("a rolled-back target must not publish")

    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", forbidden_publication)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", forbidden_publication)
    with pytest.raises(RuntimeError, match="target persistence failed"):
        await _request("room", database, config, sink)
    assert conn.targets == {(42, 2): 40.0}
    assert conn.rollbacks == 1 and conn.commits == 0
    assert pool.acquisitions == 1 and sink.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [None, {"mode_id": 2, "mode_name": "flower"}])
async def test_unguarded_callers_keep_live_active_and_veg_fallback_semantics(monkeypatch, active):
    _, _, database, config, _, legacy_reads = _environment(active)
    reads: list[tuple[int, int]] = []
    writes: list[tuple[int, int, float]] = []

    async def get_intensity(device_id, mode_id):
        reads.append((device_id, mode_id))
        return 40.0

    async def set_intensity(device_id, mode_id, target):
        writes.append((device_id, mode_id, target))
        return True

    async def publish(*_args):
        return None

    database.light_target_intensity_repo = SimpleNamespace(
        get_intensity=get_intensity, set_intensity=set_intensity
    )
    sink = SimpleNamespace(emit_nowait=lambda _event: None)
    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", publish)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", publish)
    result = await _request("room", database, config, sink, expected_mode_id=None)
    mode_id, mode_name = (1, "veg") if active is None else (2, "flower")
    assert result["mode_name"] == mode_name
    assert reads == [(42, mode_id)] and writes == [(42, mode_id, 55.0)]
    assert legacy_reads == ["active", mode_name]
