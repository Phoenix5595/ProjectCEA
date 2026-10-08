from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import chain
from math import isfinite

from monitoring_service.control_models import (
    ClimateTimelinePointOut,
    ClimateTimelineSeriesOut,
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlRecord,
    ControlRecordValue,
    DeviceTimelinePointOut,
    DeviceTimelineSeriesOut,
    LightTimelinePointOut,
    LightTimelineSeriesOut,
    PidTimelinePointOut,
    PidTimelineSeriesOut,
    SAMPLE_SUPPORTED_SPAN,
    TimelineProvenanceModel,
)
from monitoring_service.control_timeline_budget import budget_control_history
from monitoring_service.photoperiod_history import build_photoperiod_history
from shared.monitoring_contracts import Quality


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
    light_rows: Sequence[ControlRecord],
    state_rows: Sequence[ControlRecord],
    photoperiod_rows: Sequence[ControlRecord],
    setpoints_are_aggregated: bool,
    max_points: int | None,
    interval_seconds: int | None,
    *,
    photoperiod_coverage_rows: Sequence[ControlRecord],
    photoperiod_light_predecessors: Sequence[ControlRecord],
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
    # Lights always come from the raw per-device source, so their provenance is
    # exact and never aggregated at read time; the budget reduction reports its
    # own approximation separately.
    light_provenance = TimelineProvenanceModel(
        origin="recorded", quality=Quality.EXACT, is_aggregated=False
    )
    light_unavailable_provenance = TimelineProvenanceModel(
        origin="recorded", quality=Quality.UNAVAILABLE, is_aggregated=False
    )
    climate_builders = _build_climate_timelines(
        setpoint_rows, provenance, unavailable_provenance, max_points is not None
    )
    lights = _build_light_timelines(
        light_rows,
        light_provenance,
        light_unavailable_provenance,
        max_points is not None,
        history_range.end,
    )
    devices, pid = _build_device_timelines(state_rows, aggregate_provenance)
    photoperiod, snapshot_versions = build_photoperiod_history(
        history_range,
        photoperiod_rows,
        photoperiod_coverage_rows,
        chain(photoperiod_light_predecessors, light_rows),
    )
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
            LightTimelineSeriesOut(name=device, provenance=light_provenance, points=tuple(points))
            for device, points in sorted(lights.items())
        ),
        devices=devices,
        pid=pid,
        photoperiod=photoperiod,
    )
    return envelope if max_points is None else budget_control_history(envelope, max_points)


def _build_climate_timelines(
    rows: Sequence[ControlRecord],
    provenance: TimelineProvenanceModel,
    unavailable_provenance: TimelineProvenanceModel,
    preserve_gaps: bool,
) -> dict[str, list[ClimateTimelinePointOut]]:
    climate: dict[str, list[ClimateTimelinePointOut]] = {}
    if not rows:
        return climate

    climate_rows: dict[str, dict[datetime, list[ControlRecord]]] = {}
    for row in rows:
        for metric, _, _, _ in _CLIMATE_FIELDS:
            timestamp = _timestamp(row, "timestamp")
            climate_rows.setdefault(metric, {}).setdefault(timestamp, []).append(row)

    for metric, effective_column, nominal_column, ramp_column in _CLIMATE_FIELDS:
        for timestamp, siblings in sorted(climate_rows[metric].items()):
            selected_row: ControlRecord | None = None
            effective: float | None = None
            for row in siblings:
                candidate = _finite_float(row[effective_column])
                if candidate is not None:
                    selected_row = row
                    effective = candidate
                    break
            if selected_row is not None:
                climate.setdefault(metric, []).append(
                    ClimateTimelinePointOut(
                        timestamp=timestamp,
                        value=effective,
                        provenance=provenance,
                        metric=metric,
                        nominal_value=_optional_float(selected_row[nominal_column]),
                        ramp_progress=_optional_float(selected_row[ramp_column]),
                        mode=_optional_string(selected_row["mode"]),
                    )
                )
            elif preserve_gaps and metric in climate:
                climate[metric].append(
                    ClimateTimelinePointOut(
                        timestamp=timestamp,
                        value=None,
                        provenance=unavailable_provenance,
                        metric=metric,
                        mode=_optional_string(siblings[0]["mode"]),
                    )
                )
    return climate


def _build_light_timelines(
    rows: Sequence[ControlRecord],
    provenance: TimelineProvenanceModel,
    unavailable_provenance: TimelineProvenanceModel,
    preserve_gaps: bool,
    coverage_end: datetime,
) -> dict[str, list[LightTimelinePointOut]]:
    lights: dict[str, list[LightTimelinePointOut]] = {}
    if not rows:
        return lights

    light_rows: dict[str, dict[datetime, list[ControlRecord]]] = {}
    for row in rows:
        device_name = row["device_name"]
        if device_name is None:
            continue
        device = str(device_name)
        timestamp = _timestamp(row, "timestamp")
        light_rows.setdefault(device, {}).setdefault(timestamp, []).append(row)

    for device, timestamps in sorted(light_rows.items()):
        for timestamp, siblings in sorted(timestamps.items()):
            points = lights.get(device)
            if preserve_gaps and points and points[-1].value is not None:
                expires_at = points[-1].timestamp + SAMPLE_SUPPORTED_SPAN
                if timestamp > expires_at:
                    points.append(
                        LightTimelinePointOut(
                            timestamp=expires_at,
                            value=None,
                            provenance=unavailable_provenance,
                            device_name=device,
                        )
                    )
            selected_row: ControlRecord | None = None
            effective: float | None = None
            for row in siblings:
                candidate = _finite_float(row["effective_light_intensity"])
                if candidate is not None:
                    selected_row = row
                    effective = candidate
                    break
            if selected_row is not None:
                lights.setdefault(device, []).append(
                    LightTimelinePointOut(
                        timestamp=timestamp,
                        value=effective,
                        provenance=provenance,
                        device_name=device,
                        nominal_value=_optional_float(selected_row["nominal_light_intensity"]),
                        ramp_progress=_optional_float(selected_row["ramp_progress_light"]),
                        mode=_optional_string(selected_row["mode"]),
                    )
                )
            elif preserve_gaps and device in lights:
                lights[device].append(
                    LightTimelinePointOut(
                        timestamp=timestamp,
                        value=None,
                        provenance=unavailable_provenance,
                        device_name=device,
                        mode=_optional_string(siblings[0]["mode"]),
                    )
                )
        points = lights.get(device)
        if preserve_gaps and points and points[-1].value is not None:
            expires_at = points[-1].timestamp + SAMPLE_SUPPORTED_SPAN
            if coverage_end > expires_at:
                points.append(
                    LightTimelinePointOut(
                        timestamp=expires_at,
                        value=None,
                        provenance=unavailable_provenance,
                        device_name=device,
                    )
                )
    return lights


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


def _timestamp(row: ControlRecord, key: str) -> datetime:
    value = row[key]
    assert isinstance(value, datetime)
    return value


def _required_float(value: ControlRecordValue) -> float:
    assert isinstance(value, (float, int))
    return float(value)


def _optional_float(value: ControlRecordValue) -> float | None:
    return _required_float(value) if value is not None else None


def _finite_float(value: ControlRecordValue) -> float | None:
    if value is None:
        return None
    numeric = _required_float(value)
    return numeric if isfinite(numeric) else None


def _optional_int(value: ControlRecordValue) -> int | None:
    assert value is None or isinstance(value, int)
    return int(value) if value is not None else None


def _optional_string(value: ControlRecordValue) -> str | None:
    return str(value) if value is not None else None
