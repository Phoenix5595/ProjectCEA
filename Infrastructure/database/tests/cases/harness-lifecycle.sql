\set ON_ERROR_STOP on

DO $case$
DECLARE
    extension_version TEXT;
BEGIN
    IF current_database() IN ('cea_sensors', 'cea_sensors_test')
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'unsafe database identity: %', current_database();
    END IF;

    SELECT extversion
    INTO extension_version
    FROM pg_extension
    WHERE extname = 'timescaledb';

    IF split_part(extension_version, '.', 1)::INTEGER <> 2
       OR split_part(extension_version, '.', 2)::INTEGER < 23 THEN
        RAISE EXCEPTION 'expected a TimescaleDB 2.23+ compatible release, got %', extension_version;
    END IF;

    IF (SELECT count(*) FROM measurement) <> 13 THEN
        RAISE EXCEPTION 'unexpected measurement fixture count';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM measurement AS measurement_row
        JOIN sensor USING (sensor_id)
        WHERE sensor.name = 'dry_bulb_f'
    ) THEN
        RAISE EXCEPTION 'Flower Front must remain an empty fixture node';
    END IF;
END
$case$;

SELECT json_build_object(
    'case', 'harness-lifecycle',
    'database', current_database(),
    'timescaledb', (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'),
    'flower_back_samples', (
        SELECT count(*)
        FROM measurement
        JOIN sensor USING (sensor_id)
        WHERE sensor.name LIKE '%_b'
    ),
    'flower_front_samples', (
        SELECT count(*)
        FROM measurement
        JOIN sensor USING (sensor_id)
        WHERE sensor.name LIKE '%_f'
    ),
    'veg_samples', (
        SELECT count(*)
        FROM measurement
        JOIN sensor USING (sensor_id)
        WHERE sensor.name LIKE '%_v'
    )
) AS result;
