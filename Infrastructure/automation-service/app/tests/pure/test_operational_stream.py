from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.events.operational_models import (
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
    serialize_operational_event,
)
from app.events.operational_stream import OperationalEventStreamReader


class RedisReaderFake:
    def __init__(self, entries: list[tuple[str, dict[str, bytes]]]) -> None:
        self.entries = entries
        self.calls: list[tuple[dict[str, str], int, int]] = []

    async def xread(
        self, streams: dict[str, str], *, count: int, block: int
    ) -> list[tuple[str, list[tuple[str, dict[str, bytes]]]]]:
        self.calls.append((streams, count, block))
        return [("cea:events:operational", self.entries)]


def event() -> OperationalEvent:
    return OperationalEvent(
        event_id=UUID("ea489a38-0599-4ba1-9fb8-ca4a912bb873"),
        occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        source=EventSource.SYSTEM,
        category=EventCategory.SYSTEM,
        severity=EventSeverity.INFO,
        event_type="system.started",
        payload=SystemPayload(component="automation-service", state="started"),
    )


@pytest.mark.asyncio
async def test_reader_returns_valid_entries_and_counts_malformed_stream_payloads() -> None:
    # Given: one serialized operational event and one malformed Redis entry.
    redis = RedisReaderFake(
        [
            ("1-0", {"event": serialize_operational_event(event())}),
            ("2-0", {"event": b"not-json"}),
        ]
    )
    reader = OperationalEventStreamReader(redis)

    # When: the non-group reader fetches after the retained stream beginning.
    entries = await reader.read(after_id="0-0")

    # Then: valid events remain readable and malformed input is exposed in health.
    assert tuple(entry.id for entry in entries) == ("1-0",)
    assert entries[0].event == event()
    assert reader.health().malformed_entries == 1
    assert redis.calls == [({"cea:events:operational": "0-0"}, 100, 1_000)]


@pytest.mark.asyncio
async def test_reader_skips_invalid_utf8_message_id_and_returns_later_valid_entry() -> None:
    # Given: a valid payload paired with an invalid Redis identifier before a valid entry.
    redis = RedisReaderFake(
        [
            (b"\xff", {"event": serialize_operational_event(event())}),
            ("2-0", {"event": serialize_operational_event(event())}),
        ]
    )
    reader = OperationalEventStreamReader(redis)

    # When: the reader parses the complete Redis batch.
    entries = await reader.read(after_id="0-0")

    # Then: the invalid identifier is counted and cannot abort a later valid event.
    assert tuple(entry.id for entry in entries) == ("2-0",)
    assert reader.health().malformed_entries == 1
