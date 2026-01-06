# AUTOMATION SERVICE

**Generated:** 2025-01-05

## OVERVIEW
Device control and configuration service: PID control for heating/CO₂, schedule management, alarm monitoring, WebSocket real-time updates. Port 8001.

## STRUCTURE

```
automation-service/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── bootstrap.py         # Startup initialization
│   ├── middleware.py        # Request/response handling
│   ├── config.py           # Configuration & zones
│   ├── database.py         # TimescaleDB operations
│   ├── redis_client.py     # Redis Stream + state writes
│   ├── container.py        # Dependency injection
│   ├── validation.py       # Input validation
│   ├── alarm_manager.py    # Alarm logic
│   ├── background_tasks.py # Scheduled tasks
│   ├── automation/         # Automation logic (setpoints, schedules)
│   ├── control/           # Device control (CAN, relays)
│   ├── hardware/          # Hardware interfaces
│   └── routes/           # API endpoints
└── tests/
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| API endpoints | `app/routes/` | REST API + WebSocket |
| PID control | `app/control/` | Heater/CO₂ control logic |
| Setpoints | `app/automation/` | Storage, retrieval, validation |
| Schedules | `app/automation/` | Create, update, conflict detection |
| Alarms | `app/alarm_manager.py` | Alarm triggers, notifications |
| Hardware control | `app/hardware/` | CAN messages, relay control |
| Background tasks | `app/background_tasks.py` | Mode switching, setpoint activation |
| Database ops | `app/database.py` | Setpoint/historic queries |
| Redis writes | `app/redis_client.py` | State updates to Redis |

## CONVENTIONS

### Data Storage
- **Setpoints**: TimescaleDB `setpoints` table (mode-specific: DAY/NIGHT/TRANSITION)
- **Historic automation**: TimescaleDB `automation_history` table
- **Live state**: Redis keys `automation:<location>:<cluster>:<device_name>` (10s TTL)
- **Redis Stream**: Write automation state to `sensor:raw` with `type=automation`

### API Patterns
- **Setpoints**: `/api/setpoints/{location}/{cluster}` - GET/POST
- **PID params**: `/api/pid/parameters/{device_type}` - GET/POST
- **Schedules**: `/api/schedules` - CRUD operations
- **WebSocket**: `/ws` - Real-time updates to frontend

### Zones (Hardcoded)
- Flower Room (front, back)
- Veg Room (main)
- Lab (main)

### Modes
- DAY, NIGHT, TRANSITION, PRE_DAY, PRE_NIGHT
- Mode switching triggers setpoint changes

## COMMANDS

```bash
# Development
cd automation-service
uvicorn app.main:app --reload --port 8001

# Production
sudo systemctl start automation-service
sudo systemctl stop automation-service

# Logs
journalctl -u automation-service -f

# Health check
curl http://localhost:8001/health
```

## ANTI-PATTERNS (THIS SERVICE)

- **Never**: Write automation state without 10s TTL
- **Never**: Skip TimescaleDB writes (setpoints must persist)
- **Never**: Bypass validation (setpoint ranges are enforced)
- **Never**: Modify zones config at runtime (requires restart)
- **Never**: Send CAN messages without proper encoding

## NOTES

- **Frontend served**: Automation-service serves React frontend from `dist/`
- **WebSocket**: Pushes device state changes to frontend in real-time
- **PID control**: Independent PID loops for heaters and CO₂ injectors
- **Schedule conflicts**: Detected before saving to database
- **Alarm monitoring**: Checks sensor values against setpoints, triggers alerts
