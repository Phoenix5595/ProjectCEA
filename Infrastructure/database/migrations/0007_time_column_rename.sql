-- migration: downtime=true
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';
SET LOCAL search_path = public, pg_catalog;

DO $phase4_preflight$
DECLARE
    cagg_count INTEGER;
    overview_refresh_count INTEGER;
    relay_job_count INTEGER;
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION '0007_time_column_rename may only run on sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
            ('automation_state', 'timestamp'),
            ('effective_setpoints', 'timestamp'),
            ('control_history', 'timestamp'),
            ('pid_parameter_history', 'timestamp'),
            ('relay_observation', 'observed_at'),
            ('monitoring_room_photoperiod', 'observed_at'),
            ('monitoring_photoperiod_coverage', 'observed_at'),
            ('mode_transition_history', 'triggered_at')
        ) AS expected(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM information_schema.columns AS column_info
            WHERE column_info.table_schema = 'public'
              AND column_info.table_name = expected.table_name
              AND column_info.column_name = expected.column_name
              AND column_info.data_type = 'timestamp with time zone'
        ) OR EXISTS (
            SELECT 1
            FROM information_schema.columns AS column_info
            WHERE column_info.table_schema = 'public'
              AND column_info.table_name = expected.table_name
              AND column_info.column_name = 'time'
        )
    ) THEN
        RAISE EXCEPTION '0007_time_column_rename source columns drifted; refusing partial rename';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'measurement'
          AND column_name = 'time'
          AND data_type = 'timestamp with time zone'
    ) THEN
        RAISE EXCEPTION 'measurement.time is missing or has an unexpected type';
    END IF;

    SELECT count(*)
    INTO cagg_count
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public'
      AND user_view_name = ANY (ARRAY[
          'monitoring_measurement_1min', 'monitoring_measurement_5min',
          'monitoring_measurement_1hour', 'monitoring_measurement_1day',
          'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
          'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
          'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
          'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
          'monitoring_relay_duration_1day'
      ]);
    IF cagg_count <> 13 THEN
        RAISE EXCEPTION '0007_time_column_rename expected all 13 monitoring aggregates, found %', cagg_count;
    END IF;

    SELECT count(*) FILTER (WHERE proc_name = 'monitoring_refresh_overview_incremental'),
           count(*) FILTER (WHERE proc_name = 'monitoring_rebuild_relay_overview')
    INTO overview_refresh_count, relay_job_count
    FROM timescaledb_information.jobs
    WHERE proc_schema = 'public'
      AND proc_name IN ('monitoring_refresh_overview_incremental',
                        'monitoring_rebuild_relay_overview');
    IF overview_refresh_count <> 3 OR relay_job_count <> 1 THEN
        RAISE EXCEPTION '0007_time_column_rename expected three incremental overview jobs and one relay rebuild job; found % and %',
            overview_refresh_count, relay_job_count;
    END IF;

    IF to_regclass('public.monitoring_job_snapshot_0007') IS NOT NULL
       OR to_regclass('public.monitoring_refresh_policy_snapshot_0007') IS NOT NULL
       OR to_regclass('public.monitoring_index_snapshot_0007') IS NOT NULL
       OR to_regprocedure('public.monitoring_backfill_aggregates(integer,jsonb)') IS NOT NULL
       OR EXISTS (
            SELECT 1 FROM timescaledb_information.jobs
            WHERE proc_schema = 'public'
              AND proc_name = 'monitoring_backfill_aggregates'
       ) THEN
        RAISE EXCEPTION '0007_time_column_rename snapshot or backfill objects already exist';
    END IF;
END
$phase4_preflight$;

CREATE TABLE public.monitoring_job_snapshot_0007 (
    job_id INTEGER PRIMARY KEY,
    proc_name TEXT NOT NULL,
    scheduled BOOLEAN NOT NULL,
    config JSONB NOT NULL
);
INSERT INTO public.monitoring_job_snapshot_0007 (job_id, proc_name, scheduled, config)
SELECT job_id, proc_name, scheduled, config
FROM timescaledb_information.jobs
WHERE proc_schema = 'public'
  AND proc_name IN ('monitoring_refresh_overview_incremental',
                    'monitoring_rebuild_relay_overview');

