# CEA Database Schema Requirements

## Validation Surface

- Database changes are validated in production via service health endpoints,
  journals, and Grafana/data freshness checks; this repository does not retain
  a standalone DB test suite post-campaign.

## Overview

This document describes the normalized database schema for the CEA (Controlled Environment Agriculture) project. The schema implements a hierarchical structure for rooms, racks, devices, and sensors, with a unified time-series measurement table.

## Database Engine

- **PostgreSQL** (latest stable)
- **TimescaleDB** extension for time-series optimization

## Physical primary (mothernode) — streaming WAL for Iskra

The Pi primary that hosts `cea_sensors` feeds the **iskraprojectcea** standby. These settings are enforced in `postgresql.conf` / `ALTER SYSTEM` (runtime `SHOW` is authoritative):

| Setting | Value | Notes |
|---------|-------|--------|
| `wal_keep_size` | `4GB` | Extra WAL buffer beyond slot retention. |
| `max_slot_wal_keep_size` | `16GB` | Safety cap: if a replication slot falls this far behind on WAL bytes, PostgreSQL may invalidate the slot; re-base the replica. |
| `max_wal_senders` | `5` | Headroom for `pg_basebackup` + standby; **requires PostgreSQL restart** to change. |
| `wal_level` | `replica` | Required for physical replication. |

`wal_keep_size` and `max_slot_wal_keep_size` accept `systemctl reload postgresql@15-main`. Changing `max_wal_senders` requires `systemctl restart postgresql@15-main` (brief disconnect for app pools; services reconnect).

Replication slot **`iskra_recovery`** must exist on the primary (`pg_create_physical_replication_slot`) and be **consumed** by the standby (`REPLICATION_SLOT` in iskra `.env`). An unused slot with `restart_lsn` NULL does not pin WAL; the standby can fall off after `wal_keep_size` is exceeded.

**Iskra standby (`projectcea_database`):** enable **`hot_standby_feedback = on`** (set in `iskra_stack/docker-entrypoint-replica.sh` via `postgres -c`) so Grafana/`redis_sync` queries are not canceled by **`conflict with recovery`** during replay. The primary receives xmin hints and may retain dead tuples a bit longer; this is normal for an analytics replica — rely on autovacuum on the Pi. On the same `-c` path, default **`jit = off`** and **`max_parallel_workers_per_gather = 0`** so overlapping Grafana SELECTs avoid parallel-worker churn under frequent cancellation (CPU headroom does not fix that failure mode).

## Schema Structure

### Metadata Tables (Normalized Hierarchy)

1. **room**
   - `room_id` (PK, SERIAL)
   - `name` (TEXT, UNIQUE)
   - `target_vpd` (REAL, optional)
   - `target_temp` (REAL, optional)
   - `created_at` (TIMESTAMPTZ)

2. **rack**
   - `rack_id` (PK, SERIAL)
   - `room_id` (FK → room)
   - `name` (TEXT)
   - `created_at` (TIMESTAMPTZ)
   - UNIQUE(room_id, name)

3. **device**
   - `device_id` (PK, SERIAL)
   - `rack_id` (FK → rack, nullable)
   - `name` (TEXT)
   - `type` (TEXT)
   - `ip_address` (TEXT, optional)
   - `serial_number` (TEXT, optional)
   - `created_at` (TIMESTAMPTZ)

4. **sensor**
   - `sensor_id` (PK, SERIAL)
   - `device_id` (FK → device)
   - `name` (TEXT)
   - `unit` (TEXT)
   - `data_type` (TEXT)
   - `channel` (INTEGER, optional)
   - `calibration_offset` (REAL, optional, default 0.0)
   - `created_at` (TIMESTAMPTZ)
   - UNIQUE(device_id, name)

### Time-Series Table

**measurement** (TimescaleDB hypertable)
- `time` (TIMESTAMPTZ, PK)
- `sensor_id` (INTEGER, FK → sensor, PK)
- `value` (REAL)
- `status` (TEXT, optional)

**Time zone semantics:**
- Timestamp columns use `TIMESTAMPTZ` so stored values represent real instants
  independent of display timezone.
- Production DB sessions use Quebec local time (`America/Toronto`) for operator
  display and local schedule processing.
- Exports for analytics, AI training, backups, or cross-system processing must
  explicitly convert to UTC ISO output, for example:
  `to_char(time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`.
  Never depend on the active DB session timezone for exported files.

**Indexes:**
- `(sensor_id, time DESC)` - Primary index for fast queries
- `(time DESC)` - Time index for chunking
- `(sensor_id)` - Sensor lookup index

**Compression:**
- Enabled on chunks older than 90 days
- Segment by `sensor_id` for optimal compression

**Continuous Aggregates:**
- `measurement_hourly`: Hourly min/max/avg per sensor
- `measurement_daily`: Daily min/max/avg per sensor

