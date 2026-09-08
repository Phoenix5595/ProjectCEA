from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.calendar import sync_worker
from app.events.mutation_coverage import assert_mutation_route_coverage
from app.events.mutation_exclusions import MUTATION_ROUTE_EXCLUSIONS, NonPersistentEffect
from app.events.operational_models import OperationalEvent
from app.routes import calendar
from app.routes.routes import register_routes


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _SyncWorker:
    def __init__(self, sync_result: dict[str, object], test_result: list[dict[str, str]]) -> None:
        self._sync_result = sync_result
        self._test_result = test_result
        self.test_calls: list[tuple[str, str, str]] = []

    async def run_sync(self) -> dict[str, object]:
        return self._sync_result

    async def test_connection(
        self, caldav_base_url: str, username: str, app_password: str
    ) -> list[dict[str, str]]:
        self.test_calls.append((caldav_base_url, username, app_password))
        return self._test_result


class _FailingProbeWorker:
    async def test_connection(
        self, _caldav_base_url: str, _username: str, app_password: str
    ) -> list[dict[str, str]]:
        raise RuntimeError(f"connection refused for {app_password}")


class _SyncRepository:
    async def get_sync_connection(self) -> dict[str, int]:
        return {"id": 7}

    async def events_pending_sync(self) -> list[dict[str, object]]:
        return [{"id": 3, "sync_status": "pending_push"}]

    async def mark_event_synced(self, *_args: object) -> None:
        raise AssertionError("failed remote sync must not update the event")

    async def update_sync_state(self, *_args: object, **_kwargs: object) -> None:
        return None


class _SyncPool:
    async def fetchrow(self, *_args: object) -> dict[str, object]:
        return {
            "credentials_encrypted": b"encrypted",
            "account_email": "calendar-user",
            "caldav_base_url": "https://calendar.example.test/dav",
            "target_calendar_url": "https://calendar.example.test/team",
        }


@pytest.mark.asyncio
async def test_run_sync_emits_safe_action_only_after_synced_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed worker result with persisted remote push and delete counts.
    sink = _RecordingSink()
    worker = _SyncWorker(
        {"ok": True, "pushed": 2, "deleted": 1, "errors": []},
        [],
    )
    monkeypatch.setattr(calendar, "CalendarSyncWorker", lambda _database: worker)
    app = FastAPI()
    app.include_router(calendar.router)
    app.dependency_overrides[calendar.get_database] = lambda: SimpleNamespace()
    app.dependency_overrides[calendar.get_mutation_event_sink] = lambda: sink

    # When: the sync is driven through its HTTP surface.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/calendar/sync/run",
            headers={"X-Request-ID": "7552d5f1-0a9a-43e8-a63b-26a60d126c2e"},
        )

    # Then: exactly one correlated action records safe aggregate counts only.
    assert response.json() == {"ok": True, "pushed": 2, "deleted": 1, "errors": []}
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_type == "mutation.action_completed"
    assert event.correlation_id == UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e")
    assert event.entity.entity_type == "calendar_sync"
    assert [(change.key, change.after) for change in event.payload.changes] == [
        ("deleted_events", 1),
        ("pushed_events", 2),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sync_result",
    [
        {"ok": True, "pushed": 0, "deleted": 0, "errors": []},
        {"ok": False, "pushed": 0, "deleted": 0, "errors": ["CalDAV failed"]},
        {"ok": False, "error": "No sync connection configured"},
    ],
)
async def test_run_sync_emits_nothing_without_a_successful_event_sync(
    monkeypatch: pytest.MonkeyPatch,
    sync_result: dict[str, object],
) -> None:
    # Given: a worker that has no committed event synchronization to report.
    sink = _RecordingSink()
    worker = _SyncWorker(sync_result, [])
    monkeypatch.setattr(calendar, "CalendarSyncWorker", lambda _database: worker)

    # When: the route returns the no-op or failure result.
    response = await calendar.run_sync(
        SimpleNamespace(), calendar.MutationRequestContext.create(), sink
    )

    # Then: no action event overstates a completed synchronization.
    assert response == sync_result
    assert sink.events == []


@pytest.mark.asyncio
async def test_connection_probe_is_transient_and_redacts_failure_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a probe worker that receives credentials but does not persist state.
    password = "calendar-password"
    worker = _SyncWorker(
        {},
        [
            {
                "name": "Team",
                "url": "https://calendar-user:calendar-password@calendar.example.test/team",
            }
        ],
    )
    monkeypatch.setattr(calendar, "CalendarSyncWorker", lambda _database: worker)
    app = FastAPI()
    app.include_router(calendar.router)
    app.dependency_overrides[calendar.get_database] = lambda: SimpleNamespace()

    # When: a client checks a connection through the API surface.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/calendar/sync/connections/test",
            json={
                "caldav_base_url": "https://caldav.example.test/dav/",
                "username": "calendar-user",
                "app_password": password,
            },
        )

    # Then: the remote read is invoked without a persistence event or credential disclosure.
    assert response.status_code == 200
    assert worker.test_calls == [("https://caldav.example.test/dav", "calendar-user", password)]
    assert password not in response.text
    assert "calendar-user" not in response.text


@pytest.mark.asyncio
async def test_connection_probe_failure_does_not_echo_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a remote probe failure whose exception contains the supplied password.
    password = "calendar-password"
    monkeypatch.setattr(calendar, "CalendarSyncWorker", lambda _database: _FailingProbeWorker())
    app = FastAPI()
    app.include_router(calendar.router)
    app.dependency_overrides[calendar.get_database] = lambda: SimpleNamespace()

    # When: the failing probe is driven through its HTTP surface.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/calendar/sync/connections/test",
            json={
                "caldav_base_url": "https://caldav.example.test/dav",
                "username": "calendar-user",
                "app_password": password,
            },
        )

    # Then: the client receives a generic failure without the secret.
    assert response.status_code == 400
    assert password not in response.text


@pytest.mark.asyncio
async def test_sync_worker_redacts_remote_failure_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a failed remote push whose exception includes the decrypted password.
    password = "calendar-password"
    database = SimpleNamespace(calendar_repo=_SyncRepository(), pool=_SyncPool())
    worker = sync_worker.CalendarSyncWorker(database)
    monkeypatch.setattr(sync_worker, "decrypt_secret", lambda _encrypted: password)

    async def fail_remote_push(*_args: object) -> tuple[str, str | None]:
        raise RuntimeError(f"remote rejected {password}")

    monkeypatch.setattr(worker, "_push_remote", fail_remote_push)

    # When: the worker handles the failed synchronization.
    result = await worker.run_sync()

    # Then: neither the response-safe result nor logs retain the password.
    assert result["ok"] is False
    assert password not in str(result)
    assert password not in caplog.text


def test_calendar_sync_routes_have_truthful_coverage_classifications() -> None:
    # Given: the production application's complete route registry.
    app = FastAPI()
    register_routes(app)

    # When: calendar test and run endpoints are evaluated with mutation coverage.
    routes_by_path = {route.path: route for route in app.routes}
    test_exclusion = next(
        exclusion
        for exclusion in MUTATION_ROUTE_EXCLUSIONS
        if exclusion.path == "/api/calendar/sync/connections/test"
    )

    # Then: the probe remains transient while a completed sync is marked as persisted.
    assert test_exclusion.effect is NonPersistentEffect.TRANSIENT_REMOTE_CONNECTION_PROBE
    assert test_exclusion.rationale
    assert (
        getattr(
            routes_by_path["/api/calendar/sync/run"].endpoint,
            "__emits_operational_mutation__",
            False,
        )
        is True
    )
    assert_mutation_route_coverage(app)
