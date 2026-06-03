-- Run against cea_sensors (primary or replica) after applying
-- migrate_get_sensor_routing_1min_through_6h.sql (or grafana_performance_migration.sql).
--
-- 1) Function body must use whole-second span (21600 = 6h) and measurement_1min_grafana for mid tier.
SELECT pg_get_functiondef('get_sensor_data_optimized(text[],timestamptz,timestamptz)'::regprocedure)
    LIKE '%v_span_s%'
    AND pg_get_functiondef('get_sensor_data_optimized(text[],timestamptz,timestamptz)'::regprocedure)
    LIKE '%measurement_1min_grafana%'
    AS routing_fn_looks_current;

-- 2) 3 h window: median gap between consecutive buckets should be ~60 s (not ~300 s).
WITH rows AS (
    SELECT time,
           time - LAG(time) OVER (PARTITION BY sensor_name ORDER BY time) AS step
    FROM get_sensor_data_optimized(
        ARRAY['dry_bulb_b']::text[],
        NOW() - INTERVAL '3 hours',
        NOW()
    )
),
gaps AS (
    SELECT EXTRACT(EPOCH FROM step) AS gap_s
    FROM rows
    WHERE step IS NOT NULL
)
SELECT
    COUNT(*) AS n_gaps,
    MIN(gap_s) AS min_gap_s,
    MAX(gap_s) AS max_gap_s,
    ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 2) AS median_gap_s
FROM gaps;

-- 3) CAGG row sanity: expect non-zero recent rows for 1min CAGG.
SELECT COUNT(*) AS rows_1min_last_3h
FROM measurement_1min_grafana
WHERE sensor_name = 'dry_bulb_b'
  AND time >= NOW() - INTERVAL '3 hours';
