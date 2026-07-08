# ProjectCEA - Comprehensive Agent Guidelines

**Generated:** 2026-02-08 | **Branch:** main

## ⚠️ CRITICAL URLS - READ THIS FIRST

| Service | URL | Notes |
|---------|-----|-------|
| **Dashboard (Frontend)** | `http://mothernode:8080` | Main CEA dashboard (served via Caddy reverse proxy) |
| **Grafana** | `http://iskraprojectcea:3001` | `projectcea_grafana` container (Phase 5c, 2026-04-19). NOT `localhost:3000` (Pi Grafana decommissioned) and NOT `iskradocker:3000` (pre-5b). |
| **Backend API** | `http://mothernode:8080` | Sensor data (proxied through Caddy `:8080` → `:8000`) |
| **Automation API** | `http://mothernode:8080` | Control & config (proxied through Caddy `:8080` → `:8001`) |
| **Weather** | `http://mothernode:8080` | Weather service (proxied through Caddy `:8080` → `:8003`)

**Grafana is on iskradocker, NOT localhost!**

---

## 🚨 CRITICAL SAFETY RULE — PRODUCTION DATA IS SACRED

**Tests MUST NEVER connect to production databases. EVER.**

- The production database is `cea_sensors`. Tests MUST use a separate `cea_sensors_test` database.
- ANY test that `TRUNCATE`s, `DELETE`s, `DROP`s, or modifies data in `cea_sensors` will cause **crop damage** (lights off, climate control failure, data loss).
- If a test needs to clean state, it MUST use `cea_sensors_test` (create it with `CREATE DATABASE cea_sensors_test WITH TEMPLATE cea_sensors;` if needed).
- **Violation of this rule is a SEVERE FAILURE.** Production data integrity is non-negotiable.
- Before running ANY test suite, verify the DB connection string points to a test database, NOT `cea_sensors`.

**Why this matters:** On 2026-07-07, tests with `TRUNCATE TABLE device_registry` connected to `cea_sensors` wiped all devices. The control loop lost all lights → 30+ minutes of darkness → potential crop stress. This must NEVER happen again.

---

## COMPREHENSIVE SYSTEM STRUCTURE

```
ProjectCEA/
├── 📁 Infrastructure/           # All production services (see Infrastructure/AGENTS.md)
│   ├── 🚀 automation-service/   # Control loop + frontend (8001)
│   ├── 📊 backend/              # Sensor API + WebSocket (8000)
│   ├── 📡 can-processor-service/# CAN ingestion
│   ├── 🌱 soil-sensor-service/  # RS485 Modbus ingestion
│   ├── 🌤️ weather-service/      # External API ingestion
│   ├── 🎨 frontend/             # React SPA + Grafana
│   ├── 🗄️ database/             # TimescaleDB schema
│   ├── 📋 shared/               # Common libraries
│   └── 🔧 scripts/             # Maintenance scripts
├── 🔌 Sensor_Nodes/             # ESP32 firmware (see Sensor_Nodes/AGENTS.md)
├── 🧠 .sisyphus/                # AI context and planning
├── 📊 ARCHITECTURE.md            # Canonical system documentation
└── 🤖 AGENTS.md                 # This file - AI assistant guidelines
```

---

## CRITICAL SYSTEM ARCHITECTURE

### Data Flow Architecture (Non-Negotiable Pattern)

```
🌡️  Sensors (CAN/Modbus/HTTP)
    ↓ (250kbps CAN / RS485 / API)
📡 Ingestion Services (can-processor, soil-sensor, weather)
    ↓ (instant Redis + batched DB)
💾 Redis (live state) + TimescaleDB (historical)
    ↓ (<1ms Redis reads)
🎛️  Control Loop (automation-service, 1-5s tick)
    ↓ (I2C commands)
⚡ Actuators (MCP23017 relays + DFR0971 dimming)
    ↓ (state updates)
💾 Redis + TimescaleDB (state persistence)
    ↓ (WebSocket + REST APIs)
🖥️  Frontend (React SPA) + Grafana Analytics
```

### Performance Requirements (Hard SLAs)

