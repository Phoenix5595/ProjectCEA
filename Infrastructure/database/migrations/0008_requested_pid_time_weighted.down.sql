\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
SET LOCAL search_path = public, pg_catalog;

DO $phase5_rollback_preflight$
DECLARE
    backfill_job_count INTEGER;
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down may only run on sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;
    IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       AND (
           NULLIF(current_setting('app.requested_pid_test_synthetic_worker_job_id', TRUE), '') IS NOT NULL
           OR NULLIF(current_setting('app.requested_pid_test_synthetic_worker_pid', TRUE), '') IS NOT NULL
           OR NULLIF(current_setting('app.requested_pid_test_capture_quiescence', TRUE), '') IS NOT NULL
       ) THEN
        RAISE EXCEPTION '0008 down test-only quiescence overrides are disposable-only';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'monitoring_requested_pid_1hour'
          AND column_name = 'requested_tw'
    ) THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down expected the Phase 5 requested-PID aggregate shape';
    END IF;
    IF to_regclass('public.monitoring_job_snapshot_0007') IS NULL
       OR to_regclass('public.monitoring_job_snapshot_0008') IS NULL
       OR to_regprocedure('public.monitoring_backfill_aggregates(integer,jsonb)') IS NULL THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down requires the Phase 4 backfill and Phase 5 job snapshot';
    END IF;
    IF to_regclass('public.monitoring_toolkit_snapshot_0008') IS NULL
       OR (SELECT count(*) FROM public.monitoring_toolkit_snapshot_0008) <> 1 THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down requires its Toolkit extension snapshot';
    END IF;

    SELECT count(*)
    INTO backfill_job_count
    FROM timescaledb_information.jobs
    WHERE proc_schema = 'public'
      AND proc_name = 'monitoring_backfill_aggregates';
    IF backfill_job_count <> 1 THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down expected one Phase 4 aggregate backfill job, found %',
            backfill_job_count;
    END IF;
END
$phase5_rollback_preflight$;

UPDATE public.monitoring_job_snapshot_0008
SET drop_after_backfill = TRUE;

DO $pause_requested_pid_jobs$
DECLARE
    local_job_id INTEGER;
