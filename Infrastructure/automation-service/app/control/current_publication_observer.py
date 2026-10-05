"""Pure current-fact snapshots offered after a successful control tick."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import math
from typing import Any, Protocol, TypeAlias

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
    monitoring_room_series_prefix,
    normalize_monitoring_segment,
)

RoomKey: TypeAlias = tuple[str, str]
DeviceKey: TypeAlias = tuple[str, str, str]
NumericValue: TypeAlias = int | float
CURRENT_FACT_FRESHNESS = timedelta(seconds=5)
_RAMP_METRICS: tuple[str, ...] = ("heating", "cooling", "vpd", "co2")


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
    active_profiles: Mapping[RoomKey, Mapping[str, Any]],
    ramp_remaining_seconds: Mapping[RoomKey, Mapping[str, NumericValue]],
) -> CurrentSnapshot | None:
    """Build one immutable snapshot exclusively from values already in process memory.

    ``active_profiles`` carries the captured tick identity per room; a
    nonempty metadata-only CurrentSnapshot is valid for all-NULL constant
    profiles. Neither valid profile facts nor numeric setpoint facts produce
    an empty snapshot instead. No fake numeric climate targets are added.
    """
    if (
        runtime_snapshot_version < 1
        or observed_at.tzinfo is None
        or len(f"{runtime_snapshot_version:x}") > 64
    ):
        return None
    if not effective_setpoints and not active_profiles:
        return None

    observed = observed_at.astimezone(UTC)
    valid_until = observed + CURRENT_FACT_FRESHNESS
    series: list[CurrentSeriesPoint] = []
    series_ids: set[str] = set()
    phases: set[PhotoperiodPhase] = set()
    valid_rooms = 0

    profile_rooms = active_profiles
    ramp_rooms = ramp_remaining_seconds
    room_keys: set[RoomKey] = set(effective_setpoints) | set(profile_rooms)
    for room_key in sorted(room_keys):
        room = _room_identifier(room_key)
        phase = photoperiod_phases.get(room_key)
        if room is None or phase is None:
            continue
        values = effective_setpoints.get(room_key)
        profile = profile_rooms.get(room_key, {})
        ramps = ramp_rooms.get(room_key, {})

        room_series_start = len(series)
        has_profile_identity = False
        has_numeric_target = False
        for name, value in sorted((values or {}).items()):
            normalized_name = normalize_monitoring_segment(name)
            if normalized_name is None:
                continue
            point = _point(
                f"{room}.setpoint.{normalized_name}",
                value,
                observed,
                valid_until,
                series_ids,
            )
            if point is not None:
                series.append(point)
                if normalized_name.startswith(("effective_", "nominal_")):
                    has_numeric_target = True

        mode_id = profile.get("mode_id")
        submode_id = profile.get("submode_id")
        valid_mode_id = isinstance(mode_id, int) and not isinstance(mode_id, bool) and mode_id > 0
        valid_submode_id = submode_id is None or (
            isinstance(submode_id, int) and not isinstance(submode_id, bool) and submode_id > 0
        )
        if valid_mode_id and valid_submode_id:
            point = _point(
                f"{room}.setpoint.profile_mode_id",
                mode_id,
                observed,
                valid_until,
                series_ids,
            )
            if point is not None:
                series.append(point)
                has_profile_identity = True
                # NULL base profiles omit the submode fact; a known mode with
                # an absent submode fact means its base profile.
                if isinstance(submode_id, int):
                    point = _point(
                        f"{room}.setpoint.profile_submode_id",
                        submode_id,
                        observed,
                        valid_until,
                        series_ids,
                    )
                    if point is not None:
                        series.append(point)

        for metric in _RAMP_METRICS:
            if metric not in ramps:
                continue
            normalized_metric = normalize_monitoring_segment(metric)
            if normalized_metric is None:
                continue
            point = _point(
                f"{room}.setpoint.ramp_remaining_seconds_{normalized_metric}",
                ramps[metric],
                observed,
                valid_until,
                series_ids,
            )
            if point is not None:
                series.append(point)

        if not has_profile_identity and not has_numeric_target:
            for point in series[room_series_start:]:
                series_ids.discard(point.series_id.value)
            del series[room_series_start:]
            continue
        valid_rooms += 1
        phases.add(phase)

    if valid_rooms == 0:
        return None

    for device_key, values in sorted(automation_context.items()):
        device = _device_identifier(device_key)
        if device is None:
            continue
        for name, value in sorted(values.items()):
            normalized_name = normalize_monitoring_segment(name)
            if normalized_name is None:
                continue
            point = _point(
                f"{device}.automation.{normalized_name}",
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
            continue
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
    return monitoring_room_series_prefix(room_key[0], room_key[1])


def _device_identifier(device_key: DeviceKey) -> str | None:
    room = _room_identifier(device_key[:2])
    device_segment = normalize_monitoring_segment(device_key[2])
    if room is None or device_segment is None:
        return None
    return f"{room}.device.{device_segment}"
