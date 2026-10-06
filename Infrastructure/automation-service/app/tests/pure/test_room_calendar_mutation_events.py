from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.events import ConfigChangeEvent, ConfigEventType
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes import calendar, room_modes
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate, SyncConnectionCreate
from app.schemas.room_modes import SetModeRequest, UpdateParametersRequest
from app.services import climate_timeline_apply as climate_apply_module
from shared.redis_keys import climate_period_cache_key


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _ModeTransitionService:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = result

    async def execute_mode_transition(self, *_args: object, **_kwargs: object) -> dict[str, object]:
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
    new_mode = transition_result["new_mode"]
    assert isinstance(new_mode, dict)
    enriched = {
        **transition_result,
        "identity": {
            "mode_id": 2,
            "submode_id": 3 if new_mode.get("submode_name") else None,
            "mode_name": new_mode.get("mode_name"),
            "submode_name": new_mode.get("submode_name"),
            "is_constant": False,
        },
        "parameters": None,
        "config_revision": "0000009",
        "runtime_ready": True,
        "warning": None,
    }
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)
    monkeypatch.setattr(room_modes, "broadcast_mode_update", AsyncMock())

    # When: the mode route completes the transition.
    await room_modes.set_room_mode(
        "Flower Room",
        "main",
        SetModeRequest(mode_name="flower", submode_name="bulk"),
        database,
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        context,
        sink,
        _ModeTransitionService(enriched),
    )

    # Then: only a real persisted mode change reaches the operational sink.
    assert len(sink.events) == expected_events
    if expected_events:
        assert sink.events[0].correlation_id == context.correlation_id
        assert sink.events[0].payload.changes[0].key == "mode_name"
        assert sink.events[0].payload.changes[0].before == "veg"
        assert sink.events[0].payload.changes[0].after == "flower"