BEGIN
    FOR local_job_id IN
        WITH cagg_targets AS (
            SELECT user_view_schema, user_view_name, mat_hypertable_id
            FROM _timescaledb_catalog.continuous_agg
            WHERE user_view_schema = 'public'
              AND user_view_name IN ('monitoring_requested_pid_1hour',
                                     'monitoring_requested_pid_1day')
        )
        SELECT job.job_id
        FROM timescaledb_information.jobs AS job
        LEFT JOIN cagg_targets AS cagg
          ON cagg.mat_hypertable_id = CASE
              WHEN job.proc_name IN (
                  'policy_refresh_continuous_aggregate',
                  'policy_compression', 'policy_columnstore')
              THEN COALESCE(
                  NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                  NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
              ELSE NULL
          END
        WHERE (job.proc_schema = 'public'
               AND job.proc_name IN ('monitoring_backfill_aggregates',
                                     'monitoring_refresh_overview_incremental',
                                     'monitoring_rebuild_relay_overview'))
           OR (job.proc_schema = '_timescaledb_functions'
               AND job.proc_name IN (
                   'policy_refresh_continuous_aggregate',
                   'policy_compression', 'policy_columnstore')
               AND cagg.user_view_name IS NOT NULL)
    LOOP
        PERFORM alter_job(local_job_id, scheduled => FALSE);
    END LOOP;
END
$pause_requested_pid_jobs$;
COMMIT;

DO $wait_for_requested_pid_jobs$
DECLARE
    wait_timeout INTERVAL := INTERVAL '10 minutes';
    test_wait_timeout TEXT;
    synthetic_job_setting TEXT;
    synthetic_pid_setting TEXT;
    synthetic_job_id INTEGER;
    synthetic_worker_pid INTEGER;
    wait_deadline TIMESTAMPTZ;
    running_job_details TEXT;
BEGIN
    synthetic_job_setting := NULLIF(
        current_setting('app.requested_pid_test_synthetic_worker_job_id', TRUE), '');
    synthetic_pid_setting := NULLIF(
        current_setting('app.requested_pid_test_synthetic_worker_pid', TRUE), '');
    IF synthetic_job_setting IS NOT NULL OR synthetic_pid_setting IS NOT NULL THEN
        IF current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
            RAISE EXCEPTION '0008 down synthetic-worker test override is disposable-only';
        END IF;
        IF synthetic_job_setting IS NULL OR synthetic_pid_setting IS NULL THEN
            RAISE EXCEPTION '0008 down synthetic-worker test override requires both job id and pid';
        END IF;
        synthetic_job_id := synthetic_job_setting::INTEGER;
        synthetic_worker_pid := synthetic_pid_setting::INTEGER;
        IF synthetic_worker_pid < 1 THEN
            RAISE EXCEPTION '0008 down synthetic-worker test pid must be positive';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM public.monitoring_job_snapshot_0008
            WHERE job_id = synthetic_job_id
        ) THEN
            RAISE EXCEPTION '0008 down synthetic-worker test job % is not snapshotted',
                synthetic_job_id;
        END IF;
    END IF;

    IF current_database() ~ '^monitoring_test_[a-z0-9_]+$' THEN
        test_wait_timeout := NULLIF(
            current_setting('app.requested_pid_test_quiesce_timeout', TRUE), '');
        IF test_wait_timeout IS NOT NULL THEN
            wait_timeout := test_wait_timeout::INTERVAL;
        END IF;
    END IF;
    wait_deadline := clock_timestamp() + wait_timeout;

    LOOP
        WITH cagg_targets AS (
            SELECT user_view_schema, user_view_name, mat_hypertable_id
            FROM _timescaledb_catalog.continuous_agg
            WHERE user_view_schema = 'public'
              AND user_view_name IN ('monitoring_requested_pid_1hour',
                                     'monitoring_requested_pid_1day')
        ),
        target_jobs AS (
            SELECT job.job_id, job.proc_schema, job.proc_name,
                   CASE WHEN cagg.user_view_name IS NOT NULL
                        THEN cagg.user_view_schema ELSE job.hypertable_schema END
                       AS target_schema,
                   CASE WHEN cagg.user_view_name IS NOT NULL
                        THEN cagg.user_view_name ELSE job.hypertable_name END
                       AS target_name
            FROM timescaledb_information.jobs AS job
            LEFT JOIN cagg_targets AS cagg
              ON cagg.mat_hypertable_id = CASE
                  WHEN job.proc_name IN (
                      'policy_refresh_continuous_aggregate',
                      'policy_compression', 'policy_columnstore')
                  THEN COALESCE(
                      NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                      NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
                  ELSE NULL
              END
            WHERE (job.proc_schema = 'public'
                   AND job.proc_name IN ('monitoring_backfill_aggregates',
                                         'monitoring_refresh_overview_incremental',
                                         'monitoring_rebuild_relay_overview'))
               OR (job.proc_schema = '_timescaledb_functions'
                   AND job.proc_name IN (
                       'policy_refresh_continuous_aggregate',
                       'policy_compression', 'policy_columnstore')
                   AND cagg.user_view_name IS NOT NULL)
        )
        , worker_activity AS (
            SELECT activity.pid, activity.backend_start, activity.application_name
            FROM pg_stat_activity AS activity
            WHERE activity.datname = current_database()
              AND activity.backend_type <> 'client backend'
            UNION ALL
            SELECT synthetic_worker_pid,
                   clock_timestamp(),
                   format('phase5_quiesce_diagnostic [%s]', synthetic_job_id)
            WHERE synthetic_job_id IS NOT NULL
        )
        SELECT string_agg(
            format(
                'job_id=%s proc_name=%s.%s hypertable/view=%s worker_pid=%s worker_app=%s runtime=%s',
                target.job_id,
                target.proc_schema,
                target.proc_name,
                CASE
                    WHEN target.target_name IS NULL THEN '<none>'
                    ELSE format(
                        '%I.%I',
                        COALESCE(target.target_schema, 'public'),
                        target.target_name)
                END,
                worker.pid,
                worker.application_name,
                COALESCE(
                    (clock_timestamp() - stats.last_run_started_at)::TEXT,
                    (clock_timestamp() - worker.backend_start)::TEXT,
                    'unknown')),
            E'\n' ORDER BY target.job_id)
        INTO running_job_details
        FROM target_jobs AS target
        LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
        JOIN LATERAL (
            SELECT worker_activity.pid, worker_activity.backend_start,
                   worker_activity.application_name
            FROM worker_activity
            WHERE worker_activity.application_name LIKE
                  '%[' || target.job_id::TEXT || ']%'
            ORDER BY worker_activity.pid
            LIMIT 1
        ) AS worker ON TRUE;
        EXIT WHEN running_job_details IS NULL;
        IF clock_timestamp() >= wait_deadline THEN
            RAISE EXCEPTION 'requested_pid_migration_jobs_still_running'
                USING DETAIL = running_job_details,
                      HINT = 'Jobs are durably unscheduled and snapshotted; wait for canceled workers to exit (do not kill them), then re-run the migration.';
        END IF;
        PERFORM pg_sleep(1);
    END LOOP;
END
$wait_for_requested_pid_jobs$;
DO $capture_requested_pid_quiescence$
DECLARE
    capture_setting TEXT := NULLIF(
        current_setting('app.requested_pid_test_capture_quiescence', TRUE), '');
BEGIN
    IF capture_setting IS NOT NULL THEN
        IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
           OR capture_setting <> '1' THEN
            RAISE EXCEPTION '0008 down quiescence snapshot hook is disposable-only';
        END IF;
        CREATE TABLE IF NOT EXISTS public.monitoring_test_quiescence_snapshot_0008 (
            captured_at TIMESTAMPTZ NOT NULL,
            active_worker_count INTEGER NOT NULL,
            active_worker_job_ids INTEGER[] NOT NULL,
            active_worker_pids INTEGER[] NOT NULL
        );
        WITH cagg_targets AS (
            SELECT user_view_schema, user_view_name, mat_hypertable_id
            FROM _timescaledb_catalog.continuous_agg
            WHERE user_view_schema = 'public'
              AND user_view_name IN ('monitoring_requested_pid_1hour',
                                     'monitoring_requested_pid_1day')
        ),
        target_jobs AS (
            SELECT job.job_id
            FROM timescaledb_information.jobs AS job
            LEFT JOIN cagg_targets AS cagg
              ON cagg.mat_hypertable_id = CASE
                  WHEN job.proc_name IN (
                      'policy_refresh_continuous_aggregate',
                      'policy_compression', 'policy_columnstore')
                  THEN COALESCE(
                      NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                      NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
                  ELSE NULL
              END
            WHERE (job.proc_schema = 'public'
                   AND job.proc_name IN ('monitoring_backfill_aggregates',
                                         'monitoring_refresh_overview_incremental',
                                         'monitoring_rebuild_relay_overview'))
               OR (job.proc_schema = '_timescaledb_functions'
                   AND job.proc_name IN (
                       'policy_refresh_continuous_aggregate',
                       'policy_compression', 'policy_columnstore')
                   AND cagg.user_view_name IS NOT NULL)
        ),
        active_workers AS (
            SELECT target.job_id, activity.pid
            FROM target_jobs AS target
            JOIN pg_stat_activity AS activity
              ON activity.datname = current_database()
             AND activity.backend_type <> 'client backend'
             AND activity.application_name LIKE
                 '%[' || target.job_id::TEXT || ']%'
        )
        INSERT INTO public.monitoring_test_quiescence_snapshot_0008 (
            captured_at, active_worker_count, active_worker_job_ids,
            active_worker_pids)
        SELECT clock_timestamp(),
               count(active_workers.pid)::INTEGER,
               COALESCE(array_agg(DISTINCT active_workers.job_id)
                   FILTER (WHERE active_workers.job_id IS NOT NULL), '{}'::INTEGER[]),
               COALESCE(array_agg(DISTINCT active_workers.pid)
                   FILTER (WHERE active_workers.pid IS NOT NULL), '{}'::INTEGER[])
        FROM target_jobs
        LEFT JOIN active_workers USING (job_id);
    END IF;
END
$capture_requested_pid_quiescence$;


BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
SET LOCAL search_path = public, pg_catalog;

DO $drop_requested_pid_aggregates$
DECLARE
    view_name TEXT;
    attempt INTEGER;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_requested_pid_1day',
        'monitoring_requested_pid_1hour'
    ] LOOP
        FOR attempt IN 1..5 LOOP
            BEGIN
                EXECUTE format('DROP MATERIALIZED VIEW public.%I', view_name);
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
$drop_requested_pid_aggregates$;

SET LOCAL app.grow_cycle_history_authorized = '1';
SET LOCAL app.grow_cycle_history_disposable = '1';
\set monitoring_time_rename_migration true
\ir ../monitoring_history_overview.sql

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';
SET LOCAL search_path = public, pg_catalog;

DO $requested_pid_compression$
DECLARE
    view_name TEXT;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_requested_pid_1hour',
        'monitoring_requested_pid_1day'
    ] LOOP
        EXECUTE format(
            'ALTER MATERIALIZED VIEW public.%I SET (timescaledb.compress = true)',
            view_name);
        PERFORM add_compression_policy(
            format('public.%I', view_name)::REGCLASS,
            compress_after => INTERVAL '3 months',
            if_not_exists => TRUE);
    END LOOP;
