from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from socket import AF_INET, SOCK_STREAM, socket
from subprocess import DEVNULL, Popen
from time import monotonic, sleep
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from redis.asyncio import Redis
from starlette.requests import Request

from app.container import _OperationalEventRouteReader
from app.events.operational_models import (
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    RelayPayload,
)
from app.events.operational_stream import (
    ROUTINE_QUEUE_CAPACITY,
    OperationalEventDispatcher,
    OperationalEventStreamReader,
)
from app.routes.operational_events import (
    get_operational_event_reader,
    operational_event_stream,
    router,
)
from shared.redis_keys import OPERATIONAL_EVENTS_STREAM

LOOPBACK_HOST = "127.0.0.1"


class FakeHardware:
    def __init__(self) -> None:
        self.calls = 0

    def set_channel(self, _channel: int, _state: bool) -> None:
        self.calls += 1


@pytest.fixture
def ephemeral_redis_url(tmp_path) -> Iterator[str]:
    with socket(AF_INET, SOCK_STREAM) as reservation:
        reservation.bind((LOOPBACK_HOST, 0))
        port = reservation.getsockname()[1]

    process = Popen(
        [
            "redis-server",
            "--bind",
            LOOPBACK_HOST,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=DEVNULL,
        stderr=DEVNULL,
    )
    try:
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with socket(AF_INET, SOCK_STREAM) as probe:
                if probe.connect_ex((LOOPBACK_HOST, port)) == 0:
                    break
            sleep(0.01)
        else:
            raise RuntimeError("ephemeral loopback Redis did not start")
        yield f"redis://{LOOPBACK_HOST}:{port}/15"
    finally:
        process.terminate()
        process.wait(timeout=5)


def event(index: int) -> OperationalEvent:
    room = "Flower Room" if index % 4 == 0 else "Vegetation Room"
    return OperationalEvent(
        event_id=UUID(int=index + 1),
        occurred_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        source=EventSource.AUTOMATION,
        category=EventCategory.RELAY,
        severity=EventSeverity.INFO,
        event_type="relay.state_changed",
        entity=EntityContext(
            entity_type="device",
            entity_id=f"relay-{index}",
            location=room,
            cluster="main",
        ),
        payload=RelayPayload(state=True),
    )


def request() -> Request:
    async def receive() -> dict[str, str]:
        return {"type": "http.request"}

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/stream",
            "headers": [],
            "query_string": b"",
            "client": (LOOPBACK_HOST, 0),
            "server": ("test", 80),
            "scheme": "http",
        },
        receive,
    )


@pytest.mark.asyncio
async def test_end_to_end_when_replay_and_adversarial_entries_share_an_ephemeral_stream(
    ephemeral_redis_url: str,
) -> None:
    # Given: a verified loopback-only Redis process, composed route reader, and inert hardware.
    parsed = urlparse(ephemeral_redis_url)
    assert parsed.hostname == LOOPBACK_HOST
    assert parsed.port not in {None, 6379}
    redis = Redis.from_url(ephemeral_redis_url, decode_responses=False)
    await redis.ping()
    dispatcher = OperationalEventDispatcher(redis)
    await dispatcher.start()
    reader = _OperationalEventRouteReader(redis)
    hardware = FakeHardware()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_operational_event_reader] = lambda: reader

    # When: the real sink publishes 100 events, history boots at 60, then SSE replays the rest.
    for index in range(100):
        dispatcher.emit_nowait(event(index))
    await dispatcher.drain()
    stream_ids = tuple(item[0].decode() for item in await redis.xrange(OPERATIONAL_EVENTS_STREAM))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        global_history = await client.get("/api/events/history", params={"limit": 200})
        room_history = await client.get(
            "/api/events/history", params={"limit": 200, "location": "Flower Room"}
        )

    stream = await operational_event_stream(request(), reader, stream_ids[59])
    iterator = stream.body_iterator
    assert await anext(iterator) == b": connected\n\n"
    replay_frames = [await anext(iterator) for _ in range(40)]
    await iterator.aclose()

    # Then: history and replay are complete, ordered, room-filtered, and hardware remains untouched.
    assert global_history.status_code == 200
    assert tuple(item["redis_id"] for item in global_history.json()["items"]) == tuple(
        reversed(stream_ids)
    )
    assert room_history.status_code == 200
    assert len(room_history.json()["items"]) == 25
    assert all(
        item["event"]["entity"]["location"] == "Flower Room"
        for item in room_history.json()["items"]
    )
    assert tuple(frame.decode().splitlines()[0][4:] for frame in replay_frames) == stream_ids[60:]
    assert hardware.calls == 0

    # When: corrupt and unknown rows arrive before one valid row, then an old cursor is trimmed.
    cursor_before_adversarial_rows = stream_ids[-1]
    await redis.xadd(OPERATIONAL_EVENTS_STREAM, {"event": b"not-json"})
    await redis.xadd(OPERATIONAL_EVENTS_STREAM, {"event": b'{"schema_version":999}'})
    dispatcher.emit_nowait(event(100))
    await dispatcher.drain()
    parsed_entries = await OperationalEventStreamReader(redis).read(
        after_id=cursor_before_adversarial_rows
    )
    await redis.xtrim(OPERATIONAL_EVENTS_STREAM, maxlen=1, approximate=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trimmed = await client.get("/api/events/history", params={"after": stream_ids[0]})

    # Then: bad rows isolate, retained progress continues, and reset is typed before a new read.
    assert tuple(entry.event.event_id for entry in parsed_entries) == (event(100).event_id,)
    assert trimmed.status_code == 409
    assert trimmed.json()["code"] == "operational_event_cursor_trimmed"

    # When: routine capacity is exceeded and the production dispatcher shuts down.
    saturated = OperationalEventDispatcher(redis)
    for index in range(ROUTINE_QUEUE_CAPACITY + 1):
        saturated.emit_nowait(event(1_000 + index))
    await dispatcher.close()

    # Then: backpressure is observable and shutdown never turns observations into hardware commands.
    assert saturated.health().dropped_routine == 1
    assert hardware.calls == 0
