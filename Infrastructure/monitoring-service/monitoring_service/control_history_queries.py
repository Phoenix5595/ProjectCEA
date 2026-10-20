"""Existing control read-model selection and parameterized SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from monitoring_service.control_models import ControlHistoryRange, ControlReadScope

_RAW_SETPOINTS_SQL = """
SELECT "time" AS time, mode,
       effective_heating_setpoint, nominal_heating_setpoint, ramp_progress_heating,
       effective_cooling_setpoint, nominal_cooling_setpoint, ramp_progress_cooling,
       effective_humidity_setpoint, nominal_humidity_setpoint, ramp_progress_humidity,
       effective_co2_setpoint, nominal_co2_setpoint, ramp_progress_co2,
       effective_vpd_setpoint, nominal_vpd_setpoint, ramp_progress_vpd,
       FALSE AS is_aggregated
FROM effective_setpoints
WHERE location = $1 AND "time" >= $2 AND "time" < $3
ORDER BY "time"
"""

# Recorded light facts always read the raw per-device setpoint rows. The CAGG
# read models keep only the last light sample per bucket and null the ramp
# progress, so selecting lights through them erases ramp semantics whenever the
# selected range changes; the light-only filter keeps the read bounded without
# touching the climate and automation-state aggregate ladders. Photoperiod
# history reads the canonical main cluster only.
_LIGHT_SETPOINTS_SQL = """
SELECT samples."time" AS time, samples.cluster, samples.mode, samples.device_name,
       samples.effective_light_intensity, samples.nominal_light_intensity,
       samples.ramp_progress_light
FROM effective_setpoints AS samples
JOIN (
    SELECT DISTINCT device_name
    FROM effective_setpoints
    WHERE location = $1 AND cluster = 'main'
      AND "time" >= $2 AND "time" < $3
      AND device_name IS NOT NULL
      AND (effective_light_intensity IS NOT NULL OR nominal_light_intensity IS NOT NULL
           OR ramp_progress_light IS NOT NULL)
) AS recorded_lights USING (device_name)
WHERE samples.location = $1 AND samples.cluster = 'main'
  AND samples."time" >= $2 AND samples."time" < $3
ORDER BY samples."time"
"""

_CAGG_SETPOINT_COLUMNS = """
bucket AS time, mode,
effective_heating_setpoint_last AS effective_heating_setpoint,
effective_heating_setpoint_min::double precision AS effective_heating_setpoint_min,
effective_heating_setpoint_max::double precision AS effective_heating_setpoint_max,
NULL::double precision AS nominal_heating_setpoint, NULL::double precision AS ramp_progress_heating,
effective_cooling_setpoint_last AS effective_cooling_setpoint,
effective_cooling_setpoint_min::double precision AS effective_cooling_setpoint_min,
effective_cooling_setpoint_max::double precision AS effective_cooling_setpoint_max,
NULL::double precision AS nominal_cooling_setpoint, NULL::double precision AS ramp_progress_cooling,
effective_humidity_setpoint_last AS effective_humidity_setpoint,
effective_humidity_setpoint_min::double precision AS effective_humidity_setpoint_min,
effective_humidity_setpoint_max::double precision AS effective_humidity_setpoint_max,
NULL::double precision AS nominal_humidity_setpoint, NULL::double precision AS ramp_progress_humidity,
effective_co2_setpoint_last AS effective_co2_setpoint,
effective_co2_setpoint_min::double precision AS effective_co2_setpoint_min,
effective_co2_setpoint_max::double precision AS effective_co2_setpoint_max,
NULL::double precision AS nominal_co2_setpoint, NULL::double precision AS ramp_progress_co2,
effective_vpd_setpoint_last AS effective_vpd_setpoint,
effective_vpd_setpoint_min::double precision AS effective_vpd_setpoint_min,
effective_vpd_setpoint_max::double precision AS effective_vpd_setpoint_max,
NULL::double precision AS nominal_vpd_setpoint, NULL::double precision AS ramp_progress_vpd,
TRUE AS is_aggregated
"""

_SETPOINTS_1MIN_SQL = f"""
SELECT {_CAGG_SETPOINT_COLUMNS}
FROM monitoring_effective_setpoints_1min
WHERE location = $1 AND bucket >= $2 AND bucket < $3
ORDER BY bucket
"""

_SETPOINTS_5MIN_SQL = f"""
SELECT {_CAGG_SETPOINT_COLUMNS}
FROM monitoring_effective_setpoints_5min
WHERE location = $1 AND bucket >= $2 AND bucket < $3
ORDER BY bucket
"""

_STATE_COLUMNS = """
bucket, device_name, device_state_last, device_mode_last, control_reason_last,
pid_output_last, duty_cycle_percent_last,
pid_output_min, pid_output_max, duty_cycle_percent_min, duty_cycle_percent_max,
TRUE AS is_aggregated
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
_STATE_RAW_SQL = """
SELECT "time" AS bucket, device_name, device_state AS device_state_last,
       device_mode AS device_mode_last, control_reason AS control_reason_last,
       pid_output AS pid_output_last, duty_cycle_percent AS duty_cycle_percent_last,
       pid_output AS pid_output_min, pid_output AS pid_output_max,
       duty_cycle_percent AS duty_cycle_percent_min,
       duty_cycle_percent AS duty_cycle_percent_max,
       FALSE AS is_aggregated
FROM automation_state
WHERE location = $1 AND "time" >= $2 AND "time" < $3
  AND device_state IS NOT NULL
ORDER BY device_name, "time"
"""


