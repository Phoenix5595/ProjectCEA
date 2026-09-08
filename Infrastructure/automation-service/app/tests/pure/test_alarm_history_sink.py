from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.events.alarm_journal import AlarmJournal
from app.events.operational_models import (
    AlarmPayload,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    RelayPayload,
)


class _AlarmRepository:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = outcomes
        self.events: list[OperationalEvent] = []

    async def record_alarm_lifecycle(self, event: OperationalEvent) -> bool:
        self.events.append(event)
        return self._outcomes.pop(0)


async def _record_delay(delays: list[float], delay: float) -> None:
    delays.append(delay)


def _alarm_event() -> OperationalEvent:
    return OperationalEvent(
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        source=EventSource.AUTOMATION,
        category=EventCategory.ALARM,
        severity=EventSeverity.ERROR,
        event_type="alarm.opened",
        correlation_id=uuid4(),
        entity=EntityContext(
            entity_type="alarm",
            entity_id="relay_mismatch",
            location="Veg Room",
            cluster="main",
        ),
        reason_code="observed_mismatch",
        reason_text="Observed relay output differs from the desired command.",
        payload=AlarmPayload(alarm_code="relay_mismatch", state="open"),
    )


@pytest.mark.asyncio
async def test_persist_retries_alarm_until_control_history_write_succeeds() -> None:
    # Given: an alarm whose history write fails twice before succeeding.
    repository = _AlarmRepository([False, False, True])
    delays: list[float] = []
    journal = AlarmJournal(repository, sleep=lambda delay: _record_delay(delays, delay))

    # When: the dispatcher invokes the secondary sink after Redis publication.
    await journal.persist(_alarm_event())

    # Then: only the failed writes consume the configured retry delays.
    assert len(repository.events) == 3
    assert delays == [0.1, 0.5]
    assert journal.persistence_failures == 0


@pytest.mark.asyncio
async def test_persist_skips_routine_events_and_counts_exhausted_alarm_failures() -> None:
    # Given: a routine event and an alarm repository that remains unavailable.
    repository = _AlarmRepository([False, False, False, False])
    delays: list[float] = []
    journal = AlarmJournal(repository, sleep=lambda delay: _record_delay(delays, delay))
    routine = _alarm_event().model_copy(
        update={
            "category": EventCategory.RELAY,
            "severity": EventSeverity.INFO,
            "event_type": "relay.state_changed",
            "payload": RelayPayload(state=True),
        }
    )

    # When: the dispatcher supplies both events to the secondary sink.
    await journal.persist(routine)
    await journal.persist(_alarm_event())

    # Then: routine events never reach PostgreSQL and a durable failure is observable.
    assert len(repository.events) == 4
    assert delays == [0.1, 0.5, 2.0]
    assert journal.persistence_failures == 1