CREATE TABLE public.monitoring_refresh_policy_snapshot_0007 (
    view_name TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    scheduled BOOLEAN NOT NULL,
    schedule_interval INTERVAL NOT NULL,
    max_runtime INTERVAL,
    max_retries INTEGER,
    config JSONB NOT NULL
);
INSERT INTO public.monitoring_refresh_policy_snapshot_0007 (
    view_name, job_id, scheduled, schedule_interval, max_runtime, max_retries, config)
SELECT cagg.user_view_name AS view_name, job.job_id, job.scheduled, job.schedule_interval,
       job.max_runtime, job.max_retries, job.config
FROM _timescaledb_catalog.continuous_agg AS cagg
JOIN timescaledb_information.jobs AS job
  ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
WHERE cagg.user_view_schema = 'public'
  AND cagg.user_view_name = ANY (ARRAY[
      'monitoring_measurement_5min',
      'monitoring_measurement_1hour', 'monitoring_measurement_1day',
      'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
      'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
      'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
      'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
  ])
  AND job.proc_schema = '_timescaledb_functions'
  AND job.proc_name = 'policy_refresh_continuous_aggregate';

CREATE TABLE public.monitoring_index_snapshot_0007 (
    table_name TEXT NOT NULL,
    old_index_name TEXT PRIMARY KEY,
    new_index_name TEXT NOT NULL,
    old_constraint_name TEXT
);
INSERT INTO public.monitoring_index_snapshot_0007 (
    table_name, old_index_name, new_index_name, old_constraint_name)
SELECT indexes.tablename,
       indexes.indexname,
       replace(replace(indexes.indexname, 'timestamp', 'time'), 'observed_at', 'time'),
       constraint_info.conname
FROM pg_indexes AS indexes
JOIN pg_class AS index_class
  ON index_class.relname = indexes.indexname
 AND index_class.relnamespace = 'public'::regnamespace
LEFT JOIN pg_constraint AS constraint_info
  ON constraint_info.conindid = index_class.oid
 AND constraint_info.conrelid = format('public.%I', indexes.tablename)::regclass
WHERE indexes.schemaname = 'public'
  AND indexes.tablename = ANY (ARRAY[
      'automation_state', 'effective_setpoints', 'control_history',
      'pid_parameter_history', 'relay_observation',
      'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
      'mode_transition_history'
  ])
  AND (indexes.indexname LIKE '%timestamp%'
       OR indexes.indexname LIKE '%observed_at%')
  AND indexes.tablename <> 'automation_state';

DO $snapshot_shape$
BEGIN
    IF (SELECT count(*) FROM public.monitoring_job_snapshot_0007) <> 4 THEN
        RAISE EXCEPTION '0007_time_column_rename did not snapshot all four overview jobs';
    END IF;
    IF (SELECT count(*) FROM public.monitoring_refresh_policy_snapshot_0007) <> 11 THEN
        RAISE EXCEPTION '0007_time_column_rename expected eleven refresh policies for rebuilt aggregates, found %',
            (SELECT count(*) FROM public.monitoring_refresh_policy_snapshot_0007);
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.monitoring_index_snapshot_0007 AS snapshot
        JOIN pg_class AS existing_index
          ON existing_index.relnamespace = 'public'::regnamespace
         AND existing_index.relname = snapshot.new_index_name
    ) THEN
        RAISE EXCEPTION '0007_time_column_rename index rename target already exists';
    END IF;
END
$snapshot_shape$;

DO $pause_overview_jobs$
DECLARE
    snapshot_row RECORD;
BEGIN
    FOR snapshot_row IN
        SELECT job_id FROM public.monitoring_job_snapshot_0007 ORDER BY job_id
    LOOP
        PERFORM alter_job(snapshot_row.job_id, scheduled => FALSE);
    END LOOP;

    FOR snapshot_row IN
        SELECT job_id
        FROM public.monitoring_refresh_policy_snapshot_0007
        ORDER BY job_id
    LOOP
        PERFORM alter_job(snapshot_row.job_id, scheduled => FALSE);
    END LOOP;
END
$pause_overview_jobs$;

-- Dependents first. The 1-minute measurement aggregate and relay-duration day
-- aggregate do not depend on a renamed source column and remain in place.
-- A scheduled policy worker (or the launcher updating catalog rows while it
-- starts a job) can be mid-commit on these catalog rows, so a concurrent
-- catalog update surfaces as 'tuple concurrently deleted'. Every drop is
-- retried briefly inside this transaction before giving up.
DO $drop_renamed_aggregates$
DECLARE
    dropped_view TEXT;
    attempt INTEGER;
