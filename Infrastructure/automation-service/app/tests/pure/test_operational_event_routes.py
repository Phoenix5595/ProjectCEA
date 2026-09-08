from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.operational_models import (
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_stream import OperationalStreamEntry
from app.routes.operational_events import get_operational_event_reader, router
from shared.auth import APIKeyAuthMiddleware


class ReaderFake:
    def __init__(self, entries: tuple[OperationalStreamEntry, ...]) -> None:
        self.entries = entries
        self.history_calls: list[tuple[str | None, str | None, int]] = []

    async def cursor_bounds(self) -> tuple[str | None, str | None]:
        if not self.entries:
            return None, None
        return self.entries[0].id, self.entries[-1].id

    async def cursor_exists(self, cursor: str) -> bool:
        return any(entry.id == cursor for entry in self.entries)

    async def history(
        self, *, before: str | None, after: str | None, scan_limit: int
    ) -> tuple[OperationalStreamEntry, ...]:
        self.history_calls.append((before, after, scan_limit))
        if after is not None:
            return tuple(entry for entry in self.entries if entry.id > after)
        if before is not None:
            return tuple(entry for entry in reversed(self.entries) if entry.id < before)
        return tuple(reversed(self.entries))

    async def read(self, *, after_id: str) -> tuple[OperationalStreamEntry, ...]:
        return tuple(entry for entry in self.entries if entry.id > after_id)


def make_entry(
    stream_id: str,
    *,
    location: str = "Flower Room",
    cluster: str = "front",
    category: EventCategory = EventCategory.SYSTEM,
    severity: EventSeverity = EventSeverity.INFO,
    event_type: str = "system.started",
) -> OperationalStreamEntry:
    return OperationalStreamEntry(
        id=stream_id,
        event=OperationalEvent(
            event_id=UUID(f"00000000-0000-0000-0000-{int(stream_id.split('-')[0]):012d}"),
            occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            source=EventSource.SYSTEM,
            category=category,
            severity=severity,
            event_type=event_type,
            entity=EntityContext(
                entity_type="room",
                entity_id=f"{location}:{cluster}",
                location=location,
                cluster=cluster,
            ),
            payload=SystemPayload(component="automation-service", state="started"),
        ),
    )


def make_app(reader: ReaderFake) -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_operational_event_reader] = lambda: reader
    return app


@pytest.mark.asyncio
async def test_history_returns_newest_first_filtered_page_and_bounded_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: retained events spanning filter values and an enabled API key gate.
    monkeypatch.setenv("CEA_API_KEY_REQUIRE", "true")
    monkeypatch.setenv("CEA_API_KEY", "test-key")
    reader = ReaderFake(
        (
            make_entry("1-0", location="Veg Room"),
            make_entry("2-0", severity=EventSeverity.WARNING),
            make_entry("3-0", event_type="system.redis_degraded"),
        )
    )

    # When: a client asks for a filtered two-row history page.
    async with AsyncClient(
        transport=ASGITransport(app=make_app(reader)), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/events/history?limit=2&location=Flower%20Room",
            headers={"X-API-Key": "test-key"},
        )

    # Then: only matching newest-first entries and cursor/scan metadata are returned.
    assert response.status_code == 200
    payload = response.json()
    assert [item["redis_id"] for item in payload["items"]] == ["3-0", "2-0"]
    assert payload["newest_cursor"] == "3-0"
    assert payload["oldest_cursor"] == "2-0"
    assert payload["earliest_cursor"] == "1-0"
    assert payload["has_more"] is False
    assert payload["scan"]["scanned"] == 3
    assert reader.history_calls == [(None, None, 5_000)]


@pytest.mark.asyncio
async def test_history_honors_after_before_xor_limits_and_all_filters() -> None:
    # Given: three retained entries with distinct room/category/severity/type fields.
    reader = ReaderFake(
        (
            make_entry("1-0", location="Veg Room"),
            make_entry(
                "2-0",
                category=EventCategory.SYSTEM,
                severity=EventSeverity.WARNING,
                event_type="system.redis_degraded",
            ),
            make_entry("3-0", cluster="back"),
        )
    )

    # When: the oldest-direction cursor and every supported filter are supplied.
    async with AsyncClient(
        transport=ASGITransport(app=make_app(reader)), base_url="http://test"
    ) as client:
        filtered = await client.get(
            "/api/events/history?after=1-0&location=Flower%20Room&cluster=front"
            "&category=system&severity=warning&type=system.redis_degraded"
        )
        conflicting = await client.get("/api/events/history?after=1-0&before=3-0")
        too_large = await client.get("/api/events/history?limit=501")
        malformed = await client.get("/api/events/history?after=broken")

    # Then: filters apply together and query validation rejects invalid pagination.
    assert [item["redis_id"] for item in filtered.json()["items"]] == ["2-0"]
    assert conflicting.status_code == 422
    assert too_large.status_code == 422
    assert malformed.status_code == 422


@pytest.mark.asyncio
async def test_history_requires_api_key_without_query_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: API-key enforcement is configured for the protected route.
    monkeypatch.setenv("CEA_API_KEY_REQUIRE", "true")
    monkeypatch.setenv("CEA_API_KEY", "test-key")
    app = make_app(ReaderFake((make_entry("1-0"),)))

    # When: the key is missing, wrong, or supplied only as a query parameter.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/events/history")
        wrong = await client.get("/api/events/history", headers={"X-API-Key": "wrong"})
        query_token = await client.get("/api/events/history?api_key=test-key")

    # Then: the middleware rejects every unauthenticated request.
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert query_token.status_code == 401
