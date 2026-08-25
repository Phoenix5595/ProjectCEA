from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

import asyncpg

from monitoring_service.control_models import (
    ClimateTimelinePointOut,
    ClimateTimelineSeriesOut,
    ControlHistoryEnvelope,
    ControlHistoryRange,
    DeviceTimelinePointOut,
    DeviceTimelineSeriesOut,
    LightTimelinePointOut,
    LightTimelineSeriesOut,
    PhotoperiodTimelinePointOut,
    PidTimelinePointOut,
    PidTimelineSeriesOut,
    TimelineProvenanceModel,
)
from monitoring_service.control_timeline_budget import budget_control_history
from shared.monitoring_contracts import Quality

ControlRecordValue = str | float | int | datetime | None
ControlRecord = Mapping[str, ControlRecordValue] | asyncpg.Record


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


def build_control_history_envelope(
    history_range: ControlHistoryRange,
    setpoint_rows: Sequence[ControlRecord],
    state_rows: Sequence[ControlRecord],
    photoperiod_rows: Sequence[ControlRecord],
    setpoints_are_aggregated: bool,
    max_points: int | None,
    interval_seconds: int | None,
) -> ControlHistoryEnvelope:
    provenance = TimelineProvenanceModel(
        origin="recorded", quality=Quality.EXACT, is_aggregated=setpoints_are_aggregated
    )
    unavailable_provenance = TimelineProvenanceModel(
        origin="recorded", quality=Quality.UNAVAILABLE, is_aggregated=setpoints_are_aggregated
    )
    aggregate_provenance = TimelineProvenanceModel(
        origin="recorded", quality=Quality.EXACT, is_aggregated=True
    )
    climate_builders, lights = _build_target_timelines(
        setpoint_rows, provenance, unavailable_provenance, max_points is not None
    )
    devices, pid = _build_device_timelines(state_rows, aggregate_provenance)
    photoperiod, snapshot_versions = _build_photoperiod(photoperiod_rows, aggregate_provenance)
    envelope = ControlHistoryEnvelope(
        range=history_range,
        runtime_snapshot_version=max(snapshot_versions, default=0),
        requested_max_points=max_points,
        interval_seconds=interval_seconds,
        climate=tuple(
            ClimateTimelineSeriesOut(name=metric, provenance=provenance, points=tuple(points))
            for metric, points in sorted(climate_builders.items())
        ),
        lights=tuple(
            LightTimelineSeriesOut(
                name=f"Light {device}", provenance=provenance, points=tuple(points)
            )
            for (device, _mode), points in sorted(lights.items())
        ),
        devices=devices,
        pid=pid,
        photoperiod=photoperiod,
    )
    return envelope if max_points is None else budget_control_history(envelope, max_points)


def _build_target_timelines(
    rows: Sequence[ControlRecord],
    provenance: TimelineProvenanceModel,
    unavailable_provenance: TimelineProvenanceModel,
    preserve_gaps: bool,
) -> tuple[
    dict[str, list[ClimateTimelinePointOut]],
    dict[tuple[str, str], list[LightTimelinePointOut]],
]:
    climate: dict[str, list[ClimateTimelinePointOut]] = {}
    lights: dict[tuple[str, str], list[LightTimelinePointOut]] = {}
    for row in rows:
        for metric, effective_column, nominal_column, ramp_column in _CLIMATE_FIELDS:
            effective = row[effective_column]
            if effective is not None:
                climate.setdefault(metric, []).append(
                    ClimateTimelinePointOut(
                        timestamp=_timestamp(row, "timestamp"),
                        value=_required_float(effective),
                        provenance=provenance,
                        metric=metric,
                        nominal_value=_optional_float(row[nominal_column]),
                        ramp_progress=_optional_float(row[ramp_column]),
                        mode=_optional_string(row["mode"]),
                    )
                )
            elif preserve_gaps and metric in climate:
                climate[metric].append(
                    ClimateTimelinePointOut(
                        timestamp=_timestamp(row, "timestamp"),
                        value=None,
                        provenance=unavailable_provenance,
                        metric=metric,
                        mode=_optional_string(row["mode"]),
                    )
                )
        device_name = row["device_name"]
        effective_light = row["effective_light_intensity"]
        if device_name is not None:
            device = str(device_name)
            mode = _optional_string(row["mode"])
            key = (device, mode or "")
            if effective_light is not None:
                lights.setdefault(key, []).append(
                    LightTimelinePointOut(
                        timestamp=_timestamp(row, "timestamp"),
                        value=_required_float(effective_light),
                        provenance=provenance,
                        device_name=device,
                        nominal_value=_optional_float(row["nominal_light_intensity"]),
                        ramp_progress=_optional_float(row["ramp_progress_light"]),
                        mode=mode,
                    )
                )
            elif preserve_gaps and key in lights:
                lights[key].append(
                    LightTimelinePointOut(
                        timestamp=_timestamp(row, "timestamp"),
                        value=None,
                        provenance=unavailable_provenance,
                        device_name=device,
                        mode=mode,
                    )
                )
    return climate, lights