BEGIN
    FOREACH dropped_view IN ARRAY ARRAY[
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1day',
        'monitoring_measurement_1day',
        'monitoring_light_intensity_1hour',
        'monitoring_requested_pid_1hour',
        'monitoring_measurement_1hour',
        'monitoring_measurement_5min',
        'monitoring_effective_setpoints_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_automation_state_5min',
        'monitoring_automation_state_1min'
    ] LOOP
        FOR attempt IN 1..5 LOOP
            BEGIN
                EXECUTE format('DROP MATERIALIZED VIEW public.%I', dropped_view);
                EXIT;
            EXCEPTION WHEN OTHERS THEN
                IF SQLERRM NOT LIKE '%tuple concurrently deleted%'
                   OR attempt = 5 THEN
                    RAISE;
                END IF;
                PERFORM pg_sleep(0.5);
            END;
        END LOOP;
    END LOOP;
END
$drop_renamed_aggregates$;

DO $rename_event_columns$
DECLARE
    source_column RECORD;
BEGIN
    FOR source_column IN
        SELECT * FROM (VALUES
            ('automation_state', 'timestamp'),
            ('effective_setpoints', 'timestamp'),
            ('control_history', 'timestamp'),
            ('pid_parameter_history', 'timestamp'),
            ('relay_observation', 'observed_at'),
            ('monitoring_room_photoperiod', 'observed_at'),
            ('monitoring_photoperiod_coverage', 'observed_at'),
            ('mode_transition_history', 'triggered_at')
        ) AS columns_to_rename(table_name, old_column)
    LOOP
        EXECUTE format('ALTER TABLE public.%I RENAME COLUMN %I TO %I',
                       source_column.table_name, source_column.old_column, 'time');
    END LOOP;
END
$rename_event_columns$;

DO $rename_event_indexes$
DECLARE
    index_row RECORD;
BEGIN
    FOR index_row IN
        SELECT table_name, old_index_name, new_index_name, old_constraint_name
        FROM public.monitoring_index_snapshot_0007
        ORDER BY old_index_name
    LOOP
        IF index_row.old_constraint_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I RENAME CONSTRAINT %I TO %I',
                           index_row.table_name,
                           index_row.old_constraint_name,
                           index_row.new_index_name);
        ELSE
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I',
                           index_row.old_index_name,
                           index_row.new_index_name);
        END IF;
    END LOOP;
END
$rename_event_indexes$;

-- TimescaleDB 2.28.3 permits renames on compressed hypertables. It permits
-- adding NOT VALID CHECK constraints, but VALIDATE CONSTRAINT is unsupported
-- on columnstore-enabled hypertables, so prove existing rows explicitly and
-- leave PostgreSQL to enforce new writes.
DO $event_time_bounds$
DECLARE
    source_table RECORD;
    is_compressed_hypertable BOOLEAN;
    invalid_row_count BIGINT;
    lower_bound TIMESTAMPTZ := '2020-01-01'::TIMESTAMPTZ;
    constraint_name TEXT;
BEGIN
    FOR source_table IN
        SELECT table_name FROM (VALUES
            ('automation_state'),
            ('effective_setpoints'),
            ('control_history'),
            ('pid_parameter_history'),
            ('relay_observation'),
            ('monitoring_room_photoperiod'),
            ('monitoring_photoperiod_coverage'),
            ('mode_transition_history'),
            ('measurement')
        ) AS source_tables(table_name)
    LOOP
        SELECT hypertable.compression_enabled
        INTO is_compressed_hypertable
        FROM timescaledb_information.hypertables AS hypertable
        WHERE hypertable.hypertable_schema = 'public'
          AND hypertable.hypertable_name = source_table.table_name;

        constraint_name := source_table.table_name || '_time_bounds';
        IF COALESCE(is_compressed_hypertable, FALSE) THEN
            EXECUTE format(
                'SELECT count(*) FROM public.%I WHERE "time" IS NOT NULL AND ("time" < $1 OR "time" > now() + INTERVAL ''1 day'')',
                source_table.table_name)
            INTO invalid_row_count
            USING lower_bound;
            IF invalid_row_count <> 0 THEN
                RAISE EXCEPTION 'time_bounds_existing_rows_out_of_range: table=public.% count=%',
                    source_table.table_name, invalid_row_count;
            END IF;

            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK ("time" >= %L::TIMESTAMPTZ AND "time" <= now() + INTERVAL ''1 day'') NOT VALID',
                source_table.table_name, constraint_name, lower_bound);
        ELSE
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK ("time" >= %L::TIMESTAMPTZ AND "time" <= now() + INTERVAL ''1 day'') NOT VALID',
                source_table.table_name, constraint_name, lower_bound);
            EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I',
                           source_table.table_name, constraint_name);
        END IF;
    END LOOP;
