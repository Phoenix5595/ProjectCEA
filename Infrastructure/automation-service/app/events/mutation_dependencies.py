"""Route dependencies for persisted mutation event instrumentation."""

from __future__ import annotations

from typing import Final, final

from fastapi import Request

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.events.operational_ports import OperationalEventSink


@final
class NoopOperationalEventSink:
    """Safe default until Todo 15 installs the shared operational dispatcher."""

    def emit_nowait(self, event: OperationalEvent) -> None:
        """Discard an event when no runtime dispatcher has been wired."""
        del event


_NOOP_OPERATIONAL_EVENT_SINK: Final[OperationalEventSink] = NoopOperationalEventSink()


def get_mutation_event_sink() -> OperationalEventSink:
    """Provide an overridable non-blocking event sink for mutation routes."""
    return _NOOP_OPERATIONAL_EVENT_SINK


def get_mutation_request_context(request: Request) -> MutationRequestContext:
    """Provide an overridable request correlation and fixed API actor context."""
    return MutationRequestContext.from_request(request)


__all__ = [
    "NoopOperationalEventSink",
    "get_mutation_event_sink",
    "get_mutation_request_context",
]
