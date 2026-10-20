-- migration: downtime=false
\set ON_ERROR_STOP on


DO $phase5_preflight$
DECLARE
    backfill_job_count INTEGER;
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted may only run on sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;
    IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       AND (
           NULLIF(current_setting('app.requested_pid_test_synthetic_worker_job_id', TRUE), '') IS NOT NULL
           OR NULLIF(current_setting('app.requested_pid_test_synthetic_worker_pid', TRUE), '') IS NOT NULL
           OR NULLIF(current_setting('app.requested_pid_test_capture_quiescence', TRUE), '') IS NOT NULL
       ) THEN
        RAISE EXCEPTION '0008 test-only quiescence overrides are disposable-only';
    END IF;
    IF current_setting('server_version_num')::INTEGER / 10000 <> 15
       OR (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') IS DISTINCT FROM '2.28.3' THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted requires PostgreSQL 15 and TimescaleDB 2.28.3';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_available_extension_versions
        WHERE name = 'timescaledb_toolkit'
          AND trusted
    ) THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted requires an available trusted timescaledb_toolkit version';
    END IF;
    IF to_regclass('public.monitoring_requested_pid_1hour') IS NULL
       OR to_regclass('public.monitoring_requested_pid_1day') IS NULL
       OR to_regclass('public.monitoring_job_snapshot_0007') IS NULL
       OR to_regprocedure('public.monitoring_backfill_aggregates(integer,jsonb)') IS NULL THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted requires the Phase 4 monitoring aggregates and backfill';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'monitoring_requested_pid_1hour'
          AND column_name = 'requested_tw'
    ) OR NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'monitoring_requested_pid_1hour'
          AND column_name = 'requested_sum'
    ) THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted expected the Phase 4 requested-PID aggregate shape';
    END IF;

    SELECT count(*)
    INTO backfill_job_count
    FROM timescaledb_information.jobs
    WHERE proc_schema = 'public'
      AND proc_name = 'monitoring_backfill_aggregates';
    IF backfill_job_count <> 1 THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted expected one Phase 4 aggregate backfill job, found %',
            backfill_job_count;
    END IF;
END
$phase5_preflight$;
BEGIN;
SET LOCAL search_path = public, pg_catalog;
CREATE TABLE IF NOT EXISTS public.monitoring_job_snapshot_0008 (
    job_id INTEGER PRIMARY KEY,
    proc_schema TEXT NOT NULL,
    proc_name TEXT NOT NULL,
    scheduled BOOLEAN NOT NULL,
    target_schema TEXT,
    target_name TEXT,
    target_is_cagg BOOLEAN NOT NULL,
    restore_after_backfill BOOLEAN NOT NULL DEFAULT TRUE,
    drop_after_backfill BOOLEAN NOT NULL DEFAULT FALSE
);
WITH cagg_targets AS (
    SELECT user_view_schema, user_view_name, mat_hypertable_id
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public'
      AND user_view_name IN ('monitoring_requested_pid_1hour',
                             'monitoring_requested_pid_1day')
)
INSERT INTO public.monitoring_job_snapshot_0008 (
    job_id, proc_schema, proc_name, scheduled, target_schema, target_name,
    target_is_cagg, restore_after_backfill, drop_after_backfill)
