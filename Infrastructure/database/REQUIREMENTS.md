# Database Requirements

Schema and runbook contracts for the CEA database layer. See `ARCHITECTURE.md` for hardware and service context.

## Engine and Layout

- PostgreSQL with the TimescaleDB extension.
- Normalized metadata tables: `room`, `rack`, `device`, `sensor`.
- Time-series data lives in the `measurement` hypertable.
- Native continuous aggregates preserve min/max/avg: `monitoring_measurement_1min`, `monitoring_measurement_5min`, `monitoring_measurement_1hour`, and `monitoring_measurement_1day`.

## Aggregate Ladders

Consumer-specific ladders; always filter by time range.

### Monitoring service (`compute_tier`)

`Infrastructure/monitoring-service/monitoring_service/sensor_models.py`:

| Duration | Source |
|---|---|
| ≤ 1 h | raw `measurement` |
| (1 h, 6 h] | `monitoring_measurement_1min` |
| > 6 h | `monitoring_measurement_5min` |

Source: `Infrastructure/database/monitoring_read_models.sql`. Overview reads add the hour/day aggregates from `Infrastructure/database/monitoring_history_overview.sql` with whole-hour buckets of 1–12 hours chosen by point budget.

### Backend API (`_pick_aggregate_tier`)

| Span | Source |
|---|---|
| < 2 h | raw `measurement` |
| ≥ 2 h | `monitoring_measurement_1min` |
| ≥ 24 h | `monitoring_measurement_5min` |
| ≥ 7 d | `monitoring_measurement_1hour` |
| ≥ 30 d | `monitoring_measurement_1day` |

Source: `Infrastructure/backend/app/repositories/sensor_repository.py`.

Do not use hourly aggregates for ranges under 7 days on the backend, or for ranges under 6 hours in the monitoring service.

## Query Guidance

All monitoring-service and backend historical queries must include a time-range filter so TimescaleDB chunk exclusion works. Filter by `sensor_name` whenever possible; `sensor.name` is indexed.

Use these canonical views and read models instead of ad-hoc hypertable scans:

- `measurement_with_metadata` for ad-hoc time-series queries.
- The `monitoring_*` aggregates above for native monitoring reads (they own the monitoring ladder and bucketing).

For live sensor state, read the Redis current-value publications instead of scanning the history hypertable for latest values.

## Setpoints, Photoperiod Overlays, and Light Intensity

Operational setpoints are stored in two tables managed by the automation service.

The active `setpoints` table holds nominal target values configured by the operator:

```
id BIGSERIAL PRIMARY KEY
location TEXT NOT NULL
cluster TEXT NOT NULL
heating_setpoint REAL
cooling_setpoint REAL
humidity REAL
co2 REAL
vpd REAL
mode TEXT CHECK (mode IS NULL OR mode IN ('DAY','NIGHT','TRANSITION','PRE_DAY','PRE_NIGHT'))
ramp_in_duration INTEGER CHECK (ramp_in_duration IS NULL OR (ramp_in_duration >= 0 AND ramp_in_duration <= 240))
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
UNIQUE(location, cluster, mode)
```

The older `cea_schema.sql` setpoints definition (room_id/variable/target) is not used by any service and should be ignored. Writes append; readers use `ORDER BY updated_at DESC LIMIT 1` for the latest value.

The `effective_setpoints` hypertable logs the actual setpoint values used at every control tick, including ramp progress and light intensity. Columns include `effective_heating_setpoint`, `effective_cooling_setpoint`, `effective_vpd_setpoint`, `effective_light_intensity`, `nominal_heating_setpoint`, `nominal_cooling_setpoint`, `nominal_vpd_setpoint`, `ramp_progress_heating`, `ramp_progress_cooling`, `ramp_progress_vpd`, `time`.

For native monitoring temperature/VPD series:

- The monitoring service serves these from its read models; the underlying rules are: effective heating and VPD series come from `effective_setpoints`.
- Determine DAY/NIGHT mode by matching the timestamp against `schedules` rows (`location`, `cluster`, `mode`, `enabled`, `start_time`, `end_time`, optional `day_of_week`). Use `tp.time::time` comparisons so overnight ranges are handled correctly.
- If no DAY schedule matches, fall back to NIGHT setpoints.
- Only `DAY` and `NIGHT` modes are considered; legacy `NULL`/`TRANSITION` values are ignored.
- The DAY overlay series returns `100` during day and `NULL` otherwise and is styled as a translucent yellow fill.

For photoperiod chart overlays (DAY/NIGHT shading):

- Combine `schedules` (`device_name = 'room_schedule'`) with `mode_transition_history`.
- For each minute `t`, use the latest row with `time <= t` for that `location`/`cluster`, joining `new_mode_id` to `room_modes.name`.
- Moon-authority modes (`drying`, `sleep`) suppress the DAY band and show NIGHT for the full 24h. The SQL list `('drying', 'sleep')` must stay aligned with `Infrastructure/shared/room_light_authority.py` (`MOON_AUTHORITY_MODE_NAMES`).
- Before the first `mode_transition_history` row for a room/cluster, overlays use schedule only.

Room light intensity curves in the native monitoring dashboards come from `effective_setpoints.effective_light_intensity`, not from Redis `light:*` keys (those are for the live UI). Always filter light-intensity queries by `device_name` (`light_1`, `light_2`, …). The monitoring freshness coverage detects a fixture that stops logging `effective_setpoints`; the monitoring-service readiness/coverage gates surface missing or dirty source history instead of serving partial data.

An optional operator panel can list actual DB device names with:

```sql
SELECT DISTINCT device_name
FROM effective_setpoints
WHERE location = '$room'
  AND cluster = 'main'
  AND effective_light_intensity IS NOT NULL
  AND $__timeFilter("time");
```

## Time Zone

- Stored timestamps use `TIMESTAMPTZ`.
- Production sessions default to `America/Toronto` for operator display and local schedule processing.
- Exports for analytics, AI training, backups, or cross-system processing must emit UTC ISO timestamps explicitly.

## Redis vs Postgres

Redis owns live/current state; Postgres owns history and configuration. The monitoring service reads Redis publications for current values and TimescaleDB (Pi primary) for history.

## Latest Values

`latest_sensor_values` was dropped by `0012_mode_transition_history.sql` because no in-repo service consumed it. The down migration recreates the LATERAL definition only if the view existed before the upgrade.

## Anti-Patterns

- Query the hypertable without a `time >=` filter.
- Use hourly aggregates for short ranges.
- Drop min/max from rollups.
- Rely on session timezone for exported data.

## Grow-policy activation boundary

Production is `sj_cea` (renamed from `cea_sensors` on 2026-10-19; see `AGENTS.md`). Grow/Veg lifecycle activation remains an owner-authorized action. Source hypertables must not receive retention policies, or compression policies with `compress_after` under 3 months; `grow_archive_service/policy_gate.py` checks this read-only before lifecycle work.
