# PROJECT CEA - KNOWLEDGE BASE

**Generated:** 2026-01-11 | **Commit:** 89b66bd | **Branch:** dev

## OVERVIEW

Controlled Environment Agriculture system: 5 Python microservices + React frontend on Raspberry Pi 5 (512g nvme ssd & 8g ram). CAN bus sensors (ESP32), TimescaleDB storage, Redis real-time state, I2C actuators.

## STRUCTURE

```
ProjectCEA/
├── Infrastructure/           # All services + frontend
│   ├── automation-service/   # Control logic, PID, schedules (port 8001)
│   ├── backend/              # Sensor data API (port 8000)
│   ├── frontend/             # React dashboard (served by automation)
│   ├── can-processor-service/# CAN bus → Redis/DB
│   ├── soil-sensor-service/  # RS485 Modbus sensors (port 8002)
│   ├── weather-service/      # YUL Airport data (port 8003)
│   └── database/             # TimescaleDB schema + docs
├── Sensor_Nodes/             # ESP32 Arduino firmware
├── Boot_Initialisation_Services/  # CAN interface setup
├── .sisyphus/                # AI context files
└── deploy.sh                 # Atomic deployment script
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Device control logic | `Infrastructure/automation-service/app/control/` | PID, scheduler, relay manager |
| API endpoints | `Infrastructure/*/app/routes/` | FastAPI routers |
| Frontend components | `Infrastructure/frontend/src/components/` | React + TypeScript |
| Database schema | `Infrastructure/database/*.sql` | TimescaleDB hypertables |
| Hardware drivers | `Infrastructure/automation-service/app/hardware/` | I2C: DFR0971, MCP23017 |
| Sensor firmware | `Sensor_Nodes/ESP32/fullV6/` | Latest stable ESP32 code |
| Service configs | `*.service` files in Infrastructure/ | systemd units |
| Deploy/rollback | `deploy.sh`, `rollback.sh` | Atomic symlink switching |

## DEEP DIVE DOCS

| Topic | Document | Summary |
|-------|----------|---------|
| Project context | `.sisyphus/PROJECT_CONTEXT.md` | Architecture diagram, env vars, known issues |
| User preferences | `.sisyphus/USER_PREFERENCES.md` | Antoine's workflow, critical constraints |
| Code review | `code_review_report.md` | 2026-01-06 audit: issues found/fixed |
| Infrastructure setup | `Infrastructure/README.md` | Full setup guide, data storage pattern |
| Automation architecture | `Infrastructure/automation-service/README.md` | PID, modes, schedules, hardware |
| Database schema | `Infrastructure/database/REQUIREMENTS.md` | Tables, hypertables, aggregates |
| Power tracking | `RASPBERRY_PI_POWER_TRACKING.md` | UPS and power monitoring |

## SERVICE PORTS

| Service | Port | Entry Point | Purpose |
|---------|------|-------------|---------|
| backend | 8000 | `backend/app/main.py` | Sensor data API, WebSocket |
| automation-service | 8001 | `automation-service/app/main.py` | Control + serves frontend SPA |
| soil-sensor-service | 8002 | `soil-sensor-service/app/main.py` | RS485 Modbus polling |
| weather-service | 8003 | `weather-service/app/main.py` | External weather data |
| can-processor | — | `can-processor-service/app/main.py` | CAN → Redis/TimescaleDB |

## REDIS KEY PATTERNS

| Pattern | TTL | Service | Purpose |
|---------|-----|---------|---------|
| `sensor:{name}` | 10s | can-processor, backend | Real-time sensor values |
| `sensor:raw` (Stream) | MAXLEN 100K | can-processor | Raw data buffer |
| `automation:{loc}:{cluster}:{device}` | — | automation | Device states |
| `setpoint:{loc}:{cluster}:{type}` | — | automation | Target setpoints |
| `effective_setpoint:{loc}:{cluster}:{type}` | — | automation | Calculated (after ramps) |
| `mode:{loc}:{cluster}` | — | automation | DAY/NIGHT/PRE_DAY/PRE_NIGHT |
| `schedule:state:{loc}:{cluster}` | — | automation | Persistent schedule state |
| `alarm:{loc}:{cluster}:{name}` | — | automation | Active alarms |

## HARDWARE INTERFACES

| Type | Address | File | Purpose |
|------|---------|------|---------|
| I2C | 0x20 | `hardware/mcp23017.py` | 16-channel relay expander |
| I2C | 0x58-0x5A | `hardware/dfr0971.py` | 0-10V DAC light dimming |
| CAN | can0 @ 250kbps | `can-processor-service/` | Sensor node communication |
| Modbus | /dev/serial0 | `soil-sensor-service/` | RS485 soil sensors |

## STARTUP ORDER

```
postgresql → redis-server → can-setup → can-processor → soil-sensor → cea-backend → automation-service
```

## COMMANDS

```bash
# Deploy (dev → production)
ssh mothernode "cd /home/antoine/ProjectCEA && ./deploy.sh"

# Rollback (<30s)
ssh mothernode "./rollback.sh"

# Service management
ssh mothernode "systemctl status automation-service cea-backend"
ssh mothernode "journalctl -u automation-service -f"

# Health checks
ssh mothernode "curl -fsS http://127.0.0.1:8000/health && curl -fsS http://127.0.0.1:8001/health"

# Development
cd Infrastructure/backend && uvicorn app.main:app --reload --port 8000
cd Infrastructure/frontend && npm run dev  # port 3001
```

## ANTI-PATTERNS (THIS PROJECT)

| Never | Reason |
|-------|--------|
| Commit secrets (`.env`, passwords) | Security - use EnvironmentFile |
| Use TTL >10s for sensor state keys | Stale data in control loops |
| Skip TimescaleDB writes | All data must persist |
| Direct DB access from frontend | Always use backend APIs |
| Read Redis Streams in control loops | Streams = history, state keys = control |
| Start services out of order | Dependency failures |
| Modify `.service` files without daemon-reload | Config won't apply |
| Change CAN message format without updating parser | Protocol mismatch |

## COMPLEXITY HOTSPOTS

| File | Lines | Purpose |
|------|-------|---------|
| `automation-service/app/database.py` | 2,878 | Data access, migrations, batching |
| `automation-service/app/redis_client.py` | 1,411 | Streams, state, effective setpoints |
| `automation-service/app/routes/schedules.py` | 1,217 | Schedule CRUD, room schedules |
| `frontend/src/components/SetpointTimeline.tsx` | 869 | 24h timeline visualization |

## ZONES

- **Flower Room**: front, back clusters
- **Veg Room**: main cluster
- **Lab**: main cluster
- **Outside**: weather station

## NOTES

- **Atomic deploys**: `/opt/projectcea/current` symlink, 10 releases kept
- **Redis AOF corruption**: Auto-fix via `redis-aof-check.service`
- **Frontend in production**: Built `dist/` served by automation-service
- **Light schedules**: Always daily (no per-day overrides)
- **Ramp logic**: PRE_NIGHT ramps from DAY→PRE_NIGHT, PRE_DAY from NIGHT→PRE_DAY

---

## POST-WORK CHECKLIST (MANDATORY)

After completing ANY implementation work:

- [ ] Update relevant `AGENTS.md` files with new patterns/functions in every folder and sub folders. They should be freshly kept up to date.
- [ ] Add discovered anti-patterns to documentation
- [ ] Document new database functions, tables, or views
- [ ] Run `sync_dashboards.sh` if Grafana dashboards changed
- [ ] Commit documentation WITH code changes (same commit)

**Incomplete documentation = incomplete work.**

### Additional ANTI-PATTERNS

| Never | Reason | Use Instead |
|-------|--------|-------------|
| Query PostgreSQL for current sensor values in Grafana | Slow, unnecessary DB load | Redis `GET sensor:{name}` |
| Skip documentation updates after implementation | Knowledge loss, repeated mistakes | Always update AGENTS.md files |
