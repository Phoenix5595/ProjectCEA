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

### Cluster validation (Phase 5e)

`/api/sensors/{location}/{cluster}` and `/live` validate `cluster` against the canonical topology in `shared/cluster_topology.py`:

- Sensor sub-cluster valid for room → query proceeds.
- Device cluster on a room with named sub-clusters (e.g. `Flower Room/main`) → **400** with hint `"sensor data lives under ['front', 'back']"`.
- Sensor sub-cluster on a room without one (e.g. `Veg Room/front`) → **400** with hint listing valid options.
- Unknown room → **404**.

This replaces the pre-Phase-5e silent-empty-dict behavior, which masked frontend wiring bugs (the dashboard was polling the device endpoint with sensor sub-cluster names).

## QUERY STRATEGY

| Time Range | Source | Why |
|------------|--------|-----|
| Live (now) | Redis state keys | Fastest |
| ≤6 hours | Redis Stream first | Avoids DB |
| >6 hours | TimescaleDB | Full history |
| ≥12 hours | Hourly aggregates | Performance |
| Multi-day | Daily aggregates | Performance |

`get_all_sensors_for_location` selects the coarsest aggregate tier whose buckets still resolve the requested range via `_pick_aggregate_tier` (Phase 5d). Tiers: `raw` / `1min` / `5min` / `hourly` / `daily`. The previous `hourly`/`daily` code path referenced non-existent columns (`mh.time`, `md.time` instead of `bucket`) and would have crashed if exercised — now driven by `_AggregateTier`.

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Query DB first for recent data | Check Redis Stream first |
| Modify Stream data | Read-only |
| Implement device control | That's automation-service |
| Use `MAX(time)` subqueries | Use `latest_sensor_values` view |
| Skip Stream check for <6h | Required for performance |
