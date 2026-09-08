from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes import calendar, room_modes
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate, SyncConnectionCreate
from app.schemas.room_modes import SetModeRequest, UpdateParametersRequest


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _ModeTransitionService:
    def __init__(self, _database: object, result: dict[str, object]) -> None:
        self._result = result

    async def execute_mode_transition(self, **_kwargs: object) -> dict[str, object]:
        return self._result


class _CalendarRepository:
    def __init__(
        self,
        event: dict[str, object] | None,
        sync_connection: dict[str, object] | None = None,
        sync_deleted: bool = False,
    ) -> None:
        self._event = event
        self._sync_connection = sync_connection
        self._sync_deleted = sync_deleted

    async def create_event(self, _data: dict[str, object]) -> dict[str, object]:
        return self._event or {}

    async def get_event(self, _event_id: int) -> dict[str, object] | None:
        return self._event

    async def update_event(
        self, _event_id: int, _data: dict[str, object]
    ) -> dict[str, object] | None:
        return self._event

    async def soft_delete_event(self, _event_id: int) -> bool:
        return self._event is not None

    async def get_sync_connection(self) -> dict[str, object] | None:
        return self._sync_connection

    async def upsert_sync_connection(self, _data: dict[str, object]) -> dict[str, object]:
        return {
            "id": 7,
            "display_name": "Operations calendar",
            "target_calendar_url": "https://calendar.example.test/team",
        }

    async def delete_sync_connection(self) -> bool:
        return self._sync_deleted

    async def delete_grow_plan(self, _grow_plan_id: UUID) -> int:
        return self._grow_plan_deleted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transition_result", "expected_events"),
    [
        (
            {
                "success": True,
                "old_mode": {"mode_name": "veg", "submode_name": None},
                "new_mode": {"mode_name": "flower", "submode_name": "bulk"},
            },
            1,
        ),
        (
            {
                "success": True,
                "old_mode": {"mode_name": "veg", "submode_name": None},
                "new_mode": {"mode_name": "veg", "submode_name": None},
            },
            0,
        ),
    ],
)
async def test_room_mode_emits_only_for_a_changed_persisted_transition(
    monkeypatch: pytest.MonkeyPatch,
    transition_result: dict[str, object],
    expected_events: int,
) -> None:
    # Given: a transition service with an authoritative old and new mode result.
    sink = _RecordingSink()
    context = MutationRequestContext(UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"))
    database = SimpleNamespace(
        room_mode_repo=SimpleNamespace(
            get_mode_by_name=_mode_by_name,
            get_flower_submodes=_flower_submodes,
        ),
    )
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(
        room_modes,
        "ModeTransitionService",
        lambda database: _ModeTransitionService(database, transition_result),
    )
    monkeypatch.setattr(room_modes, "get_room_mode_with_params", _room_mode_response)

    # When: the mode route completes the transition.
    await room_modes.set_room_mode(
        "Veg Room",
        "main",
        SetModeRequest(mode_name="flower", submode_name="bulk"),
        database,
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        context,
        sink,
    )

    # Then: only a real persisted mode change reaches the operational sink.
    assert len(sink.events) == expected_events
    if expected_events:
        assert sink.events[0].correlation_id == context.correlation_id
        assert sink.events[0].payload.changes[0].key == "mode_name"
        assert sink.events[0].payload.changes[0].before == "veg"
        assert sink.events[0].payload.changes[0].after == "flower"


@pytest.mark.asyncio
async def test_room_mode_emits_nothing_when_transition_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a transition service which reports a failed persistence result.
    sink = _RecordingSink()
    database = SimpleNamespace(
        room_mode_repo=SimpleNamespace(
            get_mode_by_name=_mode_by_name,
            get_flower_submodes=_flower_submodes,
        )
    )
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(
        room_modes,
        "ModeTransitionService",
        lambda database: _ModeTransitionService(database, {"success": False, "message": "failed"}),
    )

    # When: the route translates the failure response.
    with pytest.raises(HTTPException):
        await room_modes.set_room_mode(
            "Veg Room",
            "main",
            SetModeRequest(mode_name="flower"),
            database,
            SimpleNamespace(),
            SimpleNamespace(),
            None,
            MutationRequestContext.create(),
            sink,
        )

    # Then: no success event is emitted.
    assert sink.events == []


@pytest.mark.asyncio
async def test_calendar_event_emits_only_after_a_changed_persisted_update() -> None:
    # Given: an existing calendar event with the same requested title.
    sink = _RecordingSink()
    stored_event = _calendar_event(title="Water plants")
    database = SimpleNamespace(calendar_repo=_CalendarRepository(stored_event))

    # When: the route accepts an idempotent update.
    response = await calendar.update_event(
        3,
        CalendarEventUpdate(title="Water plants"),
        database,
        MutationRequestContext.create(),
        sink,
    )

    # Then: the existing event response is preserved without a false mutation.
    assert response["title"] == "Water plants"
    assert sink.events == []


@pytest.mark.asyncio
async def test_calendar_event_emits_safe_details_after_persistence() -> None:
    # Given: a newly persisted calendar event containing a private note sentinel.
    sink = _RecordingSink()
    stored_event = _calendar_event(title="Water plants")
    stored_event["notes"] = "private-calendar-note"
    database = SimpleNamespace(calendar_repo=_CalendarRepository(stored_event))

    # When: event creation returns the committed row.
    await calendar.create_event(
        CalendarEventCreate(
            location="Veg Room",
            title="Water plants",
            start_date=date(2026, 9, 2),
            notes="private-calendar-note",
        ),
        database,
        MutationRequestContext.create(),
        sink,
    )

    # Then: the event exposes safe calendar identity only, never note content.
    serialized = sink.events[0].model_dump_json()
    assert sink.events[0].event_type == "mutation.created"
    assert "private-calendar-note" not in serialized


@pytest.mark.asyncio
async def test_calendar_event_route_emits_after_a_successful_http_persistence() -> None:
    # Given: an isolated calendar route with an injected committed-event repository.
    sink = _RecordingSink()
    app = FastAPI()
    app.include_router(calendar.router)
    app.dependency_overrides[calendar.get_database] = lambda: SimpleNamespace(
        calendar_repo=_CalendarRepository(_calendar_event(title="Water plants"))
    )
    app.dependency_overrides[calendar.get_mutation_event_sink] = lambda: sink

    # When: a client persists an event through the API surface.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/calendar/events",
            headers={"X-Request-ID": "7552d5f1-0a9a-43e8-a63b-26a60d126c2e"},
            json={
                "location": "Veg Room",
                "title": "Water plants",
                "start_date": "2026-09-02",
            },
        )

    # Then: the persisted response and correlated event are both observable.
    assert response.status_code == 200
    assert response.json()["id"] == "manual:3"
    assert sink.events[0].correlation_id == UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e")


