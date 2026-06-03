-- Performance: replace DISTINCT ON + full hypertable scan with per-sensor
-- index-backed "last row" (ORDER BY time DESC LIMIT 1) via LATERAL.
-- Without this, max(time) / DISTINCT ON paths read millions of rows per sensor
-- (see EXPLAIN ANALYZE on: SELECT max(m.time) FROM measurement m JOIN sensor s ...).
-- Apply on PRIMARY only; physical replica applies catalog + data via WAL.
-- Date: 2026-05-09 (Iskra Grafana / redis_sync slowness investigation)

BEGIN;

CREATE OR REPLACE VIEW latest_sensor_values AS
SELECT
    s.sensor_id,
    m.time,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.name AS device_name,
    r.name AS room_name
FROM sensor s
JOIN LATERAL (
    SELECT time, value, status
    FROM measurement mm
    WHERE mm.sensor_id = s.sensor_id
    ORDER BY time DESC
    LIMIT 1
) m ON TRUE
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id;

COMMENT ON VIEW latest_sensor_values IS
    'Latest row per sensor via LATERAL + index (sensor_id, time DESC); avoids full-chunk scans.';

COMMIT;
