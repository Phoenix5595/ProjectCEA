-- Route 1-minute continuous aggregate for Grafana spans up to 6 hours (was 3 hours).
-- Apply on Pi primary (cea_sensors); replica inherits via WAL.
-- Replaces get_sensor_data_optimized + get_sensor_stats only.
--
-- Routing uses FLOOR(EXTRACT(EPOCH FROM (p_to - p_from))) so a "Last 6 hours"
-- range that is a few milliseconds longer than six hours does not fall into the
-- 5-minute tier. After applying, run verify_get_sensor_routing.sql on the DB.

BEGIN;

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
    v_span_s := FLOOR(GREATEST(EXTRACT(EPOCH FROM (p_to - p_from)), 0::numeric))::bigint;

    IF v_span_s > 86400 THEN
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_hourly_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;

    ELSIF v_span_s > 21600 THEN
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_5min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;

    ELSIF v_span_s > 3600 THEN
        RETURN QUERY
        SELECT m.time, m.sensor_name, m.avg_value, m.min_value, m.max_value
        FROM measurement_1min_grafana m
        WHERE m.sensor_name = ANY(p_sensor_names)
          AND m.time >= p_from AND m.time <= p_to
        ORDER BY m.time;

    ELSE
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
'Auto-routes by span (whole seconds): <=1h raw; (1h,6h] measurement_1min; (6h,24h] measurement_5min; >24h measurement_hourly.';

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

COMMIT;
