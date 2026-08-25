"""Independent legacy-compatible sensor monitoring routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from fastapi import APIRouter, Depends, Query

from monitoring_service.sensor_models import (
    LiveSensorValue,
    MonitoringMetadata,
    MonitoringRange,
    MonitoringResponse,
    MonitoringValidationError,
    SensorSeries,
    SensorStatistics,
    Tier,
    compute_tier,
    derive_interval_seconds,
    resolve_room_metadata,
    source_bucket_seconds,
)

router = APIRouter(tags=["sensor-monitoring"])


class SensorReads(Protocol):
    async def series(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[Tier, tuple[SensorSeries, ...]]: ...
    async def statistics(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[SensorStatistics, ...]: ...
    async def live(self, room: str, node: str) -> tuple[LiveSensorValue, ...]: ...


def _range(start: str | None, end: str | None) -> MonitoringRange:
    if start is None and end is None:
        return MonitoringRange.from_now(timedelta(hours=1))
    if start is None or end is None:
        raise MonitoringValidationError("start and end must be supplied together")
    return MonitoringRange.from_absolute(start, end)


def get_sensor_reads() -> SensorReads:
    """Resolve the runtime-owned read repository."""
    from monitoring_service.main import sensor_reads

    if sensor_reads is None:
        raise MonitoringValidationError("sensor monitoring resources are unavailable")
    return sensor_reads


@router.get("/api/sensors/monitoring/range/{location}", response_model=MonitoringResponse)
async def range_read(
    location: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    max_points: int | None = Query(default=None, ge=10, le=100_000),
    reads: SensorReads = Depends(get_sensor_reads),
) -> MonitoringResponse:
    monitoring_range = _range(start, end)
    tier, series = await reads.series(location, monitoring_range)
    return MonitoringResponse(
        metadata=MonitoringMetadata(
            generated_at=datetime.now(UTC),
            tier=tier,
            range=monitoring_range,
            room=resolve_room_metadata(location),
            requested_max_points=max_points,
            interval_seconds=derive_interval_seconds(
                monitoring_range.duration, source_bucket_seconds(tier), max_points
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
    tier = compute_tier(monitoring_range.duration)
    return MonitoringResponse(
        metadata=MonitoringMetadata(
            generated_at=datetime.now(UTC),
            tier=tier,
            range=monitoring_range,
            room=resolve_room_metadata(location),
            requested_max_points=max_points,
            interval_seconds=derive_interval_seconds(
                monitoring_range.duration, source_bucket_seconds(tier), max_points
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
