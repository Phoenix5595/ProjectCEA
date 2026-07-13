# DATABASE LAYER

## OVERVIEW

PostgreSQL + TimescaleDB. Normalized metadata (room/rack/device/sensor) + compressed hypertables + continuous aggregates.

## ARCHITECTURE

```
[Sensors @1Hz]
    |
    v
[CAN Processor Service]
    |---> [Redis Stream sensor:raw] --> Automation Services (real-time control)
    |---> [Redis State sensor:{name}] --> Current values (10s TTL)
    |
    v
[PostgreSQL]
  - measurement (raw 1s data, hypertable)
  - measurement_1min (continuous aggregate: avg/min/max)
  - measurement_5min (continuous aggregate: avg/min/max)
  - measurement_hourly (continuous aggregate: avg/min/max)
  - measurement_daily (continuous aggregate: avg/min/max)
    |
    v
[Grafana @ iskraprojectcea:3001] <-- ONLY consumer of PostgreSQL for visualization
```

## DATA FLOW BY CONSUMER

| Consumer | Data Source | Purpose |
|----------|-------------|---------|
| Automation services | Redis Stream/State | Real-time control loops |
| Grafana dashboards | PostgreSQL only | Visualization |
| Historical analysis | PostgreSQL aggregates | Trends, reporting |

## STRUCTURE

```
database/
├── cea_schema.sql           # Main schema creation
├── timescaledb_config.sql   # Compression + retention
├── timescaledb_setup.sql    # Initial setup
├── REQUIREMENTS.md          # Full schema documentation
├── SETPOINTS_TABLE_EXPLANATION.md
└── SETPOINTS_RECOMMENDATION.md
```

## KEY TABLES

| Table | Type | Purpose |
|-------|------|---------|
| `measurement` | Hypertable | Time-series sensor data (1s resolution) |
| `measurement_1min` | Continuous Aggregate | 1-minute rollups with avg/min/max |
| `measurement_5min` | Continuous Aggregate | 5-minute rollups with avg/min/max |
| `measurement_hourly` | Continuous Aggregate | Hourly rollups with avg/min/max |
| `measurement_daily` | Continuous Aggregate | Daily rollups with avg/min/max |
| `device_registry` | Config | Core device configuration (13 devices, relays + dimmers) |
| `mode_parameters` | Config | Photoperiod source of truth (day_start_time, night_start_time) |
| `room_modes` | Config | Mode definitions (Veg, Flower, Stretch, Bulk, Ripen, Drying, Sleep) |
| `light_target_intensity` | Config | Per-light, per-mode target intensity (0-100) |
| `light_programs` | Config | Supplemental and override light programs |

## CONTINUOUS AGGREGATES (CRITICAL)

Each aggregate stores **avg + min + max** to preserve swing visibility:

| Aggregate | Bucket | Columns | Use Case |
|-----------|--------|---------|----------|
| `measurement_1min` | 1 min | avg_value, min_value, max_value | Grafana **`get_sensor_data_optimized`**: span **(1 h, 6 h]** |
| `measurement_5min` | 5 min | avg_value, min_value, max_value | Grafana: **(6 h, 24 h]**; backend API ladder uses wider ranges |
| `measurement_hourly` | 1 hour | avg_value, min_value, max_value | >7d queries |
| `measurement_daily` | 1 day | avg_value, min_value, max_value | Monthly+ trends |

**WHY min/max**: Humidity can swing 40% in 5 minutes. Averages alone hide these critical events. Min/max envelope ensures extremes are always visible.

## CONFIG TABLES

### `device_registry`

13 devices (6 Flower, 7 Veg). Columns: `device_id` (SERIAL PK), `location`, `cluster` (always `main`), `device_name` (canonical), `display_name`, `device_type`, `channel` (relay 0-15), `dimming_enabled`, `dimming_type`, `dimming_board_id`, `dimming_channel`, `safety_level`, `pid_enabled`, `interlock_with` (JSONB), `pid_setpoints` (JSONB), `per_room_index`, `created_at`, `updated_at`. Unique: `(location, cluster, device_name)`.

### `mode_parameters`

Photoperiod source of truth per room per mode. `day_start_time` / `night_start_time` define sun/moon boundaries (overnight wrap supported). `light_ramp_up_minutes` / `light_ramp_down_minutes` for ramps. `main_light_intensity` and `supplemental_light_intensity` are **DEPRECATED** — use `light_target_intensity`.

### `room_modes`

Mode definitions: Veg(1), Flower(2), Stretch(3), Bulk(4), Ripen(5), Drying(6), Sleep(7).

### `light_target_intensity`

