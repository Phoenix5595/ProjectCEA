# BACKEND SERVICE

## OVERVIEW

Sensor data API on port 8000. Serves historical data from TimescaleDB, live data from Redis. WebSocket for real-time updates.

## STRUCTURE

```
backend/
├── app/
│   ├── main.py              # FastAPI entry + lifespan
│   ├── database.py          # TimescaleDB queries
│   ├── redis_client.py      # State key + stream reading
│   ├── redis_stream_reader.py # Stream query helper
│   ├── routes/
│   │   ├── sensors.py       # Historical + live data
│   │   ├── config.py        # Locations config
│   │   └── live.py          # All live values
│   └── websocket.py         # Real-time broadcast
└── requirements.txt
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Setup | `README.md` |

## API ENDPOINTS

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/api/sensors/{location}/{cluster}` | Historical data |
| GET | `/api/sensors/{location}/{cluster}/live` | Current values from Redis |
| GET | `/api/live/all` | All current sensor values |
| GET | `/api/config/locations` | Available locations |
| WS | `/ws/{location}` | Real-time sensor stream |

## QUERY STRATEGY

| Time Range | Source | Why |
|------------|--------|-----|
| Live (now) | Redis state keys | Fastest |
| ≤6 hours | Redis Stream first | Avoids DB |
| >6 hours | TimescaleDB | Full history |
| ≥12 hours | Hourly aggregates | Performance |
| Multi-day | Daily aggregates | Performance |

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Query DB first for recent data | Check Redis Stream first |
| Modify Stream data | Read-only |
| Implement device control | That's automation-service |
| Use `MAX(time)` subqueries | Use `latest_sensor_values` view |
| Skip Stream check for <6h | Required for performance |
