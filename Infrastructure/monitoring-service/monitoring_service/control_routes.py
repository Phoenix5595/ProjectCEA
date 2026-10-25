"""Read-only HTTP routes for recorded and published control monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Protocol, cast

from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError
from monitoring_service.history_overview_models import (
    HistoryOverviewControlResponse,
    HistoryOverviewRange,
)

from monitoring_service.control_models import (
    CONTROL_CLIMATE_HISTORY_MAXIMUM,
    CONTROL_HISTORY_MAXIMUM,
    ControlHistoryRange,
    ControlHistoryEnvelope,
    ControlOverviewScope,
    ControlPublicationResponse,
    ControlReadScope,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
    RelayTimelineRange,
    RelayTimelineResponse,
)
from monitoring_service.relay_timeline import (
    MAX_RELAY_TIMELINE_LIMIT,
    RelayTimelineCursorError,
)
from monitoring_service.sensor_models import MonitoringValidationError, resolve_room_metadata

_CONTROL_HISTORY_SCOPES: Final = ("all", "room", "climate", "photoperiod")
_CONTROL_OVERVIEW_SCOPES: Final = ("all", "lights")


class ControlReadService(Protocol):
    """Supply history and coherent shared-publication reads to HTTP handlers."""

    async def history(
        self,
        location: str,
        history_range: ControlHistoryRange,
        max_points: int | None = None,
        scope: ControlReadScope = "all",
    ) -> ControlHistoryEnvelope: ...

    async def publications(self, location: str) -> ControlPublicationResponse: ...

    async def relay_timeline(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse: ...

    async def overview(
        self,
        location: str,
        history_range: HistoryOverviewRange,
        max_points: int = 600,
        scope: ControlOverviewScope = "all",
    ) -> HistoryOverviewControlResponse: ...


def register_control_routes(app: FastAPI, reads: ControlReadService) -> None:
    """Register control read routes without exposing mutation or cursor APIs."""

    @app.get(
        "/api/monitoring/control/{location}/overview",
        response_model=HistoryOverviewControlResponse,
    )
    async def overview(
        location: str,
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        max_points: int = Query(default=600, ge=10, le=4000),
        scope: str = Query(default="all"),
    ) -> HistoryOverviewControlResponse:
        control_scope = _overview_scope(scope)
        history_range = HistoryOverviewRange.from_absolute(start, end)
        history_range.require_control_minimum()
        try:
            return await reads.overview(location, history_range, max_points, control_scope)
        except (ConnectionError, OSError, RuntimeError):
            raise HTTPException(
                status_code=503,
                detail="history overview aggregate coverage is unavailable",
            ) from None

    @app.get("/api/monitoring/control/{location}/history", response_model=ControlHistoryEnvelope)
    async def history(
        location: str,
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
        max_points: int | None = Query(default=None, ge=10, le=100_000),
        scope: str = Query(default="all"),
    ) -> ControlHistoryEnvelope:
        """Return recorded control facts from the read-only database."""
        try:
            control_scope = _history_scope(scope)
            history_range = _history_range(start, end)
            _require_history_scope_range(control_scope, history_range)
            return await reads.history(location, history_range, max_points, control_scope)
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
        scope: str = Query(default="all"),
    ) -> ControlHistoryEnvelope:
        """Return one bounded live-poller page through the history read path."""
        _ = resolve_room_metadata(location)
        return await history(location, start, end, max_points, scope)

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
            raise HTTPException(status_code=503, detail="relay timeline is unavailable") from None

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


def _history_scope(scope: str) -> ControlReadScope:
    """Validate the recorded-control history/tail scope through the 400 contract."""
    if scope not in _CONTROL_HISTORY_SCOPES:
        raise MonitoringValidationError("scope must be 'all', 'room', 'climate' or 'photoperiod'")
    return cast(ControlReadScope, scope)


def _overview_scope(scope: str) -> ControlOverviewScope:
    """Validate the bounded-overview scope through the monitoring 400 contract."""
    if scope not in _CONTROL_OVERVIEW_SCOPES:
        raise MonitoringValidationError("scope must be 'all' or 'lights'")
    return cast(ControlOverviewScope, scope)


def _require_history_scope_range(
    scope: ControlReadScope, history_range: ControlHistoryRange
) -> None:
    """Reject detailed history/tail windows beyond the per-scope maximum."""
    maximum = (
        CONTROL_HISTORY_MAXIMUM if scope in ("all", "room") else CONTROL_CLIMATE_HISTORY_MAXIMUM
    )
    if history_range.end - history_range.start > maximum:
        raise MonitoringValidationError("range_too_long")


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
