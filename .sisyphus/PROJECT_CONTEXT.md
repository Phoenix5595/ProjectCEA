# ProjectCEA - Technical Context

> Auto-generated project context for AI assistants. Last updated: 2026-01-10T15:10:47Z

## Project Overview

**ProjectCEA** is a Controlled Environment Agriculture (CEA) monitoring and automation system running on a Raspberry Pi ("mothernode"). It collects sensor data via CAN bus, stores it in TimescaleDB, and provides a web dashboard for monitoring and control.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  MOTHERNODE (Raspberry Pi)                                      │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ can-setup   │→ │can-processor│→ │   Redis     │              │
│  │ (oneshot)   │  │ (CAN→DB)    │  │  Streams    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                          ↓                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │cea-backend  │← │ TimescaleDB │← │automation-  │              │
│  │ (API:8000)  │  │ (PostgreSQL)│  │service:8001 │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│         ↑                                ↑                      │
│         └────────── Frontend SPA ────────┘                      │
│                   (served by automation-service)                │
└─────────────────────────────────────────────────────────────────┘
```

## Services

| Service | Port | Entry Point | Purpose |
|---------|------|-------------|---------|
| cea-backend | 8000 | `uvicorn app.main:app` | Dashboard API |
| automation-service | 8001 | `uvicorn app.main:app` | Automation + serves frontend SPA |
| can-processor | — | `python -m app.main` | CAN bus → Redis/TimescaleDB |
| can-setup | — | `setup_can.sh` (oneshot) | Configure can0 interface |
| soil-sensor-service | 8002 | `uvicorn app.main:app` | Soil sensor polling (Modbus) |
| weather-service | — | (not installed) | Weather data (in repo only) |
| cea-frontend | 3001 | `npm run dev` | **DEPRECATED** - use built SPA |

## Shared Code

The `shared` Python package lives at:
```
/home/antoine/ProjectCEA/Infrastructure/automation-service/shared/
```

All services import `from shared.logging import ...` and require:
```
PYTHONPATH=/path/to/Infrastructure/automation-service
```

## Environment Variables

| Variable | Required | Default | Used By |
|----------|----------|---------|---------|
| POSTGRES_PASSWORD | ✅ Yes | — | All services |
| POSTGRES_HOST | No | localhost | All services |
| POSTGRES_DB | No | cea_sensors | All services |
| POSTGRES_USER | No | cea_user | All services |
| REDIS_URL | No | redis://localhost:6379 | All services |
| CAN_PROCESSOR_DISPLAY | No | 0 | can-processor |

## Frontend

- **Build:** `npm run build` → outputs to `Infrastructure/frontend/dist/`
- **Served by:** automation-service (port 8001) via FastAPI StaticFiles
- **API URLs:** Auto-detected from `window.location.hostname` (no config needed)

## Database

- **Engine:** PostgreSQL + TimescaleDB extension
- **Database:** cea_sensors
- **Tables:** device, sensor, measurement (hypertable)
- **Migrations:** SQL scripts in `Infrastructure/database/` (no Alembic)

## Hardware Dependencies

- **CAN Interface:** `can0` (SocketCAN, 250000 bitrate)
- **Soil Sensor:** Modbus RTU (via soil-sensor-service)

## Known Issues / Technical Debt

1. **Hardcoded paths** in config.py files reference `/home/antoine/ProjectCEA/...`
2. **Debug logging** to `/home/antoine/.cursor/debug.log` (should be removed)
3. **Missing import** in backend/app/main.py exception handler (`logging` not imported)
4. **Sync Redis calls** in async context (potential blocking)
5. **No resource cleanup** on shutdown (DB pool, Redis connections)

## Deployment

- **Current:** In-place at `/home/antoine/ProjectCEA/`, system Python
- **Target:** Atomic symlink deploys at `/opt/projectcea/` with per-release venvs
- **Process manager:** systemd

## Git

- **Remote:** https://github.com/Phoenix5595/ProjectCEA.git
- **Branch:** master (may have unpushed commits)