END
$event_time_bounds$;

-- The runner invokes psql -f with an absolute migration path and no cwd
-- override. \\ir resolves these includes relative to this migration file.
SET LOCAL app.grow_cycle_history_authorized = '1';
SET LOCAL app.grow_cycle_history_disposable = '1';
\set monitoring_time_rename_migration true
\ir ../monitoring_read_models.sql
\ir ../monitoring_history_overview.sql
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';

-- The six base policies are rebuilt paused as well; the backfill job enables
-- every policy only after all eleven views have reached their frontier.
DO $base_refresh_policies$
DECLARE
    policy_spec RECORD;
    refresh_job_id INTEGER;
BEGIN
    FOR policy_spec IN
        SELECT * FROM (VALUES
            ('monitoring_measurement_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_effective_setpoints_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_effective_setpoints_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_automation_state_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_automation_state_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes')
        ) AS policies(view_name, start_offset, end_offset, schedule_interval)
    LOOP
        refresh_job_id := add_continuous_aggregate_policy(
            format('public.%I', policy_spec.view_name)::REGCLASS,
            start_offset => policy_spec.start_offset,
            end_offset => policy_spec.end_offset,
            schedule_interval => policy_spec.schedule_interval,
            if_not_exists => FALSE);
        IF refresh_job_id IS NULL OR refresh_job_id < 1 THEN
            RAISE EXCEPTION 'failed to create paused refresh policy for %', policy_spec.view_name;
        END IF;
        PERFORM alter_job(refresh_job_id,
                          max_runtime => INTERVAL '60 seconds',
                          max_retries => 0,
                          scheduled => FALSE);
    END LOOP;
END
$base_refresh_policies$;

DO $aggregate_compression$
DECLARE
    view_name TEXT;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_measurement_1min', 'monitoring_measurement_5min',
        'monitoring_measurement_1hour', 'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
        'monitoring_relay_duration_1day'
    ] LOOP
        EXECUTE format('ALTER MATERIALIZED VIEW public.%I SET (timescaledb.compress = true)',
                       view_name);
        PERFORM add_compression_policy(
            format('public.%I', view_name)::REGCLASS,
            compress_after => INTERVAL '3 months',
            if_not_exists => TRUE);
    END LOOP;
END
$aggregate_compression$;


DO $create_backfill_job$
DECLARE
    source_minimum TIMESTAMPTZ;
    start_cursor TIMESTAMPTZ;
    configured JSONB;
    new_job_id INTEGER;
    backfill_views TEXT[] := ARRAY[
        'monitoring_measurement_5min',
        'monitoring_measurement_1hour',
        'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour',
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour',
        'monitoring_requested_pid_1day'
    ];
BEGIN
    SELECT min(source_time)
    INTO source_minimum
    FROM (
        SELECT min("time") AS source_time FROM public.measurement
        UNION ALL
        SELECT min("time") FROM public.effective_setpoints
        UNION ALL
        SELECT min("time") FROM public.automation_state
    ) AS source_minima;
    IF source_minimum IS NULL THEN
        RAISE EXCEPTION 'monitoring_backfill_no_source_rows';
    END IF;

    start_cursor := time_bucket(INTERVAL '1 day', source_minimum);
    configured := jsonb_build_object(
        'views', to_jsonb(backfill_views),
        'start', start_cursor,
        'cursor', start_cursor,
        'done', FALSE);
    new_job_id := add_job(
        'public.monitoring_backfill_aggregates(integer,jsonb)'::REGPROCEDURE::REGPROC,
        schedule_interval => INTERVAL '1 minute',
        config => configured,
        initial_start => clock_timestamp() + INTERVAL '1 minute',
        scheduled => TRUE,
        job_name => 'monitoring_backfill_aggregates_0007');
    IF new_job_id IS NULL OR new_job_id < 1 THEN
        RAISE EXCEPTION 'add_job returned no id for monitoring_backfill_aggregates';
    END IF;
    PERFORM alter_job(new_job_id,
                      max_runtime => INTERVAL '10 minutes',
                      max_retries => 0,
                      scheduled => TRUE);
END
$create_backfill_job$;

COMMIT;
