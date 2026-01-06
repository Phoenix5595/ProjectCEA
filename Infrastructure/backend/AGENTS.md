# BACKEND SERVICE

**Generated:** 2025-01-05

## OVERVIEW
Sensor data aggregation API: reads from Redis Stream + TimescaleDB, serves to frontend. Port 8000. No device control (handled by automation-service).

## STRUCTURE

```
backend/
├── app/
│   ├── main.py                    # FastAPI entry point
│   ├── redis_stream_reader.py      # Read from sensor:raw stream
│   ├── stream_processor.py         # Parse stream entries
│   ├── database.py                # TimescaleDB operations
│   └── routes/
│       ├── sensors.py             # Sensor data endpoints
│       └── ...other routes
└── tests/
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| API endpoints | `app/routes/` | Sensor queries, health check |
| Stream reader | `app/redis_stream_reader.py` | Read Redis Stream sensor:raw |
| Stream processor | `app/stream_processor.py` | Parse stream to sensor data |
| Database queries | `app/database.py` | TimescaleDB operations |
| Main app | `app/main.py` | FastAPI setup, CORS |

## CONVENTIONS

### Data Query Priority (Critical)
1. **Live data**: Read from Redis state keys (`sensor:*`)
2. **Recent (<6h)**: Query Redis Stream `sensor:raw`, filter by type
3. **Older**: Query TimescaleDB `measurement` table

### API Endpoints
- `GET /health` - Health check
- `GET /api/sensors/{location}/{cluster}/live` - Live sensor data (Redis)
- `GET /api/sensors/{location}/{cluster}?time_range=X` - Historical data (Stream/DB)

### Stream Reading Pattern
```python
# 1. Try Redis Stream first (for recent data)
if duration_hours <= 6:
    entries = stream_reader.read_by_time_range(
        start, end, sensor_type="can", max_count=20000
    )
    if entries:
        return process_stream_entries(entries)

# 2. Fall back to TimescaleDB
return db.get_sensor_data(start, end)
```

### Shared Utilities
These are shared patterns, use them in other services:
- `RedisStreamReader` - Read from `sensor:raw`
- `process_stream_entries_to_sensor_data()` - Parse entries

## COMMANDS

```bash
# Development
cd backend
uvicorn app.main:app --reload --port 8000

# Production
sudo systemctl start cea-backend
sudo systemctl stop cea-backend

# Logs
journalctl -u cea-backend -f

# Health check
curl http://localhost:8000/health

# Test live data
curl http://localhost:8000/api/sensors/Flower%20Room/back/live

# Test historical data
curl "http://localhost:8000/api/sensors/Flower%20Room/back?time_range=1%20Hour"
```

## ANTI-PATTERNS (THIS SERVICE)

- **Never**: Skip Stream check (required for recent data performance)
- **Never**: Query DB first (always check Stream for <6h data)
- **Never**: Modify Stream data (read-only)
- **Never**: Implement device control (handled by automation-service)
- **Never**: Bypass Redis state keys for live data (causes stale data)

## NOTES

- **Read-only service**: Only reads data, never writes
- **Performance**: Stream-first for recent data reduces DB load
- **Stream filtering**: Filter by `type` field (can/soil/automation)
- **No control endpoints**: All device control in automation-service
- **CORS**: Enabled for frontend access