| Metric | Requirement | Measurement Point |
|--------|-------------|-------------------|
| **Control Loop Latency** | ≤5 seconds max | Sensor read → Actuator response |
| **Target Control Latency** | 1-2 seconds | Normal operation |
| **Sensor Update Rate** | 1Hz (1-second) | All sensor types |
| **Redis Operations** | <1ms | GET/SET operations |
| **Database Batch Delay** | ≤100ms | 50-message threshold |
| **WebSocket Updates** | ≤1 second | Data change → UI update |
| **API Response (95th)** | <200ms | REST endpoint responses |
| **System Uptime** | 99.9% | Monthly availability |
| **Recovery Time** | <30 seconds | Automated rollback |

---

## WHERE TO LOOK - COMPREHENSIVE GUIDE

### Primary Documentation (Read First)

| Priority | Document | Purpose | Location |
|----------|----------|---------|----------|
| **🔴 CRITICAL** | **`ARCHITECTURE.md`** | **Complete system narrative + ASCII schematic** | Project root |
| **🔴 CRITICAL** | **`ARCHITECTURE_SCHEMATIC.md`** | **Mermaid diagrams + structured tables** | Project root |
| **🟡 HIGH** | **This `AGENTS.md`** | **AI assistant instructions + context** | Project root |
### Service-Specific Documentation

| Service Area | Location | Focus |
|---------------|----------|-------|
| **🎛️ Control Logic** | `Infrastructure/automation-service/app/control/` | PID, scheduling, device control |
| **📊 Sensor API** | `Infrastructure/backend/app/routes/` | Data retrieval + WebSocket |
| **📡 CAN Processing** | `Infrastructure/can-processor-service/app/` | Message decoding + distribution |
| **🗄️ Database Schema** | `Infrastructure/database/` | TimescaleDB structure + queries |
| **🎨 Frontend UI** | `Infrastructure/frontend/src/` | React components + state management |
| **📈 Grafana Dashboards** | `Infrastructure/frontend/grafana/dashboards/` | Analytics + visualization |
| **🔧 ESP32 Firmware** | `Sensor_Nodes/ESP32/fullV6/` | Sensor node implementation |
| **⚙️ Deployment** | `deploy.sh`, `rollback-deploy.sh` | Production deploy + symlink rollback + NDJSON `/var/lib/projectcea/deploy.log` |

### Configuration & Validation

| Component | Location | Validation Method |
|------------|----------|-------------------|
| **Hardware Config** | `automation_config.yaml` | Pydantic schema validation |
| **Service Config** | Individual `app/config.py` | Environment validation |
| **Database Schema** | `Infrastructure/database/cea_schema.sql` | TimescaleDB validation |
| **Frontend Build** | `Infrastructure/frontend/package.json` | npm/yarn validation |
| **Systemd Services** | `*.service` files | systemd syntax checking |

---

## NON-NEGOTIABLE SYSTEM RULES

### Real-Time Constraints (Critical)

| Rule | Reason | Violation Impact |
|------|--------|-----------------|
| **1/sec sampling minimum** | AI training requires full resolution data | Model accuracy degradation |
| **100ms max DB batch delay** | Live Redis instant, DB can buffer | Control loop starvation |
| **1-5s control tick max** | Deterministic environmental control | Crop stress, yield loss |
| **<1ms Redis operations** | Control loop performance requirement | Actuator latency |

### Control Algorithm Requirements (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **VPD is master controller** | VPD → humidity setpoint cascade | Physiological plant response |
| **Humidity is slave to VPD** | Tracks VPD-derived targets | Prevents over/under-humidification |
| **Safety interlocks mandatory** | Heating failure → exhaust inhibition | Crop protection |
| **No hardcoded setpoints** | Database-driven configuration | Flexibility, traceability |

### Data Management Rules (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **Query DB with time filters** | Always include time range | Prevent full table scans |
| **Use aggregates for long ranges** | <1h: raw, 1-3h: 1min, 3-24h: 5min, >24h: hourly | Query performance |
| **Never hourly aggregates for <7d** | Hides critical dynamics | Analysis accuracy |
| **Full TDD required** | All new code needs tests | System reliability |

### Repository Pattern Architecture (Critical)

