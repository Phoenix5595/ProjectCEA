"""Authenticated, read-only climate timeline routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import DatabaseManager
from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
from app.redis.monitoring import RedisRichTrajectoryReader
from app.repositories.climate_timeline_apply import (
    TimelineApplyRepository,
    TimelineApplyStaleRevisionError,
)
from app.repositories.climate_timeline_snapshot import (
    ClimateScheduleSnapshotBuilder,
    TimelineWindow,
)
from app.repositories.monitoring_snapshot_sources import SavedTrajectorySnapshotSource
from app.routes.climate_periods import get_database
from app.schemas.climate_timeline import (
    TimelineApplyRequest,
    TimelineApplyResponse,
    TimelinePreviewRequest,
    TimelinePreviewResponse,
    TimelineSavedResponse,
)
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from app.services.climate_timeline_apply import (
    ClimateTimelineApplyService,
    SavedTimelineConfigurationInvalidator,
    TimelineApplyValidationError,
)
from app.services.climate_timeline_preview import (
    ClimateTimelinePreviewService,
    TimelinePreviewUnavailableError,
    TimelinePreviewValidationError,
)
from shared.cluster_topology import ClusterMismatchError, UnknownRoomError, assert_device_cluster

router = APIRouter(prefix="/api/climate-timeline", tags=["climate-timeline"])


def get_preview_service(
    database: Annotated[DatabaseManager, Depends(get_database)],
) -> ClimateTimelinePreviewService:
    """Provide the preview-only read authority; it has no mutation ports."""
    redis_client = database.automation_redis
    reader = (
        None
        if redis_client is None or redis_client.redis_client is None
        else RedisRichTrajectoryReader(redis_client.redis_client)
    )
    return ClimateTimelinePreviewService(
        ClimateScheduleSnapshotBuilder(
            SavedTrajectorySnapshotSource(
                database.room_mode_repo,
                database.climate_periods_repo,
                CalendarModeScheduler(database),
                database.pool,
            )
        ),
        saved_trajectory_reader=reader,
    )


def get_apply_service(
    database: Annotated[DatabaseManager, Depends(get_database)],
) -> ClimateTimelineApplyService:
    """Provide the transaction-owning Apply service and post-commit invalidator."""
    if database.pool is None:
        raise RuntimeError("database pool is unavailable")
    return ClimateTimelineApplyService(
        TimelineApplyRepository(database.pool), SavedTimelineConfigurationInvalidator()
    )


@router.get("/{location}/{cluster}", response_model=TimelineSavedResponse)
async def get_saved_climate_timeline(
    location: str,
    cluster: str,
    service: Annotated[ClimateTimelinePreviewService, Depends(get_preview_service)],
    database: Annotated[DatabaseManager, Depends(get_database)],
    start: Annotated[str, Query(min_length=1)],
    end: Annotated[str, Query(min_length=1)],
    timezone: Annotated[str, Query(min_length=1)],
) -> TimelineSavedResponse:
    """Read the saved timeline aggregate and rich trajectory for one window."""
    try:
        assert_device_cluster(location, cluster)
    except UnknownRoomError as error:
        raise HTTPException(status_code=404, detail="unknown timeline room") from error
    except ClusterMismatchError as error:
        raise HTTPException(
            status_code=400, detail={"code": "unauthorized_room", "hint": error.hint}
        ) from error
    try:
        start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if start_time.tzinfo is None or end_time.tzinfo is None or end_time <= start_time:
            raise ValueError("invalid saved timeline window")
        window = TimelineWindow(start_time.astimezone(UTC), end_time.astimezone(UTC), timezone)
        version_id = await database.config_repo.get_latest_config_version()
        revision = f"{version_id or 0:07x}"
        return await service.saved_response(location, cluster, window, revision)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid saved timeline window") from error
    except TimelinePreviewUnavailableError as error:
        raise HTTPException(
            status_code=409, detail={"code": "timeline_unavailable", "detail": error.detail}
        ) from error


@router.post("/{location}/{cluster}/preview", response_model=TimelinePreviewResponse)
async def preview_climate_timeline(
    location: str,
    cluster: str,
    request: TimelinePreviewRequest,
    service: Annotated[ClimateTimelinePreviewService, Depends(get_preview_service)],
) -> TimelinePreviewResponse:
    """Evaluate a validated complete draft without persistence or publication."""
    try:
        assert_device_cluster(location, cluster)
    except UnknownRoomError as error:
        raise HTTPException(status_code=404, detail="unknown timeline room") from error
    except ClusterMismatchError as error:
        raise HTTPException(
            status_code=400, detail={"code": "unauthorized_room", "hint": error.hint}
        ) from error
    try:
        return await service.preview(location, cluster, request)
    except TimelinePreviewValidationError as error:
        raise HTTPException(
            status_code=422, detail={"code": error.code, "errors": error.errors}
        ) from error
    except TimelinePreviewUnavailableError as error:
        raise HTTPException(
            status_code=409, detail={"code": "timeline_preview_unavailable", "detail": error.detail}
        ) from error


@router.post("/{location}/{cluster}/apply", response_model=TimelineApplyResponse)
@emits_operational_mutation
async def apply_climate_timeline(
    location: str,
    cluster: str,
    request: TimelineApplyRequest,
    service: Annotated[ClimateTimelineApplyService, Depends(get_apply_service)],
    context: Annotated[MutationRequestContext, Depends(get_mutation_request_context)],
    sink: Annotated[OperationalEventSink, Depends(get_mutation_event_sink)],
) -> TimelineApplyResponse:
    """Commit one reviewed timeline aggregate without touching light-intensity state."""
    try:
        assert_device_cluster(location, cluster)
    except UnknownRoomError as error:
        raise HTTPException(status_code=404, detail="unknown timeline room") from error
    except ClusterMismatchError as error:
        raise HTTPException(
            status_code=400, detail={"code": "unauthorized_room", "hint": error.hint}
        ) from error
    try:
        response = await service.apply(location, cluster, request)
    except TimelineApplyValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_timeline_apply", "errors": error.errors},
        ) from error
    except TimelineApplyStaleRevisionError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_timeline_revision",
                "request_id": request.request_id,
                "expected_config_revision": error.expected,
                "draft_revision": request.draft_revision,
            },
        ) from error
    _ = emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="climate_timeline",
                entity_id=f"{location}:{cluster}:{request.mode_id}:{request.submode_id}",
                location=location,
                cluster=cluster,
            ),
            changes=safe_allowlisted_diff(
                before={"config_revision": request.expected_config_revision},
                after={"config_revision": response.config_revision},
                allowed_fields=frozenset({"config_revision"}),
            ),
        ),
        context,
    )
    return response
