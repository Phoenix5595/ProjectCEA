# DATABASE LAYER

**Generated:** 2026-01-07

## OVERVIEW
TimescaleDB storage layer managing normalized sensor metadata, compressed hypertables, and continuous aggregates for high-performance querying.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Schema definition | `cea_schema.sql` | `room`→`rack`→`device`→`sensor`→`measurement` |
| Compression config | `timescaledb_compression.sql` | >90 days, segment by `sensor_id` |
| Aggregations | `timescaledb_continuous_aggregates.sql` | Hourly (7d raw), Daily (30d raw) |
| Grafana views | `grafana_optimized_views.sql` | `latest_sensor_values`, `DISTINCT ON` patterns |
| Performance tests | `verify_performance.sql` | Index benchmarks & query analysis |
| Setpoints docs | `SETPOINTS_TABLE_EXPLANATION.md` | Automation control table specs |

## CONVENTIONS

### Schema & Hypertables
- **Structure**: Normalized metadata tables + single `measurement` hypertable
- **Partitioning**: 1-day chunks for `measurement` (optimized for ~4M rows/day)
- **Primary Key**: `(time, sensor_id)` to enforce uniqueness
- **Indexes**: `(sensor_id, time DESC)` for sensor history, `(time DESC)` for global trends

### Optimization Strategy
- **Compression**: Enabled for data >90 days (70-90% savings)
- **Aggregates**:
  - `measurement_hourly`: fast for 7-30 day ranges
  - `measurement_daily`: fast for >30 day ranges
- **Refresh**: Policies run automatically (hourly/daily) via background workers

## ANTI-PATTERNS (THIS LAYER)

- **Never**: Query `measurement` without `time` filter (scans all compressed chunks)
- **Never**: Use `MAX(time)` subqueries for latest values (use `latest_sensor_values`)
- **Never**: Alter schema without testing migration scripts (see `migrate_*.sql`)
- **Never**: Disable compression in production (critical for disk space)
- **Never**: Rely on `SELECT *` in app code (explicitly list columns)
