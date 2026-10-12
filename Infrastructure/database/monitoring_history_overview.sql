\set ON_ERROR_STOP on
-- Operator-only preparation of mergeable hourly/daily summary SOURCES for the
-- bounded all-history monitoring overviews. Never invoked by deployment,
-- startup, or any service lifespan; no supervisor/hardware interaction and no
-- automatic backfill or policy activation is performed or authorized here.
-- Production application requires separate owner authorization through the
-- canonical full-service candidate workflow; this file is NOT deployment
-- approval.
--
-- What this file creates (only pre-existing monitoring_read_models.sql
-- prerequisites plus the objects below; nothing existing is modified or
-- reintroduced):
--   * public.monitoring_overview_unacknowledged_failures(config JSONB)
--     STABLE SECURITY INVOKER failure-acknowledgement gate shared by every
--     bounded overview workflow below.
--   * public.monitoring_overview_coverage: verified-contiguous coverage per
--     overview model. Rows are written by supervised bounded backfill
--     verification and by the ongoing bounded verified refresh checkpoints
--     after each successful incremental verify, never ahead of native
--     materialization; overview readers must cross-check it against native
--     invalidation/watermark state, including the hourly ancestors of the
--     daily views.
--   * Six materialized-only continuous aggregates carrying NUMERIC mergeable
--     sufficient statistics (identity first, then counts, sums, squares,
--     minima/maxima, last_observed_at):
--       monitoring_measurement_1hour/1day  grouped by (sensor_id),
--       monitoring_light_intensity_1hour/1day and
--         monitoring_requested_pid_1hour/1day grouped by
--         (location, cluster, device_name).
--     Hour/day buckets are aligned on UTC boundaries (the TIMESTAMPTZ
--     time_bucket default), each daily aggregate rolls up its hourly parent,
--     and summed counts are cast back to BIGINT so counts stay mergeable.
--     The light source applies the exact recorded-light predicate of the
--     detailed light query, and requested samples are normalized exactly like
--     `_requested_percent`: a raw REAL fraction counts iff finite and
--     0 <= pid_output <= 1, contributing pid_output::DOUBLE PRECISION * 100
--     stored as NUMERIC; invalid/NULL values are never requested samples.
--   * One-bucket refresh policies per aggregate, created PAUSED
--     (scheduled => false) with max_runtime 60 seconds, max_retries 0,
--     buckets_per_batch 1, max_batches_per_execution 1,
--     refresh_newest_first false. Hourly sources refresh every five minutes,
--     daily sources hourly. start_offset NULL keeps every invalidated bucket
--     eligible (no bounded-lifetime truncation) and end_offset equals the
--     source width one bucket summarizes, so a refresh never materializes the
--     current partial bucket: hourly buckets complete roughly two hours after
--     their end, daily days after one extra day. Owner edits to enable/sched
--     state are preserved by repeat application; structural config drift is
--     refused, and no existing policy/index/raw schema is touched.
--   * Identity-leading group indexes on the six new aggregates only.
--
-- Authorization (choose exactly one; the script refuses everything else):
--   * Disposable proof harness: connect to a fresh `monitoring_test_*`
--     database and SET app.grow_cycle_history_disposable = '1' for the
--     session, then apply this file; or
--   * Production owner: connect as the owner of database `cea_sensors` and
--     SET app.grow_cycle_history_authorized = '1' for the session, then apply
--     this file.
-- Inherited PG*/DATABASE_URL/MONITORING_TEST_DATABASE_URL environments are
-- rejected upstream by the guarded harness; this file additionally refuses
-- unsupported databases directly.
--
-- After the shared helper, the coverage table and the six summary sources are
-- in place, `monitoring_relay_history_overview.sql` is included on the same
-- open transaction (relative include; run psql from this file's directory).
-- It owns the relay-duration hour/day sources, the exact duration-slice
-- function, the relay overview progress singleton and the paused
-- monitoring_rebuild_relay_overview procedure. A missing include file is an
-- explicit failure; any error rolls the whole application back.
BEGIN;
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '30s';

-- ------------------------------------------------------------------ §1: authorization
DO $authorization$
BEGIN
    IF current_database() ~ '^monitoring_test_[a-z0-9_]+$' THEN
        IF current_setting('app.grow_cycle_history_disposable', true) IS DISTINCT FROM '1' THEN
            RAISE EXCEPTION 'disposable grow-cycle-history authorization required';
        END IF;
    ELSIF current_database() = 'cea_sensors' THEN
        IF current_setting('app.grow_cycle_history_authorized', true) IS DISTINCT FROM '1' THEN
            RAISE EXCEPTION 'owner grow-cycle-history authorization required';
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported database for monitoring history overview';
    END IF;

    IF current_setting('server_version_num')::INTEGER / 10000 <> 15
       OR (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') IS DISTINCT FROM '2.28.3' THEN
        RAISE EXCEPTION 'requires PostgreSQL 15 and TimescaleDB 2.28.3; no upgrades or repairs performed';
    END IF;
END
$authorization$;

-- ------------------------------------------------------------------ §2: read-model prerequisites
-- The catalog/information assertions below require the helpers installed by
-- monitoring_read_models.sql; always apply that file first.
DO $read_model_prerequisites$
BEGIN
    IF to_regprocedure('public.monitoring_assert_timescaledb_catalog()') IS NULL THEN
        RAISE EXCEPTION 'missing prerequisite: apply monitoring_read_models.sql first (monitoring_assert_timescaledb_catalog)';
    END IF;
    IF to_regprocedure('public.monitoring_assert_relation_columns(text,text[],text[])') IS NULL THEN
        RAISE EXCEPTION 'missing prerequisite: apply monitoring_read_models.sql first (monitoring_assert_relation_columns)';
    END IF;
END
$read_model_prerequisites$;

SELECT monitoring_assert_timescaledb_catalog();

-- ------------------------------------------------------------------ §3: incompatible existing objects
-- Any pre-existing object with a new overview name must already be a
-- materialized-only continuous aggregate, and the coverage table must be a
-- regular table. Nothing is repaired.
DO $existing_object_preflight$
DECLARE
    object_name TEXT;
    relation_kind "char";
BEGIN
    FOREACH object_name IN ARRAY ARRAY[
        'monitoring_measurement_1hour',
        'monitoring_measurement_1day',
        'monitoring_light_intensity_1hour',
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour',
        'monitoring_requested_pid_1day'
    ] LOOP
        SELECT relkind
        INTO relation_kind
        FROM pg_class
        WHERE oid = to_regclass(format('public.%I', object_name));

        IF relation_kind IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM _timescaledb_catalog.continuous_agg
               WHERE user_view_schema = 'public'
                 AND user_view_name = object_name
                 AND materialized_only
           ) THEN
            RAISE EXCEPTION 'incompatible existing monitoring overview object: % must be a materialized-only continuous aggregate', object_name;
        END IF;
    END LOOP;

    IF to_regclass('public.monitoring_overview_coverage') IS NOT NULL
       AND (SELECT relkind FROM pg_class
            WHERE oid = to_regclass('public.monitoring_overview_coverage')) <> 'r' THEN
        RAISE EXCEPTION 'public.monitoring_overview_coverage must be a regular table; no repair performed';
    END IF;
END
$existing_object_preflight$;

-- ------------------------------------------------------------------ §4: shared failure-acknowledgement gate
-- Contract: public.monitoring_overview_unacknowledged_failures(config JSONB)
-- RETURNS BOOLEAN, STABLE SECURITY INVOKER, read-only, fully qualified.
--   * config must be a JSONB object containing a JSON-boolean `enabled` and a
--     JSON object `acknowledged_failures` whose values are nonnegative
--     integers and whose keys are only known overview logical names.
--   * Returns TRUE when any native TimescaleDB job backing a known logical
--     name has recorded more failures than its acknowledged count (missing
--     acknowledgement = 0; a job that does not exist counts as 0 failures).
--     Continuous-aggregate policy jobs resolve through
--     timescaledb_information.continuous_aggregates.materialization_hypertable_name
--     matched against the job's underlying hypertable; custom jobs resolve by
--     their public proc name. Procedures still gate their own `enabled` flag
--     plus this helper; the helper deliberately does not interpret `enabled`.
-- Known logical names: the six measurement/light_intensity/requested_pid
-- hour/day aggregate models, monitoring_relay_duration_1day,
-- monitoring_rebuild_relay_overview and
-- monitoring_refresh_overview_incremental.
DO $overview_unacknowledged_failures_guard$
DECLARE
    existing_definition RECORD;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname = 'monitoring_overview_unacknowledged_failures'
    ) THEN
        IF to_regprocedure('public.monitoring_overview_unacknowledged_failures(jsonb)') IS NULL THEN
            RAISE EXCEPTION 'existing monitoring_overview_unacknowledged_failures drifted from the (JSONB) signature; no repair performed';
        END IF;

        SELECT p.prokind, p.provolatile, p.prosecdef,
               format_type(p.prorettype, NULL) AS return_type
        INTO existing_definition
        FROM pg_proc p
        WHERE p.oid = to_regprocedure('public.monitoring_overview_unacknowledged_failures(jsonb)');

        IF existing_definition.prokind <> 'f'
           OR existing_definition.provolatile <> 's'
           OR existing_definition.prosecdef
           OR existing_definition.return_type IS DISTINCT FROM 'boolean' THEN
            RAISE EXCEPTION 'existing monitoring_overview_unacknowledged_failures drifted from STABLE SECURITY INVOKER returning BOOLEAN; no repair performed';
        END IF;
    END IF;