Per-light, per-mode target intensity. `(device_id, mode_id) → target_intensity` (REAL, 0-100, default 10.0). PK `(device_id, mode_id)`. CHECK `target_intensity >= 0 AND <= 100`. Created via direct SQL (not alembic). Currently empty — operator sets via frontend slider.

### `light_programs`

Supplemental and override light programs. Columns: `id` (IDENTITY PK), `device_id` (NULL = room-wide), `location`, `cluster`, `mode_id` (NULL = all modes), `name`, `program_type` (`supplemental`|`override`), `start_time`/`end_time` (overnight wrap supported), `cycle_enabled`, `cycle_on_seconds`/`cycle_off_seconds`, `target_intensity`, `ramp_up_minutes`/`ramp_down_minutes`, `day_of_week` (NULL = all), `enabled`, `priority` (higher wins, ties by `created_at`), `created_at`, `updated_at`. CHECK: `program_type IN ('supplemental', 'override')`. Created via direct SQL. Currently empty.

---

## ⚠️ NON-NEGOTIABLE: LIVE TRACKING PRECISION ⚠️

**THIS IS A LIVE CLIMATE MONITORING DASHBOARD WITH SENSOR DATA EVERY SECOND.**

### MANDATORY Aggregation Thresholds

| Duration | Source | Rationale |
|----------|--------|-----------|
| **< 1 hour** | Raw measurement table | Live tracking, every 1s reading |
| **1h - 6h** | measurement_1min | 1-min buckets capture swings in half-day views |
| **6h - 24h** | measurement_5min | Balance detail vs performance |
| **> 24h** | measurement_hourly | Long-term trends |

### FORBIDDEN

| Action | Why |
|--------|-----|
| Using hourly aggregate for <24h | **HIDES CRITICAL SWINGS** |
| Using 5-minute aggregate for ≤6h | **LOSES PRECISION** |
| Any aggregation for <1h | **MUST BE RAW DATA** |
| Removing min/max from aggregates | **SWINGS BECOME INVISIBLE** |

### Environmental Reality

- **Sensors report every 1 second**
- **Temperature can swing 5°C in minutes**
- **Humidity can swing 40% in 5 minutes**
- **Users MUST see these swings to respond**

---

## GRAFANA

Grafana runs in container `projectcea_grafana` on `iskraprojectcea:3001`.
It is the **only** consumer of PostgreSQL data. All queries use `get_sensor_data_optimized(sensors[], from, to)`, which returns `ts, sensor_name, value, min_val, max_val` and automatically selects the optimal aggregate based on time range.

---

## REDIS CONFIGURATION

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `sensor:{name}` | STRING | 10s | Current value for automation |
| `sensor:{name}:ts` | STRING | 10s | Timestamp of current value |
| `sensor:raw` | STREAM | 1.1M entries (~24h) | Buffer for automation services |

**Redis is NOT queried by Grafana.** Grafana uses PostgreSQL only.

---

## ANTI-PATTERNS (CRITICAL)

| Never | Reason | Use Instead |
|-------|--------|-------------|
| Query without time filter | Scans all chunks | Always add `time >=` filter |
| Use hourly aggregate for <7d | Loses swing visibility | Use 1min or 5min |
| Query Redis from Grafana | Complexity, no fallback | PostgreSQL only |
| Remove min/max from rollups | Hides 40% humidity swings | Keep envelope data |
| Set maxDataPoints > 2000 | Browser performance degradation | Keep at 1000 |
| Refresh 24h panel at 1s | Wasted load | Use 30s refresh |

---

## DATA RETENTION POLICY (AI-OPTIMIZED)

Conservative retention for AI/ML training. 512GB SSD available.

### Retention Rules

| Table | Full Resolution | Downsampled | Notes |
|-------|-----------------|-------------|-------|
| measurement | 1 year | Indefinite hourly | Primary AI training data |
| effective_setpoints | 1 year | Indefinite hourly | Control decisions |
| automation_state | 90 days | Indefinite hourly | Device behavior |
| can_messages | 30 days | None | Raw debug data |

### Why Conservative Retention

AI training needs historical patterns, seasonal variation, and spike examples. 512GB SSD holds years with compression.

### Compression Strategy

TimescaleDB compression enabled on all hypertables:
- effective_setpoints: compress after 7 days
- automation_state: compress after 7 days
- measurement: compress after 30 days

Expected compression ratio: 10-20x for time-series data

---

## MULTI-CLUSTER SCHEMA NOTES

The measurement table already has `location` and `cluster` columns. All queries MUST filter by both. Composite indexes: `(location, cluster, time)` for time-range queries, `(location, cluster, sensor_type)` for sensor lookups.

---

*Last updated: 2026-07-12*
