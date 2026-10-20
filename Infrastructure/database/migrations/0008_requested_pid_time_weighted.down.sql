\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5 minutes';
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
       OR to_regprocedure('public.monitoring_backfill_aggregates(integer,jsonb)') IS NULL THEN
        RAISE EXCEPTION '0008_requested_pid_time_weighted down requires the Phase 4 aggregate backfill';
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

DO $pause_overview_jobs$
DECLARE
    local_job_id INTEGER;
BEGIN
    FOR local_job_id IN
        SELECT job_id
        FROM timescaledb_information.jobs
        WHERE (proc_schema = 'public'
               AND proc_name IN ('monitoring_backfill_aggregates',
                                 'monitoring_refresh_overview_incremental',
                                 'monitoring_rebuild_relay_overview'))
    LOOP
        PERFORM alter_job(local_job_id, scheduled => FALSE);
    END LOOP;
END
$pause_overview_jobs$;

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
SET LOCAL statement_timeout = '5 minutes';
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
