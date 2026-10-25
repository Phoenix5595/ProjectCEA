"""Independent legacy-compatible sensor monitoring routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query
from monitoring_service.history_overview_models import (
    HistoryOverviewRange,
    HistoryOverviewSensorResponse,
)

from monitoring_service.sensor_models import (
    LiveSensorValue,
    MonitoringMetadata,
    MonitoringRange,
    MonitoringResponse,
    MonitoringValidationError,
    NICE_INTERVAL_SECONDS,
    SensorSeries,
    SensorStatistics,
    Tier,
    resolve_tier,
    derive_interval_seconds,
    resolve_room_metadata,
    source_bucket_seconds,
)

router = APIRouter(tags=["sensor-monitoring"])


class SensorReads(Protocol):
    async def series(
        self,
        room: str,
        monitoring_range: MonitoringRange,
        max_points: int | None = None,
        interval_seconds: int | None = None,
    ) -> tuple[Tier, tuple[SensorSeries, ...]]: ...
    async def statistics(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[SensorStatistics, ...]: ...
    async def live(self, room: str, node: str) -> tuple[LiveSensorValue, ...]: ...
    async def overview(
        self, room: str, history_range: HistoryOverviewRange, max_points: int = 600
    ) -> HistoryOverviewSensorResponse: ...


def _range(start: str | None, end: str | None) -> MonitoringRange:
    if start is None and end is None:
        return MonitoringRange.from_now(timedelta(hours=1))
    if start is None or end is None:
        raise MonitoringValidationError("start and end must be supplied together")
    return MonitoringRange.from_absolute(start, end)


def _requested_interval_seconds(
    interval_seconds: int | None,
    monitoring_range: MonitoringRange,
    max_points: int | None,
) -> int | None:
    """Validate an explicitly requested bucket interval under the point budget."""
    if interval_seconds is None:
        return None
    if (
        max_points is None
        or interval_seconds not in NICE_INTERVAL_SECONDS
        or ceil(monitoring_range.duration.total_seconds() / interval_seconds) > max_points
    ):
        raise MonitoringValidationError("invalid_interval_seconds")
    return interval_seconds


def get_sensor_reads() -> SensorReads:
    """Resolve the runtime-owned read repository."""
    from monitoring_service.main import sensor_reads

    if sensor_reads is None:
        raise MonitoringValidationError("sensor monitoring resources are unavailable")
    return sensor_reads


@router.get(
    "/api/sensors/monitoring/overview/{location}",
    response_model=HistoryOverviewSensorResponse,
)
async def overview_read(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    max_points: int = Query(default=600, ge=10, le=4000),
    reads: SensorReads = Depends(get_sensor_reads),
) -> HistoryOverviewSensorResponse:
    history_range = HistoryOverviewRange.from_absolute(start, end)
    return await reads.overview(location, history_range, max_points)


@router.get("/api/sensors/monitoring/range/{location}", response_model=MonitoringResponse)
async def range_read(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    max_points: int | None = Query(default=None, ge=10, le=100_000),
    interval_seconds: Annotated[int | None, Query(ge=1)] = None,
    reads: SensorReads = Depends(get_sensor_reads),
) -> MonitoringResponse:
    monitoring_range = _range(start, end)
    requested_interval_seconds = _requested_interval_seconds(
        interval_seconds, monitoring_range, max_points
    )
    tier, series = await reads.series(
        location, monitoring_range, max_points, requested_interval_seconds
    )
    return MonitoringResponse(
        metadata=MonitoringMetadata(
            generated_at=datetime.now(UTC),
            tier=tier,
            range=monitoring_range,
            room=resolve_room_metadata(location),
            requested_max_points=max_points,
            requested_interval_seconds=requested_interval_seconds,
            interval_seconds=(
                requested_interval_seconds
                if requested_interval_seconds is not None
                else derive_interval_seconds(
                    monitoring_range.duration,
                    source_bucket_seconds(tier),
                    max_points,
                )
            ),
        ),
        series=series,
        statistics=await reads.statistics(location, monitoring_range),
    )


@router.get("/api/sensors/monitoring/stats/{location}", response_model=MonitoringResponse)
async def statistics_read(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    max_points: int | None = Query(default=None, ge=10, le=100_000),
    reads: SensorReads = Depends(get_sensor_reads),
) -> MonitoringResponse:
    monitoring_range = _range(start, end)
    tier = resolve_tier(monitoring_range.duration, max_points)
    return MonitoringResponse(
        metadata=MonitoringMetadata(
            generated_at=datetime.now(UTC),
            tier=tier,
            range=monitoring_range,
            room=resolve_room_metadata(location),
            requested_max_points=max_points,
            interval_seconds=derive_interval_seconds(
                monitoring_range.duration,
                source_bucket_seconds(tier),
                max_points,
            ),
        ),
        series=(),
        statistics=await reads.statistics(location, monitoring_range),
    )


@router.get(
    "/api/sensors/monitoring/live/{location}/{node}", response_model=tuple[LiveSensorValue, ...]
)
async def live_read(
    location: str, node: str, reads: SensorReads = Depends(get_sensor_reads)
) -> tuple[LiveSensorValue, ...]:
    return await reads.live(location, node)
