# ProjectCEA - Comprehensive Agent Guidelines

## ⚠️ CRITICAL URLS - READ THIS FIRST

| Service | URL | Notes |
|---------|-----|-------|
| **Dashboard (Frontend)** | `http://mothernode:8080` | Main CEA dashboard (served via Caddy reverse proxy) |
| **Grafana** | `http://iskraprojectcea:3001` | `projectcea_grafana` container (Phase 5c, 2026-04-19). NOT `localhost:3000` (Pi Grafana decommissioned) and NOT `iskradocker:3000` (pre-5b). |
| **Backend API** | `http://mothernode:8080` | Sensor data (proxied through Caddy `:8080` → `:8000`) |
| **Automation API** | `http://mothernode:8080` | Control & config (proxied through Caddy `:8080` → `:8001`) |
| **Weather** | `http://mothernode:8080` | Weather service (proxied through Caddy `:8080` → `:8003`) |

---

## 🚨 CRITICAL SAFETY RULE — PRODUCTION DATA IS SACRED

**NEVER modify production data unless explicitly requested.**

- The production database is `cea_sensors`. ANY `TRUNCATE`, `DELETE`, `DROP`, or write operation on `cea_sensors` causes **crop damage** (lights off, climate control failure, data loss).
- **Violation is a SEVERE FAILURE.** Production data integrity is non-negotiable.

**Why this matters:** On 2026-07-07, tests with `TRUNCATE TABLE device_registry` connected to `cea_sensors` wiped all devices. The control loop lost all lights → 30+ minutes of darkness → potential crop stress. This must NEVER happen again.

---

## CRITICAL SYSTEM ARCHITECTURE

```
Sensors → Ingestion → Redis + TimescaleDB → Control Loop → Actuators → Frontend/Grafana
```

| Metric | Requirement |
|--------|-------------|
| Control loop latency | ≤5s (target 1-2s) |
| Sensor update rate | 1Hz |
| Redis operations | <1ms |
| DB batch delay | ≤100ms |

---

## WHERE TO LOOK - COMPREHENSIVE GUIDE

### API Route Inventory

- Backend (8000): `GET /api/sensors/{room}/{cluster}`, `GET /api/health`
- Automation (8001): `GET /api/devices/{room}/{cluster}`, `PATCH /api/devices/{device_id}`, `GET /api/schedules`, `GET /api/light-target-intensities`, `GET /api/light-programs`, `GET /api/setpoints`, `GET /api/mode-parameters`

---

## NON-NEGOTIABLE SYSTEM RULES

### Real-Time Constraints (Critical)

| Rule | Reason |
|------|--------|
| **1/sec sampling minimum** | AI training requires full resolution data |
| **100ms max DB batch delay** | Live Redis instant, DB can buffer |
| **1-5s control tick max** | Deterministic environmental control |
| **<1ms Redis operations** | Control loop performance requirement |

### Control Algorithm Requirements (Critical)

| Rule | Implementation |
|------|----------------|
| **VPD is master controller** | VPD → humidity setpoint cascade |
| **Humidity is slave to VPD** | Tracks VPD-derived targets |
| **Safety interlocks mandatory** | Heating failure → exhaust inhibition |
| **No hardcoded setpoints** | Database-driven configuration |

### Data Management Rules (Critical)

| Rule | Implementation |
|------|----------------|
| **Query DB with time filters** | Always include time range |
| **Use aggregates for long ranges** | <1h: raw, 1-3h: 1min, 3-24h: 5min, >24h: hourly |
| **Never hourly aggregates for <7d** | Hides critical dynamics |

### Repository Pattern Architecture (Critical)

- **ControlAction Repository**: Control action logging and retrieval
- **Device Repository**: Device states and hardware configurations
- **PID Repository**: PID controller parameters and tuning data
- **RoomMode Repository**: Room operational modes and transitions
- **Schedule Repository**: Non-light DAY/NIGHT schedule rows (heaters, fans, dehumidifiers). Light scheduling is handled by `light_target_intensity_repo` and `light_programs_repo`.
- **LightTargetIntensity Repository**: Per-light, per-mode intensity anchors
- **LightPrograms Repository**: Supplemental and override light programs with time-slot and cycle mode support
- **Schedule cache invalidation**: `schedule_repo.get_schedules()` uses StateManager keys `schedules:all`, `schedules:loc:{location}`, and `schedules:loc:{location}:cluster:{cluster}`. Any write that changes `schedules` rows must invalidate **all** keys that unfiltered `get_schedules()` can hit; otherwise the in-process `Scheduler` reloads stale rows and light targets lag the DB.
- **Sensor Repository**: Sensor data validation and storage
- **Setpoint Repository**: Environmental setpoints and targets
- **Config Repository**: System configuration and parameter storage

