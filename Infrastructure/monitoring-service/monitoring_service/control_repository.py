"""Read-only control history and shared-publication repositories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, final

import asyncpg
from pydantic import ValidationError

from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.redis_resources import RedisReadClient
from monitoring_service.control_models import (
    ClimateTimelinePointOut,
    ClimateTimelineSeriesOut,
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
    CurrentPublicationResponse,
    DeviceTimelinePointOut,
    DeviceTimelineSeriesOut,
    LightTimelinePointOut,
    LightTimelineSeriesOut,
    PhotoperiodTimelinePointOut,
    PidTimelinePointOut,
    PidTimelineSeriesOut,
    ProjectionPublicationResponse,
    TimelineProvenanceModel,
)
from shared.monitoring_contracts import CurrentSnapshot, FutureProjection, Quality


class ControlHistoryDatabase(Protocol):
    """The parameterized read capability required for recorded history."""

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> Sequence[Mapping[str, str | float | datetime] | asyncpg.Record]: ...


class PublicationRedis(Protocol):
    """The atomic multi-key read capability required for shared publications."""

    def mget(self, keys: list[str]) -> Awaitable[list[str | None]]: ...


@final
class ControlHistoryRepository:
    """Load recorded control timelines solely from committed read-model facts."""

    def __init__(self, database: ControlHistoryDatabase) -> None:
        self._database = database

    async def read(
        self, location: str, history_range: ControlHistoryRange
    ) -> ControlHistoryEnvelope:
        """Return climate, light, device, PID, and photoperiod timelines for the window."""
        setpoint_rows = await self._database.fetch(
            _SETPOINTS_SQL, location, history_range.start, history_range.end
        )
        state_rows = await self._database.fetch(
            _STATE_SQL, location, history_range.start, history_range.end
        )
        photoperiod_rows = await self._database.fetch(
            _PHOTOPERIOD_SQL, location, history_range.start, history_range.end
        )
        return _build_envelope(history_range, setpoint_rows, state_rows, photoperiod_rows)


_RECORDED_PROVENANCE = {"origin": "recorded", "quality": Quality.EXACT, "is_aggregated": False}
_AGGREGATED_PROVENANCE = {"origin": "recorded", "quality": Quality.EXACT, "is_aggregated": True}

_CLIMATE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "heating_setpoint",
        "effective_heating_setpoint",
        "nominal_heating_setpoint",
        "ramp_progress_heating",
    ),
    (
        "cooling_setpoint",
        "effective_cooling_setpoint",
        "nominal_cooling_setpoint",
        "ramp_progress_cooling",
    ),
    (
        "humidity_setpoint",
        "effective_humidity_setpoint",
        "nominal_humidity_setpoint",
        "ramp_progress_humidity",
    ),
    ("co2_setpoint", "effective_co2_setpoint", "nominal_co2_setpoint", "ramp_progress_co2"),
    ("vpd_setpoint", "effective_vpd_setpoint", "nominal_vpd_setpoint", "ramp_progress_vpd"),
)

_SETPOINTS_SQL = """
SELECT timestamp, mode,
       effective_heating_setpoint, nominal_heating_setpoint, ramp_progress_heating,
       effective_cooling_setpoint, nominal_cooling_setpoint, ramp_progress_cooling,
       effective_humidity_setpoint, nominal_humidity_setpoint, ramp_progress_humidity,
       effective_co2_setpoint, nominal_co2_setpoint, ramp_progress_co2,
       effective_vpd_setpoint, nominal_vpd_setpoint, ramp_progress_vpd,
       device_name, effective_light_intensity, nominal_light_intensity, ramp_progress_light
FROM effective_setpoints
WHERE location = $1 AND timestamp >= $2 AND timestamp < $3
ORDER BY timestamp
"""

_STATE_SQL = """
SELECT bucket, device_name, device_state_last, device_mode_last, control_reason_last,
       pid_output_last, duty_cycle_percent_last
