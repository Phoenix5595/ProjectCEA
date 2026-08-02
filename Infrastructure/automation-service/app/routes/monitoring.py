"""Read-only control-monitoring HTTP contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.cluster_config import ensure_configured_cluster
from app.database import DatabaseManager
from app.repositories.monitoring_history import MonitoringHistoryRepository
from app.repositories.monitoring_snapshot import MonitoringSnapshotRepository, SnapshotRedis
from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.monitoring import (
    ClimateTimelineSeries,
    ControlMonitoringResponse,
    FlushHealth,
    PhotoperiodTimelinePoint,
    SourceCursor,
)
from app.schemas.monitoring_models import MonitoringRange, OpaqueCursorId
from app.services.climate_projection import project_climate_timelines
from app.services.light_projection import LightProjection, project_lights

router = APIRouter(tags=["monitoring"])

Cluster = "main"
ProjectionClock = Callable[
    [MonitoringSnapshot, Callable[[], datetime]], tuple[ClimateTimelineSeries, ...]
]
ReconcileCallback = Callable[[str, str, MonitoringRange], Awaitable[ControlMonitoringResponse]]


class LoggerHealthProvider(Protocol):
    """Expose persisted photoperiod logger health without mutation."""

    def flush_health(self) -> tuple[FlushHealth, ...]: ...


class LightProjectionService(Protocol):
    """Project lights with the request's single captured UTC clock."""

    def __call__(self, snapshot: MonitoringSnapshot, *, now: datetime) -> LightProjection: ...


def get_database() -> DatabaseManager:
    """Provide the initialized database manager through the application container."""
    raise RuntimeError("monitoring database dependency is not configured")


def get_automation_redis() -> SnapshotRedis | None:
    """Provide the optional automation Redis client through the container."""
    raise RuntimeError("monitoring Redis dependency is not configured")


def get_logger_health_provider() -> LoggerHealthProvider:
    """Provide the initialized photoperiod logger health source."""
    raise RuntimeError("monitoring logger health dependency is not configured")


def get_snapshot_repository(
    database: Annotated[DatabaseManager, Depends(get_database)],
    redis: Annotated[SnapshotRedis | None, Depends(get_automation_redis)],
) -> MonitoringSnapshotRepository:
    """Build the read-only projection snapshot repository."""
    return MonitoringSnapshotRepository(db_manager=database, redis=redis)


def get_history_repository(
    database: Annotated[DatabaseManager, Depends(get_database)],
    logger_health: Annotated[LoggerHealthProvider, Depends(get_logger_health_provider)],
) -> MonitoringHistoryRepository:
    """Build the read-only historical monitoring repository."""
    return MonitoringHistoryRepository(db_manager=database, health_provider=logger_health)


def get_climate_projection() -> ProjectionClock:
    """Provide the pure climate projection service."""
    return project_climate_timelines


def get_light_projection() -> LightProjectionService:
    """Provide a clock-bound pure light projection service."""
    return project_lights


def get_reconcile_callback(
    history: Annotated[MonitoringHistoryRepository, Depends(get_history_repository)],
) -> ReconcileCallback:
    """Reload one bounded visible range after a tail continuity signal."""
    return history.load_initial_range


def _monitoring_range(start: str | None, end: str | None) -> MonitoringRange:
    if start is None and end is None:
        now = datetime.now(UTC)
        return MonitoringRange.from_absolute(now - timedelta(hours=1), now)
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="start and end must be supplied together")
    try:
        return MonitoringRange.from_absolute(
            datetime.fromisoformat(start.replace("Z", "+00:00")),
            datetime.fromisoformat(end.replace("Z", "+00:00")),
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _validate_location(location: str) -> None:
    ensure_configured_cluster(None, location, Cluster)


def _tail_cursors(values: Mapping[str, str | None]) -> dict[str, int]:
    if any(value is None for value in values.values()):
        raise HTTPException(status_code=400, detail="all per-source cursors are required")
    try:
        cursors = {source: int(value) for source, value in values.items() if value is not None}
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="monitoring cursors must be opaque numeric IDs"
        ) from error
    if any(value < 0 for value in cursors.values()):
        raise HTTPException(status_code=400, detail="monitoring cursors must not be negative")
    return cursors