@pytest.mark.asyncio
async def test_calendar_event_emits_nothing_when_create_persistence_fails() -> None:
    # Given: a calendar repository that returns no committed event.
    sink = _RecordingSink()
    database = SimpleNamespace(calendar_repo=_CalendarRepository(None))

    # When: event creation cannot produce a persisted row.
    with pytest.raises((HTTPException, KeyError)):
        await calendar.create_event(
            CalendarEventCreate(
                location="Veg Room",
                title="Water plants",
                start_date=date(2026, 9, 2),
            ),
            database,
            MutationRequestContext.create(),
            sink,
        )

    # Then: no success event is emitted.
    assert sink.events == []


@pytest.mark.asyncio
async def test_calendar_sync_event_redacts_credentials_and_urls(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a CalDAV request with credential and URL sentinels.
    sink = _RecordingSink()
    database = SimpleNamespace(calendar_repo=_CalendarRepository(None))
    monkeypatch.setattr(calendar, "encrypt_secret", lambda secret: f"encrypted:{secret}")
    body = SyncConnectionCreate(
        caldav_base_url="https://calendar-user:calendar-password@caldav.example.test/dav",
        username="calendar-user",
        app_password="calendar-password",
        target_calendar_url="https://calendar-user:calendar-password@calendar.example.test/team",
    )

    # When: the connection is persisted.
    await calendar.create_sync_connection(body, database, MutationRequestContext.create(), sink)

    # Then: its event includes only safe connection identity and host information.
    serialized = sink.events[0].model_dump_json()
    assert "calendar-password" not in serialized
    assert "calendar-user" not in serialized
    assert "https://" not in serialized
    assert "calendar.example.test" in serialized
    assert "calendar-password" not in caplog.text
    assert "calendar-user" not in caplog.text


@pytest.mark.asyncio
async def test_calendar_sync_removal_emits_only_after_a_persisted_deletion() -> None:
    # Given: a committed sync connection and a repository-confirmed deletion.
    sink = _RecordingSink()
    database = SimpleNamespace(
        calendar_repo=_CalendarRepository(
            None,
            sync_connection={
                "id": 7,
                "display_name": "Operations calendar",
                "target_calendar_url": "https://calendar.example.test/team",
            },
            sync_deleted=True,
        )
    )

    # When: a client removes the sync connection through the API surface.
    app = FastAPI()
    app.include_router(calendar.router)
    app.dependency_overrides[calendar.get_database] = lambda: database
    app.dependency_overrides[calendar.get_mutation_event_sink] = lambda: sink
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/calendar/sync/connections")

    # Then: one safe delete event follows the persisted deletion.
    assert response.json() == {"status": "disconnected"}
    assert len(sink.events) == 1
    assert sink.events[0].event_type == "mutation.deleted"
    assert sink.events[0].entity.entity_type == "calendar_sync_connection"


@pytest.mark.asyncio
@pytest.mark.parametrize("sync_deleted", [False])
async def test_calendar_sync_removal_emits_nothing_without_persisted_deletion(
    sync_deleted: bool,
) -> None:
    # Given: an available sync connection whose deletion is not confirmed.
    sink = _RecordingSink()
    database = SimpleNamespace(
        calendar_repo=_CalendarRepository(
            None,
            sync_connection={
                "id": 7,
                "display_name": "Operations calendar",
                "target_calendar_url": "https://calendar.example.test/team",
            },
            sync_deleted=sync_deleted,
        )
    )

    # When: the disconnect route completes without an authoritative deletion.
    await calendar.remove_sync_connection(database, MutationRequestContext.create(), sink)

    # Then: no false persisted-mutation event is emitted.
    assert sink.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("deleted_count", "expected_events"), [(2, 1), (0, 0)])