PHOTOPERIOD_SQL = """
WITH scoped AS (
    SELECT id, "time", phase, mode_id, submode_id, runtime_snapshot_version, source
    FROM monitoring_room_photoperiod
    WHERE location = $1 AND cluster = 'main'
), in_range AS (
    SELECT id, "time", phase, mode_id, submode_id, runtime_snapshot_version, source
    FROM scoped
    WHERE "time" >= $2 AND "time" < $3
), predecessor AS (
    SELECT DISTINCT ON (source)
           id, "time", phase, mode_id, submode_id, runtime_snapshot_version, source
    FROM scoped
    WHERE "time" < $2
      AND source IN ('photoperiod_transition', 'photoperiod')
      AND (
          source = 'photoperiod_transition'
          OR "time" >= $2::timestamptz - interval '60 seconds'
      )
    ORDER BY source, "time" DESC, id DESC
)
SELECT id, "time", phase, mode_id, submode_id, runtime_snapshot_version, source
FROM in_range
UNION ALL
SELECT id, "time", phase, mode_id, submode_id, runtime_snapshot_version, source
FROM predecessor
ORDER BY "time", id
"""

# Sparse recorder availability bounds how long a committed transition may hold.
PHOTOPERIOD_COVERAGE_SQL = """
WITH in_range AS (
    SELECT id, "time", location, cluster, state, reason, runtime_snapshot_version
    FROM monitoring_photoperiod_coverage
    WHERE location = $1 AND cluster = 'main'
      AND "time" >= $2 AND "time" < $3
), predecessor AS (
    SELECT id, "time", location, cluster, state, reason, runtime_snapshot_version
    FROM monitoring_photoperiod_coverage
    WHERE location = $1 AND cluster = 'main'
      AND "time" < $2
    ORDER BY "time" DESC, id DESC
    LIMIT 1
)
SELECT id, "time", location, cluster, state, reason, runtime_snapshot_version
FROM in_range
UNION ALL
SELECT id, "time", location, cluster, state, reason, runtime_snapshot_version
FROM predecessor
ORDER BY "time", id
"""

# Retained per-device light fact minutes remain the only bounded predecessor
# evidence for spans predating the transition stream. The complete bounded
# minute is read so tied duplicate or conflicting rows cannot hide behind a
# single-row limit.
PHOTOPERIOD_LIGHT_PREDECESSORS_SQL = """
SELECT "time", cluster, device_name, mode,
       effective_light_intensity, nominal_light_intensity, ramp_progress_light
FROM effective_setpoints
WHERE location = $1
  AND cluster = 'main'
  AND "time" >= $2::timestamptz - interval '60 seconds'
  AND "time" < $2::timestamptz
  AND device_name IS NOT NULL
  AND (effective_light_intensity IS NOT NULL OR nominal_light_intensity IS NOT NULL
       OR ramp_progress_light IS NOT NULL)
ORDER BY "time"
"""


@dataclass(frozen=True, slots=True)
class ControlHistorySources:
    """Read-only sources selected for a control history request.

    ``setpoints_sql`` and ``state_sql`` are ``None`` when the requested scope
    excludes climate and automation-state facts; the light source always stays
    the raw per-device recorded setpoint rows.
    """

    setpoints_sql: str | None
    light_sql: str
    state_sql: str | None
    setpoints_are_aggregated: bool
    source_interval_seconds: int
    setpoints_relation: str | None = None
    state_relation: str | None = None


def select_control_history_sources(
    history_range: ControlHistoryRange,
    max_points: int | None,
    scope: ControlReadScope = "all",
) -> ControlHistorySources:
    """Choose existing control read models; unbudgeted reads retain their legacy sources.

    A ``lights`` scope selects only the raw per-device light source: the
    climate and automation-state ladders are never queried. Recorded effective
    light intensities remain readable when those derived models are unavailable;
    they are setpoints, not independently measured dimmer feedback. The default
    ``all`` scope retains the existing climate and automation-state ladders.

    Lights always read the raw per-device setpoint source because
    range-dependent aggregate selection is what erased recorded ramp semantics
    through the CAGG null nominal/ramp fields.
    """
    if scope == "lights":
        return ControlHistorySources(None, _LIGHT_SETPOINTS_SQL, None, False, 60)
    if max_points is None:
        return ControlHistorySources(
            _RAW_SETPOINTS_SQL,
            _LIGHT_SETPOINTS_SQL,
            _STATE_1MIN_SQL,
            False,
            60,
            state_relation="monitoring_automation_state_1min",
        )
    duration = history_range.end - history_range.start
    if duration >= timedelta(days=1):
        return ControlHistorySources(
            _SETPOINTS_5MIN_SQL,
            _LIGHT_SETPOINTS_SQL,
            _STATE_5MIN_SQL,
            True,
            300,
            setpoints_relation="monitoring_effective_setpoints_5min",
            state_relation="monitoring_automation_state_5min",
        )
    if duration >= timedelta(hours=2):
        return ControlHistorySources(
            _SETPOINTS_1MIN_SQL,
            _LIGHT_SETPOINTS_SQL,
            _STATE_1MIN_SQL,
            True,
            60,
            setpoints_relation="monitoring_effective_setpoints_1min",
            state_relation="monitoring_automation_state_1min",
        )
    return ControlHistorySources(
        _RAW_SETPOINTS_SQL,
        _LIGHT_SETPOINTS_SQL,
        _STATE_1MIN_SQL,
        False,
        60,
        state_relation="monitoring_automation_state_1min",
    )
