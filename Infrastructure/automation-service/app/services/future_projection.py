"""Pure adapter from existing monitoring timelines to bounded publication intervals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.schemas.monitoring import ClimateTimelinePoint, ClimateTimelineSeries, LightTimelinePoint
from app.schemas.monitoring_models import Phase
from app.schemas.monitoring_models import Quality as SourceQuality
from shared.monitoring_contracts import (
    FutureProjection,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
    validate_projection_timeline,
)

from .climate_projection import project_climate_timelines
from .light_projection import LightProjection, project_lights

MAX_PROJECTION_INTERVALS: Final = 256
_CLIMATE_SERIES: Final = {
    "heating": "climate.heating_setpoint_target",
    "cooling": "climate.cooling_setpoint_target",
    "vpd": "climate.vpd_setpoint_target",
    "co2": "climate.co2_setpoint_target",
}
_PHOTOPERIOD_SERIES: Final = "light.photoperiod"


def project_future_intervals(snapshot: MonitoringSnapshot) -> tuple[FutureProjection, ...]:
    """Adapt pure climate/light schedules to complete versioned intervals without I/O."""
    if snapshot.config_version is None:
        return ()
    version = PublicationVersion(
        contract_version=1,
        config_version=snapshot.config_version,
        revision=ProjectionRevision(snapshot.projection_revision),
    )
    climate = project_climate_timelines(snapshot, lambda: snapshot.range.start)
    lights = project_lights(snapshot, now=snapshot.range.start)
    boundaries = _boundaries(snapshot, climate, lights)
    if len(boundaries) - 1 > MAX_PROJECTION_INTERVALS:
        return _unavailable_timeline(snapshot, version)
    projections = tuple(
        FutureProjection(
            version=version,
            generated_at=snapshot.range.start,
            valid_from=start,
            valid_until=end,
            series=_series_at(snapshot, climate, lights, start, end),
        )
        for start, end in zip(boundaries, boundaries[1:], strict=False)
    )
    return validate_projection_timeline(projections)


def _boundaries(
    snapshot: MonitoringSnapshot,
    climate: tuple[ClimateTimelineSeries, ...],
    lights: LightProjection,
) -> tuple[datetime, ...]:
    instants = {snapshot.range.start, snapshot.range.end}
    for series in climate:
        instants.update(point.timestamp.astimezone(UTC) for point in series.points)
    for series in lights.lights:
        instants.update(point.timestamp.astimezone(UTC) for point in series.points)
    instants.update(interval.start.astimezone(UTC) for interval in lights.photoperiod)
    return tuple(
        sorted(
            instant for instant in instants if snapshot.range.start <= instant <= snapshot.range.end
        )
    )


def _series_at(
    snapshot: MonitoringSnapshot,
    climate: tuple[ClimateTimelineSeries, ...],
    lights: LightProjection,
    start: datetime,
    end: datetime,
) -> tuple[ProjectionSeriesPoint, ...]:
    series = [
        _point(_CLIMATE_SERIES[timeline.name], _latest_value(timeline.points, start), start, end)
        for timeline in climate
    ]
    photoperiod = next(
        interval for interval in lights.photoperiod if interval.start <= start < interval.end
    )
    phase_value = 1.0 if photoperiod.phase is Phase.SUN else 0.0
    series.append(
        _point(
            _PHOTOPERIOD_SERIES,
            (phase_value, photoperiod.provenance.quality),
            start,
            end,
        )
    )
    for timeline in lights.lights:
        series.append(
            _light_point(snapshot, timeline.name, _latest_value(timeline.points, start), start, end)
        )
    return tuple(series)


def _latest_value(
    points: tuple[ClimateTimelinePoint, ...] | tuple[LightTimelinePoint, ...], instant: datetime
) -> tuple[float | None, SourceQuality]:
    point = next(point for point in reversed(points) if point.timestamp <= instant)
    return point.value, point.provenance.quality


def _light_point(
    snapshot: MonitoringSnapshot,
    name: str,
    value: tuple[float | None, SourceQuality],
    start: datetime,
    end: datetime,
) -> ProjectionSeriesPoint:
    if snapshot.active_mode is None or not _has_light_target(snapshot, name):
        return _unavailable(_light_series_id(name), start, end)
    return _point(_light_series_id(name), value, start, end)


def _has_light_target(snapshot: MonitoringSnapshot, name: str) -> bool:
    light = next((row for row in snapshot.expected_lights if row.get("device_name") == name), None)
    return light is not None and any(
        target.get("device_id") == light.get("device_id") for target in snapshot.light_targets
    )


def _point(
    series_id: str, value: tuple[float | None, SourceQuality], start: datetime, end: datetime
) -> ProjectionSeriesPoint:
    number, source_quality = value
    if number is None or source_quality is SourceQuality.UNAVAILABLE:
        return _unavailable(series_id, start, end)
    return ProjectionSeriesPoint(
        series_id=SemanticSeriesId(value=series_id),
        value=number,
        quality=Quality.ESTIMATED,
        valid_from=start,
        valid_until=end,
    )


def _unavailable(series_id: str, start: datetime, end: datetime) -> ProjectionSeriesPoint:
    return ProjectionSeriesPoint(
        series_id=SemanticSeriesId(value=series_id),
        value=None,
        quality=Quality.UNAVAILABLE,
        valid_from=start,
        valid_until=end,
    )


def _unavailable_timeline(
    snapshot: MonitoringSnapshot, version: PublicationVersion
) -> tuple[FutureProjection, ...]:
    series_ids = (*_CLIMATE_SERIES.values(), _PHOTOPERIOD_SERIES, *_light_series_ids(snapshot))
    return (
        FutureProjection(
            version=version,
            generated_at=snapshot.range.start,
            valid_from=snapshot.range.start,
            valid_until=snapshot.range.end,
            series=tuple(
                _unavailable(series_id, snapshot.range.start, snapshot.range.end)
                for series_id in series_ids
            ),
        ),
    )


def _light_series_id(name: str) -> str:
    """Translate the existing device name to the dotted-lowercase contract namespace."""
    token = "".join(character if character.isalnum() else "_" for character in name.lower())
    return f"light.intensity.{token}"


def _light_series_ids(snapshot: MonitoringSnapshot) -> tuple[str, ...]:
    """Return only contract-safe identifiers for configured light names."""
    return tuple(
        _light_series_id(name)
        for light in snapshot.expected_lights
        if isinstance(name := light.get("device_name"), str)
    )
