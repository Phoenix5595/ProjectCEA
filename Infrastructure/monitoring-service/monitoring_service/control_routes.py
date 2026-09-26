"""Read-only HTTP routes for recorded and published control monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError

from monitoring_service.control_models import (
    ControlHistoryRange,
    ControlHistoryEnvelope,
    ControlPublicationResponse,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
    RelayTimelineRange,
    RelayTimelineResponse,
)
from monitoring_service.relay_timeline import (
    MAX_RELAY_TIMELINE_LIMIT,
    RelayTimelineCursorError,
)
from monitoring_service.sensor_models import resolve_room_metadata


class ControlReadService(Protocol):
    """Supply history and coherent shared-publication reads to HTTP handlers."""

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope: ...

    async def publications(self, location: str) -> ControlPublicationResponse: ...

    async def relay_timeline(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse: ...


def register_control_routes(app: FastAPI, reads: ControlReadService) -> None:
    """Register control read routes without exposing mutation or cursor APIs."""

    @app.get("/api/monitoring/control/{location}/history", response_model=ControlHistoryEnvelope)
    async def history(
        location: str,
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
        max_points: int | None = Query(default=None, ge=10, le=100_000),
    ) -> ControlHistoryEnvelope:
        """Return recorded control facts from the read-only database."""
        try:
            history_range = _history_range(start, end)
            return await reads.history(location, history_range, max_points)
        except (ConnectionError, OSError, RuntimeError):
            raise HTTPException(
                status_code=503, detail="control monitoring history is unavailable"
            ) from None

    @app.get("/api/monitoring/control/{location}/tail", response_model=ControlHistoryEnvelope)
    async def tail(
        location: str,
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
        max_points: int | None = Query(default=None, ge=10, le=100_000),
    ) -> ControlHistoryEnvelope:
        """Return one bounded live-poller page through the history read path."""
        _ = resolve_room_metadata(location)
        return await history(location, start, end, max_points)

    @app.get(
        "/api/monitoring/control/{location}/relay-timeline",
        response_model=RelayTimelineResponse,
    )
    async def relay_timeline(
        location: str,
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        limit: int = Query(default=MAX_RELAY_TIMELINE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> RelayTimelineResponse:
        """Return exact sample-time relay facts and a bounded page of history."""
        _ = resolve_room_metadata(location)
        history_range = _relay_timeline_range(start, end)
        if not 1 <= limit <= MAX_RELAY_TIMELINE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"limit must be between 1 and {MAX_RELAY_TIMELINE_LIMIT}",
            )
        try:
            return await reads.relay_timeline(location, history_range, limit, cursor)
        except RelayTimelineCursorError:
            raise HTTPException(status_code=400, detail="invalid relay timeline cursor") from None
        except (ConnectionError, OSError, RuntimeError):
            raise HTTPException(
                status_code=503, detail="relay timeline is unavailable"
            ) from None

    @app.get(
        "/api/monitoring/control/{location}/current", response_model=CurrentPublicationResponse
    )
    async def current(location: str) -> CurrentPublicationResponse:
        """Return current control facts only when paired publications agree."""
        try:
            return (await reads.publications(location)).current
        except (ConnectionError, OSError, RuntimeError):
            raise HTTPException(
                status_code=503, detail="control monitoring publication is unavailable"
            ) from None

    @app.get(
        "/api/monitoring/control/{location}/projection",
        response_model=ProjectionPublicationResponse,
    )
    async def projection(
        location: str,
    ) -> ProjectionPublicationResponse:
        """Return only the fresh, version-matched canonical future publication."""
        try:
            return (await reads.publications(location)).projection
        except (ConnectionError, OSError, RuntimeError):
            raise HTTPException(
                status_code=503, detail="control monitoring publication is unavailable"
            ) from None


def _history_range(start: datetime | None, end: datetime | None) -> ControlHistoryRange:
    if start is None and end is None:
        end = datetime.now(UTC)
        start = end - timedelta(hours=1)
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="start and end must be supplied together")
    if end <= start:
        # Pre-validate: pydantic wraps the model validator's ValueError, which
        # would escape as an unhandled 500 instead of a client-error response.
        raise HTTPException(status_code=400, detail="end must be later than start")
    return ControlHistoryRange(start=start, end=end)

def _relay_timeline_range(start: str | None, end: str | None) -> RelayTimelineRange:
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="start and end must be supplied together")
    try:
        parsed_start = _parse_aware_timestamp(start)
        parsed_end = _parse_aware_timestamp(end)
        return RelayTimelineRange(start=parsed_start, end=parsed_end)
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="relay timeline requires an aware 5-minute to 7-day range",
        ) from None


def _parse_aware_timestamp(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed
