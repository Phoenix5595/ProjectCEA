"""Bounded all-history overview SQL statements with in-statement coverage guards.

Every statement below is one read-only SELECT whose own CTEs verify the
safe-coverage state of the exact material that serves the request, so a
source and its guards always share one snapshot:

* ``monitoring_overview_coverage`` verified-contiguous bounds per tier,
* native ``monitoring_history_cagg_watermark`` materialization watermark as an upper
  materialization bound plus pending invalidation overlap per tier,
* below-coverage regions proven raw-empty inside the same statement,
* relay support state through ``monitoring_relay_overview_hourly_ready`` and
  one request observation watermark.

Rows are emitted in two kinds: each statement returns one leading ``guard``
row (checked first by the repository) and then ``data`` rows. A ``false``
guard value means the request would need stale or unverified material and
reports 503 with ``GUARD_UNAVAILABLE_MESSAGE``; nothing is then modeled.

Per output bucket the served contributions merge (never prorated, never
double counted):

1. daily CAGG rows whose whole UTC day lies inside the bucket and inside the
   verified daily coverage window including the native watermark;
2. hourly CAGG rows whose whole UTC hour lies inside the bucket and inside
   the verified hourly coverage window, excluding hours already served by a
   used daily row;
3. raw rows only inside the outer partial-hour fragment at the requested
   start and a recent tail bounded to the last 24 hours of the request.

Every remaining span must be covered by a verified window or lie in the
below-coverage region proven raw-empty in the same statement; anything else
fails the guard. Relay durations merge ``monitoring_relay_duration_1hour``
and ``monitoring_relay_duration_1day`` rows plus exact
``monitoring_relay_duration_slices`` intervals for the uncovered recent
piece span under one request watermark; buckets without recorded owner time
report unknown elapsed microseconds, never fabricated state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Final

GUARD_UNAVAILABLE_MESSAGE: Final[str] = "history overview aggregate coverage is unavailable"

GUARD_ROW_KIND: Final[str] = "guard"
DATA_ROW_KIND: Final[str] = "data"


def bucket_points_json(bounds: Sequence[tuple[datetime, datetime, bool]]) -> str:
    """Serialize ``(clipped_start, clipped_end, partial)`` bucket bounds."""
    items: list[dict[str, object]] = []
    for index, (start, end, partial) in enumerate(bounds):
        items.append(
            {
                "x": index,
                "b": start.isoformat(),
                "e": end.isoformat(),
                "p": "t" if partial else "f",
            }
        )
    return json.dumps(items, separators=(",", ":"))


SENSOR_COLUMNS: Final[str] = (
    "kind, guard_ok, idx, b0, b1, partial, node, sensor, unit, data_type,"
    " sample_count, value_sum, value_sum_squares, min_value, max_value"
)

CONTROL_COLUMNS: Final[str] = (
    "kind, guard_ok, family, idx, b0, b1, partial, location, cluster,"
    " device_name, sample_count, value_sum, value_sum_squares, min_value,"
    " max_value, present"
)

RELAY_COLUMNS: Final[str] = (
    "kind, guard_ok, idx, b0, b1, partial, location, cluster, channel,"
    " device_id, device_name, device_type, on_microseconds, off_microseconds"
)

# ---------------------------------------------------------------- shared SQL
# Piece engine for the aggregate statements: marks are the clipped window
# edges (NULL windows degrade harmlessly to bucket edges) and consecutive
# distinct marks partition every output bucket into disjoint sub-spans whose
# union is exactly the bucket. A sub-span is valid only when it fits one of
# the allowed windows: verified hourly/daily coverage, the probe-verified
# empty below-coverage region, or an allowed raw span (start partial-hour
# fragment or the 24 hour tail).
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

_ANGLES: Final[str] = """
request AS (
    SELECT $2::timestamptz AS range_start, $3::timestamptz AS range_end
),
buckets AS (
    SELECT
        (entry ->> 'x')::INTEGER AS idx,
        (entry ->> 'b')::TIMESTAMPTZ AS b0,
        (entry ->> 'e')::TIMESTAMPTZ AS b1,
        COALESCE(entry ->> 'p', 'f')::BOOLEAN AS partial
    FROM jsonb_array_elements($4::jsonb) AS entry
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
# requested bucket could actually select daily material (``uses_daily``);
# hourly invalidations stay fully gated for every request.
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
          WHERE cagg_name = 'monitoring_measurement_1hour') AS h_wm,
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1day') AS d_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_measurement_1day') AS d_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name = 'monitoring_measurement_1day') AS d_wm
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
            WHERE cagg_name = 'monitoring_measurement_1hour'
              AND invalidation_source IS NOT NULL
              AND invalidation_start < LEAST(bounds.h_through, bounds.range_end)
              AND invalidation_end >= GREATEST(bounds.h_from, bounds.range_start)
        ) AS h_pending,
        EXISTS (
            SELECT 1
            FROM monitoring_history_cagg_watermark
            WHERE cagg_name = 'monitoring_measurement_1day'
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

# -- control statement ------------------------------------------------------
# Both control summaries (recorded light intensity plus requested PID output)
# share one statement: identical bucket arguments, one guard row, and one
# snapshot. $1 location, $2 start, $3 end, $4 bucket-bounds JSON.
# Like the sensor statement, each family gates its daily pending invalidation
# on ``uses_daily`` (a requested bucket wide enough to hold a whole day) and
# keeps its hourly pending invalidation fully gated.
_CONTROL_FAMILY_TEMPLATE: Final[str] = """
{p}_cov AS (
    SELECT
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = '{hour_model}') AS h_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = '{hour_model}') AS h_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name = '{hour_model}') AS h_wm,
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = '{day_model}') AS d_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = '{day_model}') AS d_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name = '{day_model}') AS d_wm
),
{p}_bounds AS (
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
          FROM {p}_cov
    ) AS cov
),
{p}_pending AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM monitoring_history_cagg_watermark
            WHERE cagg_name = '{hour_model}'
              AND invalidation_source IS NOT NULL
              AND invalidation_start < LEAST({p}_bounds.h_through,
                                            {p}_bounds.range_end)
              AND invalidation_end >= GREATEST({p}_bounds.h_from,
                                            {p}_bounds.range_start)
        ) AS h_pending,
        EXISTS (
            SELECT 1
            FROM monitoring_history_cagg_watermark
            WHERE cagg_name = '{day_model}'
              AND invalidation_source IS NOT NULL
              AND invalidation_start < LEAST({p}_bounds.d_through,
                                            {p}_bounds.range_end)
              AND invalidation_end >= GREATEST({p}_bounds.d_from,
                                            {p}_bounds.range_start)
        ) AS d_pending,
        EXISTS (
            SELECT 1
            FROM buckets
            WHERE b1 - b0 >= INTERVAL '24 hours'
        ) AS uses_daily
    FROM {p}_bounds
),
{p}_below_empty AS (
    SELECT CASE
        WHEN LEAST({p}_bounds.range_end, {p}_bounds.cov_from_min)
             <= {p}_bounds.range_start THEN TRUE
        ELSE NOT EXISTS (
        SELECT 1
        FROM {raw_table} AS facts
        CROSS JOIN {p}_bounds
        WHERE facts.location = $1
          AND {p}_bounds.cov_from_min IS NOT NULL
          AND facts."time" >= {p}_bounds.range_start
          AND facts."time"
              < LEAST({p}_bounds.range_end, {p}_bounds.cov_from_min)
          AND {beyond_probe}
    ) END AS empty
    FROM {p}_bounds
),
{p}_above_empty AS (
    SELECT CASE
        WHEN COALESCE({p}_bounds.cov_through_max, {p}_bounds.range_start)
             >= {p}_bounds.range_end THEN TRUE
        ELSE NOT EXISTS (
        SELECT 1 FROM (
            SELECT "time" AS observed_at FROM {raw_table} AS facts
             WHERE facts.location = $1 AND {beyond_probe}
            UNION ALL
            SELECT bucket FROM {hour_table} WHERE location = $1
            UNION ALL
            SELECT bucket FROM {hour_day_table} WHERE location = $1
        ) AS facts
        WHERE facts.observed_at >= COALESCE({p}_bounds.cov_through_max,
                                            {p}_bounds.range_start)
          AND facts.observed_at < {p}_bounds.range_end
    ) END AS empty FROM {p}_bounds
),
{p}_bucket_marks AS (
    SELECT DISTINCT b.b0, b.b1, b.partial, b.idx, marks.ts
    FROM buckets AS b
    CROSS JOIN {p}_bounds
    CROSS JOIN LATERAL (
        VALUES
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.h_from))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.h_through))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.d_from))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.d_through))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.cov_from_min))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.tail_start))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.frag_end))),
            (GREATEST(b.b0, LEAST(b.b1, {p}_bounds.frag_start))),
            (b.b0),
            (b.b1)
    ) AS marks(ts)
),
{p}_mark_line AS (
    SELECT idx, b0, b1, partial, ts,
           lag(ts) OVER (PARTITION BY idx ORDER BY ts) AS lo
    FROM {p}_bucket_marks
),
{p}_pieces AS (
    SELECT idx, b0, b1, partial, ts AS hi, lo
    FROM {p}_mark_line
    WHERE lo IS NOT NULL AND ts > lo
),
{p}_piece_labeled AS (
    SELECT pieces.idx, pieces.b0, pieces.b1, pieces.partial,
           pieces.lo, pieces.hi,
           CASE
               WHEN pieces.hi <= {p}_bounds.frag_end
                    OR pieces.lo >= {p}_bounds.frag_start THEN 'raw'
               WHEN {p}_bounds.h_from IS NOT NULL
                    AND pieces.lo >= {p}_bounds.h_from
                    AND pieces.hi <= {p}_bounds.h_through THEN 'hour'
               WHEN {p}_bounds.d_from IS NOT NULL
                    AND pieces.lo >= {p}_bounds.d_from
                    AND pieces.hi <= {p}_bounds.d_through THEN 'day'
               WHEN {p}_bounds.cov_from_min IS NOT NULL
                    AND pieces.hi <= {p}_bounds.cov_from_min THEN 'empty'
               WHEN {p}_above_empty.empty
                    AND pieces.lo >= COALESCE({p}_bounds.cov_through_max,
                                               {p}_bounds.range_start) THEN 'empty'
               WHEN pieces.lo >= {p}_bounds.tail_start THEN 'raw'
           END AS reason
    FROM {p}_pieces AS pieces
    CROSS JOIN {p}_bounds
    CROSS JOIN {p}_above_empty
),
{p}_raw_pieces AS (
    SELECT idx, lo, hi
    FROM {p}_piece_labeled
    WHERE reason = 'raw'
),
{p}_piece_guard AS (
    SELECT EXISTS (
        SELECT 1 FROM {p}_piece_labeled WHERE reason IS NULL
    ) OR FALSE AS invalid
    FROM {p}_bounds
),
{p}_day_rows AS (
    SELECT
        b.idx,
        c.bucket,
        c.location,
        c.cluster,
        c.device_name,
        {day_value_select},
        TRUE AS present
    FROM {hour_day_table} AS c
    JOIN buckets AS b
      ON c.bucket >= b.b0
     AND c.bucket + INTERVAL '24 hours' <= b.b1
    CROSS JOIN {p}_bounds
    WHERE c.location = $1
      AND c.bucket >= {p}_bounds.d_from
      AND c.bucket + INTERVAL '24 hours' <= {p}_bounds.d_through
      AND c.bucket + INTERVAL '24 hours' <= {p}_bounds.d_wm
),
{p}_hour_rows AS (
    SELECT
        b.idx,
        c.bucket,
        c.location,
        c.cluster,
        c.device_name,
        {hour_value_select},
        TRUE AS present
    FROM {hour_table} AS c
    JOIN buckets AS b
      ON time_bucket($5::bigint * INTERVAL '1 second', c.bucket,
                     '1970-01-01T00:00:00Z'::timestamptz) =
         time_bucket($5::bigint * INTERVAL '1 second', b.b0,
                     '1970-01-01T00:00:00Z'::timestamptz)
     AND c.bucket >= b.b0
     AND c.bucket + INTERVAL '1 hour' <= b.b1
    CROSS JOIN {p}_bounds
    WHERE c.location = $1
      AND c.bucket >= {p}_bounds.h_from
      AND c.bucket >= {p}_bounds.range_start
      AND c.bucket < {p}_bounds.range_end
      AND c.bucket + INTERVAL '1 hour' <= {p}_bounds.h_through
      AND c.bucket + INTERVAL '1 hour' <= {p}_bounds.h_wm
      AND NOT EXISTS (
          SELECT 1
          FROM {p}_day_rows AS dr
          WHERE dr.idx = b.idx
            AND dr.location = c.location
            AND dr.cluster = c.cluster
            AND dr.device_name = c.device_name
            AND dr.bucket <= c.bucket
            AND dr.bucket + INTERVAL '24 hours' > c.bucket
      )
),
{p}_raw_rows AS (
    SELECT
        rp.idx,
        facts.location,
        facts.cluster,
        facts.device_name,
        {raw_value_select},
        TRUE AS present
    FROM {p}_raw_pieces AS rp
    CROSS JOIN LATERAL (
        SELECT {raw_projection}
        FROM {raw_table} AS facts
        WHERE facts.location = $1
          AND facts."time" >= rp.lo
          AND facts."time" < rp.hi
          AND {raw_predicate}
        OFFSET 0
    ) AS facts
    GROUP BY rp.idx, facts.location, facts.cluster, facts.device_name
),
{p}_merged AS (
    SELECT
        n.idx,
        n.b0,
        n.b1,
        n.partial,
        u.location,
        u.cluster,
        u.device_name,
        SUM(u.sample_count)::BIGINT AS sample_count,
        SUM(u.value_sum) AS value_sum,
        SUM(u.value_sum_squares) AS value_sum_squares,
        MIN(u.min_value) AS min_value,
        MAX(u.max_value) AS max_value,
        bool_or(u.present) AS present
    FROM (
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present
        FROM {p}_day_rows
        UNION ALL
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present
        FROM {p}_hour_rows
        UNION ALL
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present
        FROM {p}_raw_rows
    ) AS u
    JOIN buckets AS n
      ON n.idx = u.idx
    GROUP BY n.idx, n.b0, n.b1, n.partial, u.location, u.cluster, u.device_name
),
"""

_LIGHT_FAMILY_SQL: Final[str] = _CONTROL_FAMILY_TEMPLATE.format(
    p="light",
    hour_model="monitoring_light_intensity_1hour",
    day_model="monitoring_light_intensity_1day",
    hour_table="monitoring_light_intensity_1hour",
    hour_day_table="monitoring_light_intensity_1day",
    raw_table="effective_setpoints",
    day_value_select=(
        "c.sample_count AS sample_count, c.value_sum AS value_sum,"
        " c.value_sum_squares AS value_sum_squares, c.min_value AS min_value,"
        " c.max_value AS max_value"
    ),
    hour_value_select=(
        "c.sample_count AS sample_count, c.value_sum AS value_sum,"
        " c.value_sum_squares AS value_sum_squares, c.min_value AS min_value,"
        " c.max_value AS max_value"
    ),
    beyond_probe=(
        "facts.device_name IS NOT NULL"
        " AND (facts.effective_light_intensity IS NOT NULL"
        " OR facts.nominal_light_intensity IS NOT NULL"
        " OR facts.ramp_progress_light IS NOT NULL)"
    ),
    raw_predicate=(
        "facts.device_name IS NOT NULL"
        " AND (facts.effective_light_intensity IS NOT NULL"
        " OR facts.nominal_light_intensity IS NOT NULL"
        " OR facts.ramp_progress_light IS NOT NULL)"
    ),
    raw_projection=(
        "facts.location, facts.cluster, facts.device_name, facts.effective_light_intensity"
    ),
    raw_value_select=(
        "count(facts.effective_light_intensity)::BIGINT AS sample_count,"
        " sum(facts.effective_light_intensity::NUMERIC) AS value_sum,"
        " sum(facts.effective_light_intensity::NUMERIC"
        " * facts.effective_light_intensity::NUMERIC) AS value_sum_squares,"
        " min(facts.effective_light_intensity::NUMERIC) AS min_value,"
        " max(facts.effective_light_intensity::NUMERIC) AS max_value"
    ),
)

_PID_FAMILY_SQL: Final[str] = _CONTROL_FAMILY_TEMPLATE.format(
    p="pid",
    hour_model="monitoring_requested_pid_1hour",
    day_model="monitoring_requested_pid_1day",
    hour_table="monitoring_requested_pid_1hour",
    hour_day_table="monitoring_requested_pid_1day",
    raw_table="automation_state",
    day_value_select=(
        "c.requested_count AS sample_count, c.requested_sum AS value_sum,"
        " c.requested_sum_squares AS value_sum_squares,"
        " c.requested_min AS min_value, c.requested_max AS max_value"
    ),
    hour_value_select=(
        "c.requested_count AS sample_count, c.requested_sum AS value_sum,"
        " c.requested_sum_squares AS value_sum_squares,"
        " c.requested_min AS min_value, c.requested_max AS max_value"
    ),
    beyond_probe="facts.location = $1",
    raw_predicate="facts.location = $1",
    raw_projection="facts.location, facts.cluster, facts.device_name, facts.pid_output",
    raw_value_select=(
        "count(*) FILTER (WHERE facts.pid_output::double precision"
        " >= 0::double precision"
        " AND facts.pid_output::double precision <= 1::double precision)::BIGINT"
        " AS sample_count,"
        " sum((facts.pid_output::double precision"
        " * 100.0::double precision)::NUMERIC)"
        " FILTER (WHERE facts.pid_output::double precision >= 0::double precision"
        " AND facts.pid_output::double precision <= 1::double precision)"
        " AS value_sum,"
        " sum((facts.pid_output::double precision"
        " * 100.0::double precision)::NUMERIC"
        " * (facts.pid_output::double precision"
        " * 100.0::double precision)::NUMERIC)"
        " FILTER (WHERE facts.pid_output::double precision >= 0::double precision"
        " AND facts.pid_output::double precision <= 1::double precision)"
        " AS value_sum_squares,"
        " min((facts.pid_output::double precision"
        " * 100.0::double precision)::NUMERIC)"
        " FILTER (WHERE facts.pid_output::double precision >= 0::double precision"
        " AND facts.pid_output::double precision <= 1::double precision)"
        " AS min_value,"
        " max((facts.pid_output::double precision"
        " * 100.0::double precision)::NUMERIC)"
        " FILTER (WHERE facts.pid_output::double precision >= 0::double precision"
        " AND facts.pid_output::double precision <= 1::double precision)"
        " AS max_value"
    ),
)

CONTROL_OVERVIEW_STATEMENT: Final[str] = (
    "WITH "
    + _ANGLES
    + ","
    + _LIGHT_FAMILY_SQL
    + _PID_FAMILY_SQL
    + """
guard AS (
    SELECT NOT (
        light_pending.h_pending
        OR (light_pending.d_pending AND light_pending.uses_daily)
        OR light_piece_guard.invalid
        OR NOT light_below_empty.empty
        OR pid_pending.h_pending
        OR (pid_pending.d_pending AND pid_pending.uses_daily)
        OR pid_piece_guard.invalid
        OR NOT pid_below_empty.empty
    ) AS guard_ok
    FROM light_pending
    CROSS JOIN light_piece_guard
    CROSS JOIN light_below_empty
    CROSS JOIN pid_pending
    CROSS JOIN pid_piece_guard
    CROSS JOIN pid_below_empty
),
final AS (
    SELECT
        'guard'::TEXT AS kind,
        guard.guard_ok AS guard_ok,
        NULL::TEXT AS family,
        NULL::INTEGER AS idx,
        NULL::TIMESTAMPTZ AS b0,
        NULL::TIMESTAMPTZ AS b1,
        NULL::BOOLEAN AS partial,
        NULL::TEXT AS location,
        NULL::TEXT AS cluster,
        NULL::TEXT AS device_name,
        NULL::BIGINT AS sample_count,
        NULL::NUMERIC AS value_sum,
        NULL::NUMERIC AS value_sum_squares,
        NULL::NUMERIC AS min_value,
        NULL::NUMERIC AS max_value,
        NULL::BOOLEAN AS present
    FROM guard
    UNION ALL
    SELECT
        'data'::TEXT AS kind,
        TRUE AS guard_ok,
        'light'::TEXT AS family,
        l.idx,
        l.b0,
        l.b1,
        l.partial,
        l.location,
        l.cluster,
        l.device_name,
        l.sample_count,
        l.value_sum,
        l.value_sum_squares,
        l.min_value,
        l.max_value,
        l.present
    FROM light_merged AS l
    UNION ALL
    SELECT
        'data'::TEXT AS kind,
        TRUE AS guard_ok,
        'pid'::TEXT AS family,
        p.idx,
        p.b0,
        p.b1,
        p.partial,
        p.location,
        p.cluster,
        p.device_name,
        p.sample_count,
        p.value_sum,
        p.value_sum_squares,
        p.min_value,
        p.max_value,
        p.present
    FROM pid_merged AS p
)
SELECT
"""
    + CONTROL_COLUMNS
    + """
FROM final
ORDER BY kind DESC
"""
)


def _single_family_statement(family: str, family_sql: str) -> str:
    """Assemble one control family into a self-guarding overview statement.

    The light-only statement must stay readable when the automation-side
    sources are missing, so only the selected family's own coverage,
    watermarks, pending invalidations, below-coverage emptiness and piece
    partition participate: never the requested-PID or relay readiness gates.
    """
    return (
        "WITH "
        + _ANGLES
        + ","
        + family_sql
        + f"""
guard AS (
    SELECT NOT (
        {family}_pending.h_pending
        OR ({family}_pending.d_pending AND {family}_pending.uses_daily)
        OR {family}_piece_guard.invalid
        OR NOT {family}_below_empty.empty
    ) AS guard_ok
    FROM {family}_pending
    CROSS JOIN {family}_piece_guard
    CROSS JOIN {family}_below_empty
),
final AS (
    SELECT
        'guard'::TEXT AS kind,
        guard.guard_ok AS guard_ok,
        NULL::TEXT AS family,
        NULL::INTEGER AS idx,
        NULL::TIMESTAMPTZ AS b0,
        NULL::TIMESTAMPTZ AS b1,
        NULL::BOOLEAN AS partial,
        NULL::TEXT AS location,
        NULL::TEXT AS cluster,
        NULL::TEXT AS device_name,
        NULL::BIGINT AS sample_count,
        NULL::NUMERIC AS value_sum,
        NULL::NUMERIC AS value_sum_squares,
        NULL::NUMERIC AS min_value,
        NULL::NUMERIC AS max_value,
        NULL::BOOLEAN AS present
    FROM guard
    UNION ALL
    SELECT
        'data'::TEXT AS kind,
        TRUE AS guard_ok,
        '{family}'::TEXT AS family,
        merged.idx,
        merged.b0,
        merged.b1,
        merged.partial,
        merged.location,
        merged.cluster,
        merged.device_name,
        merged.sample_count,
        merged.value_sum,
        merged.value_sum_squares,
        merged.min_value,
        merged.max_value,
        merged.present
    FROM {family}_merged AS merged
)
SELECT
"""
        + CONTROL_COLUMNS
        + """
FROM final
ORDER BY kind DESC
"""
    )


# A lights-scope overview reuses the recorded light family (hour/day CAGG
# rows plus the exact start-fragment and bounded 24 hour tail raw pieces) with
# only its own guard terms, so it never queries or gates requested-PID/relay
# derived readiness. $1 location, $2 start, $3 end, $4 bounds JSON, $5 bucket seconds.
LIGHT_OVERVIEW_STATEMENT: Final[str] = _single_family_statement("light", _LIGHT_FAMILY_SQL)

# -- relay statement --------------------------------------------------------
# Recorded lane durations from the relay rebuild's derived hour/day rows plus
# exact ``monitoring_relay_duration_slices`` intervals for the uncovered
# recent piece span, all under one request observation watermark. Unknown
# elapsed time is honest: buckets without recorded owner time report only
# elapsed microseconds for that lane, and coverage_complete is false when
# unknown time remains. $1 location, $2 start, $3 end, $4 bounds JSON, $5 bucket seconds.
_RELAY_MARKS: Final[str] = """            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.h_from))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.h_through))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.d_from))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.d_through))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.cov_from_min))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.cov_through_max))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.tail_start))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.frag_end))),
            (GREATEST(b.b0, LEAST(b.b1, relay_bounds.frag_start))),
            (b.b0),
            (b.b1)"""

# The relay daily pending invalidation gates only when a requested bucket is
# wide enough to hold a whole day (``uses_daily``); the hourly readiness
# window stays fully gated through ``monitoring_relay_overview_hourly_ready``.
RELAY_OVERVIEW_STATEMENT: Final[str] = (
    "WITH "
    + _ANGLES
    + """
,
relay_cov AS (
    SELECT
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_relay_duration_1hour') AS h_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_relay_duration_1hour') AS h_through,
        (SELECT covered_from FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_relay_duration_1day') AS d_from,
        (SELECT covered_through FROM monitoring_overview_coverage
          WHERE model_name = 'monitoring_relay_duration_1day') AS d_through,
        (SELECT MIN(materialization_watermark) FROM monitoring_history_cagg_watermark
          WHERE cagg_name = 'monitoring_relay_duration_1day') AS d_wm
),
relay_bounds AS (
    SELECT
        request.range_start,
        request.range_end,
        relay_cov.h_from,
        relay_cov.h_through,
        relay_cov.d_from,
        relay_cov.d_through,
        relay_cov.d_wm,
        LEAST(relay_cov.h_from, relay_cov.d_from) AS cov_from_min,
        GREATEST(relay_cov.h_through, relay_cov.d_through) AS cov_through_max,
        GREATEST(request.range_end - INTERVAL '24 hours',
                 CURRENT_TIMESTAMP - INTERVAL '24 hours') AS tail_start,
        CASE WHEN request.range_start = date_trunc('hour', request.range_start)
             THEN request.range_start
             ELSE date_trunc('hour', request.range_start) + INTERVAL '1 hour'
        END AS frag_end,
        date_trunc('hour', request.range_end) AS frag_start
    FROM request
    CROSS JOIN relay_cov
),
relay_ready AS (
    SELECT
        CASE
            WHEN GREATEST(relay_bounds.range_start, relay_bounds.h_from)
                 < LEAST(relay_bounds.range_end, relay_bounds.h_through)
            THEN monitoring_relay_overview_hourly_ready(
                     GREATEST(relay_bounds.range_start, relay_bounds.h_from),
                     LEAST(relay_bounds.range_end, relay_bounds.h_through)
                 )
            ELSE TRUE
        END AS ready
    FROM relay_bounds
),
relay_day_pending AS (
    SELECT EXISTS (
        SELECT 1
        FROM monitoring_history_cagg_watermark
        WHERE cagg_name = 'monitoring_relay_duration_1day'
          AND invalidation_source IS NOT NULL
          AND invalidation_start < LEAST(relay_bounds.d_through,
                                         relay_bounds.range_end)
          AND invalidation_end >= GREATEST(relay_bounds.d_from,
                                        relay_bounds.range_start)
    ) AS pending,
    EXISTS (
        SELECT 1
        FROM buckets
        WHERE b1 - b0 >= INTERVAL '24 hours'
    ) AS uses_daily
    FROM relay_bounds
),
relay_below_empty AS (
    SELECT NOT EXISTS (
        SELECT 1
        FROM relay_observation
        CROSS JOIN relay_bounds
        WHERE relay_bounds.cov_from_min IS NOT NULL
          AND relay_observation."time" >= relay_bounds.range_start
          AND relay_observation."time"
              < LEAST(relay_bounds.range_end, relay_bounds.cov_from_min)
    ) AS empty
    FROM relay_bounds
),
relay_watermark AS (
    SELECT COALESCE(
        (SELECT MAX(observation_id) FROM relay_observation),
        0
    )::BIGINT AS wm
),
relay_bucket_marks AS (
    SELECT DISTINCT b.b0, b.b1, b.partial, b.idx, marks.ts
    FROM buckets AS b
    CROSS JOIN relay_bounds
    CROSS JOIN LATERAL (
        VALUES
"""
    + _RELAY_MARKS
    + """
    ) AS marks(ts)
),
relay_mark_line AS (
    SELECT idx, b0, b1, partial, ts,
           lag(ts) OVER (PARTITION BY idx ORDER BY ts) AS lo
    FROM relay_bucket_marks
),
relay_pieces AS (
    SELECT idx, b0, b1, partial, ts AS hi, lo
    FROM relay_mark_line
    WHERE lo IS NOT NULL AND ts > lo
),
relay_piece_labeled AS (
    SELECT pieces.idx, pieces.b0, pieces.b1, pieces.partial,
           pieces.lo, pieces.hi,
           CASE
               WHEN pieces.hi <= relay_bounds.frag_end
                    OR pieces.lo >= relay_bounds.frag_start THEN 'slices'
               WHEN relay_bounds.h_from IS NOT NULL
                    AND pieces.lo >= relay_bounds.h_from
                    AND pieces.hi <= relay_bounds.h_through THEN 'hour'
               WHEN relay_bounds.d_from IS NOT NULL
                    AND pieces.lo >= relay_bounds.d_from
                    AND pieces.hi <= relay_bounds.d_through THEN 'day'
               WHEN relay_bounds.cov_from_min IS NOT NULL
                    AND pieces.hi <= relay_bounds.cov_from_min THEN 'empty'
               WHEN pieces.lo >= relay_bounds.tail_start THEN 'slices'
           END AS reason
    FROM relay_pieces AS pieces
    CROSS JOIN relay_bounds
),
relay_piece_guard AS (
    SELECT EXISTS (
        SELECT 1 FROM relay_piece_labeled WHERE reason IS NULL
    ) OR FALSE AS invalid
    FROM relay_bounds
),
relay_day_rows AS (
    SELECT
        b.idx,
        c.bucket,
        c.location,
        c.cluster,
        c.channel,
        c.device_id,
        c.device_name,
        c.device_type,
        c.on_microseconds,
        c.off_microseconds
    FROM monitoring_relay_duration_1day AS c
    JOIN buckets AS b
      ON c.bucket >= b.b0
     AND c.bucket + INTERVAL '24 hours' <= b.b1
    CROSS JOIN relay_bounds
    CROSS JOIN relay_watermark
    WHERE c.location = $1
      AND c.bucket >= relay_bounds.d_from
      AND c.bucket + INTERVAL '24 hours' <= relay_bounds.d_through
      AND c.bucket + INTERVAL '24 hours' <= relay_bounds.d_wm
      AND c.source_watermark <= relay_watermark.wm
),
relay_hour_rows AS (
    SELECT
        b.idx,
        c.bucket,
        c.location,
        c.cluster,
        c.channel,
        c.device_id,
        c.device_name,
        c.device_type,
        c.on_microseconds,
        c.off_microseconds
    FROM monitoring_relay_duration_1hour AS c
    JOIN buckets AS b
      ON time_bucket($5::bigint * INTERVAL '1 second', c.bucket,
                     '1970-01-01T00:00:00Z'::timestamptz) =
         time_bucket($5::bigint * INTERVAL '1 second', b.b0,
                     '1970-01-01T00:00:00Z'::timestamptz)
     AND c.bucket >= b.b0
     AND c.bucket + INTERVAL '1 hour' <= b.b1
    CROSS JOIN relay_bounds
    CROSS JOIN relay_watermark
    WHERE c.location = $1
      AND c.active
      AND c.bucket >= relay_bounds.h_from
      AND c.bucket >= relay_bounds.range_start
      AND c.bucket < relay_bounds.range_end
      AND c.bucket + INTERVAL '1 hour' <= relay_bounds.h_through
      AND c.source_watermark <= relay_watermark.wm
      AND NOT EXISTS (
          SELECT 1
          FROM relay_day_rows AS dr
          WHERE dr.idx = b.idx
            AND dr.location = c.location
            AND dr.cluster = c.cluster
            AND dr.channel = c.channel
            AND dr.device_id = c.device_id
            AND dr.device_name = c.device_name
            AND dr.device_type = c.device_type
            AND dr.bucket <= c.bucket
            AND dr.bucket + INTERVAL '24 hours' > c.bucket
      )
),
relay_slices AS (
    SELECT
        slices.location,
        slices.cluster,
        slices.channel,
        slices.device_id,
        slices.device_name,
        slices.device_type,
        slices.state,
        pieces.idx,
        (monitoring_relay_epoch_us(slices.end_at)
         - monitoring_relay_epoch_us(slices.start_at))::BIGINT AS slice_us
    FROM relay_piece_labeled AS pieces
    CROSS JOIN relay_watermark
    CROSS JOIN LATERAL monitoring_relay_duration_slices(
             pieces.lo, pieces.hi, relay_watermark.wm) AS slices
    WHERE pieces.reason = 'slices' AND slices.location = $1
),
relay_merged AS (
    SELECT
        n.idx,
        n.b0,
        n.b1,
        n.partial,
        u.location,
        u.cluster,
        u.channel,
        u.device_id,
        u.device_name,
        u.device_type,
        SUM(u.on_microseconds)::BIGINT AS on_micro,
        SUM(u.off_microseconds)::BIGINT AS off_micro
    FROM (
        SELECT h.idx, h.location, h.cluster, h.channel, h.device_id,
               h.device_name, h.device_type,
               h.on_microseconds, h.off_microseconds
        FROM relay_hour_rows AS h
        UNION ALL
        SELECT d.idx, d.location, d.cluster, d.channel, d.device_id,
               d.device_name, d.device_type,
               d.on_microseconds, d.off_microseconds
        FROM relay_day_rows AS d
        UNION ALL
        SELECT s.idx, s.location, s.cluster, s.channel, s.device_id,
               s.device_name, s.device_type,
               CASE WHEN s.state = 'on' THEN s.slice_us ELSE 0 END,
               CASE WHEN s.state = 'off' THEN s.slice_us ELSE 0 END
        FROM relay_slices AS s
    ) AS u
    JOIN buckets AS n
      ON n.idx = u.idx
    GROUP BY n.idx, n.b0, n.b1, n.partial, u.location, u.cluster, u.channel,
             u.device_id, u.device_name, u.device_type
),
guard AS (
    SELECT NOT (
        NOT relay_ready.ready
        OR (relay_day_pending.pending AND relay_day_pending.uses_daily)
        OR NOT relay_below_empty.empty
        OR relay_piece_guard.invalid
    ) AS guard_ok
    FROM relay_ready
    CROSS JOIN relay_day_pending
    CROSS JOIN relay_below_empty
    CROSS JOIN relay_piece_guard
),
final AS (
    SELECT
        'guard'::TEXT AS kind,
        guard.guard_ok AS guard_ok,
        NULL::INTEGER AS idx,
        NULL::TIMESTAMPTZ AS b0,
        NULL::TIMESTAMPTZ AS b1,
        NULL::BOOLEAN AS partial,
        NULL::TEXT AS location,
        NULL::TEXT AS cluster,
        NULL::SMALLINT AS channel,
        NULL::INTEGER AS device_id,
        NULL::TEXT AS device_name,
        NULL::TEXT AS device_type,
        NULL::BIGINT AS on_microseconds,
        NULL::BIGINT AS off_microseconds
    FROM guard
    UNION ALL
    SELECT
        'data'::TEXT AS kind,
        TRUE AS guard_ok,
        m.idx,
        m.b0,
        m.b1,
        m.partial,
        m.location,
        m.cluster,
        m.channel,
        m.device_id,
        m.device_name,
        m.device_type,
        m.on_micro,
        m.off_micro
    FROM relay_merged AS m
)
SELECT
"""
    + RELAY_COLUMNS
    + """
FROM final
ORDER BY kind DESC
"""
)