FROM monitoring_automation_state_1min
WHERE location = $1 AND bucket >= $2 AND bucket < $3 AND device_state_last IS NOT NULL
ORDER BY device_name, bucket
"""

_PHOTOPERIOD_SQL = """
SELECT observed_at, phase, mode_id, submode_id, runtime_snapshot_version
FROM monitoring_room_photoperiod
WHERE location = $1 AND observed_at >= $2 AND observed_at < $3
ORDER BY observed_at
"""


def _build_envelope(
    history_range: ControlHistoryRange,
    setpoint_rows: Sequence[Mapping[str, Any]],
    state_rows: Sequence[Mapping[str, Any]],
    photoperiod_rows: Sequence[Mapping[str, Any]],
) -> ControlHistoryEnvelope:
    provenance = TimelineProvenanceModel(**_RECORDED_PROVENANCE)
    agg_provenance = TimelineProvenanceModel(**_AGGREGATED_PROVENANCE)

    climate_builders: dict[str, list[ClimateTimelinePointOut]] = {}
    lights: dict[tuple[str, str], list[LightTimelinePointOut]] = {}
    for row in setpoint_rows:
        ts = row["timestamp"]
        mode = row["mode"]
        device_name = row["device_name"]
        for metric, eff_col, nom_col, ramp_col in _CLIMATE_FIELDS:
            effective = row[eff_col]
            if effective is None:
                continue
            climate_builders.setdefault(metric, []).append(
                ClimateTimelinePointOut(
                    timestamp=ts,
                    value=_required_float(effective),
                    provenance=provenance,
                    metric=metric,
                    nominal_value=_optional_float(row[nom_col]),
                    ramp_progress=_optional_float(row[ramp_col]),
                    mode=mode,
                    device_name=None,
                )
            )
        if device_name is not None and row["effective_light_intensity"] is not None:
            key = (str(device_name), str(mode or ""))
            lights.setdefault(key, []).append(
                LightTimelinePointOut(
                    timestamp=ts,
                    value=_required_float(row["effective_light_intensity"]),
                    provenance=provenance,
                    device_name=str(device_name),
                    nominal_value=_optional_float(row["nominal_light_intensity"]),
                    ramp_progress=_optional_float(row["ramp_progress_light"]),
                    mode=mode,
                )
            )

    climate = tuple(
        ClimateTimelineSeriesOut(name=metric, provenance=provenance, points=tuple(points))
        for metric, points in sorted(climate_builders.items())
    )
    light_series = tuple(
        LightTimelineSeriesOut(name=f"Light {device}", provenance=provenance, points=tuple(points))
        for (device, _mode), points in sorted(lights.items())
    )

    devices_by_name: dict[str, list[DeviceTimelinePointOut]] = {}
    pid_by_name: dict[str, list[PidTimelinePointOut]] = {}
    snapshot_versions: list[int] = []
    for row in state_rows:
        device = str(row["device_name"])
        reason = row["control_reason_last"]
        devices_by_name.setdefault(device, []).append(
            DeviceTimelinePointOut(
                timestamp=row["bucket"],
                provenance=agg_provenance,
                device_name=device,
                device_state=_required_float(row["device_state_last"]),
                device_mode=str(row["device_mode_last"] or "unknown"),
                control_reason=str(reason) if reason is not None else "unrecorded",
            )
        )
        pid_by_name.setdefault(device, []).append(
            PidTimelinePointOut(
                timestamp=row["bucket"],
                provenance=agg_provenance,
                device_name=device,
                pid_output=_optional_float(row["pid_output_last"]),
                duty_cycle_percent=_optional_float(row["duty_cycle_percent_last"]),
            )
        )

    photoperiod: list[PhotoperiodTimelinePointOut] = []
    for row in photoperiod_rows:
        phase = str(row["phase"])
        version_value = row["runtime_snapshot_version"]
        if isinstance(version_value, int):
            snapshot_versions.append(version_value)
        photoperiod.append(
            PhotoperiodTimelinePointOut(
                timestamp=row["observed_at"],
                provenance=agg_provenance,
                phase=phase if phase in ("SUN", "MOON", "UNKNOWN") else "UNKNOWN",
                mode_id=_optional_int(row["mode_id"]),
                submode_id=_optional_int(row["submode_id"]),
                runtime_snapshot_version=_optional_int(version_value),
            )
        )

    return ControlHistoryEnvelope(
        range=history_range,
        runtime_snapshot_version=max(snapshot_versions, default=0),
        cursors=(),
        flush_health=(),
        climate=climate,
        lights=light_series,
        devices=tuple(
            DeviceTimelineSeriesOut(name=name, provenance=agg_provenance, points=tuple(points))
            for name, points in sorted(devices_by_name.items())
        ),
        pid=tuple(
            PidTimelineSeriesOut(name=name, provenance=agg_provenance, points=tuple(points))
            for name, points in sorted(pid_by_name.items())
        ),
        photoperiod=tuple(photoperiod),
    )


def _required_float(value: object) -> float:
    return float(value)  # caller guarantees non-None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


@final
class ControlPublicationRepository:
    """Read paired current and future facts without recalculating automation state."""

    def __init__(
        self, redis: PublicationRedis, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._redis = redis
        self._clock = clock or (lambda: datetime.now(UTC))

    async def read(self, location: str) -> ControlPublicationResponse:
        """Return publications only when both authorities parse, share one version, and are valid."""
        current_payload, future_payload = await self._redis.mget(_publication_keys(location))
        current = _parse_current(current_payload)
        future = _parse_future(future_payload)
        if (
            current is None
            or future is None
            or any(item.version != current.version for item in future)
        ):
            return _unavailable_publication()
        now = self._clock()
        if current.valid_until <= now or any(item.valid_until <= now for item in future):
            return _unavailable_publication()
        return ControlPublicationResponse(
            current=CurrentPublicationResponse(quality=Quality.EXACT, value=current),
            projection=ProjectionPublicationResponse(quality=Quality.ESTIMATED, value=future),
        )


def _publication_keys(location: str) -> list[str]:
    return [f"cea:monitoring:current:{location}", f"cea:monitoring:future:{location}"]


def _parse_current(payload: str | None) -> CurrentSnapshot | None:
    if payload is None:
        return None
    try:
        return CurrentSnapshot.model_validate_json(payload)
    except ValidationError:
        return None


def _parse_future(payload: str | None) -> tuple[FutureProjection, ...] | None:
    if payload is None:
        return None
    try:
        return (FutureProjection.model_validate_json(payload),)
    except ValidationError:
        return None


def _unavailable_publication() -> ControlPublicationResponse:
    return ControlPublicationResponse(
        current=CurrentPublicationResponse(quality=Quality.UNAVAILABLE, value=None),
        projection=ProjectionPublicationResponse(quality=Quality.UNAVAILABLE, value=()),
    )


class RuntimeReadResources(Protocol):
    """Expose owned read clients while the application lifespan is active."""

    database: ReadOnlyDatabase | None
    redis_client: RedisReadClient | None


@final
class RuntimeControlReads:
    """Connect control repositories to the monitoring service's owned read clients."""

    def __init__(self, resources: RuntimeReadResources) -> None:
        self._resources = resources

    async def history(
        self, location: str, history_range: ControlHistoryRange
    ) -> ControlHistoryEnvelope:
        """Read recorded history only when the service database client is available."""
        database = self._resources.database
        if database is None:
            raise RuntimeError("monitoring database resource is unavailable")
        return await ControlHistoryRepository(database).read(location, history_range)

    async def publications(self, location: str) -> ControlPublicationResponse:
        """Read shared publications only when the service Redis client is available."""
        redis = self._resources.redis_client
        if redis is None:
            raise RuntimeError("monitoring Redis resource is unavailable")
        return await ControlPublicationRepository(redis).read(location)
