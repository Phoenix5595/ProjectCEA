"""Read-only HTTP routes for recorded and published control monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from fastapi import FastAPI, HTTPException, Query

from monitoring_service.control_models import (
    ControlHistoryRange,
    ControlHistoryEnvelope,
    ControlPublicationResponse,
    CurrentPublicationResponse,
)


class ControlReadService(Protocol):
    """Supply history and coherent shared-publication reads to HTTP handlers."""

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope: ...

    async def publications(self, location: str) -> ControlPublicationResponse: ...


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
        response_model=ControlHistoryEnvelope,
    )
    async def projection(
        location: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ControlHistoryEnvelope:
        """Return the projected-future envelope for the chart pipeline.

        Projection timelines populate once automation's projection publication
        ships; until then an empty valid envelope keeps clients contract-clean.
        """
        history_range = _history_range(start, end)
        return ControlHistoryEnvelope(
            range=history_range,
            runtime_snapshot_version=0,
        )


def _history_range(start: datetime | None, end: datetime | None) -> ControlHistoryRange:
    if start is None and end is None:
        end = datetime.now(UTC)
        start = end - timedelta(hours=1)
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="start and end must be supplied together")
    return ControlHistoryRange(start=start, end=end)
