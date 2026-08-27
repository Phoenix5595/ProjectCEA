# Database Requirements

Schema and runbook contracts for the CEA database layer. See `ARCHITECTURE.md` for hardware and service context.

## Engine and Layout

- PostgreSQL with the TimescaleDB extension.
- Normalized metadata tables: `room`, `rack`, `device`, `sensor`.
- Time-series data lives in the `measurement` hypertable.
- Continuous aggregates preserve min/max/avg: `measurement_1min`, `measurement_5min`, `measurement_hourly`, `measurement_daily`.

## Primary and Replica

The Pi primary hosts `cea_sensors` and streams WAL to the Iskra standby. Required primary settings:

| Setting | Value |
|---|---|
| `wal_level` | `replica` |
| `wal_keep_size` | `4GB` |
| `max_slot_wal_keep_size` | `16GB` |
| `max_wal_senders` | `5` |

Replication slot `iskra_recovery` must exist on the primary and be consumed by the standby. If the standby falls behind beyond `max_slot_wal_keep_size`, re-base it.

The Iskra standby enables `hot_standby_feedback = on`, `jit = off`, and `max_parallel_workers_per_gather = 0` so Grafana and `redis_sync` queries are not canceled by recovery conflicts.

## Aggregate Ladders

Grafana and the backend API use different ladders. Always filter by time range.

### Grafana (`get_sensor_data_optimized`)

| Span | Source |
|---|---|
| ≤ 1 h | raw `measurement` |
| (1 h, 6 h] | `measurement_1min` |
| (6 h, 24 h] | `measurement_5min` |
| > 24 h | `measurement_hourly` |

Source: `Infrastructure/database/grafana_performance_migration.sql`.

### Backend API (`_pick_aggregate_tier`)

| Span | Source |
|---|---|
| < 2 h | raw `measurement` |
| ≥ 2 h | `measurement_1min` |
| ≥ 24 h | `measurement_5min` |
| ≥ 7 d | `measurement_hourly` |
| ≥ 30 d | `measurement_daily` |

Source: `Infrastructure/backend/app/repositories/sensor_repository.py`.

Do not use hourly aggregates for ranges under 7 days on the backend, or for ranges under 24 hours in Grafana.

## Query Guidance

All Grafana and backend historical queries must include a time-range filter so TimescaleDB chunk exclusion works. Filter by `sensor_name` whenever possible; `sensor.name` is indexed.

Use these optimized views and functions instead of ad-hoc hypertable scans:

- `measurement_with_metadata` / `measurement_timeseries` for time-series panels.
- `latest_sensor_values` for one-row-per-sensor "latest" panels.
- `get_latest_by_pattern('%_suffix')` for latest-value lookups by sensor name pattern.
- `get_sensor_data_optimized(...)` for Grafana historical panels (uses the aggregate ladder above).

Avoid `MAX(time)` subqueries over the hypertable for "latest" values; they are slower than the dedicated view/functions.

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

The `effective_setpoints` hypertable logs the actual setpoint values used at every control tick, including ramp progress and light intensity. Columns include `effective_heating_setpoint`, `effective_cooling_setpoint`, `effective_vpd_setpoint`, `effective_light_intensity`, `nominal_heating_setpoint`, `nominal_cooling_setpoint`, `nominal_vpd_setpoint`, `ramp_progress_heating`, `ramp_progress_cooling`, `ramp_progress_vpd`, `timestamp`.

For Grafana temperature/VPD panels:

- Query `effective_setpoints` for the active heating and VPD setpoint series.
- Determine DAY/NIGHT mode by matching the timestamp against `schedules` rows (`location`, `cluster`, `mode`, `enabled`, `start_time`, `end_time`, optional `day_of_week`). Use `tp.time::time` comparisons so overnight ranges are handled correctly.
- If no DAY schedule matches, fall back to NIGHT setpoints.
- Only `DAY` and `NIGHT` modes are considered; legacy `NULL`/`TRANSITION` values are ignored.
- The DAY overlay series returns `100` during day and `NULL` otherwise and is styled as a translucent yellow fill.

For photoperiod chart overlays (DAY/NIGHT shading):

- Combine `schedules` (`device_name = 'room_schedule'`) with `mode_transition_history`.
- For each minute `t`, use the latest row with `triggered_at <= t` for that `location`/`cluster`, joining `new_mode_id` to `room_modes.name`.
- Moon-authority modes (`drying`, `sleep`) suppress the DAY band and show NIGHT for the full 24h. The SQL list `('drying', 'sleep')` must stay aligned with `Infrastructure/shared/room_light_authority.py` (`MOON_AUTHORITY_MODE_NAMES`).
- Before the first `mode_transition_history` row for a room/cluster, overlays use schedule only.

Room light intensity curves in Grafana come from `effective_setpoints.effective_light_intensity`, not from Redis `light:*` keys (those are for the live UI). Always filter light-intensity queries by `device_name` (`light_1`, `light_2`, …). Flower/Veg sector dashboards include a "Light effective_setpoints — sample freshness" table showing seconds since the last row per light; use it to detect a fixture that stops logging.

An optional operator panel can list actual DB device names with:

```sql
SELECT DISTINCT device_name
FROM effective_setpoints
WHERE location = '$room'
  AND cluster = 'main'
  AND effective_light_intensity IS NOT NULL
  AND $__timeFilter(timestamp);
```

## Time Zone

- Stored timestamps use `TIMESTAMPTZ`.
- Production sessions default to `America/Toronto` for operator display and local schedule processing.
- Exports for analytics, AI training, backups, or cross-system processing must emit UTC ISO timestamps explicitly.

## Redis vs Postgres

Redis owns live/current state; Postgres owns history and configuration. Grafana current-value panels read Redis hashes populated by `redis_sync`; time-series graphs read the Iskra Postgres replica.

## Latest Values

Use the `latest_sensor_values` view for one-row-per-sensor latest values. It is implemented as `LATERAL` index probes per sensor, not `DISTINCT ON` over the hypertable.

## Anti-Patterns

- Query the hypertable without a `time >=` filter.
- Use hourly aggregates for short ranges.
- Drop min/max from rollups.
- Rely on session timezone for exported data.
