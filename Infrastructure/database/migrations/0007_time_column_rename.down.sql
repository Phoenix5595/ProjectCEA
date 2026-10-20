\set ON_ERROR_STOP on

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

DROP MATERIALIZED VIEW public.monitoring_light_intensity_1day;
DROP MATERIALIZED VIEW public.monitoring_requested_pid_1day;
DROP MATERIALIZED VIEW public.monitoring_measurement_1day;
DROP MATERIALIZED VIEW public.monitoring_light_intensity_1hour;
DROP MATERIALIZED VIEW public.monitoring_requested_pid_1hour;
DROP MATERIALIZED VIEW public.monitoring_measurement_1hour;
DROP MATERIALIZED VIEW public.monitoring_measurement_5min;
DROP MATERIALIZED VIEW public.monitoring_effective_setpoints_5min;
DROP MATERIALIZED VIEW public.monitoring_effective_setpoints_1min;
DROP MATERIALIZED VIEW public.monitoring_automation_state_5min;
DROP MATERIALIZED VIEW public.monitoring_automation_state_1min;

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
-- The old definitions contain ROW-based last() aggregations. Rebuild them
-- oldest-first one UTC source day at a time so each call stays within a source
-- chunk and the pre-release readers regain their materialized frontiers.
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
        INTERVAL '1 day') AS day_start
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
    days.day_start + INTERVAL '1 day')
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
    start_offset INTERVAL;
    end_offset INTERVAL;
BEGIN
    FOR snapshot_row IN
        SELECT view_name, scheduled, schedule_interval, max_runtime,
               max_retries, config
        FROM public.monitoring_refresh_policy_snapshot_0007
        ORDER BY view_name
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
        PERFORM alter_job(
            local_job_id,
            schedule_interval => snapshot_row.schedule_interval,
            max_runtime => snapshot_row.max_runtime,
            max_retries => snapshot_row.max_retries,
            scheduled => snapshot_row.scheduled,
            config => local_config);
    END LOOP;
END
$restore_refresh_policy_jobs$;

DO $restore_overview_jobs$
DECLARE
    snapshot_row RECORD;
BEGIN
    FOR snapshot_row IN
        SELECT job_id, scheduled, config
        FROM public.monitoring_job_snapshot_0007
        ORDER BY job_id
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.jobs
            WHERE job_id = snapshot_row.job_id
        ) THEN
            RAISE EXCEPTION 'rollback overview job % is missing', snapshot_row.job_id;
        END IF;
        PERFORM alter_job(snapshot_row.job_id,
                          scheduled => snapshot_row.scheduled,
                          config => snapshot_row.config);
    END LOOP;
END
$restore_overview_jobs$;

DROP TABLE public.monitoring_index_snapshot_0007;
DROP TABLE public.monitoring_refresh_policy_snapshot_0007;
DROP TABLE public.monitoring_job_snapshot_0007;
COMMIT;
