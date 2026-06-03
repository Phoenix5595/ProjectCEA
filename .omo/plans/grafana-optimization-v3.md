# Grafana Performance Optimization Plan v3

**Created:** 2026-01-12
**Status:** PENDING APPROVAL
**Branch:** dev

---

## Executive Summary

| Layer | Current | Target |
|-------|---------|--------|
| **Current Values** | PostgreSQL (DISTINCT ON - slow) | **Redis** (<1ms) |
| **Time-Series** | Some use aggregates, some raw | **All use aggregates** (50-100ms) |
| **automation_state** | 4.7M rows, no hypertable | **Hypertable + index** |
| **Day/Night Overlays** | Removed (were using generate_series) | **control_history function** |
| **Documentation** | Missing patterns | **Updated AGENTS.md files** |

---

## Architecture: Data Flow

```
SENSOR DATA FLOW
================
Physical Sensors → CAN Bus → CAN Processor → Redis (real-time) + PostgreSQL (historical)

AUTOMATION DECISION LOOP
========================
Automation Service reads Redis → Evaluates Rules → Decides → Executes → Updates Redis + PostgreSQL

GRAFANA DASHBOARDS
==================
Current Value Panels → Redis (<1ms)
Time-Series Panels → PostgreSQL Aggregates (50-100ms)
```

---

## Phase 1: Install Grafana Redis Plugin

### 1.1 Install Plugin
```bash
sudo grafana-cli plugins install redis-datasource
sudo systemctl restart grafana-server
```

### 1.2 Configure Data Source (Grafana UI)
- Name: `Redis`
- Address: `localhost:6379`
- No authentication

---

## Phase 2: Update Current-Value Panels to Use Redis

### Panels to Update (Flower Room)

| Panel | Current Query (PostgreSQL) | New Query (Redis) |
|-------|---------------------------|-------------------|
| Averages Table | DISTINCT ON from measurement | MGET sensor:dry_bulb_f sensor:wet_bulb_f ... |
| Front Cluster Table | DISTINCT ON from measurement | MGET sensor:*_f |
| Back Cluster Table | DISTINCT ON from measurement | MGET sensor:*_b |

### Panels to Update (Vegetation Room)

| Panel | Current Query (PostgreSQL) | New Query (Redis) |
|-------|---------------------------|-------------------|
| Sensor Values Table | DISTINCT ON from measurement | MGET sensor:*_v |

### Redis Key Reference

| Key Pattern | Example | Data |
|-------------|---------|------|
| sensor:{name} | sensor:dry_bulb_f | Current value (float) |
| sensor:{name}:ts | sensor:dry_bulb_f:ts | Timestamp of value |
| automation:{room}:{cluster}:{device} | automation:Flower Room:main:light_1 | Device state |
| effective_setpoint:{room}:{cluster}:{type} | effective_setpoint:Flower Room:main:heating | Current setpoint |

---

## Phase 3: Optimize automation_state Table

### 3.1 Add Index (Non-Blocking)
```sql
CREATE INDEX CONCURRENTLY idx_automation_state_lookup 
ON automation_state (location, cluster, device_name, timestamp DESC);
```

### 3.2 Convert to Hypertable
```sql
SELECT create_hypertable('automation_state', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    migrate_data => true,
    if_not_exists => true);
```

### 3.3 Add Compression Policy
```sql
SELECT add_compression_policy('automation_state', INTERVAL '7 days');
```

---

## Phase 4: Add Day/Night Overlays Back

### 4.1 Create Fast Overlay Function
```sql
CREATE OR REPLACE FUNCTION get_light_periods(
    p_location TEXT, 
    p_from TIMESTAMPTZ, 
    p_to TIMESTAMPTZ
) RETURNS TABLE (time TIMESTAMPTZ, is_day BOOLEAN) AS $$
SELECT timestamp, new_state > 0
FROM control_history
WHERE location = p_location
  AND device_name LIKE 'light_%'
  AND timestamp >= p_from AND timestamp <= p_to
ORDER BY timestamp;
$$ LANGUAGE SQL STABLE;
```

### 4.2 Dashboard Overlay Query
```sql
SELECT time, 
       CASE WHEN is_day THEN 100 ELSE NULL END AS DAY Period
FROM get_light_periods('Flower Room', $__timeFrom()::timestamptz, $__timeTo()::timestamptz)
```

---

## Phase 5: Documentation Updates

### 5.1 Update Infrastructure/database/AGENTS.md

Add GRAFANA PERFORMANCE PATTERNS section with:
- Query type → Source → Method table
- Auto-routing function docs
- Continuous aggregates table

Add to ANTI-PATTERNS:
- DISTINCT ON for current values → Use Redis
- Query measurement_with_metadata → Use get_sensor_data_optimized()
- Query automation_state without time filter → Always add timestamp filter
- Use generate_series for overlays → Use get_light_periods()

### 5.2 Update Infrastructure/frontend/AGENTS.md

Add GRAFANA DASHBOARDS section with:
- Dashboard locations and sync script
- Data sources table (Redis vs PostgreSQL)
- Query patterns by panel type

### 5.3 Update Infrastructure/automation-service/AGENTS.md

Add Data Flow section explaining:
1. CAN Processor writes to Redis + PostgreSQL
2. Automation Service reads from Redis for control
3. Automation Service writes state to Redis + PostgreSQL

### 5.4 Update Root AGENTS.md

Add to ANTI-PATTERNS:
- Query PostgreSQL for current values → Use Redis
- Skip documentation updates → Always update AGENTS.md

Add POST-WORK CHECKLIST section:
- Update AGENTS.md files
- Add discovered anti-patterns
- Document new functions/tables
- Run sync_dashboards.sh if needed
- Commit docs with code

---

## Phase 6: Implementation Order

| Step | Task | Est. Time |
|------|------|-----------|
| 1 | Install Redis Grafana plugin | 2 min |
| 2 | Configure Redis data source | 3 min |
| 3 | Update current-value panels to Redis | 20 min |
| 4 | Add index to automation_state | 5 min |
| 5 | Convert automation_state to hypertable | 10 min |
| 6 | Create get_light_periods() function | 2 min |
| 7 | Add day/night overlays to dashboards | 10 min |
| 8 | Update database/AGENTS.md | 5 min |
| 9 | Update frontend/AGENTS.md | 5 min |
| 10 | Update automation-service/AGENTS.md | 3 min |
| 11 | Update root AGENTS.md | 3 min |
| 12 | Test all panels < 2 seconds | 5 min |
| 13 | Commit to dev branch | 2 min |

**Total: ~75 minutes**

---

## Phase 7: Verification Checklist

- [ ] Redis plugin installed in Grafana
- [ ] Redis data source configured
- [ ] Current-value panels query Redis (<1ms)
- [ ] Time-series panels use aggregates (<100ms)
- [ ] Day/night overlays display correctly
- [ ] automation_state is hypertable with index
- [ ] 24h dashboard loads < 2 seconds
- [ ] 7-day dashboard loads < 3 seconds
- [ ] All 4 AGENTS.md files updated
- [ ] Changes committed to dev branch

---

## Open Question

**Clarify latest_sensor_values usage:**
- Database AGENTS.md mentions this PostgreSQL view for latest values
- Proposed: Redis for real-time, view for batch/fallback
- **Awaiting confirmation**

---

**Status:** PENDING APPROVAL
