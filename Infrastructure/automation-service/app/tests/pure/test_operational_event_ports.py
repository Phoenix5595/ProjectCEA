from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.events.operational_models import (
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_ports import OperationalEventSecondarySink, OperationalEventSink


def _event() -> OperationalEvent:
    return OperationalEvent(
        event_id=UUID("ea489a38-0599-4ba1-9fb8-ca4a912bb873"),
        occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        source=EventSource.SYSTEM,
        category=EventCategory.SYSTEM,
        severity=EventSeverity.INFO,
        event_type="system.started",
        payload=SystemPayload(component="automation-service", state="started"),
    )


class SinkFake:
    def emit_nowait(self, event: OperationalEvent) -> None:
        self.emitted = event


class SecondarySinkFake:
    async def persist(self, event: OperationalEvent) -> None:
        self.persisted = event


def test_sink_fake_satisfies_the_non_blocking_port() -> None:
    # Given: a producer-facing in-memory sink.
    sink = SinkFake()

    # When: it receives a validated event without awaiting I/O.
    sink.emit_nowait(_event())

    # Then: the narrow structural port accepts the fake.
    assert isinstance(sink, OperationalEventSink)
    assert sink.emitted.event_type == "system.started"


def test_secondary_sink_fake_satisfies_the_async_persistence_port() -> None:
    # Given: a durable side-effect sink placeholder.
    sink = SecondarySinkFake()

    # When / Then: its async capability remains independently composable.
    assert isinstance(sink, OperationalEventSecondarySink)
