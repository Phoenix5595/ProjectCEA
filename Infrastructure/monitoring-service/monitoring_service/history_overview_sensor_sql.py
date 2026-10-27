"""Sensor-family history overview statement (history_overview_sql documents the shared coverage-guard contract)."""

from __future__ import annotations

from typing import Final

SENSOR_COLUMNS: Final[str] = (
    "kind, guard_ok, idx, b0, b1, partial, node, sensor, unit, data_type,"
    " sample_count, value_sum, value_sum_squares, min_value, max_value"
)

_SENSOR_ANGLES: Final[str] = """
node_patterns(node, pattern) AS (
    VALUES ($2::text, $3::text), ($4::text, $5::text)
),
request AS (
    SELECT $6::timestamptz AS range_start, $7::timestamptz AS range_end
),
buckets AS (
    SELECT
        (entry ->> 'x')::INTEGER AS idx,
        (entry ->> 'b')::TIMESTAMPTZ AS b0,
        (entry ->> 'e')::TIMESTAMPTZ AS b1,
        COALESCE(entry ->> 'p', 'f')::BOOLEAN AS partial
    FROM jsonb_array_elements($8::jsonb) AS entry
)
"""

_MARKS: Final[str] = """            (GREATEST(b.b0, LEAST(b.b1, {b}.h_from))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.h_through))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.d_from))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.d_through))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.cov_from_min))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.tail_start))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.frag_end))),
            (GREATEST(b.b0, LEAST(b.b1, {b}.frag_start))),
            (b.b0),
            (b.b1)"""

_SENSOR_PIECES: Final[str] = (
    """
bucket_marks AS (
    SELECT DISTINCT b.b0, b.b1, b.partial, b.idx, marks.ts
    FROM buckets AS b
    CROSS JOIN bounds
    CROSS JOIN LATERAL (
        VALUES
"""
    + _MARKS.format(b="bounds")
    + """
    ) AS marks(ts)
),
mark_line AS (
    SELECT idx, b0, b1, partial, ts,
           lag(ts) OVER (PARTITION BY idx ORDER BY ts) AS lo
    FROM bucket_marks
),
pieces AS (
    SELECT idx, b0, b1, partial, ts AS hi, lo
    FROM mark_line
    WHERE lo IS NOT NULL AND ts > lo
),
piece_labeled AS (
    SELECT pieces.idx, pieces.b0, pieces.b1, pieces.partial,
           pieces.lo, pieces.hi,
           CASE
               WHEN pieces.hi <= bounds.frag_end
                    OR pieces.lo >= bounds.frag_start THEN 'raw'
               WHEN bounds.h_from IS NOT NULL
                    AND pieces.lo >= bounds.h_from
                    AND pieces.hi <= bounds.h_through THEN 'hour'
               WHEN bounds.d_from IS NOT NULL
                    AND pieces.lo >= bounds.d_from
                    AND pieces.hi <= bounds.d_through THEN 'day'
               WHEN bounds.cov_from_min IS NOT NULL
                    AND pieces.hi <= bounds.cov_from_min THEN 'empty'
               WHEN above_empty.empty
                    AND pieces.lo >= COALESCE(bounds.cov_through_max,
                                               bounds.range_start) THEN 'empty'
               WHEN pieces.lo >= bounds.tail_start THEN 'raw'
           END AS reason
    FROM pieces
    CROSS JOIN bounds
    CROSS JOIN above_empty
),
raw_pieces AS (
    SELECT idx, lo, hi
    FROM piece_labeled
    WHERE reason = 'raw'
),
piece_guard AS (
    SELECT EXISTS (
        SELECT 1 FROM piece_labeled WHERE reason IS NULL
    ) OR FALSE AS invalid
    FROM bounds
)
"""
)

