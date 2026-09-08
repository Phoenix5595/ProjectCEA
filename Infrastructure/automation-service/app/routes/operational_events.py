"""Authenticated cursor history and fetch-SSE routes for operational events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Final, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.events.operational_models import EventCategory, EventSeverity
from app.events.operational_stream import OperationalStreamEntry
from app.schemas.operational_events import (
    OperationalEventCursorReset,
    OperationalEventHistory,
    OperationalEventItem,
    OperationalEventScan,
)

router = APIRouter()

DEFAULT_HISTORY_LIMIT: Final[int] = 200
MAX_HISTORY_LIMIT: Final[int] = 500
MAX_HISTORY_SCAN: Final[int] = 5_000
HEARTBEAT_READS: Final[int] = 15
CURSOR_PATTERN: Final[str] = r"^\d+-\d+$"


class OperationalEventRouteReader(Protocol):
    """Read capability that Todo 15 binds to the live Redis stream resources."""

    async def cursor_bounds(self) -> tuple[str | None, str | None]: ...

    async def cursor_exists(self, cursor: str) -> bool: ...

    async def history(
        self, *, before: str | None, after: str | None, scan_limit: int
    ) -> tuple[OperationalStreamEntry, ...]: ...

    async def read(self, *, after_id: str) -> tuple[OperationalStreamEntry, ...]: ...


def get_operational_event_reader() -> OperationalEventRouteReader:
    """Provide the route reader after Todo 15 installs the runtime dependency."""
    raise RuntimeError("operational event reader dependency not injected")


@dataclass(frozen=True, slots=True)
class OperationalEventFilters:
    """Parsed optional filters applied identically to history and live replay."""

    location: str | None
    cluster: str | None
    category: EventCategory | None
    severity: EventSeverity | None
    event_type: str | None

    def matches(self, entry: OperationalStreamEntry) -> bool:
        entity = entry.event.entity
        if self.location is not None and (entity is None or entity.location != self.location):
            return False
        if self.cluster is not None and (entity is None or entity.cluster != self.cluster):
            return False
        if self.category is not None and entry.event.category is not self.category:
            return False
        if self.severity is not None and entry.event.severity is not self.severity:
            return False
        return self.event_type is None or entry.event.event_type == self.event_type


@router.get("/api/events/history", response_model=OperationalEventHistory)
async def operational_event_history(
    reader: Annotated[OperationalEventRouteReader, Depends(get_operational_event_reader)],
    before: Annotated[str | None, Query(pattern=CURSOR_PATTERN)] = None,
    after: Annotated[str | None, Query(pattern=CURSOR_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT,
    location: str | None = None,
    cluster: str | None = None,
    category: EventCategory | None = None,
    severity: EventSeverity | None = None,
    event_type: Annotated[str | None, Query(alias="type")] = None,
) -> OperationalEventHistory | JSONResponse:
    """Return one bounded event page in the direction selected by its cursor."""
    if before is not None and after is not None:
        raise HTTPException(status_code=422, detail="before and after are mutually exclusive")
    earliest, latest = await reader.cursor_bounds()
    cursor = before or after
    if cursor is not None:
        reset = await _cursor_reset(reader, cursor, earliest, latest)
        if reset is not None:
            return reset
    entries = await reader.history(before=before, after=after, scan_limit=MAX_HISTORY_SCAN)
    filters = OperationalEventFilters(location, cluster, category, severity, event_type)
    matched = tuple(entry for entry in entries if filters.matches(entry))
    page = matched[:limit]
    items = tuple(OperationalEventItem(redis_id=entry.id, event=entry.event) for entry in page)
    return OperationalEventHistory(
        items=items,
        newest_cursor=items[0].redis_id if items else None,
        oldest_cursor=items[-1].redis_id if items else None,
        earliest_cursor=earliest,
        has_more=len(matched) > limit or len(entries) == MAX_HISTORY_SCAN,
        scan=OperationalEventScan(scanned=len(entries), limit=MAX_HISTORY_SCAN),
    )


@router.get("/api/events/stream", response_model=None)
async def operational_event_stream(
    request: Request,
    reader: Annotated[OperationalEventRouteReader, Depends(get_operational_event_reader)],
    after: Annotated[str, Query(pattern=CURSOR_PATTERN)],
    location: str | None = None,
    cluster: str | None = None,
    category: EventCategory | None = None,
    severity: EventSeverity | None = None,
    event_type: Annotated[str | None, Query(alias="type")] = None,
) -> StreamingResponse | JSONResponse:
    """Replay strictly after a retained cursor, then tail the global event stream."""
    earliest, latest = await reader.cursor_bounds()
    reset = await _cursor_reset(reader, after, earliest, latest)
    if reset is not None:
        return reset
    filters = OperationalEventFilters(location, cluster, category, severity, event_type)
    return StreamingResponse(
        _sse_frames(request, reader, after, filters),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _cursor_reset(
    reader: OperationalEventRouteReader,
    cursor: str,
    earliest: str | None,
    latest: str | None,
) -> JSONResponse | None:
    if await reader.cursor_exists(cursor):
        return None
    if (
        earliest is not None
        and latest is not None
        and _cursor_number(cursor) < _cursor_number(earliest)
    ):
        payload = OperationalEventCursorReset(earliest_cursor=earliest, latest_cursor=latest)
        return JSONResponse(status_code=409, content=payload.model_dump())
    raise HTTPException(status_code=422, detail="operational event cursor does not exist")


async def _sse_frames(
    request: Request,
    reader: OperationalEventRouteReader,
    after: str,
    filters: OperationalEventFilters,
) -> AsyncIterator[bytes]:
    yield b": connected\n\n"
    cursor = after
    idle_reads = 0
    while not await request.is_disconnected():
        entries = await reader.read(after_id=cursor)
        if not entries:
            idle_reads += 1
            if idle_reads == HEARTBEAT_READS:
                yield b": heartbeat\n\n"
                idle_reads = 0
            continue
        idle_reads = 0
        for entry in entries:
            cursor = entry.id
            if filters.matches(entry):
                data = entry.event.model_dump_json()
                yield f"id: {entry.id}\nevent: operational_event\ndata: {data}\n\n".encode()
            else:
                yield f"id: {entry.id}\n\n".encode()


def _cursor_number(cursor: str) -> tuple[int, int]:
    milliseconds, sequence = cursor.split("-", maxsplit=1)
    return int(milliseconds), int(sequence)
