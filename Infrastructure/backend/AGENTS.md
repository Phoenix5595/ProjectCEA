# Backend Service

Sensor data API on port 8000. Serves live values from Redis, historical data from TimescaleDB, and real-time WebSocket streams.

## Route groups

| Prefix | File | Purpose |
|---|---|---|
| `/api/sensors/{location}/{cluster}` | `routes/sensors.py` | Historical sensor data |
| `/api/sensors/{location}/{cluster}/live` | `routes/sensors.py` | Current Redis values |
| `/api/live/all` | `routes/live.py` | All current sensor values |
| `/api/config/locations` | `routes/config.py` | Available locations |
| `/ws/{location}` | `websocket.py` | Real-time sensor stream |
| `/api/sensors/monitoring/*` | monitoring-service `:8005` | Native monitoring data; separate read-only process and pool |

## Topology validation

All `{location}` and `{cluster}` parameters are validated against `shared/cluster_topology.py`. The API rejects cross-type cluster lookups with HTTP 400 and an actionable hint: for example, `Flower Room/main` returns a hint pointing to `front`/`back`.

## Consumer-specific aggregate ladder

The backend historical endpoint uses its own ladder in `app/repositories/sensor_repository.py`:

| Range | Tier |
|---|---|
| < 2 h | `measurement` raw |
| >= 2 h | `measurement_1min` |
| >= 24 h | `measurement_5min` |
| >= 7 d | `measurement_hourly` |
| >= 30 d | `measurement_daily` |

Grafana uses the separate `get_sensor_data_optimized` ladder in `Infrastructure/database/grafana_performance_migration.sql`. Do not conflate the two consumers.

## Native monitoring ownership

`monitoring-service :8005` owns range, publication, and control monitoring APIs. The backend does not register a monitoring router; see `Infrastructure/monitoring-service/AGENTS.md`.

## Tests

Backend tests live in `app/tests/` and use fake Redis or guarded disposable fixtures. Native monitoring tests live in `Infrastructure/monitoring-service/tests/`.
