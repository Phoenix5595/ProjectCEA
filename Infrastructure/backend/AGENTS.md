# Backend Service

Sensor data API on port 8000. Serves live values from Redis, historical data from TimescaleDB, native monitoring envelopes, and real-time WebSocket streams.

## Route groups

| Prefix | File | Purpose |
|---|---|---|
| `/api/sensors/{location}/{cluster}` | `routes/sensors.py` | Historical sensor data |
| `/api/sensors/{location}/{cluster}/live` | `routes/sensors.py` | Current Redis values |
| `/api/live/all` | `routes/live.py` | All current sensor values |
| `/api/config/locations` | `routes/config.py` | Available locations |
| `/ws/{location}` | `websocket.py` | Real-time sensor stream |
| `/api/sensors/monitoring/range/{location}` | `routes/sensor_monitoring.py` | Historical envelopes for Flower/Veg |
| `/api/sensors/monitoring/live/{location}/{node}` | `routes/sensor_monitoring.py` | Current monitoring node values |
| `/api/sensors/monitoring/stats/{location}` | `routes/sensor_monitoring.py` | Exact statistics without series |

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

## Monitoring models

`app/monitoring_models.py` defines strict Pydantic contracts: `MonitoringRange`, `SeriesPoint`, `SensorStatistics`, `MonitoringResponse`, and `compute_tier()`. The monitoring feature supports only `Flower Room` and `Veg Room`.

## Tests

Focused backend tests live in `app/tests/monitoring/` and cover models, series, statistics, and routes. Run them with the local gate in `ARCHITECTURE.md`.
