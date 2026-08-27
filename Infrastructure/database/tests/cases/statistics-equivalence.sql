\set ON_ERROR_STOP on

\ir ../../monitoring_read_models.sql

INSERT INTO measurement (time, sensor_id, value)
VALUES
    ('2026-01-02T01:00:00Z', 1, 30.0),
    ('2026-01-03T01:00:00Z', 1, 32.0);

CALL refresh_continuous_aggregate(
    'monitoring_measurement_1min',
    '2025-12-31T00:00:00Z',
    '2026-01-01T06:01:00Z'
);
CALL refresh_continuous_aggregate(
    'monitoring_measurement_5min',
    '2025-12-31T00:00:00Z',
    '2026-01-07T23:55:00Z'
);

DO $materialized$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM monitoring_measurement_1min)
       OR NOT EXISTS (SELECT 1 FROM monitoring_measurement_5min) THEN
        RAISE EXCEPTION 'measurement CAGGs were not materialized before the statistics test';
    END IF;
END
$materialized$;

-- This row arrives after both CAGGs were materialized. No refresh may follow it.
INSERT INTO measurement (time, sensor_id, value)
VALUES ('2026-01-01T01:00:30Z', 1, 40.0);

CREATE TEMPORARY VIEW statistics_test_ranges AS
SELECT *
FROM (VALUES
    ('5 minutes',  'raw',  NULL::INTERVAL, '2026-01-01T00:00:15Z'::TIMESTAMPTZ, '2026-01-01T00:05:15Z'::TIMESTAMPTZ),
    ('1 hour',     'raw',  NULL::INTERVAL, '2026-01-01T00:00:15Z'::TIMESTAMPTZ, '2026-01-01T01:00:15Z'::TIMESTAMPTZ),
    ('6 hours',    '1min', INTERVAL '1 minute', '2026-01-01T00:00:15Z'::TIMESTAMPTZ, '2026-01-01T06:00:15Z'::TIMESTAMPTZ),
    ('24 hours',   '5min', INTERVAL '5 minutes', '2026-01-01T00:00:15Z'::TIMESTAMPTZ, '2026-01-02T00:00:15Z'::TIMESTAMPTZ),
    ('7 days',     '5min', INTERVAL '5 minutes', '2026-01-02T00:05:15Z'::TIMESTAMPTZ, '2026-01-09T00:05:15Z'::TIMESTAMPTZ)
) AS ranges(range_name, tier, bucket_width, range_start, range_end);

