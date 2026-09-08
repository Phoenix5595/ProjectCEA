"""Minimal producer and durable-side-effect event capabilities."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.events.operational_models import OperationalEvent


@runtime_checkable
class OperationalEventSink(Protocol):
    """Non-blocking capability exposed to event producers."""

    def emit_nowait(self, event: OperationalEvent) -> None:
        """Queue an event without waiting on an operational dependency."""


@runtime_checkable
class OperationalEventSecondarySink(Protocol):
    """Optional durable side-effect capability composed by runtime wiring."""

    async def persist(self, event: OperationalEvent) -> None:
        """Persist one event after stream dispatch when configured."""


__all__ = ["OperationalEventSecondarySink", "OperationalEventSink"]