@pytest.mark.asyncio
async def test_sleep_switch_shuts_down_flower_lights_without_changing_veg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the six current grow lights and a persisted Veg -> Sleep transition.
    bindings = {
        "Flower Room": {
            "light_f_1": (2, 0),
            "light_f_2": (1, 1),
            "light_f_3": (2, 1),
        },
        "Veg Room": {
            "light_v_1": (0, 0),
            "light_v_2": (0, 1),
            "light_v_3": (1, 0),
        },
    }
    devices = {
        location: {
            "main": {
                name: {
                    "device_type": "light",
                    "dimming_enabled": True,
                    "dimming_board_id": board,
                    "dimming_channel": channel,
                }
                for name, (board, channel) in lights.items()
            }
        }
        for location, lights in bindings.items()
    }
    relay_states = {
        (location, "main", name): 1 for location, lights in bindings.items() for name in lights
    }
    intensities = {
        (board, channel): 50 for lights in bindings.values() for board, channel in lights.values()
    }

    async def set_relay_state(location: str, cluster: str, name: str, state: int):
        await asyncio.sleep(0)
        relay_states[(location, cluster, name)] = state
        return True, None

    def set_intensity(board: int, channel: int, intensity: int) -> bool:
        intensities[(board, channel)] = intensity
        return True

    database = SimpleNamespace(
        room_mode_repo=SimpleNamespace(
            get_mode_by_name=_mode_by_name,
            get_active_mode=AsyncMock(return_value={"mode_name": "sleep", "mode_id": 2}),
            get_room_modes=AsyncMock(
                return_value=[{"id": 2, "name": "sleep", "is_constant": True}]
            ),
            get_mode_parameters=AsyncMock(return_value=None),
        )
    )
    monkeypatch.setattr(room_modes, "broadcast_mode_update", AsyncMock())
    app = FastAPI()
    app.include_router(room_modes.router)
    app.dependency_overrides[room_modes.get_database] = lambda: database
    app.dependency_overrides[room_modes.get_mode_transition_service] = (
        lambda: _ModeTransitionService(
            {
                "success": True,
                "old_mode": {"mode_name": "veg", "submode_name": None},
                "new_mode": {"mode_name": "sleep", "submode_name": None},
                "identity": {
                    "mode_id": 2,
                    "submode_id": None,
                    "mode_name": "sleep",
                    "submode_name": None,
                    "is_constant": True,
                },
                "parameters": None,
                "config_revision": "0000009",
                "runtime_ready": True,
                "warning": None,
            }
        )
    )
    app.dependency_overrides[room_modes.get_config] = lambda: SimpleNamespace(
        get_devices=AsyncMock(return_value=devices)
    )
    app.dependency_overrides[room_modes.get_relay_manager] = lambda: SimpleNamespace(
        set_device_state=set_relay_state
    )
    app.dependency_overrides[room_modes.get_dfr0971_manager] = lambda: SimpleNamespace(
        set_intensity=set_intensity
    )
    app.dependency_overrides[room_modes.get_mutation_request_context] = (
        lambda: MutationRequestContext.create()
    )
    app.dependency_overrides[room_modes.get_mutation_event_sink] = lambda: _RecordingSink()

    # When: a client requests Sleep through the real route with isolated adapters.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/room-modes/room/Flower%20Room/main/mode", json={"mode_name": "sleep"}
        )

    # Then: the response succeeds, all Flower lights are off, and Veg stays untouched.
    assert response.status_code == 200, response.text
    assert response.json()["mode_name"] == "sleep"
    assert relay_states == {
        (location, "main", name): 0 if location == "Flower Room" else 1
        for location, lights in bindings.items()
        for name in lights
    }
    assert intensities == {
        (board, channel): 0 if location == "Flower Room" else 50
        for location, lights in bindings.items()
        for board, channel in lights.values()
    }


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

    # When: the route translates the failure response.
    with pytest.raises(HTTPException):
        await room_modes.set_room_mode(
            "Flower Room",
            "main",
            SetModeRequest(mode_name="flower"),
            database,
            SimpleNamespace(),
            SimpleNamespace(),
            None,
            MutationRequestContext.create(),
            sink,
            _ModeTransitionService({"success": False, "message": "failed"}),
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
async def test_parameters_put_commits_one_scoped_revision_with_locks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a persisted Veg Room parameter row and one changed clock field.
    state = _ParametersState()
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route merges and persists the change inside one transaction.
    response = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="18:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the advisory lock and active-row lock issue before any merge, the
    # revision is appended on the same connection, and invalidation follows
    # only after the commit.
    assert state.issued == [
        ("lock", (7_281_992,)),
        ("lock_active_row", ("Veg Room", "main")),
    ]
    assert state.trace == [
        "transaction_enter",
        "lock",
        "lock_active_row",
        "save_ok",
        "log_revision",
        "commit",
    ]
    assert state.parameters["day_start_time"] == "18:00"
    assert state.revision == 6
    assert response.config_revision == "0000006"
    assert response.notification_warning is None
    assert (response.mode_name, response.mode_id, response.submode_id) == ("veg", 2, None)
    assert response.is_constant is False
    assert response.parameters.day_start_time == "18:00"
    assert [change.key for change in sink.events[0].payload.changes] == ["day_start_time"]
    # Scope: the exact active profile key clears with the schedule caches; no
    # unrelated profile key is touched.
    assert climate_period_cache_key("Veg Room", "main", 2, None) in state_manager.deletes
    assert climate_period_cache_key("Veg Room", "main", 1, None) not in state_manager.deletes
    assert climate_period_cache_key("Veg Room", "main", 1, 4) not in state_manager.deletes
    assert any(key.startswith("schedules:") for key in state_manager.deletes)
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000006", "mode_id": 2, "submode_id": None},
        )
    ]


@pytest.mark.asyncio
async def test_parameters_put_emits_nothing_for_an_unchanged_persisted_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: room parameters already equal to the requested value.
    state = _ParametersState()
    _state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route persists the same parameter value.
    await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="17:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: no misleading mutation event and no ramp broadcast are emitted,
    # while the committed revision and stored value stay intact.
    assert sink.events == []
    assert ConfigEventType.RAMP_TIMES_CHANGED not in [
        event.event_type for event in event_bus.events
    ]
    assert "sync_light_ramps" not in state.trace
    assert state.parameters["day_start_time"] == "17:00"
    assert state.revision == 6