### View: `latest_sensor_values` (Grafana / `redis_sync`)

- **Purpose:** One row per registered sensor — the latest `measurement` row —
  joined to `sensor` / `device` / `room` metadata for **`redis_sync`** and any
  Postgres “latest” panels. **`redis_sync`** mirrors rows to **`sensor:<name>`**
  / **`sensor:<name>:ts`** and builds **Grafana Flower table hashes**
  **`cea:grafana:flower_averages`**, **`cea:grafana:flower_front`**,
  **`cea:grafana:flower_back`** (see **`Infrastructure/iskra_stack/scripts/redis_sync.py`**).
- **Required shape:** Implemented as `sensor` **`JOIN LATERAL … ON TRUE`**
  `(SELECT time, value, status FROM measurement mm WHERE mm.sensor_id = s.sensor_id ORDER BY time DESC LIMIT 1)` so the planner uses **one index probe per
  sensor** instead of scanning all hypertable chunks (the old `DISTINCT ON (sensor_id) … ORDER BY sensor_id, time DESC` over `measurement` forced multi-million-row paths for `max(time)`-style work).
- **Apply DDL on the Pi primary only**; streaming replica inherits the view
  definition. Source files: `grafana_optimized_views.sql`,
  `migrate_latest_sensor_values_lateral.sql`.

### Flower Room Flower-sector naming (`*_f` / `*_b`)

- Grafana Flower dashboards expect **`rh_f`** and **`vpd_f`** logical sensors on the **front CAN node** (device **`Node 2`**, same device as `dry_bulb_f`). If those rows were missing, provision them with **`migrate_flower_front_rh_vpd_sensors.sql`** on the primary (idempotent `ON CONFLICT DO NOTHING`).
- **Operational truth:** live greenhouse judgment uses **`*_b`** when the front cluster is unplugged or silent—no rows for `*_f` is normal hardware-wise until CAN returns.
- **Dashboard contract:** live stat tables MUST NOT **inner-join** front + back for tier averages when front has zero samples — averages must allow **back-only** values when **`*_f`** is missing. On Iskra, **`redis_sync`** applies the same rule when building **`cea:grafana:flower_averages`**; Grafana reads **`HGETALL`** on that key (not Postgres).

### Optional Tables

1. **crop_batch**
   - `batch_id` (PK, SERIAL)
   - `crop_name` (TEXT)
   - `start_date` (DATE)
   - `end_date` (DATE, optional)
   - `room_id` (FK → room)
   - Linked to **`calendar_event.crop_batch_id`** when created by the flower grow wizard.

2. **calendar_event** (see `add_calendar_tables.sql`)
   - Planned grow phases, tasks; soft-delete via `deleted_at`; `ical_uid` for CalDAV; `grow_plan_id` UUID for bulk plans.

3. **calendar_room_profile**, **calendar_mode_application**, **calendar_sync_connection**, **grow_plan_idempotency**
   - See `Infrastructure/database/add_calendar_tables.sql` and `Infrastructure/frontend/calendar-requirements.txt`.

2. **setpoints** (Created and managed by automation-service)
   - `id` (PK, BIGSERIAL)
   - `location` (TEXT) - e.g., "Flower Room"
   - `cluster` (TEXT) - e.g., "back", "front", "main"
   - `temperature` (REAL) - Temperature setpoint in Celsius
   - `humidity` (REAL) - Humidity setpoint in percent
   - `co2` (REAL) - CO2 setpoint in ppm
   - `vpd` (REAL) - VPD setpoint in kPa
   - `mode` (TEXT) - "DAY", "NIGHT", "TRANSITION", or NULL (legacy/default)
   - `updated_at` (TIMESTAMPTZ) - Last update timestamp
   
   **Note**: This table is created automatically by the automation-service on startup. 
   See `Infrastructure/database/SETPOINTS_TABLE_EXPLANATION.md` for details.
   
   **History requirement**: Setpoint writes must append (INSERT) instead of overwrite so historical values are preserved. Readers must `ORDER BY updated_at DESC LIMIT 1` to get the latest per mode; time-series (Grafana) should pick the latest row where `updated_at <= time` to show past setpoints correctly.

3. **actuator_events**
   - `event_id` (PK, SERIAL)
   - `device_id` (FK → device)
   - `action` (TEXT)
   - `value` (REAL, optional)
   - `time` (TIMESTAMPTZ)
   - Hypertable for time-series optimization

## Performance Requirements

