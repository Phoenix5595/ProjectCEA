# Database Layer

PostgreSQL + TimescaleDB for normalized metadata, time-series measurements, continuous aggregates, and monitoring read models.

## Schema and query ownership

| File | Responsibility |
|---|---|
| `cea_schema.sql` | Core tables: `room`, `rack`, `device`, `sensor`, `measurement` hypertable, `climate_periods`, `actuator_events` |
| `grafana_performance_migration.sql` | Continuous aggregates, Grafana views, and `get_sensor_data_optimized` |
| `monitoring_read_models.sql` | Materialized-only aggregates and setpoint rollups for the native monitoring feature |
| `timescaledb_config.sql` | Compression and retention policies |

## Redis live vs Postgres time series

- Redis holds live state (`sensor:{name}`, 10 s TTL) and streams (`sensor:raw`, `stream:control`, both capped at 100,000 entries).
- TimescaleDB holds historical `measurement` rows and continuous aggregates.
- Grafana on Iskra queries Postgres for historical panels **and** Redis for current-value panels via `redis_sync`.

## Consumer-specific aggregate ladders

Grafana ladder (`get_sensor_data_optimized`):

| Span | Tier |
|---|---|
| <= 1 h | raw |
| (1 h, 6 h] | `measurement_1min` |
| (6 h, 24 h] | `measurement_5min` |
| > 24 h | `measurement_hourly` |

Backend API ladder (`app/repositories/sensor_repository.py`):

| Span | Tier |
|---|---|
| < 2 h | raw |
| >= 2 h | `measurement_1min` |
| >= 24 h | `measurement_5min` |
| >= 7 d | `measurement_hourly` |
| >= 30 d | `measurement_daily` |

## Min/max preservation

Every continuous aggregate keeps `avg_value`, `min_value`, and `max_value`. Removing min/max would hide fast swings (for example, humidity spikes), so all consumers expose the envelope.

## SQL tests

Database tests live in `Infrastructure/database/tests/`. They validate idempotency, tier edge cases, statistics equivalence, and destructive-change rejection. The harness is `tests/test-monitoring-read-models.sh`. Run these locally against a disposable Postgres instance, never against production.