@pytest.mark.asyncio
async def test_parameters_put_rolls_back_when_the_parameter_save_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a repository that explicitly declines the parameter write.
    state = _ParametersState(fail_save=True)
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route receives the failed persistence result inside its transaction.
    with pytest.raises(RuntimeError, match="Failed to save mode parameters"):
        await room_modes.update_room_parameters(
            "Veg Room",
            "main",
            UpdateParametersRequest(day_start_time="18:00"),
            _parameters_database(state),
            SimpleNamespace(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: the staged merge is discarded, the revision is untouched, and no
    # event or invalidation claims a mutation that never committed.
    assert state.trace == ["transaction_enter", "lock", "lock_active_row", "rollback"]
    assert state.parameters["day_start_time"] == "17:00"
    assert state.revision == 5
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_parameters_put_rolls_back_when_the_revision_logging_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a configuration repository that cannot append the one revision.
    state = _ParametersState(fail_revision=True)
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route persists parameters but the revision stays unavailable.
    with pytest.raises(RuntimeError, match="Failed to log mode parameters"):
        await room_modes.update_room_parameters(
            "Veg Room",
            "main",
            UpdateParametersRequest(day_start_time="18:00"),
            _parameters_database(state),
            SimpleNamespace(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: the staged merge and revision cursor are both discarded with no
    # post-commit invalidation or event.
    assert state.trace == [
        "transaction_enter",
        "lock",
        "lock_active_row",
        "save_ok",
        "rollback",
    ]
    assert state.parameters["day_start_time"] == "17:00"
    assert state.revision == 5
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_parameters_put_creates_the_default_active_identity_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a room without any persisted active mode row.
    state = _ParametersState(active_row=None)
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route creates the missing Veg default inside its transaction.
    response = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="18:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the default identity is upserted on that same connection — no
    # nested pool acquire — the numeric identity is read back inside the
    # transaction, and the committed state keeps the created row.
    assert state.issued == [
        ("lock", (7_281_992,)),
        ("lock_active_row", ("Veg Room", "main")),
    ]
    assert state.trace == [
        "transaction_enter",
        "lock",
        "lock_active_row",
        "upsert_active",
        "save_ok",
        "log_revision",
        "commit",
    ]
    assert state.active_row == {
        "mode_id": 2,
        "submode_id": None,
        "mode_name": "veg",
        "submode_name": None,
    }
    assert (response.mode_name, response.mode_id, response.submode_id) == ("veg", 2, None)
    assert response.is_constant is False
    assert response.config_revision == "0000006"
    # The committed identity scopes invalidation: the exact default profile
    # key clears with the schedule caches, and the event carries the real
    # numeric IDs.
    assert climate_period_cache_key("Veg Room", "main", 2, None) in state_manager.deletes
    assert climate_period_cache_key("Veg Room", "main", 1, None) not in state_manager.deletes
    assert any(key.startswith("schedules:") for key in state_manager.deletes)
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000006", "mode_id": 2, "submode_id": None},
        )
    ]


@pytest.mark.asyncio
async def test_parameters_put_creates_the_flower_default_with_its_submode_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a flower room without any persisted active mode row.
    state = _ParametersState(active_row=None)
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route creates the missing Flower/Bulk default inside its transaction.
    response = await room_modes.update_room_parameters(
        "Flower Room",
        "main",
        UpdateParametersRequest(day_start_time="18:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: both numeric identities persist and reach the post-commit boundary.
    assert state.active_row == {
        "mode_id": 1,
        "submode_id": 4,
        "mode_name": "flower",
        "submode_name": "bulk",
    }
    assert (response.mode_name, response.mode_id, response.submode_id) == ("flower", 1, 4)
    assert climate_period_cache_key("Flower Room", "main", 1, 4) in state_manager.deletes
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000006", "mode_id": 1, "submode_id": 4},
        )
    ]


@pytest.mark.asyncio
async def test_parameters_put_syncs_light_ramp_rows_and_publishes_ramp_change_only_then(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: existing light-schedule rows and a request that changes both ramps.
    state = _ParametersState()
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route persists ramp changes and syncs the schedule rows on the
    # same transaction connection.
    await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(light_ramp_up_minutes=12, light_ramp_down_minutes=9),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the ramp sync stays inside the transaction and one RAMP_TIMES_CHANGED
    # event plus the post-commit schedule invalidation follow the commit.
    assert state.trace == [
        "transaction_enter",
        "lock",
        "lock_active_row",
        "save_ok",
        "log_revision",
        "sync_light_ramps",
        "commit",
    ]
    assert state.ramp_rows == [
        {"period_name": "Day", "ramp_up_minutes": 12, "ramp_down_minutes": 9}
    ]
    ramp_events = [
        event
        for event in event_bus.events
        if event.event_type == ConfigEventType.RAMP_TIMES_CHANGED
    ]
    assert [(event.data) for event in ramp_events] == [
        {"ramp_up_minutes": 12, "ramp_down_minutes": 9}
    ]
    assert len(sink.events) == 1
    assert climate_period_cache_key("Veg Room", "main", 2, None) in state_manager.deletes


@pytest.mark.asyncio
async def test_parameters_put_rolls_back_when_the_light_ramp_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a light-schedule ramp sync that fails inside the transaction.
    state = _ParametersState(fail_ramp=True)
    state_manager, event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: parameters and the revision were staged but the ramp rows fail.
    with pytest.raises(RuntimeError, match="injected light ramp sync failure"):
        await room_modes.update_room_parameters(
            "Veg Room",
            "main",
            UpdateParametersRequest(light_ramp_up_minutes=12),
            _parameters_database(state),
            SimpleNamespace(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: the staged parameters and the revision are both discarded and no
    # post-commit boundary observes the abandoned mutation.
    assert state.trace == [
        "transaction_enter",
        "lock",
        "lock_active_row",
        "save_ok",
        "log_revision",
        "rollback",
    ]
    assert state.parameters["light_ramp_up_minutes"] == 15
    assert state.revision == 5
    assert state.ramp_rows == [
        {"period_name": "Day", "ramp_up_minutes": 15, "ramp_down_minutes": 15}
    ]
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_parameters_put_normalizes_request_clocks_before_save_and_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: raw request clock text that is not canonical HH:MM.
    state = _ParametersState()
    _state_manager, _event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the route merges, persists and echoes the update.
    response = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="6:30", night_start_time="18:00:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the staged row and the response both carry canonical zero-padded
    # HH:MM instead of the raw request text.
    assert state.parameters["day_start_time"] == "06:30"
    assert state.parameters["night_start_time"] == "18:00"
    assert response.parameters.day_start_time == "06:30"
    assert response.parameters.night_start_time == "18:00"


@pytest.mark.asyncio
async def test_parameters_put_advances_the_revision_cursor_so_old_drafts_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two sequential direct parameter PUTs for the same room.
    state = _ParametersState()
    _state_manager, _event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: each PUT commits its own single revision.
    first = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="18:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )
    second = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="19:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the global cursor moved once per PUT, so a timeline draft that was
    # reviewed against the pre-update cursor no longer matches authority and
    # must be re-reviewed (Apply's expected_config_revision guard) instead of
    # being silently rebased over these external changes.
    assert (first.config_revision, second.config_revision) == ("0000006", "0000007")
    assert state.revision == 7
    assert state.parameters["day_start_time"] == "19:00"


@pytest.mark.asyncio
async def test_parameters_put_creates_a_missing_parameter_row_from_model_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a resolvable active identity whose parameter row does not exist.
    state = _ParametersState(parameters_row_absent=True)
    _state_manager, _event_bus = _patch_notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    monkeypatch.setattr(room_modes, "ensure_configured_cluster", _do_nothing)

    # When: the direct PUT reaches the route.
    response = await room_modes.update_room_parameters(
        "Veg Room",
        "main",
        UpdateParametersRequest(day_start_time="06:00"),
        _parameters_database(state),
        SimpleNamespace(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the create-if-absent write persists the model defaults for the
    # omitted fields (100/0 deprecated intensities, 15/15 ramps, stored clocks)
    # and the response echoes those real persisted values, not None IDs.
    assert state.parameters == {
        "day_start_time": "06:00",
        "night_start_time": "11:00",
        "light_ramp_up_minutes": 15,
        "light_ramp_down_minutes": 15,
        "main_light_intensity": 100,
        "supplemental_light_intensity": 0,
    }
    assert (response.mode_id, response.submode_id) == (2, None)
    assert response.parameters.main_light_intensity == 100
    assert response.parameters.supplemental_light_intensity == 0


async def _mode_by_name(_name: str) -> dict[str, int]:
    return {"id": 2}


async def _flower_submodes() -> list[dict[str, object]]:
    return [{"id": 4, "name": "bulk"}]


def _do_nothing(*_args: object, **_kwargs: object) -> None:
    return None


async def _async_value(value: int) -> int:
    return value


@dataclass
class _ParametersState:
    revision: int = 5
    active_row: dict[str, object] | None = field(
        default_factory=lambda: {
            "mode_id": 2,
            "submode_id": None,
            "mode_name": "veg",
            "submode_name": None,
        }
    )
    parameters: dict[str, object] = field(
        default_factory=lambda: {
            "day_start_time": "17:00",
            "night_start_time": "11:00",
            "light_ramp_up_minutes": 15,
            "light_ramp_down_minutes": 15,
            "main_light_intensity": 100,
            "supplemental_light_intensity": 0,
        }
    )
    ramp_rows: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"period_name": "Day", "ramp_up_minutes": 15, "ramp_down_minutes": 15}
        ]
    )
    fail_save: bool = False
    fail_revision: bool = False
    fail_ramp: bool = False
    parameters_row_absent: bool = False
    trace: list[str] = field(default_factory=list)
    issued: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)


def _parameters_query_tag(query: str) -> str:
    if "pg_advisory_xact_lock" in query:
        return "lock"
    if "FOR UPDATE" in query:
        return "lock_active_row"
    raise AssertionError(f"unexpected query: {query}")


class _ParametersConnection:
    def __init__(self, state: _ParametersState) -> None:
        self._state = state
        self._staged: _ParametersState | None = None

    @asynccontextmanager
    async def transaction(self):
        self._state.trace.append("transaction_enter")
        self._staged = deepcopy(self._state)
        try:
            yield
        except Exception:
            self._state.trace.append("rollback")
            raise
        else:
            self._state.revision = self._staged.revision
            self._state.active_row = self._staged.active_row
            self._state.parameters = self._staged.parameters
            self._state.ramp_rows = self._staged.ramp_rows
            self._state.trace.append("commit")

    def _issue(self, query: str, args: tuple[object, ...]) -> str:
        assert self._staged is not None
        tag = _parameters_query_tag(query)
        self._state.issued.append((tag, args))
        return tag

    async def execute(self, query: str, *args: object) -> str:
        tag = self._issue(query, args)
        if tag == "lock":
            self._state.trace.append("lock")
            return "SELECT 1"
        raise AssertionError(query)

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        tag = self._issue(query, args)
        if tag == "lock_active_row":
            self._state.trace.append("lock_active_row")
            row = self._staged.active_row
            if row is None:
                return None
            return {"mode_id": row.get("mode_id"), "submode_id": row.get("submode_id")}
        raise AssertionError(query)

    def staged_active_identity(self) -> dict[str, object]:
        assert self._staged is not None
        return dict(self._staged.active_row or {})

    def staged_parameters(self) -> dict[str, object]:
        assert self._staged is not None
        return dict(self._staged.parameters)

    def stage_parameter_merge(self, merged: dict[str, object]) -> None:
        assert self._staged is not None
        self._state.trace.append("save_ok")
        self._staged.parameters = merged

    def stage_revision(self) -> int:
        assert self._staged is not None
        self._state.trace.append("log_revision")
        self._staged.revision += 1
        return self._staged.revision

    def stage_ramp_sync(self, ramp_up: int, ramp_down: int) -> None:
        assert self._staged is not None
        self._state.trace.append("sync_light_ramps")
        self._staged.ramp_rows = [
            {**row, "ramp_up_minutes": ramp_up, "ramp_down_minutes": ramp_down}
            for row in self._staged.ramp_rows
        ]

    def stage_default_active_mode(self, mode_name: str, submode_name: str | None) -> None:
        assert self._staged is not None
        self._state.trace.append("upsert_active")
        # The persisted default resolves to real catalogue IDs, never None.
        mode_id = {"veg": 2, "flower": 1}[mode_name]
        submode_id = {"bulk": 4}.get(submode_name) if submode_name else None
        self._staged.active_row = {
            "mode_id": mode_id,
            "submode_id": submode_id,
            "mode_name": mode_name,
            "submode_name": submode_name,
        }


class _ParametersPool:
    def __init__(self, state: _ParametersState) -> None:
        self._connection = _ParametersConnection(state)

    @asynccontextmanager
    async def acquire(self):
        yield self._connection


class _ParametersRoomModeRepository:
    _MODE_NAMES: dict[int, str] = {1: "flower", 2: "veg"}

    def __init__(self, state: _ParametersState) -> None:
        self._state = state

    async def get_active_mode(
        self, _location: str, _cluster: str, conn: object = None
    ) -> dict[str, object]:
        if conn is None:
            return dict(self._state.active_row or {})
        return conn.staged_active_identity()

    async def set_active_mode(
        self,
        _location: str,
        _cluster: str,
        mode_name: str,
        submode_name: str | None,
        conn: object = None,
    ) -> bool:
        assert conn is not None
        conn.stage_default_active_mode(mode_name, submode_name)
        return True

    async def get_profile_identity_on_connection(
        self, conn: object, _location: str, mode_id: int, submode_id: int | None
    ) -> dict[str, object]:
        """The exact committed identity; a fake must never invent IDs."""
        assert conn is not None
        mode_name = self._MODE_NAMES.get(mode_id)
        assert mode_name is not None, f"identity read invented mode {mode_id}"
        submode_name = {4: "bulk"}.get(submode_id) if submode_id is not None else None
        assert submode_id is None or submode_name is not None, (
            f"identity read invented submode {submode_id}"
        )
        return {
            "mode_id": mode_id,
            "submode_id": submode_id,
            "mode_name": mode_name,
            "submode_name": submode_name,
            "is_constant": False,
            "photoperiod_hours": None,
        }

    async def get_mode_parameters(
        self,
        _location: str,
        _cluster: str,
        _mode_name: str,
        _submode_name: str | None,
        conn: object = None,
    ) -> dict[str, object]:
        assert conn is not None
        if self._state.parameters_row_absent:
            return None
        return conn.staged_parameters()

    async def save_mode_parameters(
        self,
        _location: str,
        _cluster: str,
        _mode_name: str,
        _submode_name: str | None,
        params: dict[str, object],
        conn: object = None,
    ) -> bool:
        assert conn is not None
        if self._state.fail_save:
            return False
        conn.stage_parameter_merge(dict(params))
        return True


class _ParametersConfigRepository:
    def __init__(self, state: _ParametersState) -> None:
        self._state = state

    async def log_config_version(self, **kwargs: object) -> int | None:
        conn = kwargs.get("conn")
        assert conn is not None
        if self._state.fail_revision:
            return None
        return conn.stage_revision()


class _ParametersScheduleRepository:
    def __init__(self, state: _ParametersState) -> None:
        self._state = state

    async def update_light_schedule_ramp_times(
        self,
        _location: str,
        _cluster: str,
        ramp_up_minutes: int,
        ramp_down_minutes: int,
        conn: object = None,
    ) -> int:
        assert conn is not None
        if self._state.fail_ramp:
            raise RuntimeError("injected light ramp sync failure")
        conn.stage_ramp_sync(ramp_up_minutes, ramp_down_minutes)
        return 1


def _parameters_database(state: _ParametersState) -> SimpleNamespace:
    return SimpleNamespace(
        _get_pool=AsyncMock(return_value=_ParametersPool(state)),
        room_mode_repo=_ParametersRoomModeRepository(state),
        config_repo=_ParametersConfigRepository(state),
        schedule_repo=_ParametersScheduleRepository(state),
        climate_periods_repo=SimpleNamespace(get_periods=AsyncMock(return_value=[])),
    )


class _RecordingInvalidationState:
    def __init__(self) -> None:
        self.deletes: list[str] = []

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        del skip_redis
        self.deletes.append(key)
        return True


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[ConfigChangeEvent] = []

    async def publish(self, event: ConfigChangeEvent) -> bool:
        self.events.append(event)
        return True


def _patch_notification_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_RecordingInvalidationState, _RecordingEventBus]:
    """Route both post-commit boundaries (invalidator and ramp event) to fakes."""
    state_manager = _RecordingInvalidationState()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(climate_apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(climate_apply_module, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr(room_modes, "get_event_bus", lambda: event_bus)
    return state_manager, event_bus


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