**Database Access Layer**:
- **Repository Pattern**: All data operations go through specialized repository classes
- **ControlAction Repository**: Handles control action logging and retrieval
- **Device Repository**: Manages device states and hardware configurations
- **PID Repository**: Stores and retrieves PID controller parameters and tuning data
- **RoomMode Repository**: Controls room operational modes and transitions
- **Schedule Repository**: Manages light schedules and photoperiod controls
- **Schedule cache invalidation**: `schedule_repo.get_schedules()` uses StateManager keys `schedules:all`, `schedules:loc:{location}`, and `schedules:loc:{location}:cluster:{cluster}`. Any write that changes `schedules` rows (especially `update_light_schedule_target` for POST `/api/lights/.../target`) must invalidate **all** keys that unfiltered `get_schedules()` can hit; otherwise the in-process `Scheduler` reloads stale rows and light targets lag the DB.
- **Sensor Repository**: Handles sensor data validation and storage
- **Setpoint Repository**: Manages environmental setpoints and targets
- **Config Repository**: System configuration and parameter storage

**DatabaseManager Facade**:
- **Pure Facade Pattern**: DatabaseManager now only provides connection management
- **No Direct Data Operations**: All queries handled by dedicated repositories
- **Connection Pool Management**: Centralized database connection handling
- **Transaction Coordination**: Manages cross-repository transactions when needed

**Type Checking Requirements**:
- **pyright Strict Mode**: All services must pass strict type checking (0 errors)
- **Type Coverage**: 100% type annotation coverage required for new code
- **Interface Compliance**: Repository interfaces must be fully typed
- **Runtime Type Safety**: Pydantic models for all data structures

**File Organization**:
```
Infrastructure/automation-service/app/repositories/
├── control_action.py      # ControlAction repository
├── device.py             # Device repository  
├── pid.py                # PID repository
├── room_mode.py          # RoomMode repository
├── schedule/             # Schedule repository (new directory)
│   ├── __init__.py
│   ├── models.py         # Schedule data models
│   ├── repository.py     # Schedule repository implementation
│   └── routes.py         # Schedule API routes
├── sensor.py             # Sensor repository
├── setpoint.py           # Setpoint repository
└── config.py             # Config repository
```

### Cluster Topology Contract (Critical)

The codebase distinguishes **device clusters** from **sensor sub-clusters**.
This distinction is non-negotiable; mixing them is the most common
source of "endpoint returns nothing" / 404 bugs.

The two namespaces are kept **strictly separate** by design:

- `main` is a **device-cluster name only**. It is never registered as
  a sensor sub-cluster.
- `front` and `back` are **sensor sub-cluster names only**. They never
  appear on the device plane; the device plane rejects them with a
  400.

Hierarchy:

```
Room
 └── main                 (device cluster — actuators/relays/dimmer)
      ├── front           (Flower Room sensor sub-cluster)
      └── back            (Flower Room sensor sub-cluster)
```

| Concept              | Purpose                                  | Identifier(s)         |
|----------------------|------------------------------------------|-----------------------|
| **Device cluster**   | Room-wide actuator / relay / dimmer set  | always `main`         |
| **Sensor sub-cluster** | Physically separated sensor groupings  | `front`, `back` (Flower Room only) |

Per-room mapping (canonical, **single source of truth**):

| Room          | Device cluster | Sensor sub-clusters | Sensor URL slug(s) |
|---------------|----------------|---------------------|--------------------|
| `Flower Room` | `main`         | `front` ‡, `back`   | `front` ‡, `back`  |
| `Veg Room`    | `main`         | *(none)*            | `main` †           |
| `Lab`         | `main`         | *(none)*            | `main` †           |
| `Outside`     | `main`         | *(none)*            | `main` †           |

‡ Flower Room **wiring status (2026-04)**: only `back` is physically
connected and producing telemetry; `front` is defined in the topology
because the dual-bench layout is the planned wiring, but the front
sensor harness is not in service yet. `GET /api/sensors/Flower Room/front`
correctly returns **HTTP 200 with an empty payload** (the contract
accepts the URL slug; there is just no data behind it). The frontend
emits a `[flower-cluster-warning] Configured Flower cluster 'front' has
no live sensor stream` log entry for the same reason — this is the
expected runtime signal of the unwired state, not a bug. When the
front sensors come online, no code change is required.

† For unsplit rooms (no physical sub-grouping), the
`/api/sensors/{room}/{cluster}` URL slot reuses the device-cluster
name (`main`) as a *room-wide sentinel* meaning "this room has no
sensor sub-clusters". This is a transport-layer detail of the URL
shape — `main` is **not** registered as a sensor sub-cluster in the
topology data (`sensor_subclusters_for("Veg Room")` returns the empty
tuple, by design, so the namespace separation stays visible in code).

Implementation rule:

