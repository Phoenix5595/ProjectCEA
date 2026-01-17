# ProjectCEA - Agent Guidelines

**Generated:** 2026-01-15 | **Commit:** 1f92809 | **Branch:** main

## OVERVIEW

Raspberry Pi 5 CEA automation system. 6 Python FastAPI microservices + React frontend controlling 2 grow rooms via CAN bus sensors and relay/DAC actuators.

## STRUCTURE

```
ProjectCEA/
├── Infrastructure/           # All services (see Infrastructure/AGENTS.md)
│   ├── automation-service/   # Control loop + frontend (8001)
│   ├── backend/              # Sensor API (8000)
│   ├── can-processor-service/# CAN → Redis/DB
│   ├── frontend/             # React + Grafana dashboards
│   └── database/             # TimescaleDB schema
├── Sensor_Nodes/             # ESP32 firmware (see Sensor_Nodes/AGENTS.md)
├── .sisyphus/                # AI context files
│   ├── PROJECT_CONTEXT.md    # Technical architecture
│   ├── USER_PREFERENCES.md   # Non-negotiables
│   └── plans/                # Implementation roadmap
├── deploy.sh                 # Production deployment
└── rollback.sh               # Fast rollback (<30s)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Control logic | `Infrastructure/automation-service/app/control/` | 2s deterministic loop |
| Sensor data API | `Infrastructure/backend/app/routes/` | Redis + TimescaleDB |
| CAN processing | `Infrastructure/can-processor-service/app/` | Async DB batching |
| Frontend UI | `Infrastructure/frontend/src/` | React + TypeScript |
| Grafana dashboards | `Infrastructure/frontend/grafana/dashboards/` | JSON provisioned |
| Database schema | `Infrastructure/database/` | TimescaleDB hypertables |
| ESP32 firmware | `Sensor_Nodes/ESP32/fullV6/` | Latest stable |
| Deployment | `./deploy.sh`, `./rollback.sh` | Symlink to /opt/projectcea |

## CRITICAL RULES (NON-NEGOTIABLE)

| Rule | Reason |
|------|--------|
| **1/sec sampling** | AI training requires full resolution |
| **100ms max DB batch** | Live Redis instant, DB can buffer |
| **VPD is master** | Humidity is slave to VPD |
| **Full TDD** | All new code needs tests |
| **No bare excepts** | 14 violations remain - fix them |
| **Rollback <30s** | Use `./rollback.sh` immediately if broken |

## DATA FLOW

```
ESP32 Nodes (CAN @250kbps)
    ↓
can-processor-service
    ├─→ Redis State (sensor:*) ──→ Automation loop (<1ms)
    ├─→ Redis Stream (sensor:raw) ──→ API recent queries
    └─→ TimescaleDB (measurement) ──→ Grafana/History
```

## DEPLOYMENT

```bash
# Development path
/home/antoine/ProjectCEA/

# Production path (symlink)
/opt/projectcea/current/ → /opt/projectcea/releases/[timestamp]

# Deploy (NEVER copy manually)
./deploy.sh

# Rollback (use immediately if anything breaks)
./rollback.sh
```

## SERVICE STARTUP ORDER

```
postgresql.service
redis-server.service
    ↓
can-setup.service (oneshot)
    ↓
can-processor.service
soil-sensor-service.service
weather-service.service
    ↓
