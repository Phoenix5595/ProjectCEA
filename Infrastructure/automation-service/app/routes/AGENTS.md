# API ROUTES

**Generated:** 2026-01-07

## OVERVIEW
FastAPI route modules for automation service (port 8001) handling device control, schedules, and real-time updates via WebSocket.

## STRUCTURE

```
routes/
├── routes.py              # Router registration & DI injection
├── schedules.py           # Schedule CRUD & conflict checks
├── devices.py             # Device control (ON/OFF) & state
├── setpoints.py           # Climate setpoints (Day/Night/VPD)
├── lights.py              # DFR0971 dimming & interlocks
├── websocket.py           # Real-time state broadcasting
└── *.py                   # Domain endpoints (pid, rules, alarms, status, mode)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Route registration | `routes.py` | `include_router` + DI overrides |
| Schedule logic | `schedules.py` | Complex validation & conflict checks |
| Real-time updates | `websocket.py` | `/ws` endpoint + broadcast functions |
| Manual control | `devices.py` | `POST /api/devices/.../control` |
| PID tuning | `pid.py` | Runtime parameter updates |
| Light intensity | `lights.py` | 0-100% dimming control |
| System status | `status.py` | Health & mode status |

## CONVENTIONS

- **Dependency Injection**: Use `Depends()` for DB, Config, Managers. Define `get_*` stubs in module, override in `routes.py`.
- **Router Pattern**: Define `router = APIRouter()` in each module.
- **Validation**: Strict Pydantic models for all requests (`ScheduleCreate`, `DeviceControlRequest`).
- **Async**: All handlers must be `async`.
- **Tags**: Use `tags=["domain"]` in `include_router` for OpenAPI grouping.

## COMMANDS

```bash
# API Docs
curl http://localhost:8001/docs

# Health Check
curl http://localhost:8001/health

# WebSocket Test
wscat -c ws://localhost:8001/ws
```

## ANTI-PATTERNS

- **Never**: Put business logic in routes (delegate to `control/` managers).
- **Never**: Hardcode dependencies (use DI container).
- **Never**: Skip Pydantic validation.
- **Never**: Use synchronous IO (blocks event loop).
- **Never**: Add routes without registering in `routes.py`.
