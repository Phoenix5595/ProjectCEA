# Database Layer

PostgreSQL + TimescaleDB for normalized metadata, time-series measurements, continuous aggregates, and monitoring read models.

## Schema and query ownership

| File | Responsibility |
|---|---|
| `cea_schema.sql` | Core tables: `room`, `rack`, `device`, `sensor`, `measurement` hypertable, `climate_periods`, `actuator_events` |
| `grafana_performance_migration.sql` | Continuous aggregates, Grafana views, and `get_sensor_data_optimized` |
| `monitoring_read_models.sql` | Materialized-only aggregates and setpoint rollups for the native monitoring feature |
| `timescaledb_config.sql` | Compression and retention policies |
| `operational_history_retention.sql` | Operator-only retention policies for operational-event durability (raw control histories 7 days, aggregates and control_history 30 days) |

## Redis live vs Postgres time series

- Redis holds live state (`sensor:{name}`, 10 s TTL) and streams (`sensor:raw`, `stream:control`, `cea:events:operational`). `sensor:raw` and `stream:control` cap at 100,000 entries; `cea:events:operational` caps at 50,000 entries or 24 hours.
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

## Operational event durability

Alarm and error lifecycle events are stored in the existing `control_history` hypertable using reserved rows:

- `channel = -1`
- `device_name = alarm:<alarm_name>`
- `mode = alarm:<lifecycle>:<severity>`
- `old_state`/`new_state` encode lifecycle transition
- `reason` carries correlation context where it fits

Normal control-history reads keep `channel >= 0`, so alarm rows do not appear as duplicate relay activity.

## Retention policies for operational data

`operational_history_retention.sql` defines idempotent TimescaleDB retention and refresh policies:

| Object | Retention |
|---|---|
| `automation_state` | 7 days |
| `effective_setpoints` | 7 days |
| `control_history` (including reserved alarm rows) | 30 days |
| `monitoring_automation_state_1min/5min` | 30 days |
| `monitoring_effective_setpoints_1min/5min` | 30 days |

The four aggregate refresh policies use a 6-day `start_offset`, leaving one day of margin before raw rows expire.

## Activation guardrails

`operational_history_retention.sql` aborts unless all of these are true:

- Database name matches `^monitoring_test_[a-z0-9_]+$`.
- `current_database()` is not `cea_sensors`.
- `app.operational_retention_disposable` is set to `1`.

The file is referenced only by the disposable test harness (`Infrastructure/database/tests/test-operational-history-retention.sh`). It is not invoked by service startup, `deploy.sh`, `finalize-deploy.sh`, `rollback-deploy.sh`, or `Infrastructure/services.yaml`.

## Monitoring policy activation

`monitoring_read_models_activate_policies.sql` remains a supervised, manually reviewed action. It requires all six backfill markers and complete catalog coverage before any `add_continuous_aggregate_policy` call.

## Rollback irreversibility

Removing or disabling a retention policy stops future deletion, but it cannot restore rows the policy already dropped. Plan separate backups before enabling either policy set in production.

## Min/max preservation

Every continuous aggregate keeps `avg_value`, `min_value`, and `max_value`. Removing min/max would hide fast swings (for example, humidity spikes), so all consumers expose the envelope.

## SQL tests

Database tests live in `Infrastructure/database/tests/`. They validate idempotency, tier edge cases, statistics equivalence, destructive-change rejection, and the operational retention boundaries. The harnesses are:

```bash
bash Infrastructure/database/tests/test-monitoring-read-models.sh --case reject-incompatible-and-destructive
bash Infrastructure/database/tests/test-operational-history-retention.sh
```

Run these locally against a disposable Postgres instance, never against production.

---

*Last updated: 2026-09-03*
