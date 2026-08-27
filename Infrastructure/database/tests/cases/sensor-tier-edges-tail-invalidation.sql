\set ON_ERROR_STOP on

DO $case$
BEGIN
    IF (SELECT count(*) FROM measurement WHERE sensor_id = 1 AND time < '2026-01-01T00:01:00Z') <> 3 THEN
        RAISE EXCEPTION 'partial one-minute boundary fixture is incomplete';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM measurement
        WHERE sensor_id = 1
          AND time = '2026-01-01T01:00:00Z'
    ) OR NOT EXISTS (
        SELECT 1
        FROM measurement
        WHERE sensor_id = 1
          AND time = '2026-01-01T06:00:00Z'
    ) OR NOT EXISTS (
        SELECT 1
        FROM measurement
        WHERE sensor_id = 1
          AND time = '2026-01-07T23:59:59Z'
    ) THEN
        RAISE EXCEPTION 'tier edge or seven-day tail fixture is incomplete';
    END IF;
END
$case$;

SELECT json_build_object(
    'case', 'sensor-tier-edges-tail-invalidation',
    'partial_bucket_rows', (
        SELECT count(*)
        FROM measurement
        WHERE sensor_id = 1
          AND time >= '2026-01-01T00:00:00Z'
          AND time < '2026-01-01T00:01:00Z'
    ),
    'one_hour_edge', TRUE,
    'six_hour_edge', TRUE,
    'seven_day_tail', TRUE
) AS result;