### Schedule Architecture (3-Concept Model)

**Concept 1: Photoperiod** — `mode_parameters.day_start_time` and `night_start_time` per room, per mode. Overnight-capable. Missing mode_parameters → returns True (lights ON at 10%) + CRITICAL alarm.

**Concept 2: Per-Light Intensity** — `(device_id, mode_id) → target_intensity` with CHECK (0-100). Mode-specific. 10% hardcoded failsafe if no row exists + WARNING alarm.

**Concept 3: Light Programs** — `supplemental` (adds light in dark) or `override` (replaces intensity in sun). Time-slot or cycle mode. Priority DESC, ties broken by created_at ASC. Device-level or room-level.

### Device Registry

| Field | Description |
|-------|-------------|
| **device_id** | Primary key |
| **location** | Room name |
| **cluster** | Device cluster (`main`) |
| **device_name** | Canonical identifier |
| **display_name** | Human-readable label |
| **device_type** | `light`, `fan`, `heater`, etc. |
| **channel** | Relay channel (0-15) |
| **dimming_enabled** | Boolean |
| **dimming_type** | `dfrobot` or null |
| **dimming_board_id** | I2C address |
| **dimming_channel** | Dimmer channel |
| **safety_level** | `critical`, `standard`, etc. |
| **pid_enabled** | Boolean |
| **interlock_with** | Device name or null |
| **pid_setpoints** | JSON |
| **per_room_index** | Integer |

- 13 devices total: 6 Flower Room + 7 Veg Room, seeded from `automation_config.yaml` `devices:` section

### Light Target Intensity

| Attribute | Value |
|-----------|-------|
| **Schema** | `(device_id, mode_id) → target_intensity (REAL, 0-100, default 10.0)` |
| **Creation** | Direct SQL (NOT alembic) |
| **Current state** | EMPTY — operator sets values via frontend slider |
| **Fallback** | `MINIMUM_LIGHT_INTENSITY = 10.0` when table empty |

### Light Programs

| Attribute | Value |
|-----------|-------|
| **Schema** | `device_id, location, cluster, mode_id, name, program_type, start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds, target_intensity, ramp_up_minutes, ramp_down_minutes, priority, enabled` |
| **Types** | `supplemental` (adds light in dark) or `override` (replaces intensity in sun) |
| **Creation** | Direct SQL (NOT alembic) |
| **Current state** | EMPTY |

### Relay Steal Logic

Confirmed relay steals are performed atomically by `DeviceRegistryService`; the response includes `displaced_device_id`.

### Display Name

`getChannelDisplayName()` in `relayViewModel.ts` returns `display_name` for **ALL** device types (not just lights). Falls back to `device_name` (canonical) when `display_name` is null.

### Scheduler Caches

The `Scheduler` class maintains 4 in-memory caches updated atomically: `update_mode_parameters()`, `update_light_intensities()`, `update_light_programs()`, `update_device_lookup()`. The `_ready` flag blocks ticks until populated.

### Cluster Topology Contract (Critical)

The codebase distinguishes **device clusters** from **sensor sub-clusters**. Mixing them is the most common source of "endpoint returns nothing" bugs.

- `main` = device cluster only. Never a sensor sub-cluster.
- `front`/`back` = sensor sub-clusters only. Never on the device plane.

```
Room
 └── main                 (device cluster)
      ├── front           (Flower Room sensors)
      └── back            (Flower Room sensors)
```

Per-room mapping (canonical, **single source of truth**):

| Room          | Device cluster | Sensor URL slug(s) |
|---------------|----------------|--------------------|
| `Flower Room` | `main`         | `front` ‡, `back`  |
| `Veg Room`    | `main`         | `main` †           |
| `Lab`         | `main`         | `main` †           |
| `Outside`     | `main`         | `main` †           |

