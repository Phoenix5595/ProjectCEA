# CEA INFRASTRUCTURE

**Generated:** 2025-01-05

## OVERVIEW
Microservices infrastructure for CEA system: 6 Python FastAPI services + React frontend, unified data storage pattern (Redis Stream + TimescaleDB).

## STRUCTURE

```
Infrastructure/
├── backend/                  # Sensor data API (port 8000)
├── automation-service/        # Device control API (port 8001)
├── frontend/                 # React dashboard
├── can-processor-service/    # CAN bus data processing
├── soil-sensor-service/     # RS485 soil sensors
├── weather-service/          # Weather API (YUL Airport)
├── database/                # Schema & docs
└── *.service                # Systemd service files
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Sensor API | `backend/app/` | Queries Redis + TimescaleDB |
| Automation control | `automation-service/app/` | PID control, schedules, alarms |
| Frontend UI | `frontend/src/` | React components |
| Data ingestion | `can-processor-service/app/`, `soil-sensor-service/app/` | Write to Redis Stream |
| Redis Stream reader | `backend/app/redis_stream_reader.py` | Shared utility |
| Stream processor | `backend/app/stream_processor.py` | Parse stream entries |
| Service startup | `*.service` files | systemd configuration |

## CONVENTIONS

### Service Structure
- **Entry point**: `app/main.py` for all services
- **Directory layout**:
  - `app/routes/` - API endpoints
  - `app/<domain>/` - Business logic (automation, control, hardware)
  - `tests/` - Service tests
- **Dependencies**: `requirements.txt` per service

### Data Storage Pattern (ALL SERVICES)
1. **Write to Redis Stream**: `sensor:raw` with `type` field (can/soil/automation)
2. **Write to TimescaleDB**: Full historical data in hypertables
3. **Write Redis State**: `sensor:<name>` or `automation:<loc>:<cluster>:<device>` with 10s TTL

### Service Communication
- **Backend reads**: Redis Stream + TimescaleDB
- **Automation reads**: TimescaleDB (setpoints), writes Redis
- **Frontend reads**: Backend API (port 8000) + Automation API (port 8001)

## COMMANDS

```bash
# Start services in order
sudo systemctl start postgresql redis-server can-setup
sudo systemctl start can-processor soil-sensor-service cea-backend automation-service

# Development - backend
cd backend && uvicorn app.main:app --reload --port 8000

# Development - automation
cd automation-service && uvicorn app.main:app --reload --port 8001

# Development - frontend
cd frontend && npm run dev

# Install dependencies
pip3 install -r requirements.txt  # (in each service dir)
npm install                       # (in frontend)

# Build frontend
cd frontend && npm run build
```

## ANTI-PATTERNS (THIS PROJECT)

- **Never**: Skip Redis Stream writes (required for recent data queries)
- **Never**: Skip TimescaleDB writes (required for long-term storage)
- **Never**: Use TTL >10s for state keys
- **Never**: Start services out of order (dependencies will fail)
- **Never**: Modify `.service` files without daemon-reload
- **Never**: Commit secrets (POSTGRES_PASSWORD in service files is a placeholder)

## NOTES

- **Startup order critical**: postgresql → redis → can-setup → data services → backend → automation
- **Shared utilities**: Redis Stream reader, Stream processor in backend/
- **Error handling**: All services must handle Redis/DB connection failures gracefully
- **Auto-recovery**: Redis AOF corruption auto-fix on boot
