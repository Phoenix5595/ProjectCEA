"""Control and light-scope history overview statements (history_overview_sql documents the shared coverage-guard contract)."""

from __future__ import annotations

from typing import Final

from monitoring_service.history_overview_sql import _ANGLES

CONTROL_COLUMNS: Final[str] = (
    "kind, guard_ok, family, idx, b0, b1, partial, location, cluster,"
    " device_name, sample_count, value_sum, value_sum_squares, min_value,"
    " max_value, present, weighted_value, covered_seconds"
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
{tier_value_ctes}
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
            SELECT bucket FROM {hour_table}
             WHERE location = $1 AND {hour_probe_predicate}
            UNION ALL
            SELECT bucket FROM {hour_day_table}
             WHERE location = $1 AND {day_probe_predicate}
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
    {day_value_join}
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
    {hour_value_join}
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
{raw_rows_sql}
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
        bool_or(u.present) AS present,
        SUM(u.weighted_value) AS weighted_value,
        SUM(u.covered_seconds) AS covered_seconds
    FROM (
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present,
               weighted_value, covered_seconds
        FROM {p}_day_rows
        UNION ALL
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present,
               weighted_value, covered_seconds
        FROM {p}_hour_rows
        UNION ALL
        SELECT idx, location, cluster, device_name, sample_count, value_sum,
               value_sum_squares, min_value, max_value, present,
               weighted_value, covered_seconds
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
    tier_value_ctes="",
    day_value_join="",
    hour_value_join="",
    hour_probe_predicate="TRUE",
    day_probe_predicate="TRUE",
    day_value_select=(
        "c.sample_count AS sample_count, c.value_sum AS value_sum,"
        " c.value_sum_squares AS value_sum_squares, c.min_value AS min_value,"
        " c.max_value AS max_value, NULL::NUMERIC AS weighted_value,"
        " NULL::NUMERIC AS covered_seconds"
    ),
    hour_value_select=(
        "c.sample_count AS sample_count, c.value_sum AS value_sum,"
        " c.value_sum_squares AS value_sum_squares, c.min_value AS min_value,"
        " c.max_value AS max_value, NULL::NUMERIC AS weighted_value,"
        " NULL::NUMERIC AS covered_seconds"
    ),
    beyond_probe=(
        "facts.device_name IS NOT NULL"
        " AND (facts.effective_light_intensity IS NOT NULL"
        " OR facts.nominal_light_intensity IS NOT NULL"
        " OR facts.ramp_progress_light IS NOT NULL)"
    ),
    raw_rows_sql="""
light_raw_rows AS (
    SELECT
        rp.idx,
        facts.location,
        facts.cluster,
        facts.device_name,
        count(facts.effective_light_intensity)::BIGINT AS sample_count,
        sum(facts.effective_light_intensity::NUMERIC) AS value_sum,
        sum(facts.effective_light_intensity::NUMERIC
            * facts.effective_light_intensity::NUMERIC) AS value_sum_squares,
        min(facts.effective_light_intensity::NUMERIC) AS min_value,
        max(facts.effective_light_intensity::NUMERIC) AS max_value,
        TRUE AS present,
        NULL::NUMERIC AS weighted_value,
        NULL::NUMERIC AS covered_seconds
    FROM light_raw_pieces AS rp
    CROSS JOIN LATERAL (
        SELECT facts.location, facts.cluster, facts.device_name,
               facts.effective_light_intensity
        FROM effective_setpoints AS facts
        WHERE facts.location = $1
          AND facts."time" >= rp.lo
          AND facts."time" < rp.hi
          AND facts.device_name IS NOT NULL
          AND (facts.effective_light_intensity IS NOT NULL
               OR facts.nominal_light_intensity IS NOT NULL
               OR facts.ramp_progress_light IS NOT NULL)
        OFFSET 0
    ) AS facts
    GROUP BY rp.idx, facts.location, facts.cluster, facts.device_name
),
""",
)

_PID_FAMILY_SQL: Final[str] = _CONTROL_FAMILY_TEMPLATE.format(
    p="pid",
    hour_model="monitoring_requested_pid_1hour",
    day_model="monitoring_requested_pid_1day",
    hour_table="monitoring_requested_pid_1hour",
    hour_day_table="monitoring_requested_pid_1day",
    raw_table="automation_state",
    tier_value_ctes="""
pid_hour_window AS (
    SELECT c.bucket, c.location, c.cluster, c.device_name, c.requested_tw,
           lag(c.requested_tw) OVER summary_window AS previous_tw,
           lead(c.requested_tw) OVER summary_window AS next_tw
    FROM monitoring_requested_pid_1hour AS c
    WHERE c.location = $1
      AND c.requested_tw IS NOT NULL
    WINDOW summary_window AS (
        PARTITION BY c.location, c.cluster, c.device_name
        ORDER BY c.bucket
    )
),
pid_hour_values AS (
    SELECT averaged.*,
           CASE WHEN averaged.previous_tw IS NULL
                THEN EXTRACT(EPOCH FROM (
                    averaged.bucket + INTERVAL '1 hour'
                    - first_time(averaged.requested_tw)))::NUMERIC
                ELSE 3600::NUMERIC
           END AS covered_seconds
    FROM (
        SELECT pid_hour_window.*,
               interpolated_average(
                   requested_tw, bucket, INTERVAL '1 hour',
                   previous_tw, next_tw
               ) AS requested_average
        FROM pid_hour_window
    ) AS averaged
    WHERE averaged.requested_average IS NOT NULL
),
pid_day_window AS (
    SELECT c.bucket, c.location, c.cluster, c.device_name, c.requested_tw,
           lag(c.requested_tw) OVER summary_window AS previous_tw,
           lead(c.requested_tw) OVER summary_window AS next_tw
    FROM monitoring_requested_pid_1day AS c
    WHERE c.location = $1
      AND c.requested_tw IS NOT NULL
    WINDOW summary_window AS (
        PARTITION BY c.location, c.cluster, c.device_name
        ORDER BY c.bucket
    )
),
pid_day_values AS (
    SELECT averaged.*,
           CASE WHEN averaged.previous_tw IS NULL
                THEN EXTRACT(EPOCH FROM (
                    averaged.bucket + INTERVAL '24 hours'
                    - first_time(averaged.requested_tw)))::NUMERIC
                ELSE 86400::NUMERIC
           END AS covered_seconds
    FROM (
        SELECT pid_day_window.*,
               interpolated_average(
                   requested_tw, bucket, INTERVAL '24 hours',
                   previous_tw, next_tw
               ) AS requested_average
        FROM pid_day_window
    ) AS averaged
    WHERE averaged.requested_average IS NOT NULL
),
""",
    day_value_join="""JOIN pid_day_values AS tw
      ON tw.bucket = c.bucket
     AND tw.location = c.location
     AND tw.cluster = c.cluster
     AND tw.device_name = c.device_name""",
    hour_value_join="""JOIN pid_hour_values AS tw
      ON tw.bucket = c.bucket
     AND tw.location = c.location
     AND tw.cluster = c.cluster
     AND tw.device_name = c.device_name""",
    hour_probe_predicate="requested_tw IS NOT NULL",
    day_probe_predicate="requested_tw IS NOT NULL",
    day_value_select=(
        "c.sample_count AS sample_count, NULL::NUMERIC AS value_sum,"
        " NULL::NUMERIC AS value_sum_squares,"
        " CASE WHEN tw.previous_tw IS NOT NULL"
        " AND first_time(tw.requested_tw) > c.bucket"
        " THEN LEAST(c.requested_min,"
        " last_val(tw.previous_tw)::NUMERIC)"
        " ELSE c.requested_min END AS min_value,"
        " CASE WHEN tw.previous_tw IS NOT NULL"
        " AND first_time(tw.requested_tw) > c.bucket"
        " THEN GREATEST(c.requested_max,"
        " last_val(tw.previous_tw)::NUMERIC)"
        " ELSE c.requested_max END AS max_value,"
        " tw.requested_average::NUMERIC * tw.covered_seconds AS weighted_value,"
        " tw.covered_seconds AS covered_seconds"
    ),
    hour_value_select=(
        "c.sample_count AS sample_count, NULL::NUMERIC AS value_sum,"
        " NULL::NUMERIC AS value_sum_squares,"
        " CASE WHEN tw.previous_tw IS NOT NULL"
        " AND first_time(tw.requested_tw) > c.bucket"
        " THEN LEAST(c.requested_min,"
        " last_val(tw.previous_tw)::NUMERIC)"
        " ELSE c.requested_min END AS min_value,"
        " CASE WHEN tw.previous_tw IS NOT NULL"
        " AND first_time(tw.requested_tw) > c.bucket"
        " THEN GREATEST(c.requested_max,"
        " last_val(tw.previous_tw)::NUMERIC)"
        " ELSE c.requested_max END AS max_value,"
        " tw.requested_average::NUMERIC * tw.covered_seconds AS weighted_value,"
        " tw.covered_seconds AS covered_seconds"
    ),
    beyond_probe=(
        "facts.location = $1"
        " AND facts.pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION"
        " AND facts.pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION"
    ),
    raw_rows_sql="""
pid_raw_rows AS (
    SELECT
        rp.idx,
        facts.location,
        facts.cluster,
        facts.device_name,
        facts.sample_count,
        NULL::NUMERIC AS value_sum,
        NULL::NUMERIC AS value_sum_squares,
        weighted.min_value,
        weighted.max_value,
        TRUE AS present,
        weighted.weighted_value,
        weighted.covered_seconds
    FROM pid_raw_pieces AS rp
    CROSS JOIN LATERAL (
        SELECT identities.location, identities.cluster, identities.device_name,
               count(facts.id)::BIGINT AS sample_count
        FROM (
            SELECT source.location, source.cluster, source.device_name
            FROM automation_state AS source
            WHERE source.location = $1
              AND source."time" >= rp.lo
              AND source."time" < rp.hi
            GROUP BY source.location, source.cluster, source.device_name
            UNION
            SELECT previous.location, previous.cluster, previous.device_name
            FROM (
                SELECT DISTINCT ON (
                    latest.location, latest.cluster, latest.device_name)
                    latest.location, latest.cluster, latest.device_name,
                    latest.pid_output
                FROM automation_state AS latest
                WHERE latest.location = $1
                  AND latest."time" < rp.lo
                ORDER BY latest.location, latest.cluster, latest.device_name,
                         latest."time" DESC, latest.id DESC
            ) AS previous
            WHERE previous.pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
              AND previous.pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION
        ) AS identities
        LEFT JOIN automation_state AS facts
          ON facts.location = identities.location
         AND facts.cluster = identities.cluster
         AND facts.device_name = identities.device_name
         AND facts."time" >= rp.lo
         AND facts."time" < rp.hi
        GROUP BY identities.location, identities.cluster, identities.device_name
        OFFSET 0
    ) AS facts
    CROSS JOIN LATERAL (
        SELECT
            sum(intervals.percent * intervals.covered_seconds) AS weighted_value,
            sum(intervals.covered_seconds)
                FILTER (WHERE intervals.percent IS NOT NULL) AS covered_seconds,
            min(intervals.percent) AS min_value,
            max(intervals.percent) AS max_value
        FROM (
            SELECT points.percent,
                   GREATEST(
                       0::NUMERIC,
                       EXTRACT(EPOCH FROM (
                           LEAST(rp.hi, points.next_time)
                           - GREATEST(rp.lo, points.sample_time)
                       ))::NUMERIC
                   ) AS covered_seconds
            FROM (
                SELECT samples.sample_time,
                       samples.percent,
                       lead(samples.sample_time, 1, rp.hi)
                           OVER (ORDER BY samples.sample_time, samples.id) AS next_time
                FROM (
                    (
                        SELECT previous.id, previous."time" AS sample_time,
                               CASE
                                   WHEN previous.pid_output::DOUBLE PRECISION
                                        BETWEEN 0::DOUBLE PRECISION
                                            AND 1::DOUBLE PRECISION
                                   THEN round((
                                       previous.pid_output::DOUBLE PRECISION
                                       * 100::DOUBLE PRECISION)::NUMERIC, 13)
                                   ELSE NULL::NUMERIC
                               END AS percent
                        FROM automation_state AS previous
                        WHERE previous.location = facts.location
                          AND previous.cluster = facts.cluster
                          AND previous.device_name = facts.device_name
                          AND previous."time" < rp.lo
                        ORDER BY previous."time" DESC, previous.id DESC
                        LIMIT 1
                    )
                    UNION ALL
                    (
                        SELECT current_point.id, current_point."time" AS sample_time,
                               CASE
                                   WHEN current_point.pid_output::DOUBLE PRECISION
                                        BETWEEN 0::DOUBLE PRECISION
                                            AND 1::DOUBLE PRECISION
                                   THEN round((
                                       current_point.pid_output::DOUBLE PRECISION
                                       * 100::DOUBLE PRECISION)::NUMERIC, 13)
                                   ELSE NULL::NUMERIC
                               END AS percent
                        FROM automation_state AS current_point
                        WHERE current_point.location = facts.location
                          AND current_point.cluster = facts.cluster
                          AND current_point.device_name = facts.device_name
                          AND current_point."time" >= rp.lo
                          AND current_point."time" < rp.hi
                    )
                ) AS samples
            ) AS points
        ) AS intervals
        WHERE intervals.covered_seconds > 0
          AND intervals.percent IS NOT NULL
    ) AS weighted
    WHERE weighted.covered_seconds > 0
),
""",
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
        NULL::BOOLEAN AS present,
        NULL::NUMERIC AS weighted_value,
        NULL::NUMERIC AS covered_seconds
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
        l.present,
        l.weighted_value,
        l.covered_seconds
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
        p.present,
        p.weighted_value,
        p.covered_seconds
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
        NULL::BOOLEAN AS present,
        NULL::NUMERIC AS weighted_value,
        NULL::NUMERIC AS covered_seconds
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
        merged.present,
        merged.weighted_value,
        merged.covered_seconds
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
