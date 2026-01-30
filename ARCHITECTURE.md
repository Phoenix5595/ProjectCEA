# ProjectCEA – System Architecture

**Last updated (deployed):** 2026-01-30 — First schematic; no deploy yet.

**Plan-style schematic:** **`ARCHITECTURE_SCHEMATIC.md`** (Mermaid + tables). Update both when the architecture changes and a deploy is done.

---

## Scope

Raspberry Pi 5 CEA automation: 6 Python FastAPI microservices + React frontend controlling 2 grow rooms. Data from CAN bus (ESP32) and RS485/Modbus (soil), stored in Redis (live) and TimescaleDB (history). Control loop and REST/WebSocket APIs on Pi; Grafana dashboards for visualization.

---

## Schematic (Data Flow)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              HARDWARE LAYER                                              │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│  ESP32 nodes (CAN @250kbps)     Soil sensors (RS485 Modbus)    External (HTTP)           │
│  • Flower Back (_b)             • soil-sensor-service            • weather-service (YUL)  │
│  • Flower Front (_f)            • Beds per config                • port 8003              │
│  • Veg Main (_v)                                                                         │
└──────────────┬──────────────────────────────┬──────────────────────────┬─────────────────┘
               │ CAN                          │ Modbus                   │
               ▼                              ▼                          │
┌──────────────────────────────┐  ┌──────────────────────┐  ┌─────────────┴──────────────┐
│  can-processor-service       │  │ soil-sensor-service │  │ weather-service             │
│  • Decode CAN → sensor values │  │ • Read soil temp/etc│  │ • Fetch → DB / Redis        │
│  • No HTTP port (system only) │  │ • port 8002         │  │                             │
└──────────────┬───────────────┘  └──────────┬───────────┘  └─────────────┬──────────────┘
               │                              │                            │
               │    Redis (instant)           │                            │
               ▼                              ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  REDIS                                                                                   │
│  • sensor:{name}         → live value (10s TTL)     ← used by control loop & API        │
│  • sensor:{name}:ts      → timestamp (10s TTL)                                            │
│  • sensor:raw            → stream (recent history, MAXLEN ~100K)                          │
│  • automation:*          → device/control state (10s TTL) — written by automation-svc   │
│  • effective_setpoint:*  → current targets (written by automation-service)               │
└──────────────┬──────────────────────────────────────────────────────────────────────────┘
               │
               │  TimescaleDB (batched by can-processor / soil-sensor / weather)
               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  TIMESCALEDB (PostgreSQL + TimescaleDB)                                                  │
│  • measurement           → hypertable, 1s resolution, 1yr full res, then compression    │
│  • measurement_*min/_hourly/_daily → continuous aggregates (avg/min/max)                  │
│  • effective_setpoints   → logged by automation-service                                  │
│  • automation_state      → control/device state history                                  │
│  • room, rack, device, sensor → metadata                                                 │
└──────────────┬──────────────────────────────────────────────────────────────────────────┘
               │
               │  Read by: backend (API), Grafana
               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  API & CONTROL LAYER                                                                     │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│  cea-backend (8000)              automation-service (8001)                               │