def _projection_response(
    snapshot: MonitoringSnapshot,
    climate: tuple[ClimateTimelineSeries, ...],
    lights: LightProjection,
    health: LoggerHealthProvider,
    now: datetime,
) -> ControlMonitoringResponse:
    future_start = max(snapshot.range.start, now)
    photoperiod = tuple(
        PhotoperiodTimelinePoint(
            timestamp=max(interval.start, future_start),
            phase=interval.phase,
            provenance=interval.provenance,
            runtime_snapshot_version=snapshot.runtime_snapshot_version,
        )
        for interval in lights.photoperiod
        if interval.end > future_start
    )
    return ControlMonitoringResponse(
        range=snapshot.range,
        runtime_snapshot_version=snapshot.runtime_snapshot_version,
        cursors=tuple(
            SourceCursor(
                source=source,
                cursor=None if value is None else OpaqueCursorId(str(value)),
                has_more=False,
            )
            for source, value in snapshot.source_cursors
        ),
        flush_health=health.flush_health(),
        climate=tuple(
            series.model_copy(
                update={
                    "points": tuple(
                        point for point in series.points if point.timestamp >= future_start
                    )
                }
            )
            for series in climate
        ),
        lights=tuple(
            series.model_copy(
                update={
                    "points": tuple(
                        point for point in series.points if point.timestamp >= future_start
                    )
                }
            )
            for series in lights.lights
        ),
        photoperiod=photoperiod,
    )


async def _reconcile_or_unavailable(
    callback: ReconcileCallback, location: str, monitoring_range: MonitoringRange
) -> ControlMonitoringResponse:
    try:
        return await callback(location, Cluster, monitoring_range)
    except (ConnectionError, OSError, RuntimeError):
        raise HTTPException(
            status_code=503, detail="control monitoring history is unavailable"
        ) from None


@router.get("/api/monitoring/control/{location}", response_model=ControlMonitoringResponse)
async def get_control_monitoring_range(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    history: MonitoringHistoryRepository = Depends(get_history_repository),
) -> ControlMonitoringResponse:
    """Return one atomic historical range and per-source tail high-water marks."""
    monitoring_range = _monitoring_range(start, end)
    _validate_location(location)
    try:
        return await history.load_initial_range(location, Cluster, monitoring_range)
    except (ConnectionError, OSError, RuntimeError):
        raise HTTPException(
            status_code=503, detail="control monitoring history is unavailable"
        ) from None


@router.get(
    "/api/monitoring/control/{location}/projection", response_model=ControlMonitoringResponse
)
async def get_control_projection(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    snapshot_repository: MonitoringSnapshotRepository = Depends(get_snapshot_repository),
    climate_projection: ProjectionClock = Depends(get_climate_projection),
    light_projection: LightProjectionService = Depends(get_light_projection),
    logger_health: LoggerHealthProvider = Depends(get_logger_health_provider),
) -> ControlMonitoringResponse:
    """Return only future climate, light, and photoperiod projections."""
    monitoring_range = _monitoring_range(start, end)
    _validate_location(location)
    now = datetime.now(UTC)
    try:
        snapshot = await snapshot_repository.load(location, Cluster, monitoring_range)
        return _projection_response(
            snapshot,
            climate_projection(snapshot, lambda: now),
            light_projection(snapshot, now=now),
            logger_health,
            now,
        )
    except (ConnectionError, OSError, RuntimeError):
        raise HTTPException(
            status_code=503, detail="control monitoring projection is unavailable"
        ) from None


@router.get("/api/monitoring/control/{location}/tail", response_model=ControlMonitoringResponse)
async def get_control_monitoring_tail(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    effective_setpoints_cursor: str | None = Query(default=None),
    automation_state_cursor: str | None = Query(default=None),
    photoperiod_history_cursor: str | None = Query(default=None),
    history: MonitoringHistoryRepository = Depends(get_history_repository),
    reconcile: ReconcileCallback = Depends(get_reconcile_callback),
) -> ControlMonitoringResponse:
    """Return bounded tail pages, reconciling one range after a continuity signal."""
    monitoring_range = _monitoring_range(start, end)
    _validate_location(location)
    try:
        cursors = _tail_cursors(
            {
                "effective_setpoints": effective_setpoints_cursor,
                "automation_state": automation_state_cursor,
                "photoperiod_history": photoperiod_history_cursor,
            }
        )
    except HTTPException as error:
        await _reconcile_or_unavailable(reconcile, location, monitoring_range)
        raise error
    try:
        response = await history.load_tails(location, Cluster, monitoring_range, cursors)
    except (ConnectionError, OSError, RuntimeError):
        return await _reconcile_or_unavailable(reconcile, location, monitoring_range)
    if any(item.dropped_rows > 0 for item in response.flush_health):
        return await _reconcile_or_unavailable(reconcile, location, monitoring_range)
    return response
