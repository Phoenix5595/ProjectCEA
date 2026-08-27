\set ON_ERROR_STOP on

\ir ../../monitoring_read_models.sql

CALL refresh_continuous_aggregate(
    'monitoring_measurement_1min',
    '2026-01-01T00:00:00Z',
    '2026-01-01T00:02:00Z'
);

DO $case$
DECLARE
    raw_average NUMERIC;
    wrong_average NUMERIC;
    difference NUMERIC;
BEGIN
    SELECT avg(value::NUMERIC)
    INTO raw_average
    FROM measurement
    WHERE sensor_id = 1
      AND time >= '2026-01-01T00:00:00Z'
      AND time < '2026-01-01T00:02:00Z';

    SELECT avg(value_sum / sample_count)
    INTO wrong_average
    FROM monitoring_measurement_1min
    WHERE sensor_id = 1
      AND bucket >= '2026-01-01T00:00:00Z'
      AND bucket < '2026-01-01T00:02:00Z';

    difference := abs(wrong_average - raw_average);
    IF difference <= 1e-9 THEN
        RAISE EXCEPTION 'average-of-averages unexpectedly matched raw truth: error %', difference;
    END IF;
END
$case$;

SELECT json_build_object(
    'case', 'reject-average-of-averages',
    'raw_average', (
        SELECT avg(value::NUMERIC)
        FROM measurement
        WHERE sensor_id = 1
          AND time >= '2026-01-01T00:00:00Z'
          AND time < '2026-01-01T00:02:00Z'
    ),
    'wrong_average_of_averages', (
        SELECT avg(value_sum / sample_count)
        FROM monitoring_measurement_1min
        WHERE sensor_id = 1
          AND bucket >= '2026-01-01T00:00:00Z'
          AND bucket < '2026-01-01T00:02:00Z'
    ),
    'error_exceeds_tolerance', TRUE,
    'tolerance', 1e-9
) AS result;