def _build_device_timelines(
    rows: Sequence[ControlRecord], provenance: TimelineProvenanceModel
) -> tuple[tuple[DeviceTimelineSeriesOut, ...], tuple[PidTimelineSeriesOut, ...]]:
    devices: dict[str, list[DeviceTimelinePointOut]] = {}
    pid: dict[str, list[PidTimelinePointOut]] = {}
    for row in rows:
        device = str(row["device_name"])
        devices.setdefault(device, []).append(
            DeviceTimelinePointOut(
                timestamp=_timestamp(row, "bucket"),
                provenance=provenance,
                device_name=device,
                device_state=_required_float(row["device_state_last"]),
                device_mode=_optional_string(row["device_mode_last"]) or "unknown",
                control_reason=_optional_string(row["control_reason_last"]) or "unrecorded",
            )
        )
        pid.setdefault(device, []).append(
            PidTimelinePointOut(
                timestamp=_timestamp(row, "bucket"),
                provenance=provenance,
                device_name=device,
                pid_output=_optional_float(row["pid_output_last"]),
                duty_cycle_percent=_optional_float(row["duty_cycle_percent_last"]),
            )
        )
    return (
        tuple(
            DeviceTimelineSeriesOut(name=name, provenance=provenance, points=tuple(points))
            for name, points in sorted(devices.items())
        ),
        tuple(
            PidTimelineSeriesOut(name=name, provenance=provenance, points=tuple(points))
            for name, points in sorted(pid.items())
        ),
    )


def _build_photoperiod(
    rows: Sequence[ControlRecord], provenance: TimelineProvenanceModel
) -> tuple[tuple[PhotoperiodTimelinePointOut, ...], list[int]]:
    points: list[PhotoperiodTimelinePointOut] = []
    versions: list[int] = []
    for row in rows:
        version = _optional_int(row["runtime_snapshot_version"])
        if version is not None:
            versions.append(version)
        phase = str(row["phase"])
        points.append(
            PhotoperiodTimelinePointOut(
                timestamp=_timestamp(row, "observed_at"),
                provenance=provenance,
                phase=phase if phase in ("SUN", "MOON", "UNKNOWN") else "UNKNOWN",
                mode_id=_optional_int(row["mode_id"]),
                submode_id=_optional_int(row["submode_id"]),
                runtime_snapshot_version=version,
            )
        )
    return tuple(points), versions


def _timestamp(row: ControlRecord, key: str) -> datetime:
    value = row[key]
    assert isinstance(value, datetime)
    return value


def _required_float(value: ControlRecordValue) -> float:
    assert isinstance(value, (float, int))
    return float(value)


def _optional_float(value: ControlRecordValue) -> float | None:
    return _required_float(value) if value is not None else None


def _optional_int(value: ControlRecordValue) -> int | None:
    assert value is None or isinstance(value, int)
    return int(value) if value is not None else None


def _optional_string(value: ControlRecordValue) -> str | None:
    return str(value) if value is not None else None
