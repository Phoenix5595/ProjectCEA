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
[Grafana on Raspberry Pi] <-- ONLY consumer of PostgreSQL for visualization
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
| `room_light_schedule` | Config | Light on/off times per room |

## CONTINUOUS AGGREGATES (CRITICAL)

Each aggregate stores **avg + min + max** to preserve swing visibility:

| Aggregate | Bucket | Columns | Use Case |
|-----------|--------|---------|----------|
| `measurement_1min` | 1 min | avg_value, min_value, max_value | 1h-24h queries |
| `measurement_5min` | 5 min | avg_value, min_value, max_value | 1-7d queries |
| `measurement_hourly` | 1 hour | avg_value, min_value, max_value | >7d queries |
| `measurement_daily` | 1 day | avg_value, min_value, max_value | Monthly+ trends |

**WHY min/max**: Humidity can swing 40% in 5 minutes. Averages alone hide these critical events. Min/max envelope ensures extremes are always visible.

---

## ⚠️ NON-NEGOTIABLE: LIVE TRACKING PRECISION ⚠️

**THIS IS A LIVE CLIMATE MONITORING DASHBOARD WITH SENSOR DATA EVERY SECOND.**

### MANDATORY Aggregation Thresholds

| Duration | Source | Points (8 sensors) | Rationale |
|----------|--------|-------------------|-----------|
| **< 1 hour** | Raw measurement table | ~28,800 | Live tracking, every reading |
| **1h - 24h** | measurement_1min | ~11,520 | 1-min buckets capture 40% swings |
| **1d - 7d** | measurement_5min | ~16,128 | Still shows significant events |
| **> 7 days** | measurement_hourly | ~1,344 | Long-term trends |

### FORBIDDEN

| Action | Why |
|--------|-----|
| Using hourly aggregate for <7d | **HIDES CRITICAL SWINGS** |
| Using 5-minute aggregate for <24h | **LOSES PRECISION** |
| Any aggregation for <1h | **MUST BE RAW DATA** |
| Optimizing for "fewer points" over precision | **THIS IS LIVE MONITORING** |
| Removing min/max from aggregates | **SWINGS BECOME INVISIBLE** |

### Environmental Reality

- **Sensors report every 1 second**
- **Temperature can swing 5°C in minutes**
- **Humidity can swing 40% in 5 minutes**
- **Users MUST see these swings to respond**

---

## GRAFANA PERFORMANCE (Pi-Safe)

### Panel Configuration

| Setting | Value | Rationale |
|---------|-------|-----------|
| maxDataPoints | 1000 | Pi browser can handle ~1000 points smoothly |
| interval (realtime) | 1s | Full resolution for <1h |
| interval (24h) | 1m | Uses 1-min aggregate |
| Refresh rate (realtime) | 5s | Live updates |
| Refresh rate (24h) | 30s | Reduce load on longer ranges |

### Performance Targets

| Metric | Target |
|--------|--------|
| Query latency (DB) | < 50ms |
| Points per panel | ≤ 5,000 |
| Payload size | ≤ 200KB |
| End-to-end render | < 100ms |

### Query Function

Use `get_sensor_data_optimized(sensors[], from, to)` for ALL time-series queries.

**Returns**: ts, sensor_name, value, min_val, max_val

Automatically selects optimal aggregate based on time range.

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
| Set maxDataPoints > 2000 | Crashes Pi browser | Keep at 1000 |
| Refresh 24h panel at 1s | Wasted load | Use 30s refresh |

---

## DOCUMENTATION UPDATE POLICY

**When user provides feature/function precision:**
1. Update relevant AGENTS.md immediately
2. Update USER_PREFERENCES.md if it affects preferences
3. Include specific values, thresholds, and rationale
4. Mark as NON-NEGOTIABLE if critical
