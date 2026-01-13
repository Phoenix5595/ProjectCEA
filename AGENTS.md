# ProjectCEA - Agent Reference

> Master reference for AI agents working on this codebase. See subdirectory AGENTS.md files for deep dives.

## ENVIRONMENT LOCATIONS

| Environment | Path | Purpose |
|-------------|------|---------|
| **DEV** | `/home/antoine/ProjectCEA/` | Development - edit code here |
| **LIVE/PROD** | `/opt/projectcea/` | Production - deployed releases |

### Key Paths
- **Development**: `/home/antoine/ProjectCEA/`
- **Production**: `/opt/projectcea/current/` (symlink to active release)
- **Releases**: `/opt/projectcea/releases/TIMESTAMP-SHA/`
- **Shared secrets**: `/opt/projectcea/shared/env/`

---

## PROJECT OVERVIEW

Climate control system for indoor growing environments. 6 Python microservices + React frontend + ESP32 sensor nodes.

### Data Flow
```
ESP32 Sensors (CAN bus) → can-processor → Redis Stream/State → TimescaleDB
                                              ↓
                              automation-service (PID control)
                                              ↓
                              Devices (relays, DACs) + Frontend
```

---

## ARCHITECTURE

```
ProjectCEA/
├── Infrastructure/           # All services and frontend
│   ├── automation-service/   # Control logic, PID, schedules (port 8001)
│   ├── backend/              # Sensor data API (port 8000)
│   ├── frontend/             # React + Vite + Tailwind
│   ├── can-processor-service/# CAN bus → Redis/DB
│   ├── soil-sensor-service/  # RS485 Modbus (port 8002)
│   ├── weather-service/      # External weather (port 8003)
│   ├── database/             # TimescaleDB schema
│   └── *.service             # systemd unit files
├── Sensor_Nodes/             # ESP32 firmware (Arduino)
│   └── ESP32/fullV6/         # LATEST STABLE firmware
├── Boot_Initialisation_Services/
├── Test_Scripts/
└── deploy.sh                 # Deploy dev → prod
```

---

## SERVICE DEPENDENCIES

```
postgresql.service → redis-server.service
                           ↓
                    can-setup.service (oneshot)
                           ↓
    ┌──────────────────────┼──────────────────────┐
    ↓                      ↓                      ↓
can-processor    soil-sensor-service    weather-service
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
        cea-backend              automation-service
```

---

## DEEP DIVE DOCUMENTATION

| Topic | Location |
|-------|----------|
| Infrastructure overview | `Infrastructure/AGENTS.md` |
| Backend API | `Infrastructure/backend/AGENTS.md` |
| Automation & control | `Infrastructure/automation-service/AGENTS.md` |
| Database & aggregates | `Infrastructure/database/AGENTS.md` |
| Frontend & Grafana | `Infrastructure/frontend/AGENTS.md` |
| Sensor nodes | `Sensor_Nodes/AGENTS.md` |
| Deployment workflow | `.sisyphus/agents.md` |

---

## COMMON COMMANDS

### Development
```bash
# SSH to mothernode
ssh mothernode

# Edit code (always in dev)
cd /home/antoine/ProjectCEA

# View service logs
journalctl -u automation-service -f
journalctl -u cea-backend -f
```

### Deployment
```bash
# Deploy dev → prod
ssh mothernode "cd /home/antoine/ProjectCEA && ./deploy.sh"

# Rollback (fast, ~10 seconds)
ssh mothernode "cd /home/antoine/ProjectCEA && ./rollback.sh"

# Check current release
ssh mothernode "readlink /opt/projectcea/current"
```

### Service Management
```bash
# Restart a service
sudo systemctl restart automation-service

# Check status
systemctl status cea-backend automation-service

# Health checks
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8001/health
```

---

## CRITICAL RULES (NON-NEGOTIABLE)

### Data Precision
| Duration | Source | Rationale |
|----------|--------|-----------|
| < 1 hour | Raw measurement table | Live tracking, every reading |
| 1h - 24h | measurement_1min | 1-min buckets capture 40% swings |
| 1d - 7d | measurement_5min | Still shows significant events |
| > 7 days | measurement_hourly | Long-term trends |

**NEVER use hourly aggregates for < 7 days - hides critical environmental swings.**

### Setpoint Validation
| Type | Range |
|------|-------|
| Temperature | 10-35°C |
| Humidity | 30-90% RH |
| CO2 | 400-2000 ppm |
| VPD | 0.4-1.8 kPa |

### Climate Modes
- **DAY**: Lights ON, day setpoints
- **NIGHT**: Lights OFF, night setpoints  
- **PRE_DAY**: Ramp from NIGHT → PRE_DAY (lights still OFF)
- **PRE_NIGHT**: Ramp from DAY → PRE_NIGHT (lights still ON)

---

## ANTI-PATTERNS (GLOBAL)

| Never | Reason |
|-------|--------|
| Edit production directly | Always edit in dev, then deploy |
| Skip Redis for control loops | Redis < 1ms, PostgreSQL 10-100ms |
| Use hourly aggregate for < 7d | Hides environmental swings |
| Hardcode passwords in code | Use /opt/projectcea/shared/env/ |
| Change CAN baud without updating all nodes | Protocol mismatch |
| Start services out of order | Dependencies will fail |

---

## EXTERNAL RESOURCES

> For librarian agent focused searches

### Dependencies
Python: FastAPI, SQLAlchemy, asyncpg, redis, aiohttp, pydantic
Frontend: React, Vite, Tailwind, TypeScript
Hardware: ESP32, MCP23017, DFR0971, BME280, SCD30

### Documentation
- https://fastapi.tiangolo.com
- https://docs.timescale.com
- https://redis.io/docs
- https://react.dev
- https://vitejs.dev

### GitHub Repos
- tiangolo/fastapi
- redis/redis-py
- timescale/timescaledb

---

*Last updated: 2026-01-13*
