"""Pure current-fact snapshots offered after a successful control tick."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import math
import re
from typing import Protocol, TypeAlias

from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    PersistenceCursor,
    PersistenceState,
    Photoperiod,
    PhotoperiodPhase,
    ProjectionRevision,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
)

RoomKey: TypeAlias = tuple[str, str]
DeviceKey: TypeAlias = tuple[str, str, str]
NumericValue: TypeAlias = int | float
CURRENT_FACT_FRESHNESS = timedelta(seconds=5)
_SEGMENT = re.compile(r"[^a-z0-9]+")


class ControlTickObserver(Protocol):
    """Synchronously retain a completed control tick without changing control."""

    def offer(self, snapshot: CurrentSnapshot) -> None:
        """Accept one immutable snapshot without waiting or performing I/O."""
        ...


def build_current_snapshot(
    *,
    effective_setpoints: Mapping[RoomKey, Mapping[str, NumericValue | None]],
    automation_context: Mapping[DeviceKey, Mapping[str, NumericValue | None]],
    relay_states: Mapping[DeviceKey, int],
    photoperiod_phases: Mapping[RoomKey, PhotoperiodPhase],
    runtime_snapshot_version: int,
    observed_at: datetime,
) -> CurrentSnapshot | None:
    """Build one immutable snapshot exclusively from values already in process memory."""
    if (
        runtime_snapshot_version < 1
        or observed_at.tzinfo is None
        or not effective_setpoints
        or len(f"{runtime_snapshot_version:x}") > 64
    ):
        return None

    observed = observed_at.astimezone(UTC)
    valid_until = observed + CURRENT_FACT_FRESHNESS
    series: list[CurrentSeriesPoint] = []
    series_ids: set[str] = set()
    phases: set[PhotoperiodPhase] = set()
    setpoint_count = 0

    for room_key, values in sorted(effective_setpoints.items()):
        room = _room_identifier(room_key)
        phase = photoperiod_phases.get(room_key)
        if room is None or phase is None:
            return None
        phases.add(phase)
        for name, value in sorted(values.items()):
            point = _point(
                f"{room}.setpoint.{_segment_identifier(name)}",
                value,
                observed,
                valid_until,
                series_ids,
            )
            if point is not None:
                series.append(point)
                setpoint_count += 1

    if setpoint_count == 0:
        return None

    for device_key, values in sorted(automation_context.items()):
        device = _device_identifier(device_key)
        if device is None:
            return None
        for name, value in sorted(values.items()):
            point = _point(
                f"{device}.automation.{_segment_identifier(name)}",
                value,
                observed,
                valid_until,
                series_ids,
            )
            if point is not None:
                series.append(point)

    for device_key, state in sorted(relay_states.items()):
        device = _device_identifier(device_key)
        if device is None:
            return None
        point = _point(f"{device}.relay_state", state, observed, valid_until, series_ids)
        if point is not None:
            series.append(point)

    photoperiod = _photoperiod(phases, observed, valid_until)
    return CurrentSnapshot(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(runtime_snapshot_version),
            revision=ProjectionRevision(f"{runtime_snapshot_version:07x}"),
        ),
        observed_at=observed,
        valid_until=valid_until,
        series=tuple(series),
        photoperiod=photoperiod,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def _point(
    series_id: str,
    value: NumericValue | None,
    observed_at: datetime,
    valid_until: datetime,
    series_ids: set[str],
) -> CurrentSeriesPoint | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    if series_id in series_ids:
        return None
    series_ids.add(series_id)
    return CurrentSeriesPoint(
        series_id=SemanticSeriesId(value=series_id),
        value=float(value),
        quality=Quality.EXACT,
        observed_at=observed_at,
        valid_until=valid_until,
    )


def _photoperiod(
    phases: set[PhotoperiodPhase], observed_at: datetime, valid_until: datetime
) -> Photoperiod | None:
    if len(phases) != 1:
        return None
    return Photoperiod(
        phase=next(iter(phases)),
        quality=Quality.EXACT,
        observed_at=observed_at,
        valid_until=valid_until,
    )


def _room_identifier(room_key: RoomKey) -> str | None:
    location, cluster = room_key
    location_segment = _segment_identifier(location)
    cluster_segment = _segment_identifier(cluster)
    if location_segment is None or cluster_segment is None:
        return None
    return f"{location_segment}.{cluster_segment}"


def _device_identifier(device_key: DeviceKey) -> str | None:
    room = _room_identifier(device_key[:2])
    device_segment = _segment_identifier(device_key[2])
    if room is None or device_segment is None:
        return None
    return f"{room}.device.{device_segment}"


def _segment_identifier(value: str) -> str | None:
    normalized = _SEGMENT.sub("_", value.lower()).strip("_")
    if not normalized:
        return None
    return f"v_{normalized}" if normalized[0].isdigit() else normalized
