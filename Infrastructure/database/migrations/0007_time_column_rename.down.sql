\set ON_ERROR_STOP on
-- Down also commits job quiescence before any aggregate/source DDL.
BEGIN;
SET LOCAL search_path = public, pg_catalog;
WITH cagg_targets AS (
    SELECT cagg.user_view_schema, cagg.user_view_name, cagg.mat_hypertable_id
    FROM _timescaledb_catalog.continuous_agg AS cagg
    WHERE cagg.user_view_schema = 'public'
      AND cagg.user_view_name = ANY (ARRAY[
          'monitoring_measurement_1min', 'monitoring_measurement_5min',
          'monitoring_measurement_1hour', 'monitoring_measurement_1day',
          'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
          'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
          'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
          'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
          'monitoring_relay_duration_1day'
      ])
),
target_jobs AS (
    SELECT job.job_id, job.proc_schema, job.proc_name, job.scheduled,
           job.schedule_interval, job.max_runtime, job.max_retries,
           job.retry_period, COALESCE(job.config, '{}'::JSONB) AS config,
           stats.next_start, job.check_schema::TEXT AS check_schema,
           job.check_name::TEXT AS check_name,
           job.fixed_schedule, job.initial_start, config_job.timezone,
           CASE WHEN cagg.user_view_name IS NOT NULL
                THEN cagg.user_view_schema ELSE job.hypertable_schema END AS target_schema,
           CASE WHEN cagg.user_view_name IS NOT NULL
                THEN cagg.user_view_name ELSE job.hypertable_name END AS target_name,
           cagg.user_view_name IS NOT NULL AS target_is_cagg
    FROM timescaledb_information.jobs AS job
    LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
    LEFT JOIN _timescaledb_config.bgw_job AS config_job
      ON config_job.id = job.job_id
    LEFT JOIN cagg_targets AS cagg
      ON cagg.mat_hypertable_id = CASE
          WHEN job.proc_name IN (
              'policy_refresh_continuous_aggregate',
              'policy_compression', 'policy_columnstore', 'policy_retention')
          THEN COALESCE(
              NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
              NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
          ELSE NULL
      END
    WHERE (job.proc_schema = 'public'
           AND job.proc_name IN (
               'monitoring_refresh_overview_incremental',
               'monitoring_rebuild_relay_overview',
               'monitoring_backfill_aggregates'))
       OR (job.proc_name IN (
               'policy_refresh_continuous_aggregate',
               'policy_compression', 'policy_columnstore', 'policy_retention')
           AND (
               (job.hypertable_schema = 'public'
                AND job.hypertable_name = ANY (ARRAY[
                    'automation_state', 'effective_setpoints', 'control_history',
                    'pid_parameter_history', 'relay_observation',
                    'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
                    'mode_transition_history'
                ]))
               OR cagg.user_view_name IS NOT NULL
           ))
)
INSERT INTO public.monitoring_job_snapshot_0007 (
    job_id, proc_schema, proc_name, scheduled, schedule_interval, max_runtime,
    max_retries, retry_period, config, next_start, target_schema, target_name,
    target_is_cagg, restore_after_down, check_schema, check_name, fixed_schedule,
    initial_start, timezone)
SELECT job_id, proc_schema, proc_name, scheduled, schedule_interval, max_runtime,
       max_retries, retry_period, config, next_start, target_schema, target_name,
       target_is_cagg,
       proc_name <> 'monitoring_backfill_aggregates'
         AND NOT (
             target_is_cagg
             AND proc_name IN ('policy_refresh_continuous_aggregate',
                               'policy_compression', 'policy_columnstore',
                               'policy_retention')),
       check_schema, check_name, fixed_schedule, initial_start, timezone
FROM target_jobs
ON CONFLICT (job_id) DO NOTHING;
SELECT alter_job(job.job_id, scheduled => FALSE)
FROM timescaledb_information.jobs AS job
WHERE job.job_id IN (
    SELECT job_id FROM public.monitoring_job_snapshot_0007
    UNION
    SELECT job_id FROM public.monitoring_refresh_policy_snapshot_0007
);
COMMIT;

DO $wait_for_down_phase4_jobs$
DECLARE
    jobs_running BOOLEAN;
    wait_timeout INTERVAL := INTERVAL '10 minutes';
    test_wait_timeout TEXT;
    wait_deadline TIMESTAMPTZ;
    running_job_details TEXT;
BEGIN
    IF current_database() ~ '^monitoring_test_[a-z0-9_]+$' THEN
        test_wait_timeout := NULLIF(
            current_setting('app.time_rename_test_quiesce_timeout', TRUE), '');
        IF test_wait_timeout IS NOT NULL THEN
            wait_timeout := test_wait_timeout::INTERVAL;
        END IF;
    END IF;
    wait_deadline := clock_timestamp() + wait_timeout;

    LOOP
        WITH target_jobs AS (
            SELECT job_id FROM public.monitoring_job_snapshot_0007
            UNION
            SELECT job_id FROM public.monitoring_refresh_policy_snapshot_0007
        )
        SELECT EXISTS (
            SELECT 1
            FROM target_jobs AS target
            WHERE EXISTS (
                SELECT 1
                FROM pg_stat_activity AS activity
                WHERE activity.datname = current_database()
                  AND activity.backend_type <> 'client backend'
                  AND activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
            )
        )
        INTO jobs_running;
        EXIT WHEN NOT jobs_running;
        IF clock_timestamp() >= wait_deadline THEN
            WITH target_jobs AS (
                SELECT job_id, proc_schema, proc_name, target_schema, target_name
                FROM public.monitoring_job_snapshot_0007
                UNION
                SELECT job_id, '_timescaledb_functions'::TEXT,
                       'policy_refresh_continuous_aggregate'::TEXT,
                       'public'::TEXT, view_name::TEXT
                FROM public.monitoring_refresh_policy_snapshot_0007
            )
            SELECT string_agg(
                format(
                    'job_id=%s proc_name=%s.%s hypertable/view=%s worker_pid=%s worker_app=%s runtime=%s',
                    target.job_id,
                    COALESCE(target.proc_schema, job.proc_schema::TEXT, '<unknown>'),
                    COALESCE(target.proc_name, job.proc_name::TEXT, '<unknown>'),
                    CASE
                        WHEN COALESCE(target.target_name, job.hypertable_name::TEXT) IS NULL
                            THEN '<none>'
                        ELSE format(
                            '%I.%I',
                            COALESCE(target.target_schema, job.hypertable_schema::TEXT, 'public'),
                            COALESCE(target.target_name, job.hypertable_name::TEXT))
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
            LEFT JOIN timescaledb_information.jobs AS job USING (job_id)
            LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
            LEFT JOIN LATERAL (
                SELECT activity.pid, activity.backend_start, activity.application_name
                FROM pg_stat_activity AS activity
                WHERE activity.datname = current_database()
                  AND activity.backend_type <> 'client backend'
                  AND activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
                ORDER BY activity.pid
                LIMIT 1
            ) AS worker ON TRUE
            WHERE worker.pid IS NOT NULL;
            IF running_job_details IS NULL THEN
                EXIT;
            END IF;
            RAISE EXCEPTION 'migration_jobs_still_running'
                USING DETAIL = running_job_details,
                      HINT = 'Jobs are unscheduled and snapshotted; let these workers finish (do not kill them), then re-run the migration.';
        END IF;
        PERFORM pg_sleep(1);
    END LOOP;
END
$wait_for_down_phase4_jobs$;
SELECT EXISTS (
    SELECT 1
    FROM (VALUES
        ('automation_state'),
        ('effective_setpoints'),
        ('control_history'),
        ('pid_parameter_history'),
        ('relay_observation'),
        ('monitoring_room_photoperiod'),
        ('monitoring_photoperiod_coverage'),
        ('mode_transition_history')
    ) AS source_table(table_name)
    JOIN information_schema.columns AS column_info
      ON column_info.table_schema = 'public'
     AND column_info.table_name = source_table.table_name
     AND column_info.column_name = 'time'
) AS rollback_phase_one_needed \gset
\if :rollback_phase_one_needed
-- Phase one is atomic. Its old-column shape is the durable marker; after it
-- commits, a retry skips destructive drops and renames while restoring SQL.

-- The prior canonical read-model files own their transactions. This first
-- transaction reverses the column/index names and removes the rebuilt views.
BEGIN;
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';
SET LOCAL search_path = public, pg_catalog;

DO $drop_backfill_job$
DECLARE
    local_job_id INTEGER;
BEGIN
    FOR local_job_id IN
        SELECT job_id
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name = 'monitoring_backfill_aggregates'
    LOOP
        PERFORM alter_job(local_job_id, scheduled => FALSE);
        PERFORM delete_job(local_job_id);
    END LOOP;
END
$drop_backfill_job$;
DROP PROCEDURE IF EXISTS public.monitoring_backfill_aggregates(INTEGER, JSONB);
DO $pause_phase4_jobs$
DECLARE
    local_job_id INTEGER;
BEGIN
    FOR local_job_id IN
        SELECT job.job_id
        FROM public.monitoring_job_snapshot_0007 AS snapshot
        JOIN timescaledb_information.jobs AS job USING (job_id)
    LOOP
        PERFORM alter_job(local_job_id, scheduled => FALSE);
    END LOOP;

    FOR local_job_id IN
        SELECT job.job_id
        FROM _timescaledb_catalog.continuous_agg AS cagg
        JOIN timescaledb_information.jobs AS job
          ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
        WHERE cagg.user_view_schema = 'public'
          AND (
              (job.proc_name = 'policy_refresh_continuous_aggregate'
               AND cagg.user_view_name = ANY (ARRAY[
                   'monitoring_measurement_5min',
                   'monitoring_measurement_1hour', 'monitoring_measurement_1day',
                   'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
                   'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
                   'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
                   'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
               ]))
              OR (job.proc_name = 'policy_compression'
                  AND cagg.user_view_name = ANY (ARRAY[
                      'monitoring_measurement_1min', 'monitoring_measurement_5min',
                      'monitoring_measurement_1hour', 'monitoring_measurement_1day',
                      'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
                      'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
                      'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
                      'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
                      'monitoring_relay_duration_1day'
                  ]))
          )
    LOOP
        PERFORM alter_job(local_job_id, scheduled => FALSE);
    END LOOP;
END
$pause_phase4_jobs$;

DO $remove_retained_aggregate_compression$
DECLARE
    view_to_restore TEXT;
    compressed_chunk REGCLASS;
BEGIN
    FOREACH view_to_restore IN ARRAY ARRAY[
        'monitoring_measurement_1min',
        'monitoring_relay_duration_1day'
    ] LOOP
        PERFORM remove_compression_policy(
            format('public.%I', view_to_restore)::REGCLASS,
            if_exists => TRUE);
        FOR compressed_chunk IN
            SELECT format('%I.%I', chunk.chunk_schema, chunk.chunk_name)::REGCLASS
            FROM _timescaledb_catalog.continuous_agg AS cagg
            JOIN _timescaledb_catalog.hypertable AS mat
              ON mat.id = cagg.mat_hypertable_id
            JOIN timescaledb_information.chunks AS chunk
              ON chunk.hypertable_schema = mat.schema_name
             AND chunk.hypertable_name = mat.table_name
            WHERE cagg.user_view_schema = 'public'
              AND cagg.user_view_name = view_to_restore
              AND chunk.is_compressed
        LOOP
            PERFORM decompress_chunk(compressed_chunk);
        END LOOP;
        EXECUTE format('ALTER MATERIALIZED VIEW public.%I SET (timescaledb.compress = false)',
                       view_to_restore);
    END LOOP;
END
$remove_retained_aggregate_compression$;

-- A Timescale policy launcher may be committing job metadata while a policy
-- worker exits; retry its transient catalog tuple race before aborting down.
DO $drop_rebuilt_aggregates$
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
$drop_rebuilt_aggregates$;

DO $drop_time_bounds$
DECLARE
    source_table TEXT;
BEGIN
    FOREACH source_table IN ARRAY ARRAY[
        'automation_state', 'effective_setpoints', 'control_history',
        'pid_parameter_history', 'relay_observation',
        'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
        'mode_transition_history', 'measurement'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I',
                       source_table, source_table || '_time_bounds');
    END LOOP;
END
$drop_time_bounds$;

DO $restore_event_columns$
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
        ) AS columns_to_restore(table_name, old_column)
    LOOP
        EXECUTE format('ALTER TABLE public.%I RENAME COLUMN %I TO %I',
                       source_column.table_name, 'time', source_column.old_column);
    END LOOP;
END
$restore_event_columns$;

DO $restore_event_indexes$
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
                           index_row.new_index_name,
                           index_row.old_constraint_name);
        ELSE
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I',
                           index_row.new_index_name,
                           index_row.old_index_name);
        END IF;
    END LOOP;
