"""Relay-duration history overview statement (history_overview_sql documents the shared coverage-guard contract)."""

from __future__ import annotations

from typing import Final

from monitoring_service.history_overview_sql import _ANGLES

RELAY_COLUMNS: Final[str] = (
    "kind, guard_ok, idx, b0, b1, partial, location, cluster, channel,"
    " device_id, device_name, device_type, on_microseconds, off_microseconds"
)

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