CREATE TEMPORARY VIEW statistics_hybrid_contributions AS
WITH watermark AS (
    SELECT
        cagg_name,
        min(materialization_watermark) AS materialization_watermark
    FROM monitoring_cagg_watermark
    WHERE cagg_name IN ('monitoring_measurement_1min', 'monitoring_measurement_5min')
    GROUP BY cagg_name
),
eligible_1min AS (
    SELECT ranges.range_name, cagg.*
    FROM statistics_test_ranges AS ranges
    JOIN monitoring_measurement_1min AS cagg
      ON ranges.tier = '1min'
     AND cagg.bucket >= ranges.range_start
     AND cagg.bucket + ranges.bucket_width <= ranges.range_end
    JOIN watermark
      ON watermark.cagg_name = 'monitoring_measurement_1min'
     AND cagg.bucket + ranges.bucket_width <= watermark.materialization_watermark
    WHERE NOT EXISTS (
        SELECT 1
        FROM monitoring_cagg_watermark AS pending
        WHERE pending.cagg_name = 'monitoring_measurement_1min'
          AND pending.invalidation_source IS NOT NULL
          AND pending.invalidation_start < cagg.bucket + ranges.bucket_width
          AND pending.invalidation_end >= cagg.bucket
    )
),
eligible_5min AS (
    SELECT ranges.range_name, cagg.*
    FROM statistics_test_ranges AS ranges
    JOIN monitoring_measurement_5min AS cagg
      ON ranges.tier = '5min'
     AND cagg.bucket >= ranges.range_start
     AND cagg.bucket + ranges.bucket_width <= ranges.range_end
    JOIN watermark
      ON watermark.cagg_name = 'monitoring_measurement_5min'
     AND cagg.bucket + ranges.bucket_width <= watermark.materialization_watermark
    WHERE NOT EXISTS (
        SELECT 1
        FROM monitoring_cagg_watermark AS pending
        WHERE pending.cagg_name = 'monitoring_measurement_5min'
          AND pending.invalidation_source IS NOT NULL
          AND pending.invalidation_start < cagg.bucket + ranges.bucket_width
          AND pending.invalidation_end >= cagg.bucket
    )
),
cagg_contributions AS (
    SELECT range_name, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, 'cagg'::TEXT AS source
    FROM eligible_1min
    UNION ALL
    SELECT range_name, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, 'cagg'::TEXT
    FROM eligible_5min
),
raw_contributions AS (
    SELECT
        ranges.range_name,
        measurement.sensor_id,
        1::BIGINT AS sample_count,
        measurement.value::NUMERIC AS value_sum,
        measurement.value::NUMERIC * measurement.value::NUMERIC AS value_sum_squares,
        measurement.value::NUMERIC AS min_value,
        measurement.value::NUMERIC AS max_value,
        'raw'::TEXT AS source
    FROM statistics_test_ranges AS ranges
    JOIN measurement
      ON measurement.time >= ranges.range_start
     AND measurement.time < ranges.range_end
    WHERE ranges.tier = 'raw'
       OR (
            ranges.tier = '1min'
            AND NOT EXISTS (
                SELECT 1
                FROM eligible_1min
                WHERE eligible_1min.range_name = ranges.range_name
                  AND eligible_1min.sensor_id = measurement.sensor_id
                  AND eligible_1min.bucket = time_bucket(ranges.bucket_width, measurement.time)
            )
       )
       OR (
            ranges.tier = '5min'
            AND NOT EXISTS (
                SELECT 1
                FROM eligible_5min
                WHERE eligible_5min.range_name = ranges.range_name
                  AND eligible_5min.sensor_id = measurement.sensor_id
                  AND eligible_5min.bucket = time_bucket(ranges.bucket_width, measurement.time)
            )
       )
)
SELECT * FROM cagg_contributions
UNION ALL
SELECT * FROM raw_contributions;

CREATE TEMPORARY VIEW statistics_hybrid AS
SELECT
    range_name,
    sensor_id,
    sum(sample_count)::BIGINT AS sample_count,
    sum(value_sum) / sum(sample_count) AS average,
    min(min_value) AS minimum,
    max(max_value) AS maximum,
    CASE sum(sample_count)
        WHEN 1 THEN 0::NUMERIC
        ELSE sqrt(greatest(
            0::NUMERIC,
            (
                sum(value_sum_squares)
                - sum(value_sum) * sum(value_sum) / sum(sample_count)
            ) / (sum(sample_count) - 1)
        ))
    END AS stddev_samp,
    count(*) FILTER (WHERE source = 'cagg') AS cagg_contribution_count,
    count(*) FILTER (WHERE source = 'raw') AS raw_contribution_count
FROM statistics_hybrid_contributions
GROUP BY range_name, sensor_id;

CREATE TEMPORARY VIEW statistics_raw_truth AS
SELECT
    ranges.range_name,
    measurement.sensor_id,
    count(*)::BIGINT AS sample_count,
    avg(measurement.value::NUMERIC) AS average,
    min(measurement.value::NUMERIC) AS minimum,
    max(measurement.value::NUMERIC) AS maximum,
    COALESCE(stddev_samp(measurement.value::NUMERIC), 0::NUMERIC) AS stddev_samp
FROM statistics_test_ranges AS ranges
JOIN measurement
  ON measurement.time >= ranges.range_start
 AND measurement.time < ranges.range_end
GROUP BY ranges.range_name, measurement.sensor_id;

BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;

DO $assertions$
DECLARE
    mismatch JSON;
