"""Safe, request-scoped timing events for monitoring database reads."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from types import TracebackType
from typing import Literal, Protocol, TypedDict, TypeVar

from shared.infra_logging import get_logger

logger = get_logger(__name__)

EventName = Literal["db_acquire", "db_query", "request_summary"]
Outcome = Literal["ok", "timeout", "error"]
StatementKind = Literal["SELECT", "WITH", "other"]
T = TypeVar("T")


class ObservationFields(TypedDict):
    event: EventName
    duration_ms: float
    outcome: Outcome
    statement_kind: StatementKind | None
    row_count: int | None
    tier: str | None
    query_count: int | None
    total_duration_ms: float | None


class AcquiredResource(Protocol[T]):
    async def __aenter__(self) -> T: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class QueryObservationEvent:
    """The bounded, secret-free event schema emitted at DEBUG level."""

    event: EventName
    duration_ms: float
    outcome: Outcome
    statement_kind: StatementKind | None = None
    row_count: int | None = None
    tier: str | None = None
    query_count: int | None = None
    total_duration_ms: float | None = None

    def fields(self) -> ObservationFields:
        """Return the fields consumed by shared JSON structured logging."""
        return {
            "event": self.event,
            "duration_ms": self.duration_ms,
            "outcome": self.outcome,
            "statement_kind": self.statement_kind,
            "row_count": self.row_count,
            "tier": self.tier,
            "query_count": self.query_count,
            "total_duration_ms": self.total_duration_ms,
        }


class _RequestCollector:
    """Mutable request-local accumulator used only while its context is active."""

    def __init__(self, tier: str | None) -> None:
        self.tier = _safe_tier(tier)
        self.query_count = 0
        self.total_duration_ms = 0.0

    def add_query(self, duration_ms: float) -> None:
        self.query_count += 1
        self.total_duration_ms = round(self.total_duration_ms + duration_ms, 1)


_request_collector: ContextVar[_RequestCollector | None] = ContextVar(
    "monitoring_query_observation", default=None
)


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 1)


def _outcome(exception: BaseException | None) -> Outcome:
    match exception:
        case None:
            return "ok"
        case TimeoutError():
            return "timeout"
        case _:
            return "error"


def _safe_tier(tier: str | None) -> str | None:
    match tier:
        case "raw":
            return "raw"
        case "1min":
            return "1min"
        case "5min":
            return "5min"
        case _:
            return None


def _statement_kind(query: str) -> StatementKind:
    match query.lstrip().split(maxsplit=1):
        case ["SELECT", *_] | ["select", *_]:
            return "SELECT"
        case ["WITH", *_] | ["with", *_]:
            return "WITH"
        case _:
            return "other"


def _emit(event: QueryObservationEvent) -> None:
    logger.debug("monitoring query observation", extra={"extra": event.fields()})


@asynccontextmanager
async def request_observation(tier: str | None = None) -> AsyncIterator[None]:
    """Aggregate query timing and emit one request summary when the read returns."""
    collector = _RequestCollector(tier)
    token = _request_collector.set(collector)
    started_at = perf_counter()
    try:
        yield
    finally:
        outcome = _outcome(sys.exception())
        _request_collector.reset(token)
        _emit(
            QueryObservationEvent(
                event="request_summary",
                duration_ms=_duration_ms(started_at),
                outcome=outcome,
                tier=collector.tier,
                query_count=collector.query_count,
                total_duration_ms=collector.total_duration_ms,
            )
        )


@asynccontextmanager
async def observe_acquire(resource: AcquiredResource[T]) -> AsyncIterator[T]:
    """Measure pool acquisition while preserving the resource's exit behavior."""
    started_at = perf_counter()
    try:
        connection = await resource.__aenter__()
    finally:
        _emit(
            QueryObservationEvent(
                event="db_acquire",
                duration_ms=_duration_ms(started_at),
                outcome=_outcome(sys.exception()),
            )
        )
    try:
        yield connection
    finally:
        exc_type, exc_value, traceback = sys.exc_info()
        await resource.__aexit__(exc_type, exc_value, traceback)


class QueryExecution:
    """Time a single fetch call and attach its safe count to the active request."""

    def __init__(self, query: str) -> None:
        self._statement_kind = _statement_kind(query)
        self._row_count: int | None = None
        self._started_at = 0.0

    async def __aenter__(self) -> QueryExecution:
        self._started_at = perf_counter()
        return self

    def set_row_count(self, row_count: int) -> None:
        """Store the bounded row count after a successful driver fetch."""
        self._row_count = row_count

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        duration_ms = _duration_ms(self._started_at)
        outcome = _outcome(exc_value)
        collector = _request_collector.get()
        tier = None if collector is None else collector.tier
        _emit(
            QueryObservationEvent(
                event="db_query",
                duration_ms=duration_ms,
                outcome=outcome,
                statement_kind=self._statement_kind,
                row_count=self._row_count if outcome == "ok" else None,
                tier=tier,
            )
        )
        if collector is not None:
            collector.add_query(duration_ms)
