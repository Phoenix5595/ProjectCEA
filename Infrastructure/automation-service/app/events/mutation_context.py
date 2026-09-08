"""Request-scoped context and post-persistence mutation event emission."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, TypeAlias, assert_never
from uuid import UUID, uuid4

from fastapi import Request, Response

from app.events.operational_models import (
    ActorContext,
    ActorType,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    FieldChange,
    MutationPayload,
    OperationalEvent,
)
from app.events.operational_ports import OperationalEventSink

RequestHandler: TypeAlias = Callable[[Request], Awaitable[Response]]
MutationOperation: TypeAlias = Literal["create", "update", "delete", "action"]
REQUEST_ID_HEADER: Final = "X-Request-ID"
_mutation_context: ContextVar[MutationRequestContext | None] = ContextVar(
    "mutation_request_context", default=None
)


@dataclass(frozen=True, slots=True)
class MutationRequestContext:
    """Correlation and non-identifying API actor context for one request."""

    correlation_id: UUID
    actor_id: Literal["api_client"] = "api_client"

    @classmethod
    def create(cls) -> MutationRequestContext:
        """Create the API context for a request without a valid correlation header."""
        return cls(correlation_id=uuid4())

    @staticmethod
    def parse_correlation_id(value: str | None) -> UUID:
        """Use a valid supplied correlation ID or generate a fresh request identity."""
        if value is None:
            return uuid4()
        try:
            return UUID(value)
        except ValueError:
            return uuid4()

    @classmethod
    def from_request(cls, request: Request) -> MutationRequestContext:
        """Create the fixed actor context using only the request correlation header."""
        return cls(correlation_id=cls.parse_correlation_id(request.headers.get(REQUEST_ID_HEADER)))

    @property
    def actor(self) -> ActorContext:
        """Return the non-identifying actor retained in every API mutation event."""
        return ActorContext(actor_type=ActorType.SERVICE, actor_id=self.actor_id)


@dataclass(frozen=True, slots=True)
class PersistedMutation:
    """Typed evidence supplied only after an authoritative persistence succeeds."""

    operation: MutationOperation
    entity: EntityContext
    changes: tuple[FieldChange, ...]
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MissingMutationRequestContextError(Exception):
    """Raised when a route attempts emission without the request middleware."""

    def __str__(self) -> str:
        return "persisted mutation emission requires request context middleware"


@contextmanager
def use_mutation_context(context: MutationRequestContext) -> Iterator[None]:
    """Install request context for a route execution or focused test."""
    token = _mutation_context.set(context)
    try:
        yield
    finally:
        _mutation_context.reset(token)


async def mutation_context_middleware(request: Request, call_next: RequestHandler) -> Response:
    """Provide correlation context without reading request or response bodies."""
    context = MutationRequestContext.from_request(request)
    with use_mutation_context(context):
        response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = str(context.correlation_id)
    return response


def emit_persisted_mutation(
    sink: OperationalEventSink,
    mutation: PersistedMutation,
    context: MutationRequestContext | None = None,
) -> bool:
    """Emit one safe event after persistence only when an allowlisted value changed."""
    if not mutation.changes:
        return False
    emission_context = context if context is not None else _current_mutation_context()
    sink.emit_nowait(
        OperationalEvent(
            occurred_at=mutation.occurred_at or datetime.now(UTC),
            source=EventSource.API,
            category=EventCategory.MUTATION,
            severity=EventSeverity.INFO,
            event_type=_mutation_event_type(mutation.operation),
            correlation_id=emission_context.correlation_id,
            entity=mutation.entity,
            actor=emission_context.actor,
            payload=MutationPayload(operation=mutation.operation, changes=mutation.changes),
        )
    )
    return True


def _current_mutation_context() -> MutationRequestContext:
    context = _mutation_context.get()
    if context is None:
        raise MissingMutationRequestContextError()
    return context


def _mutation_event_type(operation: MutationOperation) -> str:
    match operation:
        case "create":
            return "mutation.created"
        case "update":
            return "mutation.updated"
        case "delete":
            return "mutation.deleted"
        case "action":
            return "mutation.action_completed"
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "MissingMutationRequestContextError",
    "MutationOperation",
    "MutationRequestContext",
    "PersistedMutation",
    "emit_persisted_mutation",
    "mutation_context_middleware",
    "use_mutation_context",
]