BEGIN
    SELECT json_agg(row_to_json(comparison))
    INTO mismatch
    FROM (
        SELECT
            ranges.range_name,
            sensor.sensor_id,
            truth.sample_count AS raw_count,
            hybrid.sample_count AS hybrid_count,
            abs(hybrid.average - truth.average) AS average_error,
            abs(hybrid.minimum - truth.minimum) AS minimum_error,
            abs(hybrid.maximum - truth.maximum) AS maximum_error,
            abs(hybrid.stddev_samp - truth.stddev_samp) AS stddev_error
        FROM statistics_test_ranges AS ranges
        CROSS JOIN sensor
        LEFT JOIN statistics_raw_truth AS truth
          ON truth.range_name = ranges.range_name
         AND truth.sensor_id = sensor.sensor_id
        LEFT JOIN statistics_hybrid AS hybrid
          ON hybrid.range_name = ranges.range_name
         AND hybrid.sensor_id = sensor.sensor_id
        WHERE sensor.sensor_id BETWEEN 1 AND 6
          AND (
              (truth.sensor_id IS NULL) <> (hybrid.sensor_id IS NULL)
              OR truth.sample_count IS DISTINCT FROM hybrid.sample_count
              OR abs(hybrid.average - truth.average) > 1e-9
              OR abs(hybrid.minimum - truth.minimum) > 1e-9
              OR abs(hybrid.maximum - truth.maximum) > 1e-9
              OR abs(hybrid.stddev_samp - truth.stddev_samp) > 1e-9
          )
    ) AS comparison;

    IF mismatch IS NOT NULL THEN
        RAISE EXCEPTION 'hybrid statistics differ from raw truth: %', mismatch;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM statistics_hybrid
        WHERE sample_count = 1 AND stddev_samp = 0
    ) THEN
        RAISE EXCEPTION 'n=1 did not produce an exact zero sample deviation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM statistics_hybrid
        WHERE sensor_id IN (3, 4, 6)
    ) THEN
        RAISE EXCEPTION 'n=0 sensor unexpectedly produced a statistics row';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM monitoring_cagg_watermark
        WHERE cagg_name IN ('monitoring_measurement_1min', 'monitoring_measurement_5min')
          AND invalidation_source IS NOT NULL
          AND invalidation_start <= '2026-01-01T01:00:30Z'
          AND invalidation_end >= '2026-01-01T01:00:30Z'
    ) THEN
        RAISE EXCEPTION 'late arrival did not remain pending in the watermark adapter';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM statistics_hybrid_contributions
        WHERE range_name = '7 days' AND sensor_id = 1 AND source = 'raw'
    ) OR NOT EXISTS (
        SELECT 1
        FROM statistics_hybrid_contributions
        WHERE range_name = '7 days' AND sensor_id = 1 AND source = 'cagg'
    ) THEN
        RAISE EXCEPTION 'seven-day statistics did not combine CAGG and raw replacement rows';
    END IF;

    IF (
        SELECT raw_contribution_count
        FROM statistics_hybrid
        WHERE range_name = '7 days' AND sensor_id = 1
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM monitoring_cagg_watermark
        WHERE cagg_name = 'monitoring_measurement_5min'
          AND materialization_watermark <= '2026-01-07T23:55:00Z'
    ) THEN
        RAISE EXCEPTION 'seven-day stale-watermark tail was not replaced exactly once';
    END IF;
END
$assertions$;

SELECT json_build_object(
    'case', 'statistics-equivalence',
    'tolerance', 1e-9,
    'late_arrival', '2026-01-01T01:00:30Z',
    'comparisons', json_agg(json_build_object(
        'range', ranges.range_name,
        'tier', ranges.tier,
        'sensor_id', sensor.sensor_id,
        'sample_count', truth.sample_count,
        'no_row', truth.sensor_id IS NULL,
        'average_error', COALESCE(abs(hybrid.average - truth.average), 0),
        'minimum_error', COALESCE(abs(hybrid.minimum - truth.minimum), 0),
        'maximum_error', COALESCE(abs(hybrid.maximum - truth.maximum), 0),
        'stddev_error', COALESCE(abs(hybrid.stddev_samp - truth.stddev_samp), 0),
        'cagg_contributions', COALESCE(hybrid.cagg_contribution_count, 0),
        'raw_contributions', COALESCE(hybrid.raw_contribution_count, 0)
    ) ORDER BY ranges.range_end, sensor.sensor_id)
) AS result
FROM statistics_test_ranges AS ranges
CROSS JOIN sensor
LEFT JOIN statistics_raw_truth AS truth
  ON truth.range_name = ranges.range_name
 AND truth.sensor_id = sensor.sensor_id
LEFT JOIN statistics_hybrid AS hybrid
  ON hybrid.range_name = ranges.range_name
 AND hybrid.sensor_id = sensor.sensor_id
WHERE sensor.sensor_id BETWEEN 1 AND 6;

COMMIT;
