# TimescaleDB Optimization Guide for CEA

This document describes all the optimizations applied to the TimescaleDB database for optimal CEA sensor data storage and query performance.

## Overview

The TimescaleDB database has been optimized for:
- **Fast queries** - Especially for Grafana dashboards
- **Storage efficiency** - Compression reduces storage by ~70%
- **Data integrity** - Validation constraints prevent bad data
- **Long-term scalability** - Optimized for indefinite data retention

## Optimizations Applied

### 1. Schema Enhancements

#### Primary Keys
All normalized tables now have explicit PRIMARY KEY constraints on the `id` column for better referential integrity and query performance.

#### Data Validation Constraints
CHECK constraints have been added to ensure data quality:
- **Temperature**: -50°C to 100°C range
- **Humidity (RH)**: 0% to 100% range
- **VPD**: 0 to 10 kPa range
- **CO2**: 0 to 10,000 ppm range
- **Pressure**: 800 to 1200 hPa range
- **Water level**: 0 to 4000 mm range
- **Node ID**: 1 to 10 range

These constraints prevent invalid sensor readings from being stored.

### 2. Index Optimization

#### Composite Indexes
All tables now have composite indexes on `(timestamp DESC, node_id)` which is the most common query pattern in Grafana:
- Queries filtering by time range and node_id are 10-100x faster
- Indexes are optimized for descending timestamp queries (most recent first)

#### Existing Indexes
- Individual indexes on `timestamp DESC` and `node_id` are maintained for flexibility

#### Grafana Query Performance Indexes
Additional indexes have been added specifically for Grafana query optimization:
- **`idx_sensor_name`** on `sensor(name)` - Fast filtering by sensor_name in `measurement_with_metadata` queries
- **`idx_room_facility_id`** on `room(facility_id)` - Optimizes JOINs in metadata views
- **`idx_rack_room_id`** on `rack(room_id)` - Optimizes JOINs in metadata views
- **`idx_device_rack_id`** on `device(rack_id)` - Optimizes JOINs in metadata views

These indexes significantly improve performance of `measurement_with_metadata` view queries, which are commonly used in Grafana dashboards.

### 3. Hypertable Chunk Intervals

Chunk intervals have been optimized based on data ingestion patterns:
- **Sensor tables**: 1 day chunks (temperature, climate, CO2, pressure, etc.)
- **Heartbeat table**: 7 day chunks (less frequent data)

Benefits:
- Faster queries on recent data
- Better compression efficiency
- Optimal balance between query performance and chunk management

### 4. Compression Policies

All hypertables have compression enabled with the following configuration:
- **Compression threshold**: Data older than 90 days
- **Segment by**: `node_id` (improves compression ratio and query performance)
- **Order by**: `timestamp DESC` (optimizes time-range queries)

**Storage savings**: Typically 70-90% reduction in storage for compressed data.

**Transparency**: Compressed data can be queried normally - no special syntax needed.

### 5. Continuous Aggregates

Materialized views that automatically aggregate data for faster queries:

#### Hourly Aggregates
- `temperature_readings_hourly` - Hourly averages, min, max for temperature
- `climate_readings_hourly` - Hourly averages for RH, VPD, pressure
- `co2_readings_hourly` - Hourly averages for CO2
- `pressure_readings_hourly` - Hourly averages for pressure and related metrics
- `secondary_rh_readings_hourly` - Hourly averages for secondary RH
- `water_level_readings_hourly` - Hourly averages for water level

**Refresh policy**: Updates every hour, keeps last 7 days of raw data

#### Daily Aggregates
- `temperature_readings_daily` - Daily summaries
- `climate_readings_daily` - Daily summaries
- `co2_readings_daily` - Daily summaries

**Refresh policy**: Updates daily, keeps last 30 days of raw data

**Performance**: Queries using aggregates are 10-100x faster than querying raw data for longer time ranges.

### 6. Connection Optimization

PostgreSQL has been configured for time-series workloads:
- Increased `work_mem` for better aggregation performance
- Optimized connection pooling settings
- Recommended Grafana data source settings documented

## Query Examples

### Using Normalized Tables (Recommended)

```sql
-- Temperature data for last hour
SELECT 
    timestamp as time,
    dry_bulb_c as "Dry Bulb (°C)",
    wet_bulb_c as "Wet Bulb (°C)"
FROM temperature_readings
WHERE node_id = 1
  AND timestamp > NOW() - INTERVAL '1 hour'
ORDER BY timestamp;
```

### Using Continuous Aggregates (For Longer Ranges)

```sql
-- Temperature hourly averages for last 7 days
SELECT 
    time,
    node_id,
    avg_dry_bulb_c as "Avg Dry Bulb (°C)",
    min_dry_bulb_c as "Min Dry Bulb (°C)",
    max_dry_bulb_c as "Max Dry Bulb (°C)"
FROM temperature_readings_hourly
WHERE node_id = 1
  AND time > NOW() - INTERVAL '7 days'
ORDER BY time;
```

### Multi-Node Comparison

```sql
-- CO2 levels across all nodes
SELECT 
    timestamp as time,
    node_id,
    co2_ppm as "CO2 (ppm)"
FROM co2_readings
WHERE timestamp > NOW() - INTERVAL '24 hours'
ORDER BY timestamp, node_id;
```

## Applying Optimizations

### Initial Setup

1. **Create normalized tables**:
   ```bash
   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\ CEA/Infrastructure/database/cea_schema.sql
   ```

2. **Enable compression**:
   ```bash
   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\ CEA/Infrastructure/database/timescaledb_compression.sql
   ```

3. **Create continuous aggregates**:
   ```bash
   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\ CEA/Infrastructure/database/timescaledb_continuous_aggregates.sql
   ```