- **Source of truth (Python services):** `Infrastructure/shared/cluster_topology.py`
- **Source of truth (frontend):** `Infrastructure/frontend/src/config/clusterTopology.ts`
- These two files **must stay in sync** — the TS file is a hand-mirror
  of the Python module. CI doesn't enforce this yet; treat any change
  to one as a change to both, and update this section.

API contract (enforced as of Phase 5e):

- `GET /api/devices/{room}/{cluster}` accepts the room's **device** cluster only.
  Passing a sensor sub-cluster (`front`, `back`) returns **400**, not 404.
- `GET /api/sensors/{room}/{cluster}` accepts the room's sensor URL
  slug(s) only:
  - Split rooms (Flower) accept `front` / `back`. Passing the device
    cluster (`Flower Room/main`) returns **400** with a hint pointing
    at the correct sub-cluster.
  - Unsplit rooms (Veg / Lab / Outside) accept the device-cluster
    sentinel (`main`). Passing `front` / `back` returns **400** with
    a hint that the room has no sensor sub-clusters.
- These 400s replaced the previous "silently return empty dict"
  behavior, which masked frontend wiring bugs.

Frontend rule:

- The dashboard polls **device** endpoints over `ZONES` (one entry per
  room, all `cluster: "main"`).
- The dashboard polls **sensor** endpoints over `getSensorPollZones()`,
  which iterates `sensorUrlClustersFor(room)` — Flower fans out into
  `front` + `back`; unsplit rooms emit a single entry with `main`.
- `getDashboardPollZones()` is the union (device clusters + Flower
  sub-clusters) used **only** for sensor-plane polling and bulk Redis
  fan-out, never for the device plane. Iterating sensor sub-clusters
  against the *device* endpoint is a bug.

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

---

## DEVELOPMENT WORKFLOW & BEST PRACTICES

### Code Development Standards

**Python Services**:
- **Type Hints**: Full type annotation using Python 3.9+ syntax
- **Type Checking**: pyright strict mode enforced for all services (achieved 0 LSP errors)
- **Repository Pattern**: All data access uses dedicated repository classes (ControlAction, Device, PID, RoomMode, Schedule, Sensor, Setpoint, Config)
- **Database Architecture**: DatabaseManager is now a pure facade, repositories handle data operations
- **Async/Await**: All I/O operations must be asynchronous
- **Error Handling**: Structured exception handling with specific error types
- **Logging**: Structured JSON logging with correlation IDs
- **Testing**: pytest with >80% coverage requirement

**React Frontend**:
- **TypeScript**: Strict mode with no implicit any
- **Component Structure**: Functional components with hooks
- **State Management**: React Query for server state, useState for local state
- **Styling**: Tailwind CSS with component-scoped classes
- **Testing**: Jest + React Testing Library

---

## COMMAND REFERENCE & QUICK START

### Essential Commands

```bash
# System Operations
./deploy.sh                    # Deploy new version; auto-reverts symlink on health fail; NDJSON deploy log
./rollback-deploy.sh           # Point current at previous release + restart services
./restart_all_services.sh       # Restart all services in correct order

# Service Management
systemctl status automation-service  # Check control service status
systemctl restart cea-backend       # Restart sensor API service
journalctl -u can-processor -f       # Monitor CAN processor logs

# Database Operations
psql -U cea -d projectcea             # Connect to TimescaleDB
redis-cli                             # Connect to Redis for debugging
./Infrastructure/automation-service/config_cli.py setpoint get "Flower Room" main

# Hardware Testing
i2cdetect -y 0                        # Scan I2C bus 0 (relays)
i2cdetect -y 1                        # Scan I2C bus 1 (dimming)
candump can0                          # Monitor CAN bus traffic
```

---

## CONCLUSION

This comprehensive documentation serves as the definitive reference for ProjectCEA system architecture, operation, maintenance, and evolution. The system represents a sophisticated implementation of modern IoT principles applied to precision agriculture, with enterprise-grade reliability, performance, and scalability.

**Key Success Factors**:
- **Rigorous adherence to non-negotiable system rules**
- **Comprehensive monitoring and observability**
- **Systematic approach to troubleshooting and maintenance**
- **Continuous improvement through data-driven optimization**
- **Preparedness for emergency situations and rapid recovery**

For specific implementation details, refer to the individual service documentation and code comments. Always prioritize system stability and crop safety when making operational decisions.

---

*Last updated: 2026-02-08 - Updated documentation to reflect repository pattern refactoring, pyright type checking enforcement, and archived completed plans.*
