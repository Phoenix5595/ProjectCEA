-- migration: downtime=false
-- Require three consecutive failures before an acknowledged overview job is
-- considered failed; a later success clears the consecutive-failure gate.
BEGIN;

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
    consecutive_failures INTEGER;
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
            SELECT COALESCE(SUM(job_stats.total_failures), 0),
                   COALESCE(MAX(background_stats.consecutive_failures), 0)
            INTO recorded_failures, consecutive_failures
            FROM _timescaledb_catalog.bgw_job AS background_job
            LEFT JOIN timescaledb_information.job_stats AS job_stats
              ON job_stats.job_id = background_job.id
            LEFT JOIN _timescaledb_internal.bgw_job_stat AS background_stats
              ON background_stats.job_id = background_job.id
            WHERE background_job.proc_schema = 'public'
              AND background_job.proc_name = logical_name;
        ELSE
            SELECT COALESCE(SUM(job_stats.total_failures), 0),
                   COALESCE(MAX(background_stats.consecutive_failures), 0)
            INTO recorded_failures, consecutive_failures
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
            LEFT JOIN _timescaledb_internal.bgw_job_stat AS background_stats
              ON background_stats.job_id = refresh_job.id
            WHERE cagg.view_schema = 'public'
              AND cagg.view_name = logical_name;
        END IF;

        acknowledged_count := COALESCE(
            ((acknowledged -> logical_name) #>> '{}')::NUMERIC, 0);

        IF recorded_failures > acknowledged_count
           AND consecutive_failures >= 3 THEN
            RETURN TRUE;
        END IF;
    END LOOP;

    RETURN FALSE;
END
$function$;

COMMIT;
