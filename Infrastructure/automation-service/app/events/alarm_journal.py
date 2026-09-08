"""Durable control-history secondary sink for operational alarm events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final, Protocol

import anyio

from app.events.operational_models import EventCategory, EventSeverity, OperationalEvent

_RETRY_DELAYS: Final[tuple[float, ...]] = (0.1, 0.5, 2.0)


class AlarmLifecycleRepository(Protocol):
    """Control-history capability required for durable alarm lifecycle writes."""

    async def record_alarm_lifecycle(self, event: OperationalEvent) -> bool:
        """Write an alarm lifecycle record using the reserved history channel."""


class AlarmJournal:
    """Persists alarm/error events after Redis dispatch without blocking it."""

    def __init__(
        self,
        repository: AlarmLifecycleRepository,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._repository = repository
        self._sleep = sleep
        self._persistence_failures = 0

    @property
    def persistence_failures(self) -> int:
        """Return the number of alarm records that exhausted all retries."""
        return self._persistence_failures

    async def persist(self, event: OperationalEvent) -> None:
        """Persist only alarm/error events after their Redis publication succeeds."""
        if event.category is not EventCategory.ALARM and event.severity is not EventSeverity.ERROR:
            return

        for delay in _RETRY_DELAYS:
            if await self._repository.record_alarm_lifecycle(event):
                return
            await self._sleep(delay)

        if await self._repository.record_alarm_lifecycle(event):
            return
        self._persistence_failures += 1


__all__ = ["AlarmJournal", "AlarmLifecycleRepository"]