END
$restore_event_indexes$;
COMMIT;
DO $test_failure_after_first_commit$
DECLARE
    restored_columns INTEGER;
BEGIN
    IF current_database() ~ '^monitoring_test_[a-z0-9_]+$'
       AND COALESCE(
           current_setting('app.time_rename_test_fail_after_first_commit', TRUE) = '1',
           FALSE)
       AND to_regclass('public.monitoring_job_snapshot_0007') IS NOT NULL THEN
        SELECT count(*)
          INTO restored_columns
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name IN (
               'automation_state', 'effective_setpoints', 'control_history',
               'pid_parameter_history', 'relay_observation',
               'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
               'mode_transition_history')
           AND column_name IN ('timestamp', 'observed_at', 'triggered_at');
        IF restored_columns = 8 THEN
            RAISE EXCEPTION 'test-only failure after the first 0007 down migration commit';
        END IF;
    END IF;
END
$test_failure_after_first_commit$;
\endif

-- These exact pre-0007 canonical snapshots restore their old source-column
-- definitions and transactions. \\ir paths are relative to this down file.
SELECT set_config(
    CASE WHEN current_database() = 'sj_cea'
         THEN 'app.grow_cycle_history_authorized'
         ELSE 'app.grow_cycle_history_disposable'
    END,
    '1', FALSE);
