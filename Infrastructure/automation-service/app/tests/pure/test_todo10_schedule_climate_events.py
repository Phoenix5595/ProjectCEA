from __future__ import annotations

from datetime import time as dt_time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes import websocket
from app.routes.climate_periods import delete_climate_periods
from app.routes.schedules import base, room
from app.schemas.schedules import RoomScheduleCreate, ScheduleUpdate
from app.services.room_schedule_service import (
    ActiveModeMissingError,
    ProfileNotConfiguredError,
    RoomScheduleService,
)


class _Sink:
    def __init__(self, committed: list[bool]) -> None:
        self._committed = committed
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        assert self._committed == [True]
        self.events.append(event)


class _ScheduleRepository:
    def __init__(self, before: dict[str, object], after: dict[str, object] | None) -> None:
        self.before = before
        self.after = after
        self.committed: list[bool] = []

    async def get_schedules(self, *_args: object) -> list[dict[str, object]]:
        return [self.after if self.committed and self.after is not None else self.before]

    async def update_schedule(self, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if self.after is None:
            return None
        self.committed.append(True)
        return self.after

    async def delete_schedule(self, _schedule_id: int) -> bool:
        if self.after is None:
            return False
        self.committed.append(True)
        return True


def _schedule(start_time: str = "06:00") -> dict[str, object]:
    return {
        "id": 7,
        "name": "Day",
        "location": "Veg Room",
        "cluster": "main",
        "start_time": start_time,
        "end_time": "18:00",
        "enabled": True,
        "mode": "DAY",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("after", "expected_events", "status"),
    [(_schedule("07:00"), 1, None), (_schedule(), 0, None), (None, 0, 500)],
)
async def test_schedule_update_is_post_commit_and_silent_for_noop_or_failure(
    after: dict[str, object] | None, expected_events: int, status: int | None
) -> None:
    # Given: a schedule repository with a safe route-local before image.
    repository = _ScheduleRepository(_schedule(), after)
    sink = _Sink(repository.committed)

    # When: a changed, identical, or failed update is requested.
    try:
        await base.update_schedule(
            7,
            ScheduleUpdate(start_time="07:00"),
            SimpleNamespace(schedule_repo=repository),
            None,
            None,
            MutationRequestContext.create(),
            sink,
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: only a committed field change creates one safe event.
    assert len(sink.events) == expected_events
    if expected_events:
        assert [change.key for change in sink.events[0].payload.changes] == ["start_time"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("after", "expected_events", "status"), [({}, 1, None), (None, 0, 404)])
async def test_schedule_delete_emits_only_after_a_successful_delete(
    after: dict[str, object] | None, expected_events: int, status: int | None
) -> None:
    # Given: an existing schedule and a delete result.
    repository = _ScheduleRepository(_schedule(), after)
    sink = _Sink(repository.committed)

    # When: deletion succeeds or the repository reports no persisted row.
    try:
        await base.delete_schedule(
            7, SimpleNamespace(schedule_repo=repository), MutationRequestContext.create(), sink
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: only the committed delete has one safe before-to-empty diff.
    assert len(sink.events) == expected_events


class _ClimateRepository:
    def __init__(self, previous: list[dict[str, object]], result: bool) -> None:
        self.previous = previous
        self.result = result
        self.committed: list[bool] = []

    async def get_periods(self, *_args: object) -> list[dict[str, object]]:
        return self.previous

    async def delete_periods(self, *_args: object) -> bool:
        if self.result:
            self.committed.append(True)
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "result", "expected_events"),
    [([{"id": 1}], True, 1), ([], True, 0), ([{"id": 1}], False, 0)],
)
async def test_climate_delete_is_silent_without_a_changed_committed_result(
    previous: list[dict[str, object]], result: bool, expected_events: int
) -> None:
    # Given: persisted climate rows or an empty/failing delete boundary.
    repository = _ClimateRepository(previous, result)
    sink = _Sink(repository.committed)

    # When: the route deletes the room's periods.
    await delete_climate_periods(
        "Veg Room",
        "main",
        SimpleNamespace(climate_periods_repo=repository),
        MutationRequestContext.create(),
        sink,
    )

    # Then: no-op and database-failure outcomes produce no fake event.
    assert len(sink.events) == expected_events


@pytest.mark.asyncio
async def test_room_schedule_sync_forwards_one_sync_to_the_service_and_one_event_to_the_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a service that commits one derived aggregate for the room.
    context = MutationRequestContext.create()
    sink = _Sink([True])
    broadcast = AsyncMock()
    monkeypatch.setattr(websocket, "broadcast_room_schedule_update", broadcast)

    class _Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def sync_one(self, location: str, cluster: str) -> dict[str, object]:
            self.calls.append((location, cluster))
            return {
                "schedules_created": 2,
                "devices_configured": 3,
                "config_version_id": 5,
                "prior_parameters": {"day_start_time": "07:00", "night_start_time": "20:00"},
                "schedule": RoomScheduleCreate(
                    day_start_time="06:00",
                    day_end_time="18:00",
                    night_start_time="18:00",
                    night_end_time="06:00",
                ),
                "warning": None,
            }

    service = _Service()

    # When: the sync boundary derives nothing itself and only emits.
    response = await room.sync_room_schedule_from_mode_parameters(
        "Veg Room", "main", service, context, sink
    )

    # Then: the service receives exactly one sync and the sink one committed event.
    assert response["success"] is True
    assert service.calls == [("Veg Room", "main")]
    assert len(sink.events) == 1
    assert broadcast.called is True


@pytest.mark.asyncio
async def test_room_schedule_sync_validation_failure_is_event_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a service that cannot resolve an active persisted mode.
    sink = _Sink([])
    broadcast = AsyncMock()
    monkeypatch.setattr(websocket, "broadcast_room_schedule_update", broadcast)

    class _Service:
        async def sync_one(self, *_args: object) -> dict[str, object]:
            raise ActiveModeMissingError("No active mode for Veg Room/main. Set mode first.")

    # When: the sync boundary cannot resolve its authoritative source.
    with pytest.raises(HTTPException) as error:
        await room.sync_room_schedule_from_mode_parameters(
            "Veg Room",
            "main",
            _Service(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: validation failure emits no persisted-mutation event.
    assert error.value.status_code == 404
    assert sink.events == []
    assert broadcast.called is False


class _FakePool:
    def __init__(self, active_row: dict[str, object] | None) -> None:
        self.active_row = active_row
        self.parameters_row: dict[str, object] | None = None
        self.executed: list[tuple[str, str]] = []

    def acquire(self) -> _FakeConnection:
        return _FakeConnection(self)


class _FakeConnection:
    """Minimal asyncpg-shaped connection over shared in-memory state."""

    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def transaction(self) -> _FakeConnection:
        return self

    async def execute(self, query: str, *args: object) -> str:
        self._pool.executed.append(("execute", query))
        return "UPDATE 0"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self._pool.executed.append(("fetchrow", query))
        if "FROM room_active_mode" in query:
            return self._pool.active_row
        if "FROM mode_parameters" in query:
            return self._pool.parameters_row
        raise AssertionError(f"Unexpected fetchrow: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        raise AssertionError(f"Unexpected fetch: {query}")


class _FakeScheduleRepo:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.deleted: list[tuple[tuple[int, ...], bool]] = []
        self.created: list[tuple[str, str, str, str]] = []
        self._next_id = 100

    async def get_schedules(
        self, location: str | None = None, cluster: str | None = None, conn: object = None
    ) -> list[dict[str, object]]:
        return [
            row
            for row in self.rows
            if row.get("location") == location and row.get("cluster") == cluster
        ]

    async def delete_schedules_bulk(
        self, schedule_ids: list[int], conn: object = None
    ) -> int:
        ids = tuple(schedule_ids)
        self.deleted.append((ids, conn is not None))
        self.rows = [row for row in self.rows if row.get("id") not in ids]
        return len(ids)

    async def create_schedule(
        self,
        name: str,
        location: str,
        cluster: str,
        device_name: str,
        start_time: str,
        end_time: str,
        day_of_week: object = None,
        enabled: bool = True,
        mode: str = "light",
        target_intensity: object = None,
        ramp_up_duration: object = None,
        ramp_down_duration: object = None,
        conn: object = None,
    ) -> int:
        self._next_id += 1
        self.rows.append(
            {
                "id": self._next_id,
                "location": location,
                "cluster": cluster,
                "device_name": device_name,
                "name": name,
                "mode": mode,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        self.created.append((device_name, mode, start_time, end_time))
        return self._next_id


class _FakeRoomModeRepo:
    def __init__(
        self,
        active: dict[str, object] | None = None,
        parameters: dict[str, object] | None = None,
    ) -> None:
        self._active = active
        self._parameters = parameters
        self.active_calls: list[bool] = []
        self.params_calls: list[bool] = []
        self.saved: list[dict[str, object]] = []
        self.identity_calls = 0

    async def get_active_mode(
        self, location: str, cluster: str, conn: object = None
    ) -> dict[str, object] | None:
        self.active_calls.append(conn is not None)
        return self._active

    async def get_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None = None,
        conn: object = None,
    ) -> dict[str, object] | None:
        self.params_calls.append(conn is not None)
        return self._parameters

    async def save_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, object],
        conn: object = None,
    ) -> bool:
        self.saved.append(dict(params))
        return True

    async def get_profile_identity_on_connection(
        self, conn: object, location: str, mode_id: int, submode_id: int | None
    ) -> dict[str, object]:
        self.identity_calls += 1
        return {
            "mode_id": mode_id,
            "submode_id": submode_id,
            "mode_name": "veg",
            "submode_name": None,
            "is_constant": False,
            "photoperiod_hours": 18,
        }


class _FakeConfigRepo:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def log_config_version(
        self, *args: object, conn: object = None, **kwargs: object
    ) -> int:
        self.calls.append(conn is not None)
        return 5


def _schedule_service(
    active_row: dict[str, object] | None,
    active: dict[str, object] | None,
    parameters: dict[str, object] | None,
    schedule_rows: list[dict[str, object]],
    hierarchy: dict[str, object],
) -> tuple[RoomScheduleService, Any, _FakeScheduleRepo, _FakeRoomModeRepo, _FakeConfigRepo]:
    schedule_repo = _FakeScheduleRepo(schedule_rows)
    room_mode_repo = _FakeRoomModeRepo(active, parameters)
    config_repo = _FakeConfigRepo()
    database = SimpleNamespace(
        schedule_repo=schedule_repo,
        room_mode_repo=room_mode_repo,
        config_repo=config_repo,
        _get_pool=AsyncMock(return_value=_FakePool(active_row)),
    )
    config = SimpleNamespace(get_devices=AsyncMock(return_value=hierarchy))
    return RoomScheduleService(database, config), database, schedule_repo, room_mode_repo, config_repo


_HEATER_DAY = {"id": 3, "location": "Flower Room", "cluster": "main", "device_name": "heater"}


@pytest.mark.asyncio
async def test_replace_on_connection_preserves_protected_rows_and_skips_light_creation() -> None:
    # Given: protected room_schedule/climate rows, an unprotected heater row and a light row.
    hierarchy = {
        "heater": {"device_type": "heater", "display_name": "Heater"},
        "main light": {"device_type": "light", "display_name": "Main Light"},
    }
    rows = [
        {"id": 1, "location": "Flower Room", "cluster": "main", "device_name": "room_schedule"},
        {"id": 2, "location": "Flower Room", "cluster": "main", "device_name": "climate"},
        dict(_HEATER_DAY),
        {"id": 4, "location": "Flower Room", "cluster": "main", "device_name": "main light"},
    ]
    schedule = RoomScheduleCreate(
        day_start_time="06:00",
        day_end_time="18:00",
        night_start_time="18:00",
        night_end_time="06:00",
    )

    # When: the replacement runs on the caller-owned transaction.
    service, _database, schedule_repo, _room_mode_repo, _config_repo = _schedule_service(
        None, None, None, rows, {"Flower Room": {"main": hierarchy}}
    )
    pool: Any = await _fake_pool_from(service)
    counts = await service.replace_on_connection(
        await pool.acquire().__aenter__(),  # type: ignore[arg-type]
        "Flower Room",
        "main",
        schedule,
    )

    # Then: protected rows survive, unprotected rows (including light rows) are
    # replaced on the supplied connection, and lights get no DAY/NIGHT rows.
    assert counts == {"schedules_created": 2, "devices_configured": 2}
    assert schedule_repo.deleted == [((3, 4), True)]
    assert schedule_repo.created == [("heater", "DAY", "06:00", "18:00"),
                                     ("heater", "NIGHT", "18:00", "06:00")]


@pytest.mark.asyncio
async def test_replace_on_connection_with_empty_hierarchy_creates_zero_rows() -> None:
    # Given: a room whose configured hierarchy is empty.
    rows = [dict(_HEATER_DAY)]

    # When: the replacement runs on the caller-owned transaction.
    service, _database, schedule_repo, _room_mode_repo, _config_repo = _schedule_service(
        None, None, None, rows, {"Flower Room": {"main": {}}}
    )
    pool = await _fake_pool_from(service)
    counts = await service.replace_on_connection(
        await pool.acquire().__aenter__(), "Flower Room", "main", _valid_schedule()
    )

    # Then: zero rows are created safely instead of failing the activation.
    assert counts == {"schedules_created": 0, "devices_configured": 0}
    assert schedule_repo.deleted == [((3,), True)]
    assert schedule_repo.created == []


def _valid_schedule() -> RoomScheduleCreate:
    return RoomScheduleCreate(
        day_start_time="06:00",
        day_end_time="18:00",
        night_start_time="18:00",
        night_end_time="06:00",
    )


async def _fake_pool_from(service: RoomScheduleService) -> Any:
    return await service._database._get_pool()  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_sync_on_connection_reuses_provided_exact_parameters() -> None:
    # Given: the activation owner already read the exact raw parameters row.
    service, _database, schedule_repo, room_mode_repo, _config_repo = _schedule_service(
        None,
        None,
        None,
        [],
        {"Flower Room": {"main": {"heater": {"device_type": "heater", "display_name": "Heater"}}}},
    )

    # When: sync is given the already-read parameters (raw time objects).
    pool: Any = await service._database._get_pool()  # type: ignore[no-any-return]
    counts = await service.sync_on_connection(
        await pool.acquire().__aenter__(),  # type: ignore[arg-type]
        "Flower Room",
        "main",
        1,
        None,
        parameters={
            "day_start_time": dt_time(6, 0),
            "night_start_time": dt_time(18, 0),
            "light_ramp_up_minutes": 15,
            "light_ramp_down_minutes": 15,
        },
    )

    # Then: no second parameter read or identity lookup happens.
    assert counts == {"schedules_created": 2, "devices_configured": 1}
    assert room_mode_repo.identity_calls == 0
    assert room_mode_repo.params_calls == []
    assert ("heater", "DAY", "06:00", "18:00") in schedule_repo.created


@pytest.mark.asyncio
async def test_sync_on_connection_default_read_raises_profile_not_configured() -> None:
    # Given: an exact profile whose parameter row is missing entirely.
    service, _database, schedule_repo, room_mode_repo, _config_repo = _schedule_service(
        None,
        None,
        None,
        [],
        {"Flower Room": {"main": {"heater": {"device_type": "heater", "display_name": "Heater"}}}},
    )
    pool = await service._database._get_pool()  # type: ignore[no-any-return]
    pool.active_row = None

    # When: the default path performs the exact numeric-ID read on the connection.
    with pytest.raises(ProfileNotConfiguredError) as error:
        await service.sync_on_connection(
            await pool.acquire().__aenter__(),  # type: ignore[arg-type]
            "Flower Room",
            "main",
            1,
            None,
        )

    # Then: the typed profile_not_configured failure precedes any persistence.
    assert error.value.code == "profile_not_configured"
    assert room_mode_repo.identity_calls == 1
    assert schedule_repo.deleted == []
    assert schedule_repo.created == []


@pytest.mark.asyncio
async def test_save_locks_timeline_then_active_row_before_reads_and_writes_one_revision() -> None:
    # Given: an active mode and prior parameters for the room.
    active = {"location": "Veg Room", "cluster": "main", "mode_name": "veg", "submode_name": None}
    parameters = {
        "day_start_time": "07:00",
        "night_start_time": "20:00",
        "main_light_intensity": 100,
    }
    service, database, _schedule_repo, room_mode_repo, config_repo = _schedule_service(
        {"mode_id": 1, "submode_id": None}, active, parameters, [], {"Veg Room": {"main": {
            "heater": {"device_type": "heater", "display_name": "Heater"}}}}
    )

    # When: the ordinary save commits.
    result = await service.save("Veg Room", "main", _valid_schedule())

    # Then: the timeline advisory lock precedes the active row lock and every
    # read/write joins the transaction, with exactly one room_schedule revision.
    pool = await _fake_pool_from(service)
    assert "pg_advisory_xact_lock" in pool.executed[0][1]
    assert "FOR UPDATE" in pool.executed[1][1]
    assert room_mode_repo.active_calls == [True]
    assert room_mode_repo.params_calls == [True]
    assert config_repo.calls == [True]
    assert result["config_version_id"] == 5
    assert result["prior_parameters"] is parameters
    # Non-timeline values survive the merge.
    assert room_mode_repo.saved[0]["main_light_intensity"] == 100
    assert room_mode_repo.saved[0]["day_start_time"] == "06:00"


@pytest.mark.asyncio
async def test_save_postcommit_failures_return_a_truthful_warning_not_a_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: cache clearing and event publishing both fail after commit.
    sequence: list[str] = []
    state = SimpleNamespace(
        delete=AsyncMock(
            side_effect=lambda key: (
                sequence.append(f"delete:{key}") or (_ for _ in ()).throw(RuntimeError("state down"))
            )
        )
    )
    bus = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("bus down")))
    monkeypatch.setattr("app.state.get_state_manager", lambda: state)
    monkeypatch.setattr("app.events.get_event_bus", lambda: bus)
    active = {"location": "Veg Room", "cluster": "main", "mode_name": "veg", "submode_name": None}
    service, _database, _schedule_repo, _room_mode_repo, _config_repo = _schedule_service(
        {"mode_id": 1, "submode_id": None}, active, {"day_start_time": "07:00",
                                                    "night_start_time": "20:00"}, [],
        {"Veg Room": {"main": {"heater": {"device_type": "heater", "display_name": "Heater"}}}}
    )

    # When: the save commits and then the post-commit notifications fail.
    result = await service.save("Veg Room", "main", _valid_schedule())

    # Then: the committed result is returned with a truthful warning and caches
    # were cleared before the event was attempted.
    assert result["config_version_id"] == 5
    assert result["warning"] == (
        "schedule_cache_clear_failed; room_schedule_notification_failed"
    )
    assert sequence[:3] == [
        "delete:schedules:loc:Veg Room:cluster:main",
        "delete:schedules:loc:Veg Room:cluster:main:climate",
        "delete:schedules:all",
    ]


@pytest.mark.asyncio
async def test_sync_one_derives_bounds_from_active_parameters_in_one_transaction() -> None:
    # Given: an active veg profile with stored clocks and ramps.
    active = {"location": "Veg Room", "cluster": "main", "mode_name": "veg", "submode_name": None}
    parameters = {
        "day_start_time": "06:00",
        "night_start_time": "18:00",
        "light_ramp_up_minutes": 15,
        "light_ramp_down_minutes": 15,
        "main_light_intensity": 100,
    }
    service, _database, schedule_repo, room_mode_repo, _config_repo = _schedule_service(
        {"mode_id": 1, "submode_id": None},
        active,
        parameters,
        [{"id": 3, "location": "Veg Room", "cluster": "main", "device_name": "heater"}],
        {"Veg Room": {"main": {"heater": {"device_type": "heater", "display_name": "Heater"}}}},
    )

    # When: the internal sync entry point runs.
    result = await service.sync_one("Veg Room", "main")

    # Then: the derived bounds replace the schedules and one revision is logged.
    assert result["schedules_created"] == 2
    assert result["devices_configured"] == 1
    applied = result["schedule"]
    assert (applied.day_start_time, applied.day_end_time) == ("06:00", "18:00")  # type: ignore[union-attr]
    assert schedule_repo.deleted == [((3,), True)]
    assert room_mode_repo.saved[0]["light_ramp_up_minutes"] == 15


@pytest.mark.asyncio
async def test_sync_all_reports_one_outcome_per_room_without_http_events() -> None:
    # Given: two rooms, one of which has no active mode.
    hierarchy: dict[str, object] = {
        "Veg Room": {"main": {"heater": {"device_type": "heater"}}},
        "Flower Room": {"main": {"heater": {"device_type": "heater"}}},
    }
    service, _database, _schedule_repo, _room_mode_repo, _config_repo = _schedule_service(
        None, None, None, [], hierarchy
    )
    synced: list[str] = []

    async def _sync_one(location: str, cluster: str) -> dict[str, Any]:
        synced.append(f"{location}/{cluster}")
        if location == "Flower Room":
            raise ActiveModeMissingError("No active mode for Flower Room/main. Set mode first.")
        return {"schedules_created": 2, "devices_configured": 1, "schedule": _valid_schedule()}

    service.sync_one = _sync_one  # type: ignore[method-assign]

    # When: sync-all iterates the configured hierarchy.
    outcomes = await service.sync_all()

    # Then: each room keeps its own outcome and no HTTP event was emitted.
    assert synced == ["Veg Room/main", "Flower Room/main"]
    assert outcomes[0]["success"] is True
    assert outcomes[0]["schedules_created"] == 2
    assert outcomes[1]["success"] is False
    assert outcomes[1]["error"] == "No active mode for Flower Room/main. Set mode first."
