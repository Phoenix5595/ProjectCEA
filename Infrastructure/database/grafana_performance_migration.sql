-- ============================================
-- ProjectCEA Grafana Performance Optimization
-- Migration Script v1.0
-- ============================================

BEGIN;

-- ============================================
-- PHASE 1: Create Continuous Aggregates
-- ============================================

-- 1-minute aggregate (for spans up to 6h via get_sensor_data_optimized; see migrate_get_sensor_routing_1min_through_6h.sql on existing DBs)
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_1min
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 minute', time) AS bucket,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS sample_count
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

-- 5-minute aggregate (for spans >6h and <=24h via get_sensor_data_optimized)
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_5min
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('5 minutes', time) AS bucket,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS sample_count
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

-- Hourly aggregate (for 1-7d range)
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', time) AS bucket,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS sample_count
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

-- Daily aggregate (for >7d range)
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_daily
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', time) AS bucket,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS sample_count
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

COMMIT;

-- ============================================
-- PHASE 2: Add Refresh Policies
-- ============================================

SELECT add_continuous_aggregate_policy('measurement_1min',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('measurement_5min',
    start_offset => INTERVAL '30 minutes',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('measurement_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('measurement_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ============================================
-- PHASE 3: Create Grafana Views with Metadata
-- ============================================

CREATE OR REPLACE VIEW measurement_1min_grafana AS
SELECT 
    m.bucket AS time,
    s.sensor_id,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    m.avg_value,
    m.min_value,
    m.max_value,
    m.sample_count
FROM measurement_1min m
JOIN sensor s ON m.sensor_id = s.sensor_id;

CREATE OR REPLACE VIEW measurement_5min_grafana AS
SELECT 
    m.bucket AS time,
    s.sensor_id,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    m.avg_value,
    m.min_value,
    m.max_value,
    m.sample_count
FROM measurement_5min m
JOIN sensor s ON m.sensor_id = s.sensor_id;

CREATE OR REPLACE VIEW measurement_hourly_grafana AS
SELECT 
    m.bucket AS time,
    s.sensor_id,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    m.avg_value,
    m.min_value,
    m.max_value,
    m.sample_count
FROM measurement_hourly m
JOIN sensor s ON m.sensor_id = s.sensor_id;

CREATE OR REPLACE VIEW measurement_daily_grafana AS
SELECT 
    m.bucket AS time,
    s.sensor_id,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    m.avg_value,
    m.min_value,
    m.max_value,
    m.sample_count
FROM measurement_daily m
JOIN sensor s ON m.sensor_id = s.sensor_id;

-- ============================================
-- PHASE 4: Auto-Routing Function
-- ============================================

CREATE OR REPLACE FUNCTION get_sensor_data_optimized(
    p_sensor_names TEXT[],
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
)
RETURNS TABLE (
    time TIMESTAMPTZ,
    sensor_name TEXT,
    value REAL,
    min_val REAL,
    max_val REAL
) AS $$
DECLARE
    v_span_s bigint;
BEGIN
    -- Whole-second span avoids Grafana $__timeTo/$__timeFrom being a few ms over
    -- a nominal preset (e.g. "Last 6 hours") and incorrectly selecting 5min CAGG.
    v_span_s := FLOOR(GREATEST(EXTRACT(EPOCH FROM (p_to - p_from)), 0::numeric))::bigint;

    IF v_span_s > 86400 THEN
        -- Use hourly aggregate for >24h
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_hourly_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;
        
    ELSIF v_span_s > 21600 THEN
        -- Use 5-minute aggregate for (6h, 24h]
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_5min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;
        
    ELSIF v_span_s > 3600 THEN
        -- Use 1-minute aggregate for (1h, 6h]
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_1min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;
        
    ELSE
        -- Use raw data for <1 hour (live data)
        RETURN QUERY
        SELECT m.time, s.name, m.value, m.value, m.value
        FROM measurement m
        JOIN sensor s ON m.sensor_id = s.sensor_id
        WHERE s.name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_sensor_data_optimized IS
'Span routing (whole seconds): <=1h raw; (1h,6h] measurement_1min; (6h,24h] measurement_5min; >24h measurement_hourly. Returns time, sensor_name, value, min_val, max_val.';

-- ============================================
-- PHASE 5: Statistics Function for Min/Max Panels
-- ============================================

CREATE OR REPLACE FUNCTION get_sensor_stats(
    p_sensor_names TEXT[],
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
)
RETURNS TABLE (
    sensor_name TEXT,
    min_value REAL,
    max_value REAL,
    avg_value REAL
) AS $$
DECLARE
    v_span_s bigint;
BEGIN
    v_span_s := FLOOR(GREATEST(EXTRACT(EPOCH FROM (p_to - p_from)), 0::numeric))::bigint;

    IF v_span_s > 86400 THEN
        RETURN QUERY
        SELECT m.sensor_name, MIN(m.min_value), MAX(m.max_value), AVG(m.avg_value)::REAL
        FROM measurement_hourly_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        GROUP BY m.sensor_name;
    ELSIF v_span_s > 21600 THEN
        RETURN QUERY
        SELECT m.sensor_name, MIN(m.min_value), MAX(m.max_value), AVG(m.avg_value)::REAL
        FROM measurement_5min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        GROUP BY m.sensor_name;
    ELSIF v_span_s > 3600 THEN
        RETURN QUERY
        SELECT m.sensor_name, MIN(m.min_value), MAX(m.max_value), AVG(m.avg_value)::REAL
        FROM measurement_1min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        GROUP BY m.sensor_name;
    ELSE
        RETURN QUERY
        SELECT s.name, MIN(m.value), MAX(m.value), AVG(m.value)::REAL
        FROM measurement m
        JOIN sensor s ON m.sensor_id = s.sensor_id
        WHERE s.name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        GROUP BY s.name;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================
-- VERIFICATION QUERIES (run after backfill)
-- ============================================

-- SELECT view_name FROM timescaledb_information.continuous_aggregates;
-- SELECT * FROM timescaledb_information.jobs WHERE proc_name LIKE '%refresh%';