# Daily rows serve a display bucket only when a whole 24-hour day fits inside
# it, so the daily pending-invalidation gate activates only when some
# requested bucket could actually select daily material (``uses_daily``).
# Measurement hour/day watermarks and invalidations include the 1min ancestor.
SENSOR_OVERVIEW_STATEMENT: Final[str] = (
    "WITH "
    + _SENSOR_ANGLES
    + """
,
cov AS (
    SELECT
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1hour') AS h_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1hour') AS h_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name IN ('monitoring_measurement_1hour',
                              'monitoring_measurement_1min')) AS h_wm,
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1day') AS d_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1day') AS d_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name IN ('monitoring_measurement_1day',
                              'monitoring_measurement_1hour',
                              'monitoring_measurement_1min')) AS d_wm
),
bounds AS (
    SELECT request.range_start, request.range_end,
           cov.h_from, cov.h_through, cov.h_wm,
           cov.d_from, cov.d_through, cov.d_wm,
           LEAST(cov.h_from, cov.d_from) AS cov_from_min,
           GREATEST(cov.h_through, cov.d_through) AS cov_through_max,
           GREATEST(request.range_end - INTERVAL '24 hours',
                    CURRENT_TIMESTAMP - INTERVAL '24 hours') AS tail_start,
           CASE WHEN request.range_start = date_trunc('hour', request.range_start)
                THEN request.range_start
                ELSE date_trunc('hour', request.range_start) + INTERVAL '1 hour'
           END AS frag_end,
           date_trunc('hour', request.range_end) AS frag_start
    FROM request
    CROSS JOIN (
        SELECT h_from, h_wm, d_from, d_wm,
               CASE WHEN h_wm IS NOT NULL THEN LEAST(h_through, h_wm) END AS h_through,
               CASE WHEN d_wm IS NOT NULL THEN LEAST(d_through, d_wm) END AS d_through
          FROM cov
    ) AS cov
),
pending AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM monitoring_history_cagg_watermark
            WHERE cagg_name IN ('monitoring_measurement_1hour',
                                'monitoring_measurement_1min')
              AND invalidation_source IS NOT NULL
              AND invalidation_start < LEAST(bounds.h_through, bounds.range_end)
              AND invalidation_end >= GREATEST(bounds.h_from, bounds.range_start)
        ) AS h_pending,
        EXISTS (
            SELECT 1
            FROM monitoring_history_cagg_watermark
            WHERE cagg_name IN ('monitoring_measurement_1day',
                                'monitoring_measurement_1hour',
                                'monitoring_measurement_1min')
              AND invalidation_source IS NOT NULL
              AND invalidation_start < LEAST(bounds.d_through, bounds.range_end)
              AND invalidation_end >= GREATEST(bounds.d_from, bounds.range_start)
        ) AS d_pending,
        EXISTS (
            SELECT 1
            FROM buckets
            WHERE b1 - b0 >= INTERVAL '24 hours'
        ) AS uses_daily
    FROM bounds
),
below_empty AS (
    SELECT CASE
        WHEN LEAST(bounds.range_end, bounds.cov_from_min) <= bounds.range_start
        THEN TRUE
        ELSE NOT EXISTS (
        SELECT 1
        FROM measurement
        JOIN sensor ON sensor.sensor_id = measurement.sensor_id
        JOIN device ON device.device_id = sensor.device_id
        LEFT JOIN rack ON rack.rack_id = device.rack_id
        JOIN room ON room.room_id = rack.room_id
        JOIN node_patterns
          ON node_patterns.pattern <> ''
         AND sensor.name LIKE node_patterns.pattern
        CROSS JOIN bounds
        WHERE room.name = $1
          AND bounds.cov_from_min IS NOT NULL
          AND measurement.time >= bounds.range_start
          AND measurement.time
              < LEAST(bounds.range_end, bounds.cov_from_min)
    ) END AS empty
    FROM bounds
),
room_sensors AS (
    SELECT DISTINCT
        sensor.sensor_id,
        sensor.name AS sensor,
        sensor.unit,
        sensor.data_type,
        node_patterns.node
    FROM sensor
    JOIN device ON device.device_id = sensor.device_id
    LEFT JOIN rack ON rack.rack_id = device.rack_id
    JOIN room ON room.room_id = rack.room_id
    JOIN node_patterns
      ON node_patterns.pattern <> ''
     AND sensor.name LIKE node_patterns.pattern
    WHERE room.name = $1
),
above_empty AS (
    SELECT CASE
        WHEN COALESCE(bounds.cov_through_max, bounds.range_start) >= bounds.range_end
        THEN TRUE
        ELSE NOT EXISTS (
        SELECT 1 FROM (
            SELECT time AS observed_at, sensor_id FROM measurement
            UNION ALL
            SELECT bucket, sensor_id FROM monitoring_measurement_1hour
            UNION ALL
            SELECT bucket, sensor_id FROM monitoring_measurement_1day
        ) AS facts JOIN room_sensors USING (sensor_id)
        WHERE facts.observed_at >= COALESCE(bounds.cov_through_max, bounds.range_start)
          AND facts.observed_at < bounds.range_end
    ) END AS empty FROM bounds
),
"""
    + _SENSOR_PIECES
    + """
,
day_rows AS (
    SELECT
        b.idx,
        c.bucket,
        c.sensor_id,
        c.sample_count,
        c.value_sum,
        c.value_sum_squares,
        c.min_value,
        c.max_value,
        TRUE AS present
    FROM monitoring_measurement_1day AS c
    JOIN buckets AS b
      ON c.bucket >= b.b0
     AND c.bucket + INTERVAL '24 hours' <= b.b1
    CROSS JOIN bounds
    WHERE c.bucket >= bounds.d_from
      AND c.bucket + INTERVAL '24 hours' <= bounds.d_through
      AND c.bucket + INTERVAL '24 hours' <= bounds.d_wm
),
hour_rows AS (
    SELECT
        b.idx,
        c.sensor_id,
        c.sample_count,
        c.value_sum,
        c.value_sum_squares,
        c.min_value,
        c.max_value,
        TRUE AS present
    FROM monitoring_measurement_1hour AS c
    JOIN buckets AS b
      ON time_bucket($9::bigint * INTERVAL '1 second', c.bucket,
                     '1970-01-01T00:00:00Z'::timestamptz) =
         time_bucket($9::bigint * INTERVAL '1 second', b.b0,
                     '1970-01-01T00:00:00Z'::timestamptz)
     AND c.bucket >= b.b0
     AND c.bucket + INTERVAL '1 hour' <= b.b1
    CROSS JOIN bounds
    WHERE c.bucket >= bounds.h_from
      AND c.bucket >= bounds.range_start
      AND c.bucket < bounds.range_end
      AND c.sensor_id IN (SELECT sensor_id FROM room_sensors)
      AND c.bucket + INTERVAL '1 hour' <= bounds.h_through
      AND c.bucket + INTERVAL '1 hour' <= bounds.h_wm
      AND NOT EXISTS (
          SELECT 1
          FROM day_rows AS dr
          WHERE dr.idx = b.idx
            AND dr.sensor_id = c.sensor_id
            AND dr.bucket <= c.bucket
            AND dr.bucket + INTERVAL '24 hours' > c.bucket
      )
),
raw_rows AS (
    SELECT
        rp.idx,
        measurement.sensor_id,
        1::BIGINT AS sample_count,
        measurement.value::NUMERIC AS value_sum,
        measurement.value::NUMERIC * measurement.value::NUMERIC AS value_sum_squares,
        measurement.value::NUMERIC AS min_value,
        measurement.value::NUMERIC AS max_value,
        TRUE AS present
    FROM raw_pieces AS rp
    CROSS JOIN room_sensors AS requested
    CROSS JOIN LATERAL (
        SELECT raw.sensor_id, raw.value
        FROM measurement AS raw
        WHERE raw.sensor_id = requested.sensor_id
          AND raw.time >= rp.lo
          AND raw.time < rp.hi
        -- Keep the piece bounds parameterized; flattening this lookup can
        -- scan retained history before applying the dynamic time window.
        OFFSET 0
    ) AS measurement
),
merged AS (
    SELECT
        n.idx,
        n.b0,
        n.b1,
        n.partial,
        room_sensors.node,
        room_sensors.sensor,
        room_sensors.unit,
        room_sensors.data_type,
        SUM(u.sample_count)::BIGINT AS sample_count,
        SUM(u.value_sum) AS value_sum,
        SUM(u.value_sum_squares) AS value_sum_squares,
        MIN(u.min_value) AS min_value,
        MAX(u.max_value) AS max_value,
        bool_or(u.present) AS present
    FROM (
        SELECT idx, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, present
        FROM day_rows
        UNION ALL
        SELECT idx, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, present
        FROM hour_rows
        UNION ALL
        SELECT idx, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, present
        FROM raw_rows
    ) AS u
    JOIN buckets AS n
      ON n.idx = u.idx
    JOIN room_sensors
      ON room_sensors.sensor_id = u.sensor_id
    GROUP BY n.idx, n.b0, n.b1, n.partial, room_sensors.node,
             room_sensors.sensor, room_sensors.unit, room_sensors.data_type
),
guard AS (
    SELECT NOT (
        pending.h_pending
        OR (pending.d_pending AND pending.uses_daily)
        OR piece_guard.invalid
        OR NOT below_empty.empty
    ) AS guard_ok
    FROM pending
    CROSS JOIN piece_guard
    CROSS JOIN below_empty
),
final AS (
    SELECT
        'guard'::TEXT AS kind,
        guard.guard_ok AS guard_ok,
        NULL::INTEGER AS idx,
        NULL::TIMESTAMPTZ AS b0,
        NULL::TIMESTAMPTZ AS b1,
        NULL::BOOLEAN AS partial,
        NULL::TEXT AS node,
        NULL::TEXT AS sensor,
        NULL::TEXT AS unit,
        NULL::TEXT AS data_type,
        NULL::BIGINT AS sample_count,
        NULL::NUMERIC AS value_sum,
        NULL::NUMERIC AS value_sum_squares,
        NULL::NUMERIC AS min_value,
        NULL::NUMERIC AS max_value
    FROM guard
    UNION ALL
    SELECT
        'data'::TEXT AS kind,
        TRUE AS guard_ok,
        b.idx,
        b.b0,
        b.b1,
        b.partial,
        s.node,
        s.sensor,
        s.unit,
        s.data_type,
        COALESCE(m.sample_count, 0)::BIGINT,
        m.value_sum,
        m.value_sum_squares,
        m.min_value,
        m.max_value
    FROM room_sensors AS s CROSS JOIN buckets AS b
    LEFT JOIN merged AS m ON m.idx = b.idx
        AND m.node = s.node AND m.sensor = s.sensor
)
SELECT
"""
    + SENSOR_COLUMNS
    + """
FROM final
ORDER BY kind DESC
"""
)
