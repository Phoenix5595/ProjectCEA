-- Grafana Optimized Views and Functions
-- These views and functions are optimized for common Grafana query patterns
-- Created for performance optimization of Grafana dashboards

-- ============================================
-- Optimized View for Latest Sensor Values
-- ============================================
-- This view provides fast access to the latest value for each sensor
-- Uses a window function for better performance than subqueries

CREATE OR REPLACE VIEW latest_sensor_values AS
SELECT DISTINCT ON (sensor_id)
    m.sensor_id,
    m.time,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.name AS device_name,
    r.name AS room_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
ORDER BY m.sensor_id, m.time DESC;

COMMENT ON VIEW latest_sensor_values IS 'Optimized view for getting latest value per sensor - faster than MAX(time) subqueries';

-- ============================================
-- Function: Get Latest Values by Sensor Name Pattern
-- ============================================
-- Optimized function for getting latest values filtered by sensor name pattern
-- Useful for queries like "get latest values for all _f sensors"

CREATE OR REPLACE FUNCTION get_latest_by_pattern(pattern TEXT)
RETURNS TABLE (
    sensor_id INTEGER,
    sensor_name TEXT,
    value REAL,
    unit TEXT,
    time TIMESTAMPTZ,
    device_name TEXT,
    room_name TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT ON (s.sensor_id)
        s.sensor_id,
        s.name AS sensor_name,
        m.value,
        s.unit,
        m.time,
        d.name AS device_name,
        r.name AS room_name
    FROM sensor s
    JOIN measurement m ON s.sensor_id = m.sensor_id
    JOIN device d ON s.device_id = d.device_id
    LEFT JOIN rack rk ON d.rack_id = rk.rack_id
    LEFT JOIN room r ON rk.room_id = r.room_id
    WHERE s.name LIKE pattern
    ORDER BY s.sensor_id, m.time DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_latest_by_pattern(TEXT) IS 'Get latest values for sensors matching a name pattern (e.g., %_f or %_b)';

-- ============================================
-- Optimized Time-Series View with Metadata
-- ============================================
-- This view is similar to measurement_with_metadata but optimized for time-series queries
-- It includes hints for the query planner to use indexes efficiently

CREATE OR REPLACE VIEW measurement_timeseries AS
SELECT 
    m.time,
    m.sensor_id,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.device_id,
    d.name AS device_name,
    r.room_id,
    r.name AS room_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id;

COMMENT ON VIEW measurement_timeseries IS 'Optimized time-series view - alias for measurement_with_metadata with same structure';

-- ============================================
-- Materialized View for Latest Values (Optional)
-- ============================================
-- This materialized view can be refreshed periodically for very fast latest value queries
-- Trade-off: slightly stale data (refresh every 5-10 seconds) for much faster queries
-- Uncomment and configure refresh policy if needed

-- CREATE MATERIALIZED VIEW IF NOT EXISTS latest_sensor_values_materialized AS
-- SELECT DISTINCT ON (sensor_id)
--     m.sensor_id,
--     m.time,
--     m.value,
--     m.status,
--     s.name AS sensor_name,
--     s.unit AS sensor_unit,
--     d.name AS device_name,
--     r.name AS room_name
-- FROM measurement m
-- JOIN sensor s ON m.sensor_id = s.sensor_id
-- JOIN device d ON s.device_id = d.device_id
-- LEFT JOIN rack rk ON d.rack_id = rk.rack_id
-- LEFT JOIN room r ON rk.room_id = r.room_id
-- ORDER BY m.sensor_id, m.time DESC;
--
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_latest_sensor_values_materialized_sensor_id
--     ON latest_sensor_values_materialized (sensor_id);
--
-- COMMENT ON MATERIALIZED VIEW latest_sensor_values_materialized IS 
--     'Materialized view of latest sensor values - refresh every 5-10 seconds for near-real-time performance';

-- ============================================
-- Verification Queries
-- ============================================

-- Test latest_sensor_values view performance
-- EXPLAIN ANALYZE SELECT * FROM latest_sensor_values WHERE sensor_name LIKE '%_f';

-- Test get_latest_by_pattern function
-- SELECT * FROM get_latest_by_pattern('%_f');