END
$overview_unacknowledged_failures_guard$;

CREATE OR REPLACE FUNCTION public.monitoring_overview_unacknowledged_failures(config JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    aggregate_model_names TEXT[] := ARRAY[
        'monitoring_measurement_1hour',
        'monitoring_measurement_1day',
        'monitoring_light_intensity_1hour',
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour',
        'monitoring_requested_pid_1day',
        'monitoring_relay_duration_1day'
    ];
    custom_job_names TEXT[] := ARRAY[
        'monitoring_rebuild_relay_overview',
        'monitoring_refresh_overview_incremental'
    ];
    known_names TEXT[];
    acknowledged JSONB;
    logical_name TEXT;
    is_custom BOOLEAN;
    entry_key TEXT;
    entry_value JSONB;
    entry_count NUMERIC;
    recorded_failures INTEGER;
    acknowledged_count NUMERIC;
BEGIN
    IF config IS NULL OR jsonb_typeof(config) <> 'object' THEN
        RAISE EXCEPTION 'overview job config must be a JSONB object';
    END IF;
    IF NOT (config ? 'enabled') OR jsonb_typeof(config -> 'enabled') <> 'boolean' THEN
        RAISE EXCEPTION 'overview job config must contain enabled as a JSON boolean';
    END IF;
    IF NOT (config ? 'acknowledged_failures')
       OR jsonb_typeof(config -> 'acknowledged_failures') <> 'object' THEN
        RAISE EXCEPTION 'overview job config must contain acknowledged_failures as a JSON object';
    END IF;
    acknowledged := config -> 'acknowledged_failures';
    known_names := aggregate_model_names || custom_job_names;

    FOR entry_key, entry_value IN
        SELECT each.key, each.value
        FROM jsonb_each(acknowledged) AS each(key, value)
    LOOP
        IF NOT entry_key = ANY (known_names) THEN
            RAISE EXCEPTION 'acknowledged_failures key % is not a known monitoring overview logical name', entry_key;
        END IF;
        IF jsonb_typeof(entry_value) <> 'number' THEN
            RAISE EXCEPTION 'acknowledged failure count for % must be a nonnegative integer', entry_key;
        END IF;
        entry_count := (entry_value #>> '{}')::NUMERIC;
        IF entry_count IS NULL
           OR entry_count <> floor(entry_count)
           OR entry_count < 0
           OR entry_count > 2147483647 THEN
            RAISE EXCEPTION 'acknowledged failure count for % must be a whole number between 0 and 2147483647', entry_key;
        END IF;
    END LOOP;

    FOR logical_name, is_custom IN
        SELECT aggregate_model, FALSE
        FROM unnest(aggregate_model_names) AS aggregate_model
        UNION ALL
        SELECT job_name, TRUE
        FROM unnest(custom_job_names) AS job_name
    LOOP
        IF is_custom THEN
            SELECT COALESCE(SUM(job_stats.total_failures), 0)
            INTO recorded_failures
            FROM _timescaledb_catalog.bgw_job AS background_job
            JOIN timescaledb_information.job_stats AS job_stats
              ON job_stats.job_id = background_job.id
            WHERE background_job.proc_schema = 'public'
              AND background_job.proc_name = logical_name;
        ELSE
            SELECT COALESCE(SUM(job_stats.total_failures), 0)
            INTO recorded_failures
            FROM timescaledb_information.continuous_aggregates AS cagg
            JOIN _timescaledb_catalog.hypertable AS materialized
              ON materialized.schema_name = cagg.materialization_hypertable_schema
             AND materialized.table_name = cagg.materialization_hypertable_name
            JOIN _timescaledb_catalog.bgw_job AS refresh_job
              ON refresh_job.hypertable_id = materialized.id
             AND refresh_job.proc_name = 'policy_refresh_continuous_aggregate'
             AND refresh_job.proc_schema = '_timescaledb_functions'
            LEFT JOIN timescaledb_information.job_stats AS job_stats
              ON job_stats.job_id = refresh_job.id
            WHERE cagg.view_schema = 'public'
              AND cagg.view_name = logical_name;
        END IF;

        acknowledged_count := COALESCE(
            ((acknowledged -> logical_name) #>> '{}')::NUMERIC, 0);

        IF recorded_failures > acknowledged_count THEN
            RETURN TRUE;
        END IF;
    END LOOP;

    RETURN FALSE;
END
$function$;

-- ------------------------------------------------------------------ §5: coverage table
-- Contract: public.monitoring_overview_coverage(model_name TEXT PRIMARY KEY,
-- covered_from TIMESTAMPTZ NOT NULL, covered_through TIMESTAMPTZ NOT NULL).
-- One verified-contiguous coverage window per overview model name. Rows are
-- written after verified bounded commands only: the supervised backfill's
-- fixture equivalence and the ongoing bounded verified refresh checkpoints
-- following each successful native refresh; a row never precedes native
-- materialization. They are preserved across repeat application.
-- Completion/activation provenance of these sources keeps REUSING
-- public.monitoring_cagg_backfill_marker exactly as created by
-- monitoring_read_models.sql (four columns unchanged): a monitored coverage
-- row is only meaningful together with the marker's supervised backfill
-- record, and no marker or watermark state is altered by this file.
CREATE TABLE IF NOT EXISTS public.monitoring_overview_coverage (
    model_name TEXT PRIMARY KEY,
    covered_from TIMESTAMPTZ NOT NULL,
    covered_through TIMESTAMPTZ NOT NULL
);

DO $overview_coverage_shape$
BEGIN
    PERFORM monitoring_assert_relation_columns(
        'monitoring_overview_coverage',
        ARRAY['model_name', 'covered_from', 'covered_through']::TEXT[],
        ARRAY['text', 'timestamp with time zone', 'timestamp with time zone']::TEXT[]
    );

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.monitoring_overview_coverage'::regclass
          AND contype = 'p'
          AND array_length(conkey, 1) = 1
          AND (SELECT attname
               FROM pg_attribute
               WHERE attrelid = 'public.monitoring_overview_coverage'::regclass
                 AND attnum = conkey[1]) = 'model_name'
    ) THEN
        RAISE EXCEPTION 'public.monitoring_overview_coverage drifted from its model_name primary key; no repair performed';
    END IF;
END
$overview_coverage_shape$;

-- ------------------------------------------------------------------ §6: mergeable hourly/daily summary sources
-- Measurement sources mirror the existing 1min/5min measurement aggregates
-- exactly (same columns, types and value aggregations) at hourly/day width.
CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_measurement_1hour
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 hour', time) AS bucket,
    sensor_id,
    count(value) AS sample_count,
    sum(value::NUMERIC) AS value_sum,
    sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
    min(value::NUMERIC) AS min_value,
    max(value::NUMERIC) AS max_value,
    max(time) AS last_observed_at
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_measurement_1day
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
    hourly.sensor_id,
    sum(hourly.sample_count)::BIGINT AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
FROM monitoring_measurement_1hour AS hourly
GROUP BY 1, hourly.sensor_id
WITH NO DATA;

-- Light sources summarize the exact recorded-light facts of the detailed
-- light query (_LIGHT_SETPOINTS_SQL): device-attributable rows where any of
-- effective_light_intensity / nominal_light_intensity / ramp_progress_light
-- is recorded. sample_count counts non-NULL effective intensities, so rows
-- that only carry nominal/ramp recordings remain explicit count 0 samples.
-- These are intensity summaries, never reconstructed ramps, watts or energy.
CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_light_intensity_1hour
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    count(effective_light_intensity) AS sample_count,
    sum(effective_light_intensity::NUMERIC) AS value_sum,
    sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS value_sum_squares,
    min(effective_light_intensity::NUMERIC) AS min_value,
    max(effective_light_intensity::NUMERIC) AS max_value,
    max(timestamp) AS last_observed_at
FROM effective_setpoints
WHERE device_name IS NOT NULL
  AND (effective_light_intensity IS NOT NULL
       OR nominal_light_intensity IS NOT NULL
       OR ramp_progress_light IS NOT NULL)
GROUP BY bucket, location, cluster, device_name
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_light_intensity_1day
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    sum(hourly.sample_count)::BIGINT AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
FROM monitoring_light_intensity_1hour AS hourly
GROUP BY 1, hourly.location, hourly.cluster, hourly.device_name
WITH NO DATA;

-- Requested PID sources summarize the recorded requested-fraction samples
-- with the exact `_requested_percent` semantics: a raw REAL value counts
-- exactly once iff finite and inside 0..1, its recorded percent is
-- pid_output::DOUBLE PRECISION * 100 stored as NUMERIC statistics, and rows
-- without a valid requested output stay part of sample_count but never
-- contribute requested samples. Relay state, duty or power are never derived
-- from these values.
CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_requested_pid_1hour
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    count(*) AS sample_count,
    count(pid_output) FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                              AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_count,
    sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
        FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum,
    sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC
        * (pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
        FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum_squares,
    min((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
        FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_min,
    max((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
        FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_max,
    max(timestamp) AS last_observed_at
FROM automation_state
GROUP BY bucket, location, cluster, device_name
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_requested_pid_1day
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    sum(hourly.sample_count)::BIGINT AS sample_count,
    sum(hourly.requested_count)::BIGINT AS requested_count,
    sum(hourly.requested_sum) AS requested_sum,
    sum(hourly.requested_sum_squares) AS requested_sum_squares,
    min(hourly.requested_min) AS requested_min,
    max(hourly.requested_max) AS requested_max,
    max(hourly.last_observed_at) AS last_observed_at
FROM monitoring_requested_pid_1hour AS hourly
GROUP BY 1, hourly.location, hourly.cluster, hourly.device_name
WITH NO DATA;

-- ------------------------------------------------------------------ §7: exact exported source contracts
DO $overview_source_columns$
BEGIN
    PERFORM monitoring_assert_relation_columns(
        'monitoring_measurement_1hour',
        ARRAY['bucket', 'sensor_id', 'sample_count', 'value_sum', 'value_sum_squares',
              'min_value', 'max_value', 'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'integer', 'bigint', 'numeric', 'numeric',
              'numeric', 'numeric', 'timestamp with time zone']::TEXT[]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_measurement_1day',
        ARRAY['bucket', 'sensor_id', 'sample_count', 'value_sum', 'value_sum_squares',
              'min_value', 'max_value', 'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'integer', 'bigint', 'numeric', 'numeric',
              'numeric', 'numeric', 'timestamp with time zone']::TEXT[]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_light_intensity_1hour',
        ARRAY['bucket', 'location', 'cluster', 'device_name', 'sample_count',
              'value_sum', 'value_sum_squares', 'min_value', 'max_value',
              'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'text', 'text', 'text', 'bigint',
              'numeric', 'numeric', 'numeric', 'numeric', 'timestamp with time zone']::TEXT[]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_light_intensity_1day',
        ARRAY['bucket', 'location', 'cluster', 'device_name', 'sample_count',
              'value_sum', 'value_sum_squares', 'min_value', 'max_value',
              'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'text', 'text', 'text', 'bigint',
              'numeric', 'numeric', 'numeric', 'numeric', 'timestamp with time zone']::TEXT[]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_requested_pid_1hour',
        ARRAY['bucket', 'location', 'cluster', 'device_name', 'sample_count',
              'requested_count', 'requested_sum', 'requested_sum_squares',
              'requested_min', 'requested_max', 'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'text', 'text', 'text', 'bigint', 'bigint',
              'numeric', 'numeric', 'numeric', 'numeric',
              'timestamp with time zone']::TEXT[]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_requested_pid_1day',
        ARRAY['bucket', 'location', 'cluster', 'device_name', 'sample_count',
              'requested_count', 'requested_sum', 'requested_sum_squares',
              'requested_min', 'requested_max', 'last_observed_at']::TEXT[],
        ARRAY['timestamp with time zone', 'text', 'text', 'text', 'bigint', 'bigint',
              'numeric', 'numeric', 'numeric', 'numeric',
              'timestamp with time zone']::TEXT[]
    );
END
$overview_source_columns$;

-- ------------------------------------------------------------------ §8: identity-leading group indexes (new aggregates only)
CREATE INDEX IF NOT EXISTS monitoring_measurement_1hour_group_idx
    ON monitoring_measurement_1hour (sensor_id, bucket);
CREATE INDEX IF NOT EXISTS monitoring_measurement_1day_group_idx
    ON monitoring_measurement_1day (sensor_id, bucket);
CREATE INDEX IF NOT EXISTS monitoring_light_intensity_1hour_group_idx
    ON monitoring_light_intensity_1hour (location, cluster, device_name, bucket);
CREATE INDEX IF NOT EXISTS monitoring_light_intensity_1day_group_idx
    ON monitoring_light_intensity_1day (location, cluster, device_name, bucket);
CREATE INDEX IF NOT EXISTS monitoring_requested_pid_1hour_group_idx
    ON monitoring_requested_pid_1hour (location, cluster, device_name, bucket);
CREATE INDEX IF NOT EXISTS monitoring_requested_pid_1day_group_idx
    ON monitoring_requested_pid_1day (location, cluster, device_name, bucket);

DO $overview_index_guard$
BEGIN
    IF (
        SELECT count(*)
        FROM (VALUES
            ('monitoring_measurement_1hour_group_idx',
             'monitoring_measurement_1hour',
             ARRAY['sensor_id', 'bucket']::TEXT[]),
            ('monitoring_measurement_1day_group_idx',
             'monitoring_measurement_1day',
             ARRAY['sensor_id', 'bucket']::TEXT[]),
            ('monitoring_light_intensity_1hour_group_idx',
             'monitoring_light_intensity_1hour',
             ARRAY['location', 'cluster', 'device_name', 'bucket']::TEXT[]),
            ('monitoring_light_intensity_1day_group_idx',
             'monitoring_light_intensity_1day',
             ARRAY['location', 'cluster', 'device_name', 'bucket']::TEXT[]),
            ('monitoring_requested_pid_1hour_group_idx',
             'monitoring_requested_pid_1hour',
             ARRAY['location', 'cluster', 'device_name', 'bucket']::TEXT[]),
            ('monitoring_requested_pid_1day_group_idx',
             'monitoring_requested_pid_1day',
             ARRAY['location', 'cluster', 'device_name', 'bucket']::TEXT[])
        ) AS desired(index_name, cagg_name, index_columns)
        WHERE EXISTS (
            SELECT 1
            FROM _timescaledb_catalog.continuous_agg AS cagg
            JOIN _timescaledb_catalog.hypertable AS materialized
              ON materialized.id = cagg.mat_hypertable_id
            JOIN pg_class materialized_relation
              ON materialized_relation.relname = materialized.table_name
             AND materialized_relation.relnamespace = (
                 SELECT oid FROM pg_namespace
                 WHERE nspname = materialized.schema_name)
            JOIN pg_index i ON i.indrelid = materialized_relation.oid
            JOIN pg_class index_class ON index_class.oid = i.indexrelid
            WHERE cagg.user_view_schema = 'public'
              AND cagg.user_view_name = desired.cagg_name
              AND index_class.relname = desired.index_name
              AND i.indisvalid
              AND (SELECT array_agg(a.attname::TEXT ORDER BY k.ordinality)
                   FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                   JOIN pg_attribute a
                     ON a.attrelid = i.indrelid AND a.attnum = k.attnum)
                  = desired.index_columns
        )) <> 6 THEN
        RAISE EXCEPTION 'overview group indexes drifted from the planned definitions; no repair performed';
    END IF;
END
$overview_index_guard$;

-- ------------------------------------------------------------------ §9: materialized-only hierarchy and source identity
-- Each hourly source must aggregate exactly its declared raw hypertable, and
-- each daily source must be a child of exactly its hourly parent.
DO $overview_materialization_guard$
DECLARE
    source_hypertable_id INTEGER;
    hourly_record RECORD;
    daily_record RECORD;
BEGIN
    IF (SELECT count(*)
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name IN (
              'monitoring_measurement_1hour',
              'monitoring_measurement_1day',
              'monitoring_light_intensity_1hour',
              'monitoring_light_intensity_1day',
              'monitoring_requested_pid_1hour',
              'monitoring_requested_pid_1day'
          )
       ) <> 6 THEN
        RAISE EXCEPTION 'monitoring overview continuous aggregate catalog is incomplete';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name IN (
              'monitoring_measurement_1hour',
              'monitoring_measurement_1day',
              'monitoring_light_intensity_1hour',
              'monitoring_light_intensity_1day',
              'monitoring_requested_pid_1hour',
              'monitoring_requested_pid_1day'
          )
          AND NOT materialized_only
    ) THEN
        RAISE EXCEPTION 'monitoring overview continuous aggregates must be materialized-only';
    END IF;

    -- measurement: raw measurement -> hourly -> daily
    SELECT id INTO source_hypertable_id
    FROM _timescaledb_catalog.hypertable
    WHERE schema_name = 'public' AND table_name = 'measurement';
    SELECT mat_hypertable_id, parent_mat_hypertable_id, raw_hypertable_id
    INTO hourly_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_measurement_1hour';
    SELECT parent_mat_hypertable_id, raw_hypertable_id
    INTO daily_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_measurement_1day';

    IF source_hypertable_id IS NULL OR hourly_record IS NULL OR daily_record IS NULL
       OR hourly_record.parent_mat_hypertable_id IS NOT NULL
       OR hourly_record.raw_hypertable_id IS DISTINCT FROM source_hypertable_id
       OR daily_record.parent_mat_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id
       OR daily_record.raw_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id THEN
        RAISE EXCEPTION 'monitoring measurement overview aggregate hierarchy drifted';
    END IF;

    -- light: raw effective_setpoints -> hourly -> daily
    SELECT id INTO source_hypertable_id
    FROM _timescaledb_catalog.hypertable
    WHERE schema_name = 'public' AND table_name = 'effective_setpoints';
    SELECT mat_hypertable_id, parent_mat_hypertable_id, raw_hypertable_id
    INTO hourly_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_light_intensity_1hour';
    SELECT parent_mat_hypertable_id, raw_hypertable_id
    INTO daily_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_light_intensity_1day';

    IF source_hypertable_id IS NULL OR hourly_record IS NULL OR daily_record IS NULL
       OR hourly_record.parent_mat_hypertable_id IS NOT NULL
       OR hourly_record.raw_hypertable_id IS DISTINCT FROM source_hypertable_id
       OR daily_record.parent_mat_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id
       OR daily_record.raw_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id THEN
        RAISE EXCEPTION 'monitoring light intensity overview aggregate hierarchy drifted';
    END IF;

    -- requested pid: raw automation_state -> hourly -> daily
    SELECT id INTO source_hypertable_id
    FROM _timescaledb_catalog.hypertable
    WHERE schema_name = 'public' AND table_name = 'automation_state';
    SELECT mat_hypertable_id, parent_mat_hypertable_id, raw_hypertable_id
    INTO hourly_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_requested_pid_1hour';
    SELECT parent_mat_hypertable_id, raw_hypertable_id
    INTO daily_record
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public' AND user_view_name = 'monitoring_requested_pid_1day';

    IF source_hypertable_id IS NULL OR hourly_record IS NULL OR daily_record IS NULL
       OR hourly_record.parent_mat_hypertable_id IS NOT NULL
       OR hourly_record.raw_hypertable_id IS DISTINCT FROM source_hypertable_id
       OR daily_record.parent_mat_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id
       OR daily_record.raw_hypertable_id IS DISTINCT FROM hourly_record.mat_hypertable_id THEN
        RAISE EXCEPTION 'monitoring requested pid overview aggregate hierarchy drifted';
    END IF;
END
$overview_materialization_guard$;

-- ------------------------------------------------------------------ §10: paused one-bucket refresh policies
-- Hourly sources: five-minute schedule, end_offset one hour (source width of
-- one hourly bucket). Daily sources: hourly schedule, end_offset one day
-- (source width of one daily bucket). start_offset NULL keeps the entire
-- invalidated history eligible; the current partial bucket is therefore never
-- materialized, and mergeable backfill stays under owner control. Created jobs
-- are paused with max_runtime 60 seconds and max_retries 0. Repeat
-- application verifies these structural settings and preserves owner-edited
-- scheduled/enabled state; a drifted job is refused, never repaired.
DO $overview_policy_setup$
DECLARE
    policy_spec RECORD;
    materialization_id INTEGER;
    refresh_job_count INTEGER;
    job_id INTEGER;
    job_schedule INTERVAL;
    job_runtime INTERVAL;
    job_retries INTEGER;
    job_config JSONB;
    new_job_id INTEGER;
BEGIN
    FOR policy_spec IN
        SELECT cagg_name, schedule_interval, end_offset FROM (VALUES
            ('monitoring_measurement_1hour',     INTERVAL '5 minutes', INTERVAL '1 hour'),
            ('monitoring_measurement_1day',      INTERVAL '1 hour',    INTERVAL '1 day'),
            ('monitoring_light_intensity_1hour', INTERVAL '5 minutes', INTERVAL '1 hour'),
            ('monitoring_light_intensity_1day',  INTERVAL '1 hour',    INTERVAL '1 day'),
            ('monitoring_requested_pid_1hour',   INTERVAL '5 minutes', INTERVAL '1 hour'),
            ('monitoring_requested_pid_1day',    INTERVAL '1 hour',    INTERVAL '1 day')
        ) AS spec(cagg_name, schedule_interval, end_offset)
    LOOP
        SELECT mat_hypertable_id
        INTO materialization_id
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name = policy_spec.cagg_name;

        IF materialization_id IS NULL THEN
            RAISE EXCEPTION 'continuous aggregate % is missing from the catalog', policy_spec.cagg_name;
        END IF;

        SELECT count(*)
        INTO refresh_job_count
        FROM _timescaledb_catalog.bgw_job
        WHERE hypertable_id = materialization_id
          AND proc_name = 'policy_refresh_continuous_aggregate'
          AND proc_schema = '_timescaledb_functions';

        IF refresh_job_count > 1 THEN
            RAISE EXCEPTION 'multiple refresh policies for %; refusing to reconcile', policy_spec.cagg_name;
        END IF;

        IF refresh_job_count = 1 THEN
            SELECT id, schedule_interval, max_runtime, max_retries, config
            INTO job_id, job_schedule, job_runtime, job_retries, job_config
            FROM _timescaledb_catalog.bgw_job
            WHERE hypertable_id = materialization_id
              AND proc_name = 'policy_refresh_continuous_aggregate'
              AND proc_schema = '_timescaledb_functions';

            IF jsonb_typeof(job_config) IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION 'refresh job config for % drifted from a JSONB object; no repair performed', policy_spec.cagg_name;
            END IF;

            IF job_schedule IS DISTINCT FROM policy_spec.schedule_interval
               OR job_runtime IS DISTINCT FROM INTERVAL '60 seconds'
               OR job_retries IS DISTINCT FROM 0 THEN
                RAISE EXCEPTION 'refresh job settings for % drifted from the paused overview contract; no repair performed', policy_spec.cagg_name;
            END IF;

            IF (job_config -> 'start_offset') IS NULL
               OR jsonb_typeof(job_config -> 'start_offset') <> 'null'
               OR (job_config -> 'end_offset') IS NULL
               OR jsonb_typeof(job_config -> 'end_offset') <> 'string'
               OR (job_config -> 'buckets_per_batch') IS NULL
               OR jsonb_typeof(job_config -> 'buckets_per_batch') <> 'number'
               OR (job_config -> 'max_batches_per_execution') IS NULL
               OR jsonb_typeof(job_config -> 'max_batches_per_execution') <> 'number'
               OR (job_config -> 'refresh_newest_first') IS NULL
               OR jsonb_typeof(job_config -> 'refresh_newest_first') <> 'boolean'
               OR (job_config ->> 'end_offset')::INTERVAL IS DISTINCT FROM policy_spec.end_offset
               OR (job_config ->> 'buckets_per_batch')::INTEGER IS DISTINCT FROM 1
               OR (job_config ->> 'max_batches_per_execution')::INTEGER IS DISTINCT FROM 1
               OR (job_config -> 'refresh_newest_first') IS DISTINCT FROM 'false'::JSONB
               OR job_config ? 'include_tiered_data'
               OR job_config ? 'compress_after_refresh' THEN
                RAISE EXCEPTION 'refresh policy config for % drifted from the paused one-bucket overview contract; no repair performed', policy_spec.cagg_name;
            END IF;
        ELSE
            SELECT add_continuous_aggregate_policy(
                format('public.%I', policy_spec.cagg_name)::regclass,
                start_offset => NULL::INTERVAL,
                end_offset => policy_spec.end_offset,
                schedule_interval => policy_spec.schedule_interval,
                buckets_per_batch => 1,
                max_batches_per_execution => 1,
                refresh_newest_first => FALSE,
                if_not_exists => FALSE)
            INTO new_job_id;

            IF new_job_id IS NULL OR new_job_id < 1 THEN
                RAISE EXCEPTION 'failed to create the paused refresh policy for %', policy_spec.cagg_name;
            END IF;

            PERFORM alter_job(new_job_id,
                max_runtime => INTERVAL '60 seconds',
                max_retries => 0,
                scheduled => FALSE);
        END IF;
    END LOOP;
END
$overview_policy_setup$;

-- ------------------------------------------------------------------ §10.5: ongoing bounded verified refresh checkpoints
-- Each native PAUSED one-bucket refresh policy (§10) advances its own
-- hourly/daily materialization; the supervised backfill flow (re)seeds the
-- coverage rows in public.monitoring_overview_coverage for all six models.
-- This procedure is the ONLY ongoing advancing writer for those six model
-- rows after seeding: one invocation can advance the HOURLY column of one
-- family by exactly one complete UTC hour and the DAILY column of the same
-- family by exactly one full UTC day, each only after its own verification
-- lands, with all reads and all coverage writes inside ONE atomic
-- checkpoint transaction that rolls the whole candidate window back on any
-- error or cancellation (missing/cancel/failure can never advance).
-- Exit paths that find nothing to verify release the family advisory lock
-- and commit once — a no-op invocation never leaks its lock.
--
-- Verification per candidate window:
--   * the hourly frontier must be hour-aligned and the daily frontier
--     day-aligned; drift raises and rolls back instead of being repaired;
--   * the native materialization watermark must already cover the
--     candidate's end and NO pending native invalidation may overlap the
--     candidate window (checked for the hourly tier against the hourly
--     cagg and for the daily tier against both the daily cagg and the
--     hourly cagg of the day it consumes), so each candidate window is
--     proven freshly materialized, not merely present;
--   * the candidate window's rows equal the same facts recomputed
--     directly from the raw hypertable with per-hour bucket keys
--     (symmetric EXCEPT ALL) for the hourly tier, and the daily tier's
--     rows equal the rollup of those verified hourly rows keyed by the
--     day bucket;
--   * candidate ends stay settled: an hourly candidate must end at
--     least two hours ago (the policy's end_offset one hour plus refresh
--     lag documented in §6) and a daily candidate day at least 26 hours
--     ago (one extra day plus that same lag), so the current partial
--     buckets are never claimed and the parent's bounded raw-read tail is
--     never raced ahead of by this checkpoint.
--
-- The daily tier intentionally stays available only for days its hourly
-- data has already verified (the daily candidate's end cannot pass the
-- hourly frontier of the previous pass), so a paused daily policy stalls
-- ONLY the daily column while the hourly column keeps advancing.
--
-- The family comes from the job's own config (canonical TimescaleDB
-- custom-job signature (job_id, config)): at rest (config disabled or
-- scheduled=false) a manual run_job is a silent no-op; an owner-enabled
-- config is required before any verification runs.
CREATE OR REPLACE PROCEDURE public.monitoring_refresh_overview_incremental(
    job_id  INTEGER,
    config  JSONB
)
LANGUAGE plpgsql
SECURITY INVOKER
AS $procedure$
DECLARE
    v_cfg            JSONB   := COALESCE(config, '{}'::JSONB);
    v_enabled        BOOLEAN := FALSE;
    v_no_work        BOOLEAN := FALSE;
    v_family         TEXT    := COALESCE(v_cfg ->> 'family', ''::TEXT);
    v_locked         BOOLEAN;
    v_lock_key       BIGINT;
    v_hour_view      TEXT;
    v_day_view       TEXT;
    v_hour_cvg_from  TIMESTAMPTZ;
    v_hour_front     TIMESTAMPTZ;
    v_day_cvg_from   TIMESTAMPTZ;
    v_day_front      TIMESTAMPTZ;
    v_hour_cand_end  TIMESTAMPTZ;
    v_day_cand_end   TIMESTAMPTZ;
    v_hour_ready     BOOLEAN;
    v_hour_parity    BOOLEAN;
    v_day_ready      BOOLEAN;
    v_day_parity     BOOLEAN;
    v_fault_sqlstate TEXT;
    v_fault_message  TEXT;
BEGIN
    IF job_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs AS owned
        WHERE owned.job_id = monitoring_refresh_overview_incremental.job_id
          AND owned.proc_schema = 'public'
          AND owned.proc_name = 'monitoring_refresh_overview_incremental'
          AND owned.config ->> 'family' = v_family
    ) THEN
        RAISE EXCEPTION 'checkpoint job identity does not match family %', v_family;
    END IF;
    -----------------------------------------------------------------
    -- 0. Owner enable flag: silent no-op when disabled (manual run_job
    --    ignores scheduled=false, so ONLY the config gate keeps paused
    --    jobs from verifying).
    -----------------------------------------------------------------
    IF jsonb_typeof(v_cfg -> 'enabled') = 'boolean' THEN
        v_enabled := (v_cfg -> 'enabled')::BOOLEAN;
    END IF;
    IF v_enabled IS NOT TRUE THEN
        v_no_work := TRUE;
    END IF;

    IF v_no_work IS NOT TRUE THEN
        IF v_family IS NOT DISTINCT FROM 'measurement' THEN
            v_hour_view := 'monitoring_measurement_1hour';
            v_day_view  := 'monitoring_measurement_1day';
        ELSIF v_family IS NOT DISTINCT FROM 'light_intensity' THEN
            v_hour_view := 'monitoring_light_intensity_1hour';
            v_day_view  := 'monitoring_light_intensity_1day';
        ELSIF v_family IS NOT DISTINCT FROM 'requested_pid' THEN
            v_hour_view := 'monitoring_requested_pid_1hour';
            v_day_view  := 'monitoring_requested_pid_1day';
        ELSE
            RAISE EXCEPTION 'unknown monitoring overview refresh family: %', v_family;
        END IF;
    END IF;

    -----------------------------------------------------------------
    -- 1. Failure gate (shared helper): unacknowledged job failures pause
    --    ONLY this family's job (owner config preserved) and re-raise so
    --    the scheduler keeps the refusal evidence.
    -----------------------------------------------------------------
    IF v_no_work IS NOT TRUE THEN
        IF public.monitoring_overview_unacknowledged_failures(v_cfg) THEN
            IF job_id IS NOT NULL THEN
                PERFORM alter_job(job_id, scheduled => FALSE);
            END IF;
            COMMIT;
            RAISE EXCEPTION
                'monitoring_refresh_overview_incremental: unacknowledged job failures for %; paused pending owner acknowledgement', v_family;
        END IF;

        -----------------------------------------------------------------
        -- 2. Exclusive session advisory lock per family: concurrent
        --    invocations of the same family cannot verify the same
        --    candidate day at different instants, so a late insert can
        --    never be exposed to one verifier's stale snapshot while
        --    another succeeds. If it is taken the invocation leaves as a
        --    silent no-op (v_no_work) without acquiring the failure gate.
        -----------------------------------------------------------------
        v_lock_key := hashtextextended(
            'projectcea.overview-incremental-verify-' || v_family, 0);
        v_locked := pg_try_advisory_lock(v_lock_key);
        IF NOT v_locked THEN
            v_no_work := TRUE;  -- another invocation owns this checkpoint
        END IF;
    END IF;

    -----------------------------------------------------------------
    -- 3. The atomic verification transaction: candidates, native
    --    watermark/invalidation state, raw source parity, daily rollup
    --    parity and BOTH coverage writes all commit together; any error,
    --    cancellation or failed guard rolls the entire transaction back.
    -----------------------------------------------------------------
    IF v_no_work IS NOT TRUE THEN
        SET LOCAL lock_timeout = '1s';
        BEGIN
            SELECT covered_from, covered_through
              INTO v_hour_cvg_from, v_hour_front
              FROM public.monitoring_overview_coverage
             WHERE model_name = v_hour_view;

            SELECT covered_from, covered_through
              INTO v_day_cvg_from, v_day_front
              FROM public.monitoring_overview_coverage
             WHERE model_name = v_day_view;

            IF v_hour_cvg_from IS NULL THEN
                RAISE EXCEPTION 'missing coverage row for %', v_hour_view;
            END IF;
            IF v_day_cvg_from IS NULL THEN
                RAISE EXCEPTION 'missing coverage row for %', v_day_view;
            END IF;

            -----------------------------------------------------------------
            -- Hourly candidate = [v_hour_front, + 1 hour); daily candidate
            -- = [v_day_front, + 1 day). Hourly checkpoints must keep the
            -- frontier inside the reader's bounded raw tail at midnight too.
            -----------------------------------------------------------------
            IF v_hour_front IS DISTINCT FROM
                   date_trunc('hour', v_hour_front AT TIME ZONE 'UTC')
                       AT TIME ZONE 'UTC' THEN
                RAISE EXCEPTION 'verified coverage for % is not hour-aligned', v_hour_view;
            END IF;
            IF v_day_front IS DISTINCT FROM
                   date_trunc('day', v_day_front AT TIME ZONE 'UTC')
                       AT TIME ZONE 'UTC' THEN
                RAISE EXCEPTION 'verified coverage for % is not day-aligned', v_day_view;
            END IF;

            v_hour_cand_end := v_hour_front + INTERVAL '1 hour';
            IF v_hour_cand_end > now() - INTERVAL '2 hours' THEN
                v_no_work := TRUE;  -- nothing settled on the hourly tier
            END IF;

            -- Hourly tier's candidate day native freshness.
            v_hour_ready := FALSE;
            IF v_no_work IS NOT TRUE THEN
                v_hour_ready := EXISTS (
                    SELECT 1
                      FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = v_hour_view
                       AND materialization_watermark >= v_hour_cand_end
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = v_hour_view
                       AND invalidation_start < v_hour_cand_end
                       AND invalidation_end >= v_hour_front
                );
            END IF;

            IF v_hour_ready THEN
                v_hour_parity := NULL;

                -----------------------------------------------------------------
                -- 3b. Hourly raw parity: the candidate day's hourly rows
                --     must equal the same facts recomputed from the raw
                --     hypertable, and vice versa (symmetric EXCEPT ALL
                --     over identical bucket/identity/value column lists).
                --     The bucket keys prove the hour-wise row identity,
                --     not just aggregate totals across the day. Each
                --     branch mirrors its §6 view definition exactly.
                -----------------------------------------------------------------

                IF v_family IS NOT DISTINCT FROM 'measurement' THEN
                    v_hour_parity := NOT EXISTS (
                        (SELECT hour.bucket, hour.sensor_id, hour.sample_count,
                                hour.value_sum, hour.value_sum_squares,
                                hour.min_value, hour.max_value,
                                hour.last_observed_at
                           FROM public.monitoring_measurement_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end
                         EXCEPT ALL
                         SELECT hour_raw.bucket, hour_raw.sensor_id,
                                hour_raw.sample_count, hour_raw.value_sum,
                                hour_raw.value_sum_squares, hour_raw.min_value,
                                hour_raw.max_value, hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
                                      sensor_id,
                                      count(value) AS sample_count,
                                      sum(value::NUMERIC) AS value_sum,
                                      sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
                                      min(value::NUMERIC) AS min_value,
                                      max(value::NUMERIC) AS max_value,
                                      max(time) AS last_observed_at
                                 FROM public.measurement
                                WHERE time >= v_hour_front
                                  AND time < v_hour_cand_end
                                GROUP BY 1, 2
                           ) AS hour_raw)
                        UNION ALL
                        (SELECT hour_raw.bucket, hour_raw.sensor_id,
                                hour_raw.sample_count, hour_raw.value_sum,
                                hour_raw.value_sum_squares, hour_raw.min_value,
                                hour_raw.max_value, hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
                                      sensor_id,
                                      count(value) AS sample_count,
                                      sum(value::NUMERIC) AS value_sum,
                                      sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
                                      min(value::NUMERIC) AS min_value,
                                      max(value::NUMERIC) AS max_value,
                                      max(time) AS last_observed_at
                                 FROM public.measurement
                                WHERE time >= v_hour_front
                                  AND time < v_hour_cand_end
                                GROUP BY 1, 2
                           ) AS hour_raw
                         EXCEPT ALL
                         SELECT hour.bucket, hour.sensor_id, hour.sample_count,
                                hour.value_sum, hour.value_sum_squares,
                                hour.min_value, hour.max_value,
                                hour.last_observed_at
                           FROM public.monitoring_measurement_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end)
                    );
                ELSIF v_family IS NOT DISTINCT FROM 'light_intensity' THEN
                    v_hour_parity := NOT EXISTS (
                        (SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name, hour.sample_count,
                                hour.value_sum, hour.value_sum_squares,
                                hour.min_value, hour.max_value,
                                hour.last_observed_at
                           FROM public.monitoring_light_intensity_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end
                         EXCEPT ALL
                         SELECT hour_raw.bucket, hour_raw.location,
                                hour_raw.cluster, hour_raw.device_name,
                                hour_raw.sample_count, hour_raw.value_sum,
                                hour_raw.value_sum_squares, hour_raw.min_value,
                                hour_raw.max_value, hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
                                      location, cluster, device_name,
                                      count(effective_light_intensity) AS sample_count,
                                      sum(effective_light_intensity::NUMERIC) AS value_sum,
                                      sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS value_sum_squares,
                                      min(effective_light_intensity::NUMERIC) AS min_value,
                                      max(effective_light_intensity::NUMERIC) AS max_value,
                                      max(timestamp) AS last_observed_at
                                 FROM public.effective_setpoints
                                WHERE device_name IS NOT NULL
                                  AND (effective_light_intensity IS NOT NULL
                                       OR nominal_light_intensity IS NOT NULL
                                       OR ramp_progress_light IS NOT NULL)
                                  AND timestamp >= v_hour_front
                                  AND timestamp < v_hour_cand_end
                                GROUP BY 1, 2, 3, 4
                           ) AS hour_raw)
                        UNION ALL
                        (SELECT hour_raw.bucket, hour_raw.location,
                                hour_raw.cluster, hour_raw.device_name,
                                hour_raw.sample_count, hour_raw.value_sum,
                                hour_raw.value_sum_squares, hour_raw.min_value,
                                hour_raw.max_value, hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
                                      location, cluster, device_name,
                                      count(effective_light_intensity) AS sample_count,
                                      sum(effective_light_intensity::NUMERIC) AS value_sum,
                                      sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS value_sum_squares,
                                      min(effective_light_intensity::NUMERIC) AS min_value,
                                      max(effective_light_intensity::NUMERIC) AS max_value,
                                      max(timestamp) AS last_observed_at
                                 FROM public.effective_setpoints
                                WHERE device_name IS NOT NULL
                                  AND (effective_light_intensity IS NOT NULL
                                       OR nominal_light_intensity IS NOT NULL
                                       OR ramp_progress_light IS NOT NULL)
                                  AND timestamp >= v_hour_front
                                  AND timestamp < v_hour_cand_end
                                GROUP BY 1, 2, 3, 4
                           ) AS hour_raw
                         EXCEPT ALL
                         SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name, hour.sample_count,
                                hour.value_sum, hour.value_sum_squares,
                                hour.min_value, hour.max_value,
                                hour.last_observed_at
                           FROM public.monitoring_light_intensity_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end)
                    );
                ELSE  -- requested_pid
                    v_hour_parity := NOT EXISTS (
                        (SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name, hour.sample_count,
                                hour.requested_count, hour.requested_sum,
                                hour.requested_sum_squares, hour.requested_min,
                                hour.requested_max, hour.last_observed_at
                           FROM public.monitoring_requested_pid_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end
                         EXCEPT ALL
                         SELECT hour_raw.bucket, hour_raw.location,
                                hour_raw.cluster, hour_raw.device_name,
                                hour_raw.sample_count, hour_raw.requested_count,
                                hour_raw.requested_sum,
                                hour_raw.requested_sum_squares,
                                hour_raw.requested_min, hour_raw.requested_max,
                                hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
                                      location, cluster, device_name,
                                      count(*) AS sample_count,
                                      count(pid_output) FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_count,
                                      sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum,
                                      sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC
                                          * (pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum_squares,
                                      min((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_min,
                                      max((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_max,
                                      max(timestamp) AS last_observed_at
                                 FROM public.automation_state
                                WHERE timestamp >= v_hour_front
                                  AND timestamp < v_hour_cand_end
                                GROUP BY 1, 2, 3, 4
                           ) AS hour_raw)
                        UNION ALL
                        (SELECT hour_raw.bucket, hour_raw.location,
                                hour_raw.cluster, hour_raw.device_name,
                                hour_raw.sample_count, hour_raw.requested_count,
                                hour_raw.requested_sum,
                                hour_raw.requested_sum_squares,
                                hour_raw.requested_min, hour_raw.requested_max,
                                hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
                                      location, cluster, device_name,
                                      count(*) AS sample_count,
                                      count(pid_output) FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_count,
                                      sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum,
                                      sum((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC
                                          * (pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_sum_squares,
                                      min((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_min,
                                      max((pid_output::DOUBLE PRECISION * 100.0::DOUBLE PRECISION)::NUMERIC)
                                          FILTER (
                                          WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                            AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_max,
                                      max(timestamp) AS last_observed_at
                                 FROM public.automation_state
                                WHERE timestamp >= v_hour_front
                                  AND timestamp < v_hour_cand_end
                                GROUP BY 1, 2, 3, 4
                           ) AS hour_raw
                         EXCEPT ALL
                         SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name, hour.sample_count,
                                hour.requested_count, hour.requested_sum,
                                hour.requested_sum_squares, hour.requested_min,
                                hour.requested_max, hour.last_observed_at
                           FROM public.monitoring_requested_pid_1hour AS hour
                          WHERE hour.bucket >= v_hour_front
                            AND hour.bucket < v_hour_cand_end)
                    );
                END IF;

                IF v_hour_parity IS TRUE THEN
                    -- The verified-frontier advance ONLY when every check
                    -- above landed; this UPDATE stays inside the same
                    -- atomic transaction and commits together with any
                    -- daily write below.
                    UPDATE public.monitoring_overview_coverage
                       SET covered_through = v_hour_cand_end
                     WHERE model_name = v_hour_view;
                END IF;
            END IF;

            -----------------------------------------------------------------
            -- 3c. Daily candidate day = [v_day_front, + 1 day). Its end
            --     may not run past the hourly frontier of the PREVIOUS
            --     pass (the hourly data it consumes must already be
            --     verified), must sit at least 26 hours behind now(),
            --     must be covered by the native DAILY watermark with no
            --     pending DAILY invalidation, and the HOURLY tier inside
            --     that window must still show a clean materialization
            --     with no pending hourly invalidation. The parity compare
            --     below branches on the concrete family pairs
            --     (measurement/light_intensity/requested_pid) with
            --     bucket keys proving the day-hour-rollup row identity.
            -----------------------------------------------------------------
            v_day_cand_end := v_day_front + INTERVAL '1 day';
            v_day_ready := v_day_cand_end <= v_hour_front
                AND v_day_cand_end <= now() - INTERVAL '26 hours'
                AND EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = v_day_view
                       AND materialization_watermark >= v_day_cand_end
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = v_day_view
                       AND invalidation_start < v_day_cand_end
                       AND invalidation_end >= v_day_front
                );

            IF v_day_ready THEN
                IF EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = v_hour_view
                       AND invalidation_start < v_day_cand_end
                       AND invalidation_end >= v_day_front
                ) THEN
                    v_day_ready := FALSE;  -- hourly freshness of the day pending
                END IF;
            END IF;

            IF v_day_ready THEN
                v_day_parity := NULL;

                IF v_family IS NOT DISTINCT FROM 'measurement' THEN
                    v_day_parity := NOT EXISTS (
                        (SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.sensor_id, sum(day.sample_count)::BIGINT,
                                sum(day.value_sum), sum(day.value_sum_squares),
                                min(day.min_value), max(day.max_value),
                                max(day.last_observed_at)
                           FROM public.monitoring_measurement_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2
                         EXCEPT ALL
                         SELECT hour.bucket, hour.sensor_id,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.value_sum), sum(hour.value_sum_squares),
                                min(hour.min_value), max(hour.max_value),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.sensor_id,
                                      hourly.sample_count,
                                      hourly.value_sum,
                                      hourly.value_sum_squares,
                                      hourly.min_value,
                                      hourly.max_value,
                                      hourly.last_observed_at
                                 FROM public.monitoring_measurement_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour GROUP BY 1, 2)
                        UNION ALL
                        (SELECT hour.bucket, hour.sensor_id,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.value_sum), sum(hour.value_sum_squares),
                                min(hour.min_value), max(hour.max_value),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.sensor_id,
                                      hourly.sample_count,
                                      hourly.value_sum,
                                      hourly.value_sum_squares,
                                      hourly.min_value,
                                      hourly.max_value,
                                      hourly.last_observed_at
                                 FROM public.monitoring_measurement_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour
                          GROUP BY 1, 2
                         EXCEPT ALL
                         SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.sensor_id, sum(day.sample_count)::BIGINT,
                                sum(day.value_sum), sum(day.value_sum_squares),
                                min(day.min_value), max(day.max_value),
                                max(day.last_observed_at)
                           FROM public.monitoring_measurement_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2)
                    );
                ELSIF v_family IS NOT DISTINCT FROM 'light_intensity' THEN
                    v_day_parity := NOT EXISTS (
                        (SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.location, day.cluster, day.device_name,
                                sum(day.sample_count)::BIGINT,
                                sum(day.value_sum), sum(day.value_sum_squares),
                                min(day.min_value), max(day.max_value),
                                max(day.last_observed_at)
                           FROM public.monitoring_light_intensity_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2, 3, 4
                         EXCEPT ALL
                         SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.value_sum), sum(hour.value_sum_squares),
                                min(hour.min_value), max(hour.max_value),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.location, hourly.cluster,
                                      hourly.device_name, hourly.sample_count,
                                      hourly.value_sum,
                                      hourly.value_sum_squares,
                                      hourly.min_value, hourly.max_value,
                                      hourly.last_observed_at
                                 FROM public.monitoring_light_intensity_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour GROUP BY 1, 2, 3, 4)
                        UNION ALL
                        (SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.value_sum), sum(hour.value_sum_squares),
                                min(hour.min_value), max(hour.max_value),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.location, hourly.cluster,
                                      hourly.device_name, hourly.sample_count,
                                      hourly.value_sum,
                                      hourly.value_sum_squares,
                                      hourly.min_value, hourly.max_value,
                                      hourly.last_observed_at
                                 FROM public.monitoring_light_intensity_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour
                          GROUP BY 1, 2, 3, 4
                         EXCEPT ALL
                         SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.location, day.cluster, day.device_name,
                                sum(day.sample_count)::BIGINT,
                                sum(day.value_sum), sum(day.value_sum_squares),
                                min(day.min_value), max(day.max_value),
                                max(day.last_observed_at)
                           FROM public.monitoring_light_intensity_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2, 3, 4)
                    );
                ELSE  -- requested_pid
                    v_day_parity := NOT EXISTS (
                        (SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.location, day.cluster, day.device_name,
                                sum(day.sample_count)::BIGINT,
                                sum(day.requested_count)::BIGINT,
                                sum(day.requested_sum),
                                sum(day.requested_sum_squares),
                                min(day.requested_min), max(day.requested_max),
                                max(day.last_observed_at)
                           FROM public.monitoring_requested_pid_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2, 3, 4
                         EXCEPT ALL
                         SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.requested_count)::BIGINT,
                                sum(hour.requested_sum),
                                sum(hour.requested_sum_squares),
                                min(hour.requested_min), max(hour.requested_max),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.location, hourly.cluster,
                                      hourly.device_name, hourly.sample_count,
                                      hourly.requested_count,
                                      hourly.requested_sum,
                                      hourly.requested_sum_squares,
                                      hourly.requested_min,
                                      hourly.requested_max,
                                      hourly.last_observed_at
                                 FROM public.monitoring_requested_pid_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour GROUP BY 1, 2, 3, 4)
                        UNION ALL
                        (SELECT hour.bucket, hour.location, hour.cluster,
                                hour.device_name,
                                sum(hour.sample_count)::BIGINT,
                                sum(hour.requested_count)::BIGINT,
                                sum(hour.requested_sum),
                                sum(hour.requested_sum_squares),
                                min(hour.requested_min), max(hour.requested_max),
                                max(hour.last_observed_at)
                           FROM (
                               SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                      hourly.location, hourly.cluster,
                                      hourly.device_name, hourly.sample_count,
                                      hourly.requested_count,
                                      hourly.requested_sum,
                                      hourly.requested_sum_squares,
                                      hourly.requested_min,
                                      hourly.requested_max,
                                      hourly.last_observed_at
                                 FROM public.monitoring_requested_pid_1hour AS hourly
                                WHERE hourly.bucket >= v_day_front
                                  AND hourly.bucket < v_day_cand_end
                           ) AS hour
                          GROUP BY 1, 2, 3, 4
                         EXCEPT ALL
                         SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                day.location, day.cluster, day.device_name,
                                sum(day.sample_count)::BIGINT,
                                sum(day.requested_count)::BIGINT,
                                sum(day.requested_sum),
                                sum(day.requested_sum_squares),
                                min(day.requested_min), max(day.requested_max),
                                max(day.last_observed_at)
                           FROM public.monitoring_requested_pid_1day AS day
                          WHERE day.bucket >= v_day_front
                            AND day.bucket < v_day_cand_end
                          GROUP BY 1, 2, 3, 4)
                    );
                END IF;

                IF v_day_parity IS TRUE THEN
                    UPDATE public.monitoring_overview_coverage
                       SET covered_through = v_day_cand_end
                     WHERE model_name = v_day_view;
                END IF;
            END IF;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            GET STACKED DIAGNOSTICS v_fault_sqlstate = RETURNED_SQLSTATE,
                                    v_fault_message = MESSAGE_TEXT;
        END;
    END IF;

    -----------------------------------------------------------------
    -- 4. Failure checkpoint: pause ONLY this family's job, then re-raise
    --    the ORIGINAL SQLSTATE with its message preserved. Coverage rows
    --    stay untouched (the verification transaction rolled back). The
    --    pause commits BEFORE the advisory ownership drops; on a cleanup
    --    failure the lock releases first so a later invocation of this
    --    family can retry the day after this session ends.
    -----------------------------------------------------------------
    IF v_fault_sqlstate IS NOT NULL THEN
        BEGIN
            IF job_id IS NOT NULL THEN
                PERFORM alter_job(job_id, scheduled => FALSE);
            END IF;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- outside the exception subtransaction; ownership retained
        PERFORM pg_advisory_unlock(v_lock_key);
        RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                              MESSAGE = v_fault_message;
    END IF;

    -----------------------------------------------------------------
    -- 5. Success path: the coverage writes happen inside the atomic
    --    verification transaction; the final plain COMMIT lands only
    --    traces of this run, releases the family advisory lock, and
    --    keeps every exit (even the no-work return) clean of leaked
    --    locks or partial writes.
    -----------------------------------------------------------------
    COMMIT;
    PERFORM pg_advisory_unlock(v_lock_key);
END;
$procedure$;
COMMENT ON PROCEDURE public.monitoring_refresh_overview_incremental(INTEGER, JSONB) IS
'Pausable bounded verified checkpoint: one UTC hour and at most one UTC day
per invocation per aggregate family (the family is read from the job''s config). The
native paused one-bucket policies advance the materialization and the
checkpoint verifies it against the raw source (symmetric EXCEPT ALL over
the candidate window) and the native per-catalog watermark/invalidation state
before advancing the hourly and (once the daily materialization catches
up) daily coverage rows atomically; missing/cancel/failure never advances
and unacknowledged failures pause only this family job (scheduled=false)
with owner config preserved.';

-- ------------------------------------------------------------------ §10.6: three custom paused verification-checkpoint jobs
-- One job per aggregate family. Config is exactly
-- {"enabled":false,"acknowledged_failures":{},"family":"<family>"} at
-- creation: manual run_job is a silent no-op until the owner sets
-- enabled=true in this config, and pausing stays owner-controlled via
-- scheduled=false. On repeat the job shape/config shapes are verified and
-- owner-edited state (enabled / scheduled / acknowledged_failures /
-- schedule) is preserved verbatim; drift in any other shape is refused,
-- never repaired.
DO $overview_checkpoint_jobs$
DECLARE
    family_key TEXT;
    local_job_id INTEGER;
    local_drifted BOOLEAN;
BEGIN
    FOREACH family_key IN ARRAY ARRAY['measurement', 'light_intensity',
                                      'requested_pid'] LOOP
        SELECT count(*) > 1 INTO local_drifted
          FROM timescaledb_information.jobs
         WHERE proc_schema = 'public'
           AND proc_name = 'monitoring_refresh_overview_incremental'
           AND config ->> 'family' = family_key;
        IF local_drifted THEN
            RAISE EXCEPTION 'duplicate checkpoint job for overview family %', family_key;
        END IF;

        IF EXISTS (
            SELECT 1 FROM timescaledb_information.jobs
             WHERE proc_schema = 'public'
               AND proc_name = 'monitoring_refresh_overview_incremental'
               AND config ->> 'family' = family_key
        ) THEN
            SELECT job_id,
                   (
                        schedule_interval = INTERVAL '5 minutes'
                    AND max_runtime = INTERVAL '60 seconds'
                    AND max_retries = 0
                    AND jsonb_typeof(config -> 'enabled') = 'boolean'
                    AND jsonb_typeof(config -> 'acknowledged_failures') = 'object'
                    AND jsonb_typeof(config -> 'family') = 'string'
                    AND config ->> 'family' = family_key
                   ) IS DISTINCT FROM TRUE
              INTO local_job_id, local_drifted
              FROM timescaledb_information.jobs
             WHERE proc_schema = 'public'
               AND proc_name = 'monitoring_refresh_overview_incremental'
               AND config ->> 'family' = family_key;
            IF local_drifted THEN
                RAISE EXCEPTION 'checkpoint job for overview family % drifted; no repair performed', family_key;
            END IF;
            PERFORM public.monitoring_overview_unacknowledged_failures(
                (SELECT config FROM timescaledb_information.jobs WHERE job_id = local_job_id));
            -- owner enabled / scheduled / acknowledged state preserved verbatim
        ELSE
            local_job_id := add_job(
                proc           => 'public.monitoring_refresh_overview_incremental(integer, jsonb)'::regprocedure::regproc,
                schedule_interval => INTERVAL '5 minutes',
                config         => jsonb_build_object(
                    'enabled', FALSE,
                    'acknowledged_failures', '{}'::JSONB,
                    'family', family_key),
                initial_start  => NULL,
                scheduled      => FALSE,
                job_name       => format('overview_incremental_%s', family_key));
            IF local_job_id IS NULL THEN
                RAISE EXCEPTION 'add_job returned no job id for family %', family_key;
            END IF;
            PERFORM alter_job(local_job_id,
                              max_runtime => INTERVAL '60 seconds',
                              max_retries => 0,
                              scheduled => FALSE);
        END IF;
    END LOOP;
END
$overview_checkpoint_jobs$;

-- ------------------------------------------------------------------ §10.7: revoke PUBLIC execution of the mutating checkpoint
-- The owner and the job runner keep EXECUTE via ownership; the acceptance
-- helpers (coverage/readiness gates) remain PUBLIC-readable for
-- monitoring/API roles.
REVOKE EXECUTE ON PROCEDURE public.monitoring_refresh_overview_incremental(INTEGER, JSONB) FROM PUBLIC;

-- ------------------------------------------------------------------ §11: relay-duration and relay-rebuild overview slice
-- Owned by the relay slice: relay-duration hour/day sources, the exact
-- read-only duration-slice function, the statement-level dirty tracking on the
-- relay observation hypertable, the paused monitoring_rebuild_relay_overview
-- procedure and the paused daily policy. Included inside the same
-- authorization transaction; the shared failure gate and the coverage table
-- above already exist for it.
\ir monitoring_relay_history_overview.sql

COMMIT;
