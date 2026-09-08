from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.events.operational_models import (
    AlarmPayload,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
)
from app.repositories.control_actions import ControlActionRepository


class _Connection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.queries: list[str] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, query: str, *params: Any) -> None:
        self.executions.append((query, params))

    async def fetch(self, query: str, *_params: Any) -> list[Any]:
        self.queries.append(query)
        return []


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    def acquire(self) -> AbstractAsyncContextManager[_Connection]:
        return self.connection


@pytest.fixture
def alarm_event() -> OperationalEvent:
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
async def test_alarm_lifecycle_uses_reserved_channel_and_preserves_correlation(
    alarm_event: OperationalEvent,
) -> None:
    # Given: a control-history repository and a lifecycle alarm event.
    pool = _Pool()
    repository = ControlActionRepository(pool)

    # When: the durable alarm seam records the event.
    persisted = await repository.record_alarm_lifecycle(alarm_event)

    # Then: the row is distinct from relay history and retains its correlation in reason text.
    assert persisted is True
    query, params = pool.connection.executions[0]
    assert params[2] == "alarm:relay_mismatch"
    assert "-1" in query
    assert params[3:5] == (0, 1)
    assert params[5] == "alarm:open:error"
    assert str(alarm_event.correlation_id) in params[6]


@pytest.mark.asyncio
async def test_relay_history_queries_exclude_reserved_alarm_channel() -> None:
    # Given: the ordinary relay read repository.
    pool = _Pool()
    repository = ControlActionRepository(pool)

    # When: callers request recent and filtered relay history.
    await repository.get_recent_control_history("Veg Room", "main")
    await repository.get_control_history_filtered("Veg Room", "main")

    # Then: neither query can surface channel -1 alarm rows.
    assert all("channel >= 0" in query for query in pool.connection.queries)
