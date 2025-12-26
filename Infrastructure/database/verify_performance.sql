-- Performance Verification Script for Grafana Query Optimization
-- Run these queries to verify that indexes are being used and queries are performing well
-- 
-- Usage: psql -U cea_user -d cea_sensors -f verify_performance.sql

-- ============================================
-- 1. Verify Indexes Exist
-- ============================================

-- Check sensor.name index
SELECT 
    indexname, 
    indexdef 
FROM pg_indexes 
WHERE tablename = 'sensor' 
  AND indexname = 'idx_sensor_name';

-- Check foreign key indexes
SELECT 
    indexname, 
    tablename,
    indexdef 
FROM pg_indexes 
WHERE indexname IN (
    'idx_room_facility_id',
    'idx_rack_room_id',
    'idx_device_rack_id'
)
ORDER BY tablename, indexname;

-- Check measurement indexes
SELECT 
    indexname, 
    indexdef 
FROM pg_indexes 
WHERE tablename = 'measurement'
ORDER BY indexname;

-- ============================================
-- 2. Test Query Performance (Latest Values)
-- ============================================

-- Test optimized latest values query (using DISTINCT ON)
EXPLAIN ANALYZE
WITH latest_f AS (
    SELECT DISTINCT ON (s.sensor_id) 
        s.name AS sensor_name, 
        m.value, 
        m.time 
    FROM measurement m 
    JOIN sensor s ON m.sensor_id = s.sensor_id 
    WHERE s.name LIKE '%_f' 
      AND s.name NOT LIKE 'secondary_%' 
      AND m.time >= NOW() - INTERVAL '10 minutes' 
    ORDER BY s.sensor_id, m.time DESC
)
SELECT * FROM latest_f;

-- ============================================
-- 3. Test Query Performance (Time-Series)
-- ============================================

-- Test time-series query with sensor_name filter
EXPLAIN ANALYZE
SELECT 
    time, 
    sensor_name, 
    value 
FROM measurement_with_metadata 
WHERE sensor_name = 'dry_bulb_b'
  AND time >= NOW() - INTERVAL '1 hour'
  AND time <= NOW()
ORDER BY time;

-- Test time-series query with multiple sensors
EXPLAIN ANALYZE
SELECT 
    time, 
    sensor_name, 
    value 
FROM measurement_with_metadata 
WHERE sensor_name IN ('dry_bulb_f', 'wet_bulb_f', 'rh_f', 'vpd_f')
  AND time >= NOW() - INTERVAL '6 hours'
  AND time <= NOW()
ORDER BY time, sensor_name;

-- ============================================
-- 4. Test Query Performance (Statistics)
-- ============================================

-- Test statistics query
EXPLAIN ANALYZE
SELECT 
    sensor_name,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    AVG(value) AS avg_value,
    STDDEV(value) AS stddev_value
FROM measurement_with_metadata 
WHERE sensor_name IN ('dry_bulb_f', 'wet_bulb_f', 'rh_f', 'vpd_f')
  AND time >= NOW() - INTERVAL '6 hours'
  AND time <= NOW()
GROUP BY sensor_name;

-- ============================================
-- 5. Verify Index Usage in Query Plans
-- ============================================

-- Check if idx_sensor_name is being used
EXPLAIN (ANALYZE, BUFFERS)
SELECT time, sensor_name, value
FROM measurement_with_metadata
WHERE sensor_name = 'dry_bulb_b'
  AND time >= NOW() - INTERVAL '1 hour'
ORDER BY time;

-- Check if idx_measurement_sensor_time is being used
EXPLAIN (ANALYZE, BUFFERS)
SELECT time, value
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
WHERE s.name = 'dry_bulb_b'
  AND m.time >= NOW() - INTERVAL '1 hour'
ORDER BY m.time;

-- ============================================
-- 6. Performance Benchmarks
-- ============================================

-- Benchmark: Latest values query (should be < 50ms)
\timing on
SELECT DISTINCT ON (s.sensor_id) 
    s.name AS sensor_name, 
    m.value, 
    m.time 
FROM measurement m 
JOIN sensor s ON m.sensor_id = s.sensor_id 
WHERE s.name LIKE '%_f' 
  AND m.time >= NOW() - INTERVAL '10 minutes' 
ORDER BY s.sensor_id, m.time DESC
LIMIT 10;

-- Benchmark: Time-series query for 6 hours (should be < 200ms)
SELECT 
    time, 
    sensor_name, 
    value 
FROM measurement_with_metadata 
WHERE sensor_name IN ('dry_bulb_f', 'wet_bulb_f')
  AND time >= NOW() - INTERVAL '6 hours'
  AND time <= NOW()
ORDER BY time, sensor_name
LIMIT 1000;

\timing off

-- ============================================
-- 7. Check Table Statistics
-- ============================================

-- Check if statistics are up to date
SELECT 
    schemaname,
    tablename,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE tablename IN ('measurement', 'sensor', 'device', 'room', 'rack')
ORDER BY tablename;

-- ============================================
-- 8. Check Index Usage Statistics
-- ============================================

-- See which indexes are being used
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan AS index_scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
  AND tablename IN ('measurement', 'sensor', 'device', 'room', 'zone', 'rack')
ORDER BY tablename, indexname;

-- ============================================
-- 9. Recommendations
-- ============================================

-- If queries are slow, check:
-- 1. Are indexes being used? (Look for "Index Scan" in EXPLAIN output)
-- 2. Are statistics up to date? (Run VACUUM ANALYZE if needed)
-- 3. Is PostgreSQL configured optimally? (Check timescaledb_config.sql)
-- 4. Are connection pool settings correct in Grafana?

-- To update statistics:
-- VACUUM ANALYZE measurement;
-- VACUUM ANALYZE sensor;
-- VACUUM ANALYZE device;
-- VACUUM ANALYZE room;
-- VACUUM ANALYZE rack;


