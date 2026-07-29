# ProjectCEA – Architecture Schematic (Plan-Agent Style)

**Last updated (deployed):** 2026-07-12 — Schedule architecture redesign: photoperiod from mode_parameters, per-light intensity from light_target_intensity, light_programs for supplemental/override; removed runtime synthesis and dead Redis schedule-state code.

> This file is the plan-style schematic: diagram-first, structured sections. Keep in sync with `ARCHITECTURE.md`. When deploying relevant changes, update both and copy previous versions to **`archive/`** (project root) with dated filenames.

---

## TL;DR

- **System**: Raspberry Pi 5, 6 Python services + React, 2 grow rooms.
- **Data in**: CAN (ESP32), Modbus (soil), HTTP (weather) → Redis (live) + TimescaleDB (history).
- **Control**: automation-service 1–5 s tick; backend 8000 (sensors), automation 8001 (control + frontend).
- **Hardware**: MCP23017 relays (I2C 0), DFR0971 dimming (I2C 1). Deploy: `deploy.sh` / `rollback-deploy.sh`.

---

## Diagram (Mermaid – Plan-Agent Style)

```mermaid
flowchart TB
  subgraph HARDWARE["Hardware"]
    ESP32["ESP32 nodes (CAN)\nFlower _b/_f, Veg _v"]
    SOIL["Soil sensors\nRS485 Modbus"]
    W1["1-Wire DS18B20\nGPIO 24 (lab/water)"]
    EXT["External\n(weather HTTP)"]
  end

  subgraph INGEST["Ingestion Services"]
    CAN["can-processor\n(no port)"]
    SOIL_SVC["soil-sensor\n:8002"]
    OW["onewire-worker\n:8004"]
    WX["weather\n:8003"]
  end

  subgraph STORE["Data Stores"]
    REDIS["Redis\nsensor:* (10s TTL)\nsensor:raw stream\nautomation:*"]
    TSDB["TimescaleDB\nmeasurement\naggregates\neffective_setpoints"]
  end

  subgraph API["API & Control"]
    BACK["cea-backend :8000\nsensor API, WebSocket"]
    AUTO["automation-service :8001\ncontrol loop, setpoints\nPID, devices\nserves frontend"]
  end

  subgraph UI["User-Facing"]
    FE["Frontend (React)\nDashboard (weather Quebec City top-right)\nZoneConfig"]
    GRAF["Grafana\nPostgreSQL only"]
  end

  ESP32 -->|CAN| CAN
  SOIL -->|Modbus| SOIL_SVC
  W1 -->|sysfs| OW
  EXT -->|HTTP| WX

  CAN --> REDIS
  CAN --> TSDB
  SOIL_SVC --> REDIS
  SOIL_SVC --> TSDB
  OW --> REDIS
  WX --> REDIS
  WX --> TSDB

  REDIS <-->|read/write| AUTO
  TSDB <-->|read/write| AUTO
  REDIS <-->|read| BACK
  TSDB <-->|read| BACK

  BACK --> FE
  AUTO --> FE
  TSDB --> GRAF
```

---

## Data Flow (Simplified)

```mermaid
sequenceDiagram
  participant ESP32 as ESP32/CAN
  participant CAN as can-processor
  participant R as Redis
  participant DB as TimescaleDB
  participant AUTO as automation-service
  participant FE as Frontend

  ESP32->>CAN: CAN frames
  CAN->>R: sensor:*, sensor:raw (instant)
  CAN->>DB: measurement (batched)
  AUTO->>R: read sensor:*, effective_setpoint
  AUTO->>R: write automation:*
  AUTO->>DB: effective_setpoints, automation_state
  FE->>AUTO: REST (setpoints, devices, schedules)
  FE->>CAN/Backend: live data via Backend :8000
```

---

## Components

| Layer | Component | Role |
|-------|-----------|------|
| Hardware | ESP32 (CAN) | Flower Back/Front, Veg Main; 1 Hz sensors |
| Hardware | Soil (RS485) | soil-sensor-service reads Modbus |
| Hardware | 1-Wire DS18B20 | onewire-worker on GPIO 24 (lab temp, water temp) |
| Hardware | MCP23017 | Relays only, I2C bus 0, 16 channels |
| Hardware | DFR0971 | Dimming only, I2C bus 1, 6 channels |
| Ingest | can-processor | CAN → Redis state + stream + TimescaleDB |
| Ingest | soil-sensor-service | Modbus → Redis + TimescaleDB |
| Ingest | onewire-worker | 1-Wire sysfs → Redis (lab_temp, water_temperature) |
| Ingest | weather-service | External API → DB / Redis |
| Store | Redis | sensor:*, sensor:raw, automation:*, effective_setpoint:* |
| Store | TimescaleDB | measurement, aggregates, effective_setpoints, automation_state |
| API | cea-backend :8000 | Sensor API, WebSocket; no static UI |
| API | automation-service :8001 | Control loop, setpoints, devices, PID; serves frontend dist/ |
| UI | Frontend | React; backend 8000 + automation 8001 |
| UI | Grafana | Dashboards; PostgreSQL only |

---

## Control Loop (automation-service)

1. Read `sensor:*` from Redis.
2. Load config (zones, setpoints) from DB.
3. **Scheduler** (startup-gated by `asyncio.Event`):
   - Photoperiod: `mode_parameters.day_start_time/night_start_time` → `is_in_photoperiod()`
   - Intensity: `light_target_intensity` table (mode-specific, per-device)
   - Programs: `light_programs` table (supplemental/override, priority-based)
   - Climate: `climate_periods` (named periods with ramp_minutes)
4. PID + VPD cascade; safety interlocks.
5. Device commands → MCP/DFR; state → Redis `automation:*` and DB.

**Tick:** 1–5 s (configurable; max 5 s non-negotiable).

**Schedule-related tables:**

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `mode_parameters` | Photoperiod + ramp durations (room-level, per-mode) | `day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes` |
| `light_target_intensity` | Per-light intensity (mode-specific) | `device_id`, `mode_id`, `target_intensity` (default 10%) |
| `light_programs` | Supplemental/override programs | `program_type`, `start_time`, `end_time`, `cycle_enabled`, `priority` |
| `schedules` | Non-light DAY/NIGHT rows only | `device_name`, `mode` (DAY/NIGHT), `start_time`, `end_time` |
| `climate_periods` | Climate setpoints (independent of light) | `period_name`, `start_time`, `end_time`, `ramp_minutes`, setpoints |

**Removed (T10):** `room_schedule` rows, per-device SUN/MOON rows for lights, `expand_light_schedules_for_control()` runtime synthesis, `SchedulesMixin` dead Redis code.

---

## Deployment

| Action | Command / Path |
|--------|----------------|
| Deploy | `./deploy.sh` |
| Rollback | `./rollback-deploy.sh` (<30 s) |
| Prod root | `/opt/projectcea/current` → `releases/<timestamp>-<git>` |
| Dev root | `/home/antoine/ProjectCEA` |

**Startup order:** postgresql, redis → can-setup → can-processor, soil-sensor, weather → cea-backend, automation-service.

---

## Reference for Agents

- **Full narrative:** `ARCHITECTURE.md` (project root).
- **This file:** Plan-style schematic (Mermaid + tables); update both when architecture changes and a deploy is done.
- **Archive:** **`archive/`** at project root — dated copies (e.g. `ARCHITECTURE_SCHEMATIC_2026-01-30.md`).