│  • Sensor API (historical + live)  • Control loop (1–5s tick)                           │
│  • /api/sensors/..., /api/live/*   • PID, scheduler, device_controller                │
│  • WebSocket /ws/{location}        • Setpoints, schedules, room modes                   │
│  • Serves no static UI              • REST API for frontend + config_cli                 │
│  • Reads: Redis + TimescaleDB      • Reads: Redis (sensor:*, effective_setpoint)       │
│                                     • Writes: Redis (automation:*), TimescaleDB          │
│                                     • Hardware: MCP23017 (relays), DFR0971 (dimming)     │
│                                     • Serves frontend dist/ (React SPA)                 │
└──────────────┬──────────────────────────────────┬───────────────────────────────────────┘
               │                                  │
               │  Frontend (React)                │  Frontend (React)
               │  • Backend 8000: live + history  │  • Automation 8001: setpoints, devices,
               │  • WebSocket for live            │    schedules, PID, room modes
               ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React + TypeScript + Vite)                                                    │
│  • Built dist/ served by automation-service (8001)                                       │
│  • api.ts: backend 8000 + automation 8001 + weather 8003                                │
│  • Pages: Dashboard, ZoneConfig | Components: SetpointTimeline, LightManager, etc.      │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  GRAFANA                                                                                 │
│  • Dashboards: flower_sector, flower_sector_soil, veg_sector (provisioned)              │
│  • Datasource: PostgreSQL (TimescaleDB) — no Redis from Grafana                           │
│  • Query strategy: raw measurement <1h, 1min/5min/hourly aggregates for longer ranges   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Services (Ports & Purpose)

| Service                 | Port  | Purpose |
|-------------------------|-------|--------|
| cea-backend             | 8000  | Sensor data API (Redis + TimescaleDB), WebSocket live |
| automation-service      | 8001  | Control loop, setpoints, schedules, devices, PID; serves frontend `dist/` |
| soil-sensor-service     | 8002  | RS485 Modbus soil sensors → Redis + TimescaleDB |
| weather-service         | 8003  | External weather (e.g. YUL) → DB / Redis |
| can-processor-service   | —     | CAN bus → Redis state + stream + TimescaleDB (no HTTP) |

---

## Control Loop (automation-service)

1. Read sensor values from Redis (`sensor:*`).
2. Load config snapshot (zones, devices, setpoints from DB).
3. Scheduler: current mode (DAY / NIGHT / PRE_DAY / PRE_NIGHT) and effective setpoints (with ramp).
4. PID controllers + VPD cascade (VPD master, humidity slave).
5. Safety / interlocks (e.g. heating failure → inhibit exhaust).
6. Device commands → RelayManager (MCP23017) and DFR0971 (dimming); state → Redis `automation:*` and TimescaleDB.

Tick interval: 1–5 s (configurable; non-negotiable max 5 s).

---

## Hardware (on Pi)

- **CAN**: ESP32 nodes (Flower Back/Front, Veg Main) → can-processor.
- **I2C – Relays**: MCP23017, bus 0, address 0x27; 16 channels (0–15).
- **I2C – Dimming**: DFR0971, bus 1, addresses 0x88/0x89/0x90; 3 boards × 2 channels = 6 dimming channels.
- Config: `automation_config.yaml`; validated at startup (Pydantic). Duplicate channels or invalid dimming_board_id → service refuses to start.

---

## Deployment

- **Dev**: `/home/antoine/ProjectCEA/`
- **Prod**: `/opt/projectcea/current` → symlink to `/opt/projectcea/releases/<timestamp>-<git-short>`
- **Deploy**: `./deploy.sh` (rsync Infrastructure/, build venvs + frontend, atomic symlink, restart services).
- **Rollback**: `./rollback.sh` (switch symlink to previous release, restart; target &lt;30 s).

Startup order: postgresql, redis → can-setup (oneshot) → can-processor, soil-sensor, weather → cea-backend, automation-service.

---

## Key Paths for Agents

| Concern            | Where to look |
|--------------------|----------------|
| End-to-end picture | This file: `ARCHITECTURE.md`; plan-style: `ARCHITECTURE_SCHEMATIC.md` (project root) |
| Control logic      | `Infrastructure/automation-service/app/control/` |
| Sensor API         | `Infrastructure/backend/app/routes/` |
| CAN ingestion      | `Infrastructure/can-processor-service/app/` |
| DB schema          | `Infrastructure/database/cea_schema.sql` |
| Frontend            | `Infrastructure/frontend/src/` |
| Config validation  | `Infrastructure/automation-service/app/models/config_schema.py` |
| Deploy / rollback  | `deploy.sh`, `rollback.sh` |

---

## Non-Negotiables (Summary)

- 1–5 s control tick; 1/s sampling for data.
- Redis: live state and stream instant; DB batch ≤100 ms where applicable.
- VPD master, humidity slave; safety interlocks (e.g. heating failure).
- Setpoints and config from DB; no hardcoded setpoints in code.
- Query DB with time filters; use aggregates for longer ranges (no hourly for &lt;7d).
- MCP = relays only; DFR0971 = dimming only; do not swap roles.

---

## Archive

Previous versions of this schematic are stored in **`archive/`** at project root with dated filenames (e.g. `ARCHITECTURE_2026-01-30.md`). When updating after a deploy, copy the current file there first, then edit this file and bump **Last updated (deployed)**.