‡ Flower Room `back` is wired and producing telemetry; `front` is planned but not in service yet. `GET /api/sensors/Flower Room/front` returns HTTP 200 with empty payload — expected, not a bug.

† Unsplit rooms reuse `main` as a sensor URL sentinel meaning "no sub-clusters". `main` is **not** registered as a sensor sub-cluster in topology data.

**Sources of truth:** `Infrastructure/shared/cluster_topology.py` (Python) and `Infrastructure/frontend/src/config/clusterTopology.ts` (frontend). Keep them in sync.

**API contract:** `GET /api/devices/{room}/{cluster}` → device cluster only (passing `front`/`back` → 400). `GET /api/sensors/{room}/{cluster}` → sensor URL slugs only (Flower accepts `front`/`back`; unsplit rooms accept `main` only). Wrong cluster → 400 with hint.

**Frontend rule:** Device polling uses `ZONES` (all `cluster: "main"`). Sensor polling uses `getSensorPollZones()` (Flower → `front`+`back`; unsplit → `main`). Never iterate sensor sub-clusters against the device endpoint.

### Hardware Rules (Critical)

| Rule | Hardware Mapping | Reason |
|------|------------------|--------|
| **MCP23017 = relays only** | I2C bus 0, address 0x27, channels 0-15 | Digital on/off control |
| **DFR0971 = dimming only** | I2C bus 1, addresses 0x88/0x89/0x90 | Analog 0-10V control |
| **Never swap roles** | MCP for dimming or DFR for relays | Hardware capability limits |
| **Bus separation mandatory** | Different I2C buses for relay/dimming | Prevent interference |

### Operational Rules (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **Rollback <30s** | `./rollback-deploy.sh` (after a successful deploy, uses `deploy_state.json`) | Minimize crop stress |
| **No bare excepts** | Proper exception handling | System reliability |
| **Config validation required** | Service startup fails on invalid config | Prevent runtime errors |
| **Never touch working systems** | Unless explicitly requested | Production stability |

### Subagent QA Safety (Critical — Permanent Ban)

**F3 (Real Manual QA) is PERMANENTLY BANNED from making HTTP requests to production endpoints.** On 2026-07-07, an F3 QA subagent issued `DELETE` requests to the production automation API (port 8001), wiping the `device_registry` table. The control loop lost all devices, lights went dark for 36+ hours, and crops were put under severe stress. This must NEVER happen again.

**Replacement for F3 — Static Checks and Local Verification Only:**

| Check | Command |
|-------|---------|
| **Backend lint** | `cd Infrastructure/automation-service && ruff check .` |
| **Frontend type check** | `cd Infrastructure/frontend && npx tsc --noEmit` |
| **Frontend build** | `cd Infrastructure/frontend && npm run build` |

**Testing:** No automated test suite currently exists. All tests were removed. Verify changes via `ruff check .` (backend) and `npx tsc --noEmit && npm run build` (frontend).

**Production HTTP Rules for ALL Subagents:**

- **NO subagent may call DELETE, POST, or PUT against production endpoints.**
- **GET is allowed for read-only verification only.** Production endpoints at ports 8000, 8001, and 8003 may be queried with `GET` to confirm responses, headers, or payload shape. No state change.
- **One exception:** A subagent MAY send `curl -X DELETE` with a non-existent device ID (for example, `999`) to verify the `X-Confirm-Destructive` guard returns HTTP 403. No subagent may DELETE a real device ID. This is a guard-verification probe, not a destructive test.
- **Violation of this rule is a SEVERE FAILURE.** Production system integrity is non-negotiable.

---

## COMMAND REFERENCE

```bash
./deploy.sh                    # Deploy with auto-rollback on health fail
./rollback-deploy.sh           # Point current at previous release
systemctl status automation-service
journalctl -u can-processor -f
psql -U cea -d projectcea
redis-cli
i2cdetect -y 0                 # Scan I2C bus 0 (relays)
i2cdetect -y 1                 # Scan I2C bus 1 (dimming)
```

---

## CONCLUSION

Prioritize system stability and crop safety. Refer to service-specific docs for implementation details.

---

*Last updated: 2026-07-12*