\ir 0007_time_column_rename.down.d/monitoring_read_models.sql
\ir 0007_time_column_rename.down.d/monitoring_history_overview.sql
DO $restore_dependent_views$
DECLARE
    snapshot_row RECORD;
    existing_relation OID;
    existing_relkind TEXT;
    existing_definition TEXT;
BEGIN
    FOR snapshot_row IN
        SELECT view_schema, view_name, view_definition
        FROM public.monitoring_dependent_view_snapshot_0007
        ORDER BY view_schema, view_name
    LOOP
        SELECT relation.oid, relation.relkind::TEXT,
               CASE WHEN relation.relkind = 'v'
                    THEN pg_get_viewdef(relation.oid, TRUE) END
        INTO existing_relation, existing_relkind, existing_definition
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = snapshot_row.view_schema
          AND relation.relname = snapshot_row.view_name;

        IF NOT FOUND THEN
            EXECUTE format('CREATE VIEW %I.%I AS %s',
                           snapshot_row.view_schema,
                           snapshot_row.view_name,
                           snapshot_row.view_definition);
        ELSIF existing_relkind <> 'v' THEN
            RAISE EXCEPTION 'rollback dependent object %.% is not a view',
                snapshot_row.view_schema, snapshot_row.view_name;
        ELSIF existing_definition IS DISTINCT FROM snapshot_row.view_definition THEN
            RAISE EXCEPTION 'rollback dependent view %.% has a different definition',
                snapshot_row.view_schema, snapshot_row.view_name;
        END IF;
    END LOOP;