async def test_grow_plan_deletion_emits_only_for_deleted_rows(
    deleted_count: int, expected_events: int
) -> None:
    # Given: a grow-plan repository with a known deletion count.
    sink = _RecordingSink()
    grow_plan_id = UUID("d32b865d-c0bb-4d3a-bd45-1d310090db44")
    repository = SimpleNamespace(delete_grow_plan=lambda _grow_plan_id: _async_value(deleted_count))
    database = SimpleNamespace(calendar_repo=repository)

    # When: the deletion route returns its persisted-row result.
    response = await calendar.delete_grow_plan(
        grow_plan_id, database, MutationRequestContext.create(), sink
    )

    # Then: only a positive committed count produces a deletion event.
    assert response["deleted"] == deleted_count
    assert len(sink.events) == expected_events


@pytest.mark.asyncio
async def test_room_parameters_emit_only_after_a_changed_persisted_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: room parameters with one changed persisted value.
    sink = _RecordingSink()
    repository = _RoomParametersRepository({"day_start_time": "17:00"})
    database = SimpleNamespace(room_mode_repo=repository)
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(room_modes, "get_room_mode_with_params", _room_mode_response)

    # When: the route saves a distinct parameter value.
    await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="18:00"),
        database,
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: exactly the persisted field change is emitted.
    assert [change.key for change in sink.events[0].payload.changes] == ["day_start_time"]