cea-backend.service (8000)
automation-service.service (8001)
```

## ANTI-PATTERNS

| Never | Why |
|-------|-----|
| Edit `/opt/projectcea/` directly | Use deploy.sh |
| Touch working systems | Unless explicitly asked |
| Use `as any`, `@ts-ignore` | Type safety required |
| Query DB without time filter | Scans all chunks |
| Use hourly aggregates for <7d | Hides critical swings |
| Block the control loop | 2s tick is deterministic |
| Hardcode setpoints | Use database |

## CURRENT PHASE: 1 (Reliability)

1. ~~Fix device_type bug~~ ✓
2. Fix bare excepts (14 remaining)
3. Document and verify rollback
4. Add watchdog
5. Implement 100ms DB batching

## LOCKED DECISIONS

| Topic | Decision |
|-------|----------|
| PID | Self-tuning, UI shows K values + reset button |
| Leaf Temp | Manual day/night delta, interpolates |
| Data | 1yr full resolution, indefinite aggregates |
| Safety | Software-only for now |
| CO2 | ASC on, design for FRC when enrichment added |

## FUTURE REMINDERS

- **MQTT**: When adding Lab/Water Management nodes
- **IR Camera**: Replace manual leaf temp delta
- **CO2 FRC**: If enrichment added, disable ASC

---

## CRITICAL LESSON (2026-01-15)

### NEVER MAKE CHANGES WITHOUT UNDERSTANDING THE WHOLE SYSTEM

**What went wrong:**
1. Made changes to device_controller.py without understanding system
2. Modified Grafana datasources without checking existing config
3. Reset passwords that didn't need resetting
4. Created duplicate datasources
5. Did not use proper deployment process
6. Made assumptions instead of reading documentation first

**Before ANY change:**
1. READ `.sisyphus/PROJECT_CONTEXT.md` and `USER_PREFERENCES.md`
2. READ this `AGENTS.md` file
3. Check git history to understand what exists
4. Understand deployment: `deploy.sh` / `rollback.sh`
5. ASK if unsure - do not assume
6. Test in isolation before deploying

**If something is working, DO NOT TOUCH IT unless explicitly asked.**

---

## COMPLEXITY HOTSPOTS

| File | Lines | Issue |
|------|-------|-------|
| `automation-service/app/database.py` | 2,879 | God object - needs splitting |
| `automation-service/app/redis_client.py` | 1,411 | High complexity |
| `automation-service/app/control/scheduler.py` | 756 | Mode transition logic |
| `automation-service/app/control/control_engine.py` | 665 | Central orchestrator |

## COMMANDS

```bash
# View all service logs
journalctl -u can-processor -u cea-backend -u automation-service -f

# Check service status
systemctl status can-processor cea-backend automation-service

# Restart all (correct order)
./restart_all_services.sh

# CLI setpoint management
./Infrastructure/automation-service/config_cli.py setpoint get "Flower Room" main
```

## CHILD AGENTS.MD

| Path | Purpose |
|------|---------|
| `Infrastructure/AGENTS.md` | Service architecture |
| `Infrastructure/automation-service/AGENTS.md` | Control + API details |
| `Infrastructure/automation-service/app/control/AGENTS.md` | Control loop internals |
| `Infrastructure/frontend/AGENTS.md` | React + Grafana |
| `Infrastructure/database/AGENTS.md` | Schema + aggregation rules |
| `Infrastructure/backend/AGENTS.md` | Sensor API |
| `Infrastructure/can-processor-service/AGENTS.md` | CAN ingestion |
| `Sensor_Nodes/AGENTS.md` | ESP32 firmware |

## EXTERNAL RESOURCES

> Librarian uses this for focused searches. No Context7 MCP configured.

### Dependencies

**Python:** fastapi, uvicorn, pydantic, redis, asyncpg, psycopg2-binary, python-can, sqlalchemy, websockets, pyyaml, smbus2
**JavaScript:** react, react-dom, react-router-dom, @tanstack/react-query, axios, recharts, tailwindcss, vite, typescript

### GitHub Repos
- tiangolo/fastapi
- redis/redis-py
- MagicStack/asyncpg
- pydantic/pydantic
- facebook/react
- TanStack/query
- timescale/timescaledb

### Documentation
- https://fastapi.tiangolo.com
- https://redis.io/docs
- https://magicstack.github.io/asyncpg
- https://docs.pydantic.dev
- https://react.dev
- https://tanstack.com/query/latest
- https://docs.timescale.com
- https://tailwindcss.com/docs
- https://vitejs.dev/guide