- **Ingestion Rate**: 50 sensors × 1 sample/second = 4.3M datapoints/day
- **Retention**: 90 days raw data, then compress
- **Compression**: Automatic compression on chunks older than 90 days
- **Query Performance**: 
  - **Iskra Flower operator graphs (panel ref A):** use **`get_sensor_data_optimized`** — **≤ 1 h** raw **`measurement`**; **> 1 h–≤ 6 h** **`measurement_1min`**; **> 6 h–≤ 24 h** **`measurement_5min`**; **> 24 h** **`measurement_hourly`** (see **`grafana_performance_migration.sql`**; existing DBs: **`migrate_get_sensor_routing_1min_through_6h.sql`**). Routing compares **`FLOOR` of span length in whole seconds** so Grafana presets slightly over a nominal boundary (e.g. a few ms past 6 h) do not skip to the next tier. After DDL, run **`verify_get_sensor_routing.sql`** to confirm the live function and **~60 s** median gaps for a 3 h window. **`measurement_daily`** exists but is **not** selected by that function today.
  - **Other APIs / ad-hoc:** Live/short (≤1 hour): raw **`measurement`** / **`measurement_with_metadata`** where appropriate; multi-day and historical analytics: prefer continuous aggregates or compressed chunk reads as documented elsewhere.

## Migration

### Flower Room: devices / DB control cluster → `main`

After updating `automation_config.yaml` so Flower **`devices:`** use **`main` only**, run **`migrate_flower_devices_to_main.sql`** (with backup) so Postgres rows for schedules, `climate_periods`, `device_mappings`, etc. use **`Flower Room` + `main`**. CAN node IDs and Redis sensor keys remain **`front`** / **`back`** for telemetry suffixes.

### From can_messages to Normalized Schema

The migration script (`migrate_to_normalized_schema.py`) performs:

1. **Metadata Creation**:
   - Creates rooms from node_id mapping:
     - node_id 1 → "Flower Room" (back) — **sensor cluster** for CAN telemetry only
     - node_id 2 → "Flower Room" (front) — **sensor cluster** for CAN telemetry only (Automation **devices** / control DB use **`Flower Room` + `main`**; see `migrate_flower_devices_to_main.sql`.)
     - node_id 3 → "Veg Room" (main)
     - node_id 4 → "Lab" (main)
     - node_id 5 → "Outside" (main)
   - Creates devices: One per unique node_id
   - Creates sensors: One per unique sensor name pattern

2. **Data Migration**:
   - Extracts sensor values from `can_messages.decoded_data` JSONB
   - Inserts into `measurement` table with proper sensor_id references
   - Preserves all timestamps and values

3. **Verification**:
   - Row counts
   - Value ranges
   - Timestamp ranges

## API Endpoints

### POST /api/v1/measurement

Ingest a new measurement.

**Request:**
```json
{
  "time": "2024-01-15T10:30:00Z",
  "sensor_id": 123,
  "value": 23.5,
  "status": "ok"
}
```

**Response:** 201 Created
```json
{
  "time": "2024-01-15T10:30:00Z",
  "sensor_id": 123,
  "value": 23.5,
  "status": "ok",
  "message": "Measurement recorded for sensor dry_bulb_b (ID: 123)"
}
```

### GET /api/v1/measurement/sensor/{sensor_id}

Get measurements for a specific sensor.

**Query Parameters:**
- `start_time` (optional): ISO 8601 timestamp
- `end_time` (optional): ISO 8601 timestamp
- `limit` (optional, default: 100, max: 1000)

## Grafana Integration