SELECT job.job_id, job.proc_schema, job.proc_name, job.scheduled,
       CASE WHEN cagg.user_view_name IS NOT NULL
            THEN cagg.user_view_schema ELSE job.hypertable_schema END,
       CASE WHEN cagg.user_view_name IS NOT NULL
            THEN cagg.user_view_name ELSE job.hypertable_name END,
       cagg.user_view_name IS NOT NULL,
       TRUE,
       FALSE
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
ON CONFLICT (job_id) DO NOTHING;
UPDATE public.monitoring_job_snapshot_0008
SET drop_after_backfill = FALSE;

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
    jobs_running BOOLEAN;
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
            RAISE EXCEPTION '0008 synthetic-worker test override is disposable-only';
        END IF;
        IF synthetic_job_setting IS NULL OR synthetic_pid_setting IS NULL THEN
            RAISE EXCEPTION '0008 synthetic-worker test override requires both job id and pid';
        END IF;
        synthetic_job_id := synthetic_job_setting::INTEGER;
        synthetic_worker_pid := synthetic_pid_setting::INTEGER;
        IF synthetic_worker_pid < 1 THEN
            RAISE EXCEPTION '0008 synthetic-worker test pid must be positive';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM public.monitoring_job_snapshot_0008
            WHERE job_id = synthetic_job_id
        ) THEN
            RAISE EXCEPTION '0008 synthetic-worker test job % is not snapshotted',
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
        )
        , worker_activity AS (
            SELECT activity.pid, activity.application_name
            FROM pg_stat_activity AS activity
            WHERE activity.datname = current_database()
              AND activity.backend_type <> 'client backend'
            UNION ALL
            SELECT synthetic_worker_pid,
                   format('phase5_quiesce_diagnostic [%s]', synthetic_job_id)
            WHERE synthetic_job_id IS NOT NULL
        )
        SELECT EXISTS (
            SELECT 1
            FROM target_jobs AS target
            WHERE EXISTS (
                SELECT 1
                FROM worker_activity AS activity
                WHERE activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
            )
        )
        INTO jobs_running;
        EXIT WHEN NOT jobs_running;
        IF clock_timestamp() >= wait_deadline THEN
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
                    COALESCE(worker.pid::TEXT, 'unknown'),
                    COALESCE(worker.application_name::TEXT, 'unknown'),
                    COALESCE(
                        (clock_timestamp() - stats.last_run_started_at)::TEXT,
                        (clock_timestamp() - worker.backend_start)::TEXT,
                        'unknown')),
                E'\n' ORDER BY target.job_id)
            INTO running_job_details
            FROM target_jobs AS target
            LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
            LEFT JOIN LATERAL (
                SELECT activity.pid, activity.backend_start, activity.application_name
                FROM worker_activity AS activity
                WHERE activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
                ORDER BY activity.pid
                LIMIT 1
            ) AS worker ON TRUE
            WHERE worker.pid IS NOT NULL;
            IF running_job_details IS NULL THEN
                EXIT;
            END IF;
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
            RAISE EXCEPTION '0008 quiescence snapshot hook is disposable-only';
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
CREATE TABLE public.monitoring_toolkit_snapshot_0008 (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    extension_preexisting BOOLEAN NOT NULL
);
INSERT INTO public.monitoring_toolkit_snapshot_0008 (extension_preexisting)
SELECT EXISTS (
    SELECT 1 FROM pg_extension WHERE extname = 'timescaledb_toolkit'
);

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
\set monitoring_requested_pid_time_weighted true
\ir ../monitoring_history_overview.sql
CREATE OR REPLACE PROCEDURE public.monitoring_restore_requested_pid_job_schedule_0008()
LANGUAGE plpgsql
SECURITY INVOKER
AS $restore_requested_pid_jobs$
DECLARE
    snapshot_row RECORD;
    local_job_id INTEGER;
    local_mat_hypertable_id INTEGER;
    matching_jobs INTEGER;
    drop_snapshot BOOLEAN;
BEGIN
    IF to_regclass('public.monitoring_job_snapshot_0008') IS NULL THEN
        RETURN;
    END IF;

    SELECT COALESCE(bool_or(drop_after_backfill), FALSE)
    INTO drop_snapshot
    FROM public.monitoring_job_snapshot_0008;

    FOR snapshot_row IN
        SELECT *
        FROM public.monitoring_job_snapshot_0008
        WHERE restore_after_backfill
        ORDER BY job_id
    LOOP
        local_job_id := NULL;
        IF snapshot_row.target_is_cagg THEN
            SELECT cagg.mat_hypertable_id
            INTO local_mat_hypertable_id
            FROM _timescaledb_catalog.continuous_agg AS cagg
            WHERE cagg.user_view_schema = snapshot_row.target_schema
              AND cagg.user_view_name = snapshot_row.target_name;
            IF local_mat_hypertable_id IS NULL THEN
                RAISE EXCEPTION 'cannot restore 0008 job %: continuous aggregate %.% is missing',
                    snapshot_row.job_id, snapshot_row.target_schema, snapshot_row.target_name;
            END IF;

            SELECT count(*), min(job.job_id)
            INTO matching_jobs, local_job_id
            FROM timescaledb_information.jobs AS job
            WHERE job.proc_schema = snapshot_row.proc_schema
              AND (
                  job.proc_name = snapshot_row.proc_name
                  OR (
                      snapshot_row.proc_name IN ('policy_compression', 'policy_columnstore')
                      AND job.proc_name IN ('policy_compression', 'policy_columnstore')
                  )
              )
              AND COALESCE(
                      NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                      NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
                  = local_mat_hypertable_id;
        ELSE
            SELECT count(*), min(job.job_id)
            INTO matching_jobs, local_job_id
            FROM timescaledb_information.jobs AS job
            WHERE job.job_id = snapshot_row.job_id
              AND job.proc_schema = snapshot_row.proc_schema
              AND job.proc_name = snapshot_row.proc_name;
        END IF;
        IF matching_jobs <> 1 OR local_job_id IS NULL THEN
            RAISE EXCEPTION 'cannot restore 0008 job % (% %.%): found % matches',
                snapshot_row.job_id, snapshot_row.proc_schema,
                snapshot_row.proc_name, snapshot_row.target_name, matching_jobs;
        END IF;
        PERFORM alter_job(local_job_id, scheduled => snapshot_row.scheduled);
    END LOOP;

    IF drop_snapshot THEN
        DROP TABLE public.monitoring_job_snapshot_0008;
    END IF;
END
$restore_requested_pid_jobs$;
REVOKE EXECUTE ON PROCEDURE public.monitoring_restore_requested_pid_job_schedule_0008() FROM PUBLIC;


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
        RAISE EXCEPTION '0008_requested_pid_time_weighted could not find the Phase 4 backfill job';
    END IF;
    PERFORM alter_job(backfill_job_id,
                      config => configured,
                      next_start => clock_timestamp() + INTERVAL '1 minute',
                      scheduled => TRUE);
END
$restart_aggregate_backfill$;

COMMIT;
