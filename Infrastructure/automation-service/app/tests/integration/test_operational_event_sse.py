from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from starlette.requests import Request

from app.events.operational_models import (
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_stream import OperationalStreamEntry
from app.routes.operational_events import (
    get_operational_event_reader,
    operational_event_stream,
    router,
)
from shared.auth import APIKeyAuthMiddleware


class RedisBackedReaderFake:
    def __init__(self, entries: tuple[OperationalStreamEntry, ...]) -> None:
        self.entries = entries
        self.read_calls: list[str] = []
        self.cancelled = False

    async def cursor_bounds(self) -> tuple[str | None, str | None]:
        return self.entries[0].id, self.entries[-1].id

    async def cursor_exists(self, cursor: str) -> bool:
        return any(entry.id == cursor for entry in self.entries)

    async def history(
        self, *, before: str | None, after: str | None, scan_limit: int
    ) -> tuple[OperationalStreamEntry, ...]:
        return self.entries

    async def read(self, *, after_id: str) -> tuple[OperationalStreamEntry, ...]:
        self.read_calls.append(after_id)
        try:
            return tuple(entry for entry in self.entries if entry.id > after_id)
        except BaseException:
            self.cancelled = True
            raise


def make_entry(stream_id: str, event_type: str) -> OperationalStreamEntry:
    return OperationalStreamEntry(
        id=stream_id,
        event=OperationalEvent(
            event_id=UUID(f"00000000-0000-0000-0000-{int(stream_id.split('-')[0]):012d}"),
            occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            source=EventSource.SYSTEM,
            category=EventCategory.SYSTEM,
            severity=EventSeverity.INFO,
            event_type=event_type,
            payload=SystemPayload(component="automation-service", state="started"),
        ),
    )


def make_app(reader: RedisBackedReaderFake) -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_operational_event_reader] = lambda: reader
    return app


@pytest.mark.asyncio
async def test_stream_replays_strictly_after_cursor_with_sse_headers_and_frames() -> None:
    # Given: an isolated route app with a Redis-shaped reader containing two later events.
    reader = RedisBackedReaderFake(
        (
            make_entry("1-0", "system.started"),
            make_entry("2-0", "system.ready"),
            make_entry("3-0", "system.healthy"),
        )
    )

    # When: the fetch-SSE route opens strictly after the first Redis ID.
    response = await operational_event_stream(make_request(), reader, "1-0")
    iterator = response.body_iterator
    connected = await anext(iterator)
    replayed = await anext(iterator)
    second_replay = await anext(iterator)
    await iterator.aclose()

    # Then: SSE starts with a comment and replays only the later named data event once.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert connected == b": connected\n\n"
    assert b"id: 2-0\n" in replayed
    assert b"event: operational_event\n" in replayed
    assert b'"event_type":"system.ready"' in replayed
    assert b"id: 3-0\n" in second_replay
    assert b'"event_type":"system.healthy"' in second_replay


@pytest.mark.asyncio
async def test_stream_stops_when_the_fetch_client_disconnects() -> None:
    # Given: a disconnected fetch-SSE request and a retained cursor.
    reader = RedisBackedReaderFake((make_entry("1-0", "system.started"),))

    # When: the SSE iterator advances after sending its initial comment.
    response = await operational_event_stream(make_request(disconnected=True), reader, "1-0")
    iterator = response.body_iterator
    assert await anext(iterator) == b": connected\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    # Then: it exits without opening an additional Redis read.
    assert reader.read_calls == []


@pytest.mark.asyncio
async def test_stream_emits_cursor_progress_and_heartbeat_for_filtered_events() -> None:
    # Given: replayed events excluded by a location filter followed by idle Redis reads.
    reader = RedisBackedReaderFake(
        (make_entry("1-0", "system.started"), make_entry("2-0", "system.ready"))
    )
    response = await operational_event_stream(make_request(), reader, "1-0", location="Veg Room")
    iterator = response.body_iterator

    # When: the stream passes one excluded event and fifteen empty one-second reads.
    assert await anext(iterator) == b": connected\n\n"
    assert await anext(iterator) == b"id: 2-0\n\n"
    heartbeat = await anext(iterator)
    await iterator.aclose()

    # Then: a cursor-only progress frame and heartbeat keep the browser resume point alive.
    assert heartbeat == b": heartbeat\n\n"


@pytest.mark.asyncio
async def test_stream_returns_cursor_reset_before_opening_read_for_trimmed_cursor() -> None:
    # Given: Redis retention begins after the supplied cursor.
    reader = RedisBackedReaderFake((make_entry("10-0", "system.started"),))

    # When: a client resumes from a trimmed cursor.
    async with AsyncClient(
        transport=ASGITransport(app=make_app(reader)), base_url="http://test"
    ) as client:
        response = await client.get("/api/events/stream?after=1-0")

    # Then: it receives typed reset data and no dangling stream read begins.
    assert response.status_code == 409
    assert response.json() == {
        "code": "operational_event_cursor_trimmed",
        "earliest_cursor": "10-0",
        "latest_cursor": "10-0",
    }
    assert reader.read_calls == []


def make_request(*, disconnected: bool = False) -> Request:
    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect" if disconnected else "http.request"}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
        },
        receive,
    )