- **TimescaleDB contract:** dashboards and helper SQL MAY use **`time_bucket`**, **`last`**, and continuous-aggregate views. There is no supported “vanilla PostgreSQL only” mode for operator graphs; replica and primary MUST run TimescaleDB with the extension loaded.
- **Postgres datasource (Iskra):** provisioned from **[`../iskra_stack/provisioning/datasources/datasources.yaml.template`](../iskra_stack/provisioning/datasources/datasources.yaml.template)** — **`jsonData.database`** MUST be **`cea_sensors`** (Grafana 12+ SQL path). Pool **`connMaxLifetime`** is kept short (**90** s) so Grafana does not reuse TCP sessions the replica already closed; if panels show **`db query error` / `driver: bad connection`**, restart **`projectcea_grafana`** after confirming **`projectcea_database`** is healthy.
- **Iskra Flower dashboard (mandatory):** provisioned **[`Infrastructure/iskra_stack/dashboards/flower_sector/flower_sector.json`](../iskra_stack/dashboards/flower_sector/flower_sector.json)** — main climate graph (panel **id 4**, ref **A**) and **CO₂ & Pressure** (panel **id 5**, ref **A**) **MUST** call **`get_sensor_data_optimized(...)`** defined in **[`grafana_performance_migration.sql`](grafana_performance_migration.sql)** (routing thresholds also updated by **[`migrate_get_sensor_routing_1min_through_6h.sql`](migrate_get_sensor_routing_1min_through_6h.sql)** on already-provisioned primaries). **Prerequisites on the Pi primary** (replica applies via WAL): run that migration (or equivalent DDL), create continuous aggregates **`measurement_1min`**, **`measurement_5min`**, **`measurement_hourly`**, **`measurement_daily`**, add refresh policies, create **`measurement_*_grafana`** views, **backfill** CAGGs for historical windows, and grant **`cea_user` EXECUTE** on **`get_sensor_data_optimized`** (and **`get_sensor_stats`** if used). Missing objects cause Grafana SQL errors or empty series.
- **Flower cluster tables (panels 1–3):** **Averages**, **Front Cluster**, **Back Cluster** **MUST** use the **Redis** datasource (**`HGETALL`**) on **`cea:grafana:flower_averages`**, **`cea:grafana:flower_front`**, **`cea:grafana:flower_back`**, populated by **[`../iskra_stack/scripts/redis_sync.py`](../iskra_stack/scripts/redis_sync.py)** each sync interval. On Iskra, **`SYNC_INTERVAL_SEC`** in **`docker-compose.yml`** should stay **short** (default **1 s**) so these tables track the dashboard’s **1 s** refresh; graphs query Postgres every refresh, so a long sync interval feels stale. Rebuild/restart **`projectcea_redis_sync`** after editing that script. The Grafana Redis plugin returns **`HGETALL`** as **one wide row** (each hash field is a **column**), not **`Field` / `Value`** rows — those panels **MUST** apply a **Transpose** transformation with **`firstFieldName`: Sensor** and **`restFieldsName`: Value** so the table shows the usual two-column **Sensor | Value** layout.
- **Trailing-edge behavior:** continuous aggregates refresh on a schedule; the **last few minutes** of a curve can lag raw **`measurement`** — operators may perceive light “buffering” vs pre-CAGG dashboards. **`get_sensor_data_optimized`** uses **raw `measurement`** only when the selected span is **≤ 1 hour** (see **`Infrastructure/REQUIREMENTS.md`** routing table).
- **Statistics table:** the Flower **Statistics** panel may remain on **`measurement_with_metadata`** with an explicit **`sensor_name IN (...)`** filter because **`get_sensor_stats`** returns **min/max/avg** only (no **Std Dev**). Future work: extend **`get_sensor_stats`** or add a CAGG-aware stats path if **Std Dev** must come from rollups.

### Query Examples

**Recent Data (< 90 days):**
```sql
SELECT 
    m.time,
    s.name as sensor_name,
    r.name as room_name,
    m.value
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
WHERE r.name = 'Flower Room'
AND m.time > NOW() - INTERVAL '24 hours'
ORDER BY m.time DESC;
```

**Historical Data (> 90 days) - Use Aggregates:**
```sql
SELECT 
    md.time,
    s.name as sensor_name,
    r.name as room_name,
    md.avg_value as value
FROM measurement_daily md
JOIN sensor s ON md.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
WHERE r.name = 'Flower Room'
AND md.time > NOW() - INTERVAL '30 days'
ORDER BY md.time DESC;
```

## Schema Audit (current)

- Reviewed automation-related tables (`schedules`, `setpoints`, `pid_parameters`, `config_versions`, `effective_setpoints`). No unused tables/columns identified for removal; keep `ramp_in_duration` aligned with UI expectations.

## Monitoring

### Monitoring Scripts

- `monitor_can_processor.sh` - Monitor CAN processor service, CAN bus interface, Redis stream, and recent CAN messages
- `monitor_redis_stream.sh` - Monitor Redis stream (`sensor:raw`) and display live sensor values

### Backpressure Monitoring

- Stream length > 5000: Warning logged
- Monitor pending message count
- Monitor ingestion rate (measurements/second)

## Files

- `Infrastructure/database/cea_schema.sql` - Schema creation script

## Weather Sensors

The weather service collects data from YUL Airport (CYUL) via Aviation Weather Center METAR API and stores it in the "Outside" room.

### Weather Sensors

The following sensors are created under the "Weather Station YUL" device:

- `outside_temp` - Outside temperature (°C)
- `outside_rh` - Outside relative humidity (%)
- `outside_pressure` - Atmospheric pressure (hPa)
- `outside_wind_speed` - Wind speed (m/s)
- `outside_wind_direction` - Wind direction (degrees, 0-360)
- `outside_precipitation` - Precipitation (mm, may be null if no precipitation)

### Data Source

- **Service**: `weather-service` (port 8003)
- **API**: Aviation Weather Center METAR API
- **Station**: CYUL (Montréal-Pierre Elliott Trudeau International Airport)
- **Poll Interval**: 15 minutes
- **Room**: "Outside"
- **Device**: "Weather Station YUL" (rack_id is NULL)

## Autostart Configuration

Run `enable_autostart.sh` to enable all services for boot autostart:

```bash
./enable_autostart.sh
```

Services enabled:
- redis-server
- postgresql
- can-setup
- can-processor
- cea-backend
- automation-service
- weather-service





