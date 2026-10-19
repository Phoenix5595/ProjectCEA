-- Run as the database owner after reviewing paused overview jobs. This
-- acknowledges current failure totals and resumes only the paused custom jobs.
BEGIN;

DO $acknowledge_monitoring_failures$
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
    logical_name TEXT;
    is_custom BOOLEAN;
    recorded_failures INTEGER;
    failure_counts JSONB := '{}'::JSONB;
    job_record RECORD;
    updated_config JSONB;
    checked_jobs INTEGER := 0;
BEGIN
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
            LEFT JOIN timescaledb_information.job_stats AS job_stats
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

        failure_counts := jsonb_set(
            failure_counts,
            ARRAY[logical_name],
            to_jsonb(recorded_failures),
            TRUE);
    END LOOP;

    FOR job_record IN
        SELECT job_id, config, scheduled
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name = ANY (custom_job_names)
        ORDER BY job_id
    LOOP
        checked_jobs := checked_jobs + 1;

        IF job_record.config IS NULL
           OR jsonb_typeof(job_record.config) <> 'object'
           OR NOT (job_record.config ? 'enabled')
           OR jsonb_typeof(job_record.config -> 'enabled') <> 'boolean' THEN
            RAISE EXCEPTION 'overview job % has invalid config; no jobs resumed', job_record.job_id;
        END IF;

        IF NOT job_record.scheduled THEN
            updated_config := jsonb_set(
                job_record.config,
                '{acknowledged_failures}',
                failure_counts,
                TRUE);
            PERFORM public.alter_job(
                job_record.job_id,
                config => updated_config,
                scheduled => TRUE);
        END IF;
    END LOOP;

    IF checked_jobs = 0 THEN
        RAISE EXCEPTION 'no monitoring overview jobs found; no jobs resumed';
    END IF;

    FOR job_record IN
        SELECT job_id, config
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name = ANY (custom_job_names)
        ORDER BY job_id
    LOOP
        IF public.monitoring_overview_unacknowledged_failures(job_record.config) THEN
            RAISE EXCEPTION 'monitoring overview failure gate remains tripped for job %; changes rolled back',
                job_record.job_id;
        END IF;
    END LOOP;
END
$acknowledge_monitoring_failures$;

COMMIT;