END
$requested_pid_compression$;

DO $restore_toolkit_extension$
DECLARE
    extension_preexisting BOOLEAN;
BEGIN
    SELECT snapshot.extension_preexisting
    INTO extension_preexisting
    FROM public.monitoring_toolkit_snapshot_0008 AS snapshot
    WHERE snapshot.singleton;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted Toolkit extension snapshot is missing';
    END IF;
    IF NOT extension_preexisting
       AND EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb_toolkit') THEN
        EXECUTE 'DROP EXTENSION timescaledb_toolkit';
    END IF;
    DROP TABLE public.monitoring_toolkit_snapshot_0008;
END
$restore_toolkit_extension$;

DO $restart_aggregate_backfill$
DECLARE
    source_minimum TIMESTAMPTZ;
    start_cursor TIMESTAMPTZ;
    configured JSONB;
    backfill_job_id INTEGER;
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
    SELECT job_id
    INTO backfill_job_id
    FROM timescaledb_information.jobs
    WHERE proc_schema = 'public'
      AND proc_name = 'monitoring_backfill_aggregates';
    IF backfill_job_id IS NULL THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down could not find the Phase 4 backfill job';
    END IF;
    PERFORM alter_job(backfill_job_id,
                      config => configured,
                      next_start => clock_timestamp() + INTERVAL '1 minute',
                      scheduled => TRUE);
END
$restart_aggregate_backfill$;

COMMIT;