@pytest.mark.asyncio
async def test_room_parameters_emit_nothing_for_an_unchanged_persisted_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: room parameters already equal to the requested value.
    sink = _RecordingSink()
    repository = _RoomParametersRepository({"day_start_time": "17:00"})
    database = SimpleNamespace(room_mode_repo=repository)
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(room_modes, "get_room_mode_with_params", _room_mode_response)

    # When: the route persists the same parameter value.
    await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="17:00"),
        database,
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: no misleading mutation event is emitted.
    assert sink.events == []


@pytest.mark.asyncio
async def test_room_parameters_emit_nothing_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a repository whose save operation fails.
    sink = _RecordingSink()
    repository = _RoomParametersRepository({"day_start_time": "17:00"}, save_error=True)
    database = SimpleNamespace(room_mode_repo=repository)
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route attempts to save changed parameters.
    with pytest.raises(OSError):
        await room_modes.update_room_parameters(
            "Veg Room",
            "main",
            UpdateParametersRequest(day_start_time="18:00"),
            database,
            SimpleNamespace(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: no event claims persistence succeeded.
    assert sink.events == []


@pytest.mark.asyncio
async def test_room_parameters_emit_nothing_when_repository_rejects_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a repository that explicitly declines the parameter write.
    sink = _RecordingSink()
    repository = _RoomParametersRepository({"day_start_time": "17:00"}, save_result=False)
    database = SimpleNamespace(room_mode_repo=repository)
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(room_modes, "get_room_mode_with_params", _room_mode_response)

    # When: the route receives the failed persistence result.
    with pytest.raises(HTTPException) as error:
        await room_modes.update_room_parameters(
            "Veg Room",
            "main",
            UpdateParametersRequest(day_start_time="18:00"),
            database,
            SimpleNamespace(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: no event asserts a mutation that the repository rejected.
    assert error.value.status_code == 500
    assert sink.events == []


async def _mode_by_name(_name: str) -> dict[str, int]:
    return {"id": 2}


async def _flower_submodes() -> list[dict[str, object]]:
    return [{"id": 4, "name": "bulk"}]


async def _room_mode_response(*_args: object, **_kwargs: object) -> dict[str, str]:
    return {"mode_name": "flower"}


def _do_nothing(*_args: object, **_kwargs: object) -> None:
    return None


async def _async_value(value: int) -> int:
    return value


class _RoomParametersRepository:
    def __init__(
        self,
        parameters: dict[str, object],
        save_error: bool = False,
        save_result: bool = True,
    ) -> None:
        self._parameters = parameters
        self._save_error = save_error
        self._save_result = save_result

    async def get_active_mode(self, _location: str, _cluster: str) -> dict[str, object]:
        return {"mode_name": "veg", "submode_name": None}

    async def get_mode_parameters(
        self, _location: str, _cluster: str, _mode_name: str, _submode_name: str | None
    ) -> dict[str, object]:
        return self._parameters

    async def save_mode_parameters(
        self,
        _location: str,
        _cluster: str,
        _mode_name: str,
        _submode_name: str | None,
        parameters: dict[str, object],
    ) -> bool:
        if self._save_error:
            raise OSError("write failed")
        self._parameters = parameters
        return self._save_result


def _calendar_event(title: str) -> dict[str, object]:
    return {
        "id": 3,
        "location": "Veg Room",
        "cluster": "main",
        "event_type": "planned_task",
        "title": title,
        "start_date": date(2026, 9, 2),
        "end_date": None,
        "all_day": True,
    }
