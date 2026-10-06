"""Existing control read-model selection and parameterized SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from monitoring_service.control_models import ControlHistoryRange

_RAW_SETPOINTS_SQL = """
SELECT timestamp, mode,
       effective_heating_setpoint, nominal_heating_setpoint, ramp_progress_heating,
       effective_cooling_setpoint, nominal_cooling_setpoint, ramp_progress_cooling,
       effective_humidity_setpoint, nominal_humidity_setpoint, ramp_progress_humidity,
       effective_co2_setpoint, nominal_co2_setpoint, ramp_progress_co2,
       effective_vpd_setpoint, nominal_vpd_setpoint, ramp_progress_vpd
FROM effective_setpoints
WHERE location = $1 AND timestamp >= $2 AND timestamp < $3
ORDER BY timestamp
"""

# Recorded light facts always read the raw per-device setpoint rows. The CAGG
# read models keep only the last light sample per bucket and null the ramp
# progress, so selecting lights through them erases ramp semantics whenever the
# selected range changes; the light-only filter keeps the read bounded without
# touching the climate and automation-state aggregate ladders.
_LIGHT_SETPOINTS_SQL = """
SELECT samples.timestamp, samples.mode, samples.device_name,
       samples.effective_light_intensity, samples.nominal_light_intensity,
       samples.ramp_progress_light
FROM effective_setpoints AS samples
JOIN (
    SELECT DISTINCT device_name
    FROM effective_setpoints
    WHERE location = $1 AND timestamp >= $2 AND timestamp < $3
      AND device_name IS NOT NULL
      AND (effective_light_intensity IS NOT NULL OR nominal_light_intensity IS NOT NULL
           OR ramp_progress_light IS NOT NULL)
) AS recorded_lights USING (device_name)
WHERE samples.location = $1 AND samples.timestamp >= $2 AND samples.timestamp < $3
ORDER BY timestamp
"""

_CAGG_SETPOINT_COLUMNS = """
bucket AS timestamp, mode,
effective_heating_setpoint_last AS effective_heating_setpoint,
NULL::double precision AS nominal_heating_setpoint, NULL::double precision AS ramp_progress_heating,
effective_cooling_setpoint_last AS effective_cooling_setpoint,
NULL::double precision AS nominal_cooling_setpoint, NULL::double precision AS ramp_progress_cooling,
effective_humidity_setpoint_last AS effective_humidity_setpoint,
NULL::double precision AS nominal_humidity_setpoint, NULL::double precision AS ramp_progress_humidity,
effective_co2_setpoint_last AS effective_co2_setpoint,
NULL::double precision AS nominal_co2_setpoint, NULL::double precision AS ramp_progress_co2,
effective_vpd_setpoint_last AS effective_vpd_setpoint,
NULL::double precision AS nominal_vpd_setpoint, NULL::double precision AS ramp_progress_vpd
"""

_SETPOINTS_1MIN_SQL = f"""
SELECT {_CAGG_SETPOINT_COLUMNS}
FROM monitoring_effective_setpoints_1min
WHERE location = $1 AND bucket >= $2 AND bucket < $3
ORDER BY timestamp
"""

_SETPOINTS_5MIN_SQL = f"""
SELECT {_CAGG_SETPOINT_COLUMNS}
FROM monitoring_effective_setpoints_5min
WHERE location = $1 AND bucket >= $2 AND bucket < $3
ORDER BY timestamp
"""

_STATE_COLUMNS = """
bucket, device_name, device_state_last, device_mode_last, control_reason_last,
pid_output_last, duty_cycle_percent_last
"""

_STATE_1MIN_SQL = f"""
SELECT {_STATE_COLUMNS}
FROM monitoring_automation_state_1min
WHERE location = $1 AND bucket >= $2 AND bucket < $3 AND device_state_last IS NOT NULL
ORDER BY device_name, bucket
"""

_STATE_5MIN_SQL = f"""
SELECT {_STATE_COLUMNS}
FROM monitoring_automation_state_5min
WHERE location = $1 AND bucket >= $2 AND bucket < $3 AND device_state_last IS NOT NULL
ORDER BY device_name, bucket
"""

PHOTOPERIOD_SQL = """
SELECT observed_at, phase, mode_id, submode_id, runtime_snapshot_version
FROM monitoring_room_photoperiod
WHERE location = $1 AND observed_at >= $2 AND observed_at < $3
ORDER BY observed_at
"""


@dataclass(frozen=True, slots=True)
class ControlHistorySources:
    """Read-only sources selected for a control history request."""

    setpoints_sql: str
    light_sql: str
    state_sql: str
    setpoints_are_aggregated: bool
    source_interval_seconds: int


def select_control_history_sources(
    history_range: ControlHistoryRange, max_points: int | None
) -> ControlHistorySources:
    """Choose existing control read models; unbudgeted reads retain their legacy sources.

    The climate and automation-state ladders are range-dependent as before.
    Lights always read the raw per-device setpoint source because range-dependent
    aggregate selection is what erased recorded ramp semantics through the CAGG
    null nominal/ramp fields.
    """
    if max_points is None:
        return ControlHistorySources(
            _RAW_SETPOINTS_SQL, _LIGHT_SETPOINTS_SQL, _STATE_1MIN_SQL, False, 60
        )
    duration = history_range.end - history_range.start
    if duration >= timedelta(days=1):
        return ControlHistorySources(
            _SETPOINTS_5MIN_SQL, _LIGHT_SETPOINTS_SQL, _STATE_5MIN_SQL, True, 300
        )
    if duration >= timedelta(hours=2):
        return ControlHistorySources(
            _SETPOINTS_1MIN_SQL, _LIGHT_SETPOINTS_SQL, _STATE_1MIN_SQL, True, 60
        )
    return ControlHistorySources(
        _RAW_SETPOINTS_SQL, _LIGHT_SETPOINTS_SQL, _STATE_1MIN_SQL, False, 60
    )