END
$restore_dependent_views$;
-- The old definitions contain ROW-based last() aggregations. Rebuild them
-- oldest-first in elapsed 24-hour UTC windows; saved definitions create only
-- missing aggregates and each refresh window is safe to replay after failure.
WITH source_bounds AS (
    SELECT min(source_min) AS source_min, max(source_max) AS source_max
    FROM (
        SELECT min("time") AS source_min, max("time") AS source_max
        FROM public.measurement
        UNION ALL
        SELECT min("timestamp"), max("timestamp") FROM public.effective_setpoints
        UNION ALL
        SELECT min("timestamp"), max("timestamp") FROM public.automation_state
    ) AS bounds
),
days AS (
    SELECT generate_series(
        date_trunc('day', source_min AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
        date_trunc('day', source_max AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
        INTERVAL '24 hours') AS day_start
    FROM source_bounds
),
views(ordinal, view_name) AS (
    VALUES
        (1, 'monitoring_measurement_5min'),
        (2, 'monitoring_measurement_1hour'),
        (3, 'monitoring_measurement_1day'),
        (4, 'monitoring_effective_setpoints_1min'),
        (5, 'monitoring_effective_setpoints_5min'),
        (6, 'monitoring_automation_state_1min'),
        (7, 'monitoring_automation_state_5min'),
        (8, 'monitoring_light_intensity_1hour'),
        (9, 'monitoring_light_intensity_1day'),
        (10, 'monitoring_requested_pid_1hour'),
        (11, 'monitoring_requested_pid_1day')
)
SELECT format(
    'CALL public.refresh_continuous_aggregate(%L::REGCLASS, %L::TIMESTAMPTZ, %L::TIMESTAMPTZ);',
    format('public.%I', views.view_name),
    days.day_start,
    days.day_start + INTERVAL '24 hours')
FROM days CROSS JOIN views
ORDER BY days.day_start, views.ordinal
\gexec

BEGIN;
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';
SET LOCAL search_path = public, pg_catalog;

DO $restore_refresh_policy_jobs$
DECLARE
    snapshot_row RECORD;
    materialization_id INTEGER;
    local_job_id INTEGER;
    local_config JSONB;
    local_check_config REGPROC;
    start_offset INTERVAL;
    end_offset INTERVAL;
BEGIN
    FOR snapshot_row IN
        SELECT view_name, scheduled, schedule_interval, max_runtime,
               max_retries, retry_period, config, next_start, check_schema,
               check_name, fixed_schedule, initial_start, timezone
        FROM public.monitoring_refresh_policy_snapshot_0007
LOOP
        SELECT mat_hypertable_id
        INTO materialization_id
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name = snapshot_row.view_name;
        IF materialization_id IS NULL THEN
            RAISE EXCEPTION 'rollback missing continuous aggregate %', snapshot_row.view_name;
        END IF;

        SELECT job.job_id
        INTO local_job_id
        FROM _timescaledb_catalog.continuous_agg AS cagg
        JOIN timescaledb_information.jobs AS job
          ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
        WHERE cagg.user_view_schema = 'public'
          AND cagg.user_view_name = snapshot_row.view_name
          AND job.proc_schema = '_timescaledb_functions'
          AND job.proc_name = 'policy_refresh_continuous_aggregate';

        IF NOT FOUND THEN
            start_offset := CASE
                WHEN jsonb_typeof(snapshot_row.config -> 'start_offset') = 'null'
                    THEN NULL::INTERVAL
                ELSE (snapshot_row.config ->> 'start_offset')::INTERVAL
            END;
            end_offset := CASE
                WHEN jsonb_typeof(snapshot_row.config -> 'end_offset') = 'null'
                    THEN NULL::INTERVAL
                ELSE (snapshot_row.config ->> 'end_offset')::INTERVAL
            END;
            local_job_id := add_continuous_aggregate_policy(
                format('public.%I', snapshot_row.view_name)::REGCLASS,
                start_offset => start_offset,
                end_offset => end_offset,
                schedule_interval => snapshot_row.schedule_interval,
                initial_start => snapshot_row.initial_start,
                timezone => snapshot_row.timezone,
                if_not_exists => FALSE);
            IF local_job_id IS NULL OR local_job_id < 1 THEN
                RAISE EXCEPTION 'rollback could not recreate refresh policy for %',
                    snapshot_row.view_name;
            END IF;
        END IF;

        local_config := jsonb_set(
            snapshot_row.config,
            '{mat_hypertable_id}',
            to_jsonb(materialization_id),
            TRUE);
        local_check_config := CASE
            WHEN snapshot_row.check_schema IS NULL
              OR snapshot_row.check_name IS NULL THEN NULL::REGPROC
            ELSE format(
                '%I.%I', snapshot_row.check_schema, snapshot_row.check_name)::REGPROC
END;
        PERFORM alter_job(
            local_job_id,
            schedule_interval => snapshot_row.schedule_interval,
            max_runtime => snapshot_row.max_runtime,
            max_retries => snapshot_row.max_retries,
            retry_period => snapshot_row.retry_period,
            scheduled => snapshot_row.scheduled,
            config => local_config,
            next_start => NULLIF(snapshot_row.next_start, '-infinity'::TIMESTAMPTZ),
            check_config => local_check_config,
            fixed_schedule => snapshot_row.fixed_schedule,
            initial_start => snapshot_row.initial_start,
            timezone => snapshot_row.timezone);
    END LOOP;
END
$restore_refresh_policy_jobs$;

CALL public.monitoring_restore_quiesced_jobs_0007();

DROP PROCEDURE public.monitoring_restore_quiesced_jobs_0007();
DROP TABLE public.monitoring_dependent_view_snapshot_0007;
DROP TABLE public.monitoring_index_snapshot_0007;
DROP TABLE public.monitoring_refresh_policy_snapshot_0007;
DROP TABLE public.monitoring_job_snapshot_0007;
COMMIT;