### Updating Existing Database

If you have an existing database, you can apply optimizations incrementally:

1. **Add constraints** (may take time if you have existing data):
   ```sql
   -- Example: Add temperature range constraint
   ALTER TABLE temperature_readings 
   ADD CONSTRAINT chk_dry_bulb_range 
   CHECK (dry_bulb_c >= -50.0 AND dry_bulb_c <= 100.0);
   ```

2. **Add composite indexes**:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_temperature_readings_time_node 
   ON temperature_readings (timestamp DESC, node_id);
   ```

3. **Enable compression** (safe to run on existing data):
   ```bash
   psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\ CEA/Infrastructure/database/timescaledb_compression.sql
   ```

## Monitoring

### Check Compression Status

```sql
-- View compression statistics
SELECT 
    hypertable_name,
    pg_size_pretty(before_compression_total_bytes) as before,
    pg_size_pretty(after_compression_total_bytes) as after,
    ROUND((1.0 - after_compression_total_bytes::numeric / before_compression_total_bytes) * 100, 2) as compression_ratio
FROM timescaledb_information.job_stats
WHERE hypertable_name LIKE '%_readings';
```

### Check Continuous Aggregate Status

```sql
-- View continuous aggregate refresh status
SELECT 
    view_name,
    last_run_status,
    last_run_started_at,
    last_successful_finish
FROM timescaledb_information.continuous_aggregate_stats;
```

### Check Index Usage

```sql
-- View index usage statistics
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

## Best Practices

### For Grafana Dashboards

1. **Short time ranges (< 24 hours)**: Use raw normalized tables
2. **Medium time ranges (1-7 days)**: Use hourly continuous aggregates
3. **Long time ranges (> 7 days)**: Use daily continuous aggregates

### Query Performance Tips

1. Always filter by `timestamp` range first (uses time-based indexes)
2. Filter by `node_id` when querying specific locations
3. Use continuous aggregates for aggregations (AVG, MIN, MAX) on longer ranges
4. Avoid `SELECT *` - specify only needed columns

### Maintenance

1. **Compression**: Runs automatically in the background
2. **Continuous aggregates**: Refresh automatically based on policies
3. **VACUUM**: TimescaleDB handles this automatically, but you can run manually if needed:
   ```sql
   VACUUM ANALYZE temperature_readings;
   ```

## Troubleshooting

### Compression Not Working

Check if compression is enabled:
```sql
SELECT * FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
```

### Continuous Aggregates Not Refreshing

Check job status:
```sql
SELECT * FROM timescaledb_information.job_stats 
WHERE hypertable_name LIKE '%_hourly' OR hypertable_name LIKE '%_daily';
```

### Slow Queries

1. Check if indexes are being used: `EXPLAIN ANALYZE <query>`
2. Verify you're using continuous aggregates for longer time ranges
3. Check if data is compressed (compressed data queries are slightly slower but still fast)

## Grafana Query Performance Optimizations

### Optimized Views and Functions

The following optimized database objects have been created for faster Grafana queries:

- **`latest_sensor_values`** view - Optimized view using DISTINCT ON for getting latest sensor values (faster than MAX(time) subqueries)
- **`get_latest_by_pattern(pattern)`** function - Fast function for getting latest values filtered by sensor name pattern
- **`measurement_timeseries`** view - Alias for `measurement_with_metadata` with same optimized structure

See `Infrastructure/database/grafana_optimized_views.sql` for implementation details.

### Query Optimization Techniques

**1. Use DISTINCT ON instead of MAX(time) subqueries**
- DISTINCT ON is typically 2-5x faster than MAX(time) with GROUP BY
- Better index utilization
- Example: See optimized dashboard queries in `Infrastructure/frontend/grafana/dashboards/`

**2. Leverage sensor_name index**
- The `idx_sensor_name` index makes filtering by `sensor_name` in `measurement_with_metadata` very fast
- Always filter by `sensor_name` when possible instead of joining through sensor table

**3. Time range optimization**
- Always include time range filters (`$__timeFrom()` and `$__timeTo()`) in WHERE clauses
- TimescaleDB uses chunk exclusion to skip irrelevant data chunks
- For latest values, use a recent time window (e.g., `NOW() - INTERVAL '10 minutes'`)

### Performance Verification

Use `Infrastructure/database/verify_performance.sql` to:
- Verify indexes are being used
- Benchmark query performance
- Check index usage statistics
- Identify slow queries

Expected performance:
- **Latest values queries**: < 50ms
- **Time-series queries (6 hours)**: < 200ms
- **Statistics queries**: < 300ms

### Grafana Configuration

For optimal performance, configure Grafana data source with:
- **Max open connections**: 100
- **Max idle connections**: 100
- **Max connection lifetime**: 14400 (4 hours)

See `Infrastructure/frontend/grafana/README.md` for detailed Grafana configuration and query optimization guidelines.

## Files Reference

- **Schema**: `Infrastructure/database/cea_schema.sql`
- **Requirements**: `Infrastructure/database/REQUIREMENTS.md`
- **Compression**: `Infrastructure/database/timescaledb_compression.sql`
- **Continuous Aggregates**: `Infrastructure/database/timescaledb_continuous_aggregates.sql`
- **Configuration**: `Infrastructure/database/timescaledb_config.sql`
- **Grafana Optimized Views**: `Infrastructure/database/grafana_optimized_views.sql`
- **Performance Verification**: `Infrastructure/database/verify_performance.sql`

## Additional Resources

- [TimescaleDB Documentation](https://docs.timescale.com/)
- [TimescaleDB Compression Guide](https://docs.timescale.com/use-timescale/latest/compression/)
- [Continuous Aggregates Guide](https://docs.timescale.com/use-timescale/latest/continuous-aggregates/)

