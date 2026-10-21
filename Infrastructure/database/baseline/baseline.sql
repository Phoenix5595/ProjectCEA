--
-- PostgreSQL database dump
--

\restrict HbAVjU46hjlQhzvg1lanmOqfHpnyanOqZgtRQJi7ODkfRoVzScNj2boYD3g9Thl

-- Dumped from database version 15.19 (Debian 15.19-0+deb12u1)
-- Dumped by pg_dump version 15.19 (Debian 15.19-0+deb12u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: timescaledb; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS timescaledb WITH SCHEMA public;


--
-- Name: EXTENSION timescaledb; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION timescaledb IS 'Enables scalable inserts and complex queries for time-series data (Community Edition)';


--
-- Name: timescaledb_toolkit; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit WITH SCHEMA public;


--
-- Name: EXTENSION timescaledb_toolkit; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION timescaledb_toolkit IS 'Library of analytical hyperfunctions, time-series pipelining, and other SQL utilities';


--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: pg_stat_statements; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;


--
-- Name: EXTENSION pg_stat_statements; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_stat_statements IS 'track planning and execution statistics of all SQL statements executed';


--
-- Name: clear_deleted_water_tank_monitor_sensor(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.clear_deleted_water_tank_monitor_sensor() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE water_tank_monitor_config
    SET sensor_id = NULL,
        full_distance_mm = NULL,
        empty_distance_mm = NULL,
        capacity_liters = NULL,
        updated_at = NOW()
    WHERE sensor_id = OLD.sensor_id;
    RETURN OLD;
END
$$;


ALTER FUNCTION public.clear_deleted_water_tank_monitor_sensor() OWNER TO postgres;

--
-- Name: get_day_night_periods(text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.get_day_night_periods(p_room text, p_from timestamp with time zone, p_to timestamp with time zone) RETURNS TABLE("time" timestamp with time zone, is_day boolean, period_value real)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_lights_on TIME;
    v_lights_off TIME;
    v_current_date DATE;
    v_lights_on_ts TIMESTAMPTZ;
    v_lights_off_ts TIMESTAMPTZ;
BEGIN
    SELECT rls.lights_on, rls.lights_off 
    INTO v_lights_on, v_lights_off
    FROM room_light_schedule rls
    WHERE rls.room_name = p_room AND rls.is_active = TRUE
    ORDER BY rls.effective_from DESC
    LIMIT 1;
    
    IF v_lights_on IS NULL THEN
        v_lights_on := '06:00';
        v_lights_off := '18:00';
    END IF;
    
    FOR v_current_date IN 
        SELECT generate_series(p_from::date, p_to::date, '1 day'::interval)::date
    LOOP
        v_lights_on_ts := (v_current_date + v_lights_on)::timestamptz;
        v_lights_off_ts := (v_current_date + v_lights_off)::timestamptz;
        
        IF v_lights_on > v_lights_off THEN
            IF v_lights_on_ts >= p_from AND v_lights_on_ts <= p_to THEN
                "time" := v_lights_on_ts;
                is_day := TRUE;
                period_value := 100.0;
                RETURN NEXT;
            END IF;
            
            v_lights_off_ts := (v_current_date + INTERVAL '1 day' + v_lights_off)::timestamptz;
            IF v_lights_off_ts >= p_from AND v_lights_off_ts <= p_to THEN
                "time" := v_lights_off_ts;
                is_day := FALSE;
                period_value := NULL;
                RETURN NEXT;
            END IF;
        ELSE
            IF v_lights_on_ts >= p_from AND v_lights_on_ts <= p_to THEN
                "time" := v_lights_on_ts;
                is_day := TRUE;
                period_value := 100.0;
                RETURN NEXT;
            END IF;
            
            IF v_lights_off_ts >= p_from AND v_lights_off_ts <= p_to THEN
                "time" := v_lights_off_ts;
                is_day := FALSE;
                period_value := NULL;
                RETURN NEXT;
            END IF;
        END IF;
    END LOOP;
END;
$$;


ALTER FUNCTION public.get_day_night_periods(p_room text, p_from timestamp with time zone, p_to timestamp with time zone) OWNER TO postgres;

--
-- Name: get_light_periods(text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.get_light_periods(p_location text, p_from timestamp with time zone, p_to timestamp with time zone) RETURNS TABLE(ts timestamp with time zone, "DAY Period Overlay" real, "NIGHT Period Overlay" real)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_lights_on TIME;
    v_lights_off TIME;
    v_current_date DATE;
    v_on_ts TIMESTAMPTZ;
    v_off_ts TIMESTAMPTZ;
BEGIN
    SELECT rls.lights_on, rls.lights_off 
    INTO v_lights_on, v_lights_off
    FROM room_light_schedule rls
    WHERE rls.room_name = p_location AND rls.is_active = TRUE
    ORDER BY rls.effective_from DESC
    LIMIT 1;
    
    IF v_lights_on IS NULL THEN
        v_lights_on := '17:00';
        v_lights_off := '11:00';
    END IF;
    
    FOR v_current_date IN 
        SELECT generate_series(p_from::date - 1, p_to::date + 1, '1 day'::interval)::date
    LOOP
        v_on_ts := (v_current_date + v_lights_on)::timestamptz;
        
        IF v_lights_on > v_lights_off THEN
            v_off_ts := (v_current_date + INTERVAL '1 day' + v_lights_off)::timestamptz;
        ELSE
            v_off_ts := (v_current_date + v_lights_off)::timestamptz;
        END IF;
        
        -- DAY starts (lights on)
        IF v_on_ts >= p_from AND v_on_ts <= p_to THEN
            ts := v_on_ts;
            "DAY Period Overlay" := 100;
            "NIGHT Period Overlay" := NULL;
            RETURN NEXT;
        END IF;
        
        -- NIGHT starts (lights off)
        IF v_off_ts >= p_from AND v_off_ts <= p_to THEN
            ts := v_off_ts;
            "DAY Period Overlay" := NULL;
            "NIGHT Period Overlay" := 100;
            RETURN NEXT;
        END IF;
    END LOOP;
END;
$$;


ALTER FUNCTION public.get_light_periods(p_location text, p_from timestamp with time zone, p_to timestamp with time zone) OWNER TO postgres;

--
-- Name: grow_lifecycle_enforce_write_once(); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.grow_lifecycle_enforce_write_once() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    -- The batch linkage of a grow UUID is fixed; divergent linkage is rejected
    -- rather than merged or re-invented.
    IF NEW.crop_batch_id IS DISTINCT FROM OLD.crop_batch_id THEN
        RAISE EXCEPTION 'grow_lifecycle crop batch linkage is fixed for grow %', OLD.grow_plan_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'grow_lifecycle_crop_batch_write_once';
    END IF;
    -- original_drying_end and first_closed_at are written once at the first
    -- explicit closure and never change afterwards; reopening preserves both.
    IF OLD.first_closed_at IS NOT NULL
       AND (NEW.first_closed_at IS DISTINCT FROM OLD.first_closed_at
            OR NEW.original_drying_end IS DISTINCT FROM OLD.original_drying_end) THEN
        RAISE EXCEPTION 'grow_lifecycle first closure is write-once for grow %', OLD.grow_plan_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'grow_lifecycle_first_closure_write_once';
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.grow_lifecycle_enforce_write_once() OWNER TO cea_user;

--
-- Name: monitoring_assert_relation_columns(text, text[], text[]); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_assert_relation_columns(relation_name text, expected_names text[], expected_types text[]) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    actual_names TEXT[];
    actual_types TEXT[];
BEGIN
    SELECT
        array_agg(column_name::TEXT ORDER BY ordinal_position),
        array_agg(data_type::TEXT ORDER BY ordinal_position)
    INTO actual_names, actual_types
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = relation_name;

    IF actual_names IS DISTINCT FROM expected_names
       OR actual_types IS DISTINCT FROM expected_types THEN
        RAISE EXCEPTION 'incompatible monitoring object %: columns % / types %',
            relation_name, actual_names, actual_types;
    END IF;
END
$$;


ALTER FUNCTION public.monitoring_assert_relation_columns(relation_name text, expected_names text[], expected_types text[]) OWNER TO cea_user;

--
-- Name: monitoring_assert_timescaledb_catalog(); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_assert_timescaledb_catalog() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    extension_version TEXT;
    actual_columns TEXT[];
BEGIN
    SELECT extversion
    INTO extension_version
    FROM pg_extension
    WHERE extname = 'timescaledb';

    IF extension_version IS NULL
       OR split_part(extension_version, '.', 1)::INTEGER <> 2
       OR split_part(extension_version, '.', 2)::INTEGER NOT BETWEEN 23 AND 28 THEN
        RAISE EXCEPTION 'monitoring read models require TimescaleDB 2.23 through 2.28.x, got %',
            COALESCE(extension_version, 'not installed');
    END IF;

    SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
    INTO actual_columns
    FROM information_schema.columns
    WHERE table_schema = '_timescaledb_catalog'
      AND table_name = 'continuous_agg';
    IF actual_columns IS DISTINCT FROM ARRAY[
        'mat_hypertable_id', 'raw_hypertable_id', 'parent_mat_hypertable_id',
        'user_view_schema', 'user_view_name', 'partial_view_schema',
        'partial_view_name', 'direct_view_schema', 'direct_view_name',
        'materialized_only', 'schema_change_timestamp'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'unsupported TimescaleDB continuous_agg catalog shape: %', actual_columns;
    END IF;

    SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
    INTO actual_columns
    FROM information_schema.columns
    WHERE table_schema = '_timescaledb_catalog'
      AND table_name = 'continuous_aggs_hypertable_invalidation_log';
    IF actual_columns IS DISTINCT FROM ARRAY[
        'hypertable_id', 'lowest_modified_value', 'greatest_modified_value'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'unsupported TimescaleDB hypertable invalidation catalog shape: %', actual_columns;
    END IF;

    SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
    INTO actual_columns
    FROM information_schema.columns
    WHERE table_schema = '_timescaledb_catalog'
      AND table_name = 'continuous_aggs_materialization_invalidation_log';
    IF actual_columns IS DISTINCT FROM ARRAY[
        'materialization_id', 'lowest_modified_value', 'greatest_modified_value'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'unsupported TimescaleDB materialization invalidation catalog shape: %', actual_columns;
    END IF;

    SELECT array_agg(column_name::TEXT ORDER BY ordinal_position)
    INTO actual_columns
    FROM information_schema.columns
    WHERE table_schema = '_timescaledb_catalog'
      AND table_name = 'hypertable';
    IF actual_columns IS DISTINCT FROM ARRAY[
        'id', 'schema_name', 'table_name', 'associated_schema_name',
        'associated_table_prefix', 'num_dimensions', 'chunk_sizing_func_schema',
        'chunk_sizing_func_name', 'chunk_target_size', 'compression_state',
        'compressed_hypertable_id', 'status'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'unsupported TimescaleDB hypertable catalog shape: %', actual_columns;
    END IF;

    IF to_regprocedure('_timescaledb_functions.cagg_watermark(integer)') IS NULL
       OR to_regprocedure('_timescaledb_functions.to_timestamp(bigint)') IS NULL THEN
        RAISE EXCEPTION 'required TimescaleDB watermark functions are unavailable';
    END IF;
END
$$;


ALTER FUNCTION public.monitoring_assert_timescaledb_catalog() OWNER TO cea_user;

--
-- Name: monitoring_backfill_aggregates(integer, jsonb); Type: PROCEDURE; Schema: public; Owner: cea_user
--

CREATE PROCEDURE public.monitoring_backfill_aggregates(IN job_id integer, IN config jsonb)
    LANGUAGE plpgsql
    AS $$
DECLARE
    expected_views TEXT[] := ARRAY[
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
    configured_views TEXT[];
    configured JSONB := COALESCE(config, '{}'::JSONB);
    view_name TEXT;
    cursor_start TIMESTAMPTZ;
    next_cursor TIMESTAMPTZ;
    configured_start TIMESTAMPTZ;
    policy_job_id INTEGER;
    policy_count INTEGER;
BEGIN
    IF jsonb_typeof(configured) IS DISTINCT FROM 'object'
       OR jsonb_typeof(configured -> 'views') IS DISTINCT FROM 'array'
       OR jsonb_typeof(configured -> 'done') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(configured -> 'start') IS DISTINCT FROM 'string'
       OR jsonb_typeof(configured -> 'cursor') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'monitoring_backfill_aggregates: invalid job config';
    END IF;

    SELECT array_agg(entry.view_name ORDER BY entry.ordinality)
    INTO configured_views
    FROM jsonb_array_elements_text(configured -> 'views') WITH ORDINALITY
         AS entry(view_name, ordinality);
    IF configured_views IS DISTINCT FROM expected_views THEN
        RAISE EXCEPTION 'monitoring_backfill_aggregates: unexpected view order';
    END IF;

    configured_start := (configured ->> 'start')::TIMESTAMPTZ;
    cursor_start := (configured ->> 'cursor')::TIMESTAMPTZ;
    IF cursor_start < configured_start
       OR cursor_start IS DISTINCT FROM time_bucket(INTERVAL '1 day', cursor_start) THEN
        RAISE EXCEPTION 'monitoring_backfill_aggregates: cursor must be a UTC day boundary at or after start';
    END IF;

    IF (configured ->> 'done')::BOOLEAN THEN
        PERFORM alter_job(job_id, scheduled => FALSE);
        RETURN;
    END IF;

    -- Source windows are complete elapsed UTC days, independent of session
    -- TimeZone and daylight-saving transitions.
    next_cursor := cursor_start + INTERVAL '24 hours';
    FOREACH view_name IN ARRAY configured_views LOOP
        CALL public.refresh_continuous_aggregate(
            format('public.%I', view_name)::REGCLASS,
            cursor_start,
            next_cursor,
            TRUE,
            '{"buckets_per_batch":0}'::JSONB);
    END LOOP;

    configured := jsonb_set(configured, '{cursor}', to_jsonb(next_cursor), TRUE);
    IF next_cursor >= now() - INTERVAL '1 hour' THEN
        SELECT count(*)
        INTO policy_count
        FROM _timescaledb_catalog.continuous_agg AS cagg
        JOIN timescaledb_information.jobs AS policy
          ON (policy.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
        WHERE cagg.user_view_schema = 'public'
          AND cagg.user_view_name = ANY (expected_views)
          AND policy.proc_schema = '_timescaledb_functions'
          AND policy.proc_name = 'policy_refresh_continuous_aggregate';
        IF policy_count <> cardinality(expected_views) THEN
            RAISE EXCEPTION 'monitoring_backfill_aggregates: expected % refresh policies, found %',
                cardinality(expected_views), policy_count;
        END IF;

        FOR policy_job_id IN
            SELECT policy.job_id
            FROM _timescaledb_catalog.continuous_agg AS cagg
            JOIN timescaledb_information.jobs AS policy
              ON (policy.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
            WHERE cagg.user_view_schema = 'public'
              AND cagg.user_view_name = ANY (expected_views)
              AND policy.proc_schema = '_timescaledb_functions'
              AND policy.proc_name = 'policy_refresh_continuous_aggregate'
        LOOP
            PERFORM alter_job(policy_job_id, scheduled => TRUE);
        END LOOP;

        CALL public.monitoring_restore_quiesced_jobs_0007();

        configured := jsonb_set(configured, '{done}', 'true'::JSONB, TRUE);
        PERFORM alter_job(job_id, scheduled => FALSE, config => configured);
        IF to_regclass('public.monitoring_job_snapshot_0008') IS NOT NULL
           AND to_regprocedure(
               'public.monitoring_restore_requested_pid_job_schedule_0008()'
           ) IS NOT NULL THEN
            EXECUTE 'CALL public.monitoring_restore_requested_pid_job_schedule_0008()';
        END IF;
    ELSE
        PERFORM alter_job(job_id, config => configured);
    END IF;
END
$$;


ALTER PROCEDURE public.monitoring_backfill_aggregates(IN job_id integer, IN config jsonb) OWNER TO cea_user;

--
-- Name: PROCEDURE monitoring_backfill_aggregates(IN job_id integer, IN config jsonb); Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON PROCEDURE public.monitoring_backfill_aggregates(IN job_id integer, IN config jsonb) IS 'Oldest-first UTC-day materialization of the eleven Phase 4 aggregates; on completion schedules their refresh policies and restores Phase 4 jobs plus the Phase 5 requested-PID job schedules when present.';


--
-- Name: monitoring_overview_unacknowledged_failures(jsonb); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_overview_unacknowledged_failures(config jsonb) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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
$$;


ALTER FUNCTION public.monitoring_overview_unacknowledged_failures(config jsonb) OWNER TO cea_user;

--
-- Name: monitoring_rebuild_relay_overview(integer, jsonb); Type: PROCEDURE; Schema: public; Owner: cea_user
--

CREATE PROCEDURE public.monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_cfg            JSONB := COALESCE(config, '{}'::JSONB);
    v_enabled        BOOLEAN := FALSE;
    v_locked         BOOLEAN;
    v_prior          public.monitoring_relay_overview_progress%ROWTYPE;
    v_watermark      BIGINT;
    v_claim_from     TIMESTAMPTZ;
    v_claim_through  TIMESTAMPTZ;
    v_empty_through  TIMESTAMPTZ;
    v_hour_start     TIMESTAMPTZ;
    v_hour_end       TIMESTAMPTZ;
    v_hour_index     INTEGER;
    v_cvg_from       TIMESTAMPTZ;
    v_cvg_through    TIMESTAMPTZ;
    v_daily_from     TIMESTAMPTZ;
    v_daily_through  TIMESTAMPTZ;
    v_daily_front    TIMESTAMPTZ;
    v_daily_ok       BOOLEAN;
    v_walk_i         INTEGER;
    v_build_failed   BOOLEAN := FALSE;
    v_fault_sqlstate TEXT;
    v_fault_message  TEXT;

    v_lock_key BIGINT := hashtextextended('projectcea.relay-overview-rebuild', 0);
BEGIN
    IF job_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs AS owned
        WHERE owned.job_id = monitoring_rebuild_relay_overview.job_id
          AND owned.proc_schema = 'public'
          AND owned.proc_name = 'monitoring_rebuild_relay_overview'
    ) THEN
        RAISE EXCEPTION 'relay rebuild job identity does not match this procedure';
    END IF;
    -----------------------------------------------------------------
    -- 0. Owner enable flag.
    -----------------------------------------------------------------
    IF jsonb_typeof(v_cfg -> 'enabled') = 'boolean' THEN
        v_enabled := (v_cfg -> 'enabled')::BOOLEAN;
    END IF;
    IF v_enabled IS NOT TRUE THEN
        RETURN;  -- owner-disabled: silent no-op, no fault, no pause
    END IF;

    -----------------------------------------------------------------
    -- 1. Failure gate (shared helper).
    -----------------------------------------------------------------
    IF public.monitoring_overview_unacknowledged_failures(v_cfg) THEN
        IF job_id IS NOT NULL THEN
            PERFORM alter_job(job_id, scheduled => FALSE);
        END IF;
        UPDATE public.monitoring_relay_overview_progress
           SET last_error = 'unacknowledged job failures: paused pending owner acknowledgement'
         WHERE id;
        COMMIT;
        RAISE EXCEPTION
            'monitoring_rebuild_relay_overview: unacknowledged job failures; paused pending owner acknowledgement';
    END IF;

    -----------------------------------------------------------------
    -- 2. Exclusive session advisory lock.
    --    The invocation holds it until BOTH fault checkpoints commit or
    --    the successful path releases it, so a concurrent invocation can
    --    never inherit a half-cleared claim while a previous one's
    --    cleanup transaction is still pending release.
    -----------------------------------------------------------------
    v_locked := pg_try_advisory_lock(v_lock_key);
    IF NOT v_locked THEN
        RETURN;  -- another invocation owns the rebuild path
    END IF;

    -----------------------------------------------------------------
    -- 3. Short claim transaction.
    -----------------------------------------------------------------
    SET LOCAL lock_timeout = '1s';

    BEGIN
        SELECT * INTO v_prior
          FROM public.monitoring_relay_overview_progress WHERE id FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'monitoring_relay_overview_progress singleton is missing';
        END IF;

        IF v_prior.claimed_through IS NOT NULL THEN
            -- The session lock proves the previous invocation is gone.
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL, claimed_through = NULL,
                   dirty_during_claim_from = NULL
             WHERE id;
        END IF;

        IF v_prior.dirty_from IS NOT NULL
           AND v_prior.dirty_through IS NOT NULL
           AND v_prior.dirty_from < v_prior.dirty_through THEN
            v_claim_from := date_trunc('day',
                LEAST(v_prior.dirty_from,
                      COALESCE(v_prior.covered_through, v_prior.dirty_from))
                AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
            SELECT COALESCE(MAX(observation_id), 0) INTO v_watermark
              FROM public.relay_observation;
            v_empty_through := public.monitoring_relay_empty_span_end(
                v_claim_from, v_prior.dirty_through, v_watermark);
            IF v_empty_through > v_claim_from + INTERVAL '24 hours' THEN
                IF v_prior.covered_from IS NOT NULL
                   AND v_empty_through < v_prior.covered_from THEN
                    v_cvg_from := v_claim_from;
                    v_cvg_through := v_empty_through;
                    v_prior.dirty_through := GREATEST(
                        v_prior.dirty_through, v_prior.covered_through);
                ELSE
                    v_cvg_from := LEAST(v_prior.covered_from, v_claim_from);
                    v_cvg_through := GREATEST(v_prior.covered_through, v_empty_through);
                END IF;
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from, covered_through = v_cvg_through,
                       dirty_from = CASE WHEN v_prior.dirty_through > v_empty_through
                                         THEN v_empty_through END,
                       dirty_through = CASE WHEN v_prior.dirty_through > v_empty_through
                                            THEN v_prior.dirty_through END,
                       last_error = NULL
                 WHERE id;
                INSERT INTO public.monitoring_overview_coverage
                    (model_name, covered_from, covered_through)
                VALUES ('monitoring_relay_duration_1hour', v_cvg_from, v_cvg_through)
                ON CONFLICT (model_name) DO UPDATE
                    SET covered_from = EXCLUDED.covered_from,
                        covered_through = EXCLUDED.covered_through;
                -- Verified empty coverage, not a multi-day build claim.
                v_claim_from := NULL;
            ELSIF v_claim_from + INTERVAL '24 hours' <=
                  date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' THEN
                v_claim_through := v_claim_from + INTERVAL '24 hours';
                UPDATE public.monitoring_relay_overview_progress
                   SET claimed_from = v_claim_from, claimed_through = v_claim_through,
                       dirty_during_claim_from = NULL
                 WHERE id;
            ELSE
                -- Leave the unfinished UTC day to the exact bounded raw tail.
                -- Heartbeats inside it must not dirty materialized future time.
                v_claim_from := NULL;
                v_cvg_from := v_prior.covered_from;
                v_cvg_through := v_prior.covered_through;
            END IF;
        ELSE
            -- A separate daily refresh may have finished after the last build.
            v_cvg_from := v_prior.covered_from;
            v_cvg_through := v_prior.covered_through;
        END IF;
    EXCEPTION WHEN query_canceled OR OTHERS THEN
        GET STACKED DIAGNOSTICS v_fault_sqlstate = RETURNED_SQLSTATE,
                                v_fault_message = MESSAGE_TEXT;
        v_build_failed := TRUE;
    END;

    IF v_build_failed THEN
        -- Failure checkpoint with ownership retained until both commits
        -- have landed: the job pause and the fault metadata write happen
        -- BEFORE the session advisory lock is released, so a concurrent
        -- next invocation never observes a stale claim mid-cleanup. If a
        -- commit is refused (e.g. a busy row or external termination) the
        -- lock releases before the ORIGINAL SQLSTATE re-raises, matching
        -- the previous exception-safe unlock semantics.
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
        BEGIN
            SET LOCAL lock_timeout = '1s';
            UPDATE public.monitoring_relay_overview_progress
               SET last_error = v_fault_sqlstate || ': ' || v_fault_message
             WHERE id;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- fault metadata landed before ownership drops
        PERFORM pg_advisory_unlock(v_lock_key);
        RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                              MESSAGE = v_fault_message;
    END IF;

    COMMIT;  -- claim committed; the singleton row lock releases here

    -----------------------------------------------------------------
    -- 4. Build transaction (one claim day).
    -----------------------------------------------------------------
    SET LOCAL lock_timeout = '1s';

    BEGIN  -- subtransaction: the whole day rolls back on any catchable error
        IF v_claim_from IS NOT NULL THEN
        -- Additive rewrite of the claimed day: mark the claimed span's
        -- prior rows inactive first, then upsert the verified snapshot's
        -- own hour rows back to active, so no derived row or index is ever
        -- deleted. Inactive rows are ignored by the daily continuous
        -- aggregate and by the parent's API readers (unknown time), and
        -- unknown-only legitimate lanes stay active exactly as before.
        UPDATE public.monitoring_relay_duration_1hour AS prior_row
           SET active = FALSE
         WHERE prior_row.bucket >= v_claim_from
           AND prior_row.bucket < v_claim_through;

        FOR v_hour_index IN 0..23 LOOP
            v_hour_start := v_claim_from + make_interval(hours => v_hour_index);
            v_hour_end := v_hour_start + INTERVAL '1 hour';

            -- Sum integer-microsecond slice lengths. Unknown-only identities
            -- survive only when a qualified owner was actually recorded in
            -- this hour; no inactive owner is filled across unrecorded years.
            INSERT INTO public.monitoring_relay_duration_1hour AS rebuilt
                (bucket, location, cluster, channel, device_id, device_name,
                 device_type, on_microseconds, off_microseconds,
                 last_observed_at, source_watermark, active)
            SELECT derived.bucket,
                   derived.location, derived.cluster, derived.channel,
                   derived.device_id, derived.device_name, derived.device_type,
                   derived.on_us, derived.off_us,
                   owner_at.last_observed_at, v_watermark, TRUE
              FROM (
                SELECT v_hour_start AS bucket,
                       slices.location, slices.cluster, slices.channel,
                       slices.device_id, slices.device_name, slices.device_type,
                       COALESCE(sum(
                           (extract(epoch FROM slices.end_at - slices.start_at)
                            * 1000000)::BIGINT)
                           FILTER (WHERE slices.state = 'on'), 0)::BIGINT AS on_us,
                       COALESCE(sum(
                           (extract(epoch FROM slices.end_at - slices.start_at)
                            * 1000000)::BIGINT)
                           FILTER (WHERE slices.state = 'off'), 0)::BIGINT AS off_us
                  FROM public.monitoring_relay_duration_slices(
                           v_hour_start, v_hour_end, v_watermark) AS slices
                 GROUP BY 1, 2, 3, 4, 5, 6, 7
              ) AS derived
              JOIN LATERAL (
                SELECT max(ro."time") AS last_observed_at
                  FROM public.relay_observation AS ro
                 WHERE ro.observation_id <= v_watermark
                   AND ro."time" < v_hour_end
                   AND ro.location = derived.location
                   AND ro.cluster = derived.cluster
                   AND ro.channel = derived.channel
                   AND ro.device_id = derived.device_id
                   AND ro.device_name = derived.device_name
                   AND ro.device_type = derived.device_type
              ) AS owner_at ON TRUE
             WHERE derived.on_us > 0 OR derived.off_us > 0
                OR owner_at.last_observed_at >= v_hour_start
             ON CONFLICT (bucket, location, cluster, channel, device_id,
                          device_name, device_type) DO UPDATE SET
                    on_microseconds  = EXCLUDED.on_microseconds,
                    off_microseconds = EXCLUDED.off_microseconds,
                    last_observed_at = EXCLUDED.last_observed_at,
                    source_watermark = EXCLUDED.source_watermark,
                    active           = TRUE;
        END LOOP;

        -----------------------------------------------------------------
        -- Final short checkpoint (same transaction as the hour rows).
        -----------------------------------------------------------------
        SELECT * INTO v_prior FROM public.monitoring_relay_overview_progress WHERE id FOR UPDATE;

        IF v_prior.claimed_from IS NULL
           OR v_prior.claimed_through IS NULL
           OR v_prior.claimed_from <> v_claim_from
           OR v_prior.claimed_through <> v_claim_through THEN
            RAISE EXCEPTION 'relay overview claim state changed under the build transaction';
        END IF;

        IF v_prior.dirty_during_claim_from IS NOT NULL
           AND v_prior.dirty_during_claim_from < v_claim_through THEN
            -- Overlapping dirt entered the claimed span during the build:
            -- retain the earliest dirty work, advance no coverage, release
            -- the claim; the claim day stays pending for a verifiably
            -- clean pass.
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL,
                   claimed_through = NULL,
                   dirty_during_claim_from = NULL,
                   last_error = NULL
             WHERE id;
        ELSE
            -- Clean checkpoint: coverage advances through the verified
            -- claim day (contiguity holds because claims run at the covered
            -- frontier or inside verified range where GREATEST is a no-op);
            -- the claim day is consumed from the dirty bounds; dirt beyond
            -- it stays pending.
            IF v_prior.covered_from IS NOT NULL
               AND v_claim_through < v_prior.covered_from THEN
                -- Restart a verified prefix, retaining dirt through the old
                -- frontier. Never declare the intervening gap covered.
                v_cvg_from := v_claim_from;
                v_cvg_through := v_claim_through;
                v_prior.dirty_through := GREATEST(
                    v_prior.dirty_through, v_prior.covered_through);
            ELSE
                v_cvg_from := LEAST(v_prior.covered_from, v_claim_from);
                v_cvg_through := GREATEST(v_prior.covered_through, v_claim_through);
            END IF;

            IF v_prior.dirty_through > v_claim_through THEN
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from,
                       covered_through = v_cvg_through,
                       dirty_from = v_claim_through,
                       dirty_through = v_prior.dirty_through,
                       claimed_from = NULL,
                       claimed_through = NULL,
                       dirty_during_claim_from = NULL,
                       last_error = NULL
                 WHERE id;
            ELSE
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from,
                       covered_through = v_cvg_through,
                       dirty_from = NULL,
                       dirty_through = NULL,
                       claimed_from = NULL,
                       claimed_through = NULL,
                       dirty_during_claim_from = NULL,
                       last_error = NULL
                 WHERE id;
            END IF;

            INSERT INTO public.monitoring_overview_coverage
                (model_name, covered_from, covered_through)
            VALUES ('monitoring_relay_duration_1hour', v_cvg_from, v_cvg_through)
            ON CONFLICT (model_name) DO UPDATE
                SET covered_from = EXCLUDED.covered_from,
                    covered_through = EXCLUDED.covered_through;

        END IF;
        END IF;
            -----------------------------------------------------------------
            -- Daily coverage verification (read-only against the daily
            -- materialization; NO refresh happens here). The daily frontier
            -- advances only across days whose hourly rows are covered AND
            -- whose daily materialization rows equal those hourly rows
            -- exactly (symmetric EXCEPT ALL: additions to either side prove
            -- staleness; verified-empty days compare as equal absences —
            -- their "no invented owner" emptiness was proven by the very
            -- machine runs that advanced the hourly frontier). The walk is
            -- capped so the checkpoint stays short; a paused daily policy
            -- means the materialization matches only what refreshes have
            -- processed so far, so an unrefreshed day stalls the walk.
            -----------------------------------------------------------------
        IF v_cvg_from IS NOT NULL AND v_cvg_through IS NOT NULL THEN
            SELECT covered_from, covered_through
              INTO v_daily_from, v_daily_through
              FROM public.monitoring_overview_coverage
             WHERE model_name = 'monitoring_relay_duration_1day';

            IF v_daily_from IS NOT NULL AND v_daily_from <> v_cvg_from THEN
                -- An earlier verified prefix temporarily supersedes the old
                -- daily coverage; its gap must be refreshed and checked too.
                UPDATE public.monitoring_overview_coverage
                   SET covered_from = v_cvg_from, covered_through = v_cvg_from
                 WHERE model_name = 'monitoring_relay_duration_1day';
                v_daily_through := NULL;
            END IF;

            v_daily_front := COALESCE(v_daily_through, v_cvg_from);

            FOR v_walk_i IN 1..24 LOOP
                EXIT WHEN (v_daily_front + INTERVAL '24 hours') > v_cvg_through;
                EXIT WHEN NOT EXISTS (
                    SELECT 1 FROM public.monitoring_history_cagg_watermark
                     WHERE cagg_name = 'monitoring_relay_duration_1day'
                       AND materialization_watermark >= v_daily_front + INTERVAL '24 hours');
                EXIT WHEN EXISTS (
                    SELECT 1 FROM public.monitoring_history_cagg_watermark
                     WHERE cagg_name = 'monitoring_relay_duration_1day'
                       AND invalidation_start < v_daily_front + INTERVAL '24 hours'
                       AND invalidation_end >= v_daily_front);

                SELECT NOT EXISTS (
                    (SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            sum(prior_row.on_microseconds)::BIGINT,
                            sum(prior_row.off_microseconds)::BIGINT,
                            max(prior_row.last_observed_at),
                            max(prior_row.source_watermark)
                       FROM public.monitoring_relay_duration_1hour AS prior_row
                      WHERE prior_row.active
                        AND prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '24 hours'
                      GROUP BY 1, 2, 3, 4, 5, 6
                     EXCEPT ALL
                     SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            prior_row.on_microseconds,
                            prior_row.off_microseconds,
                            prior_row.last_observed_at,
                            prior_row.source_watermark
                       FROM public.monitoring_relay_duration_1day AS prior_row
                      WHERE prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '24 hours')
                    UNION ALL
                    (SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            prior_row.on_microseconds,
                            prior_row.off_microseconds,
                            prior_row.last_observed_at,
                            prior_row.source_watermark
                       FROM public.monitoring_relay_duration_1day AS prior_row
                      WHERE prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '24 hours'
                     EXCEPT ALL
                     SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            sum(prior_row.on_microseconds)::BIGINT,
                            sum(prior_row.off_microseconds)::BIGINT,
                            max(prior_row.last_observed_at),
                            max(prior_row.source_watermark)
                       FROM public.monitoring_relay_duration_1hour AS prior_row
                      WHERE prior_row.active
                        AND prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '24 hours'
                      GROUP BY 1, 2, 3, 4, 5, 6)
                ) INTO v_daily_ok;

                IF v_daily_ok IS NOT TRUE THEN
                    EXIT;
                END IF;

                v_daily_front := v_daily_front + INTERVAL '24 hours';
            END LOOP;

            IF v_daily_front > v_cvg_from THEN
                INSERT INTO public.monitoring_overview_coverage
                    (model_name, covered_from, covered_through)
                VALUES ('monitoring_relay_duration_1day', v_cvg_from, v_daily_front)
                ON CONFLICT (model_name) DO UPDATE
                    SET covered_from = EXCLUDED.covered_from,
                        covered_through = EXCLUDED.covered_through;
            END IF;
        END IF;
    EXCEPTION
        WHEN query_canceled OR OTHERS THEN
            GET STACKED DIAGNOSTICS v_fault_sqlstate = RETURNED_SQLSTATE,
                                    v_fault_message = MESSAGE_TEXT;
            v_build_failed := TRUE;
    END;

    -----------------------------------------------------------------
    -- 5. Failure path: runs OUTSIDE the failed subtransaction so the
    -- pause/fault commit lands even though the build did not. Dirty and
    -- covered columns are untouched (the subtransaction already returned
    -- them); the claim releases together with the recorded error, and the
    -- advisory lock is relinquished only after both fault commits so a
    -- concurrent invocation can never inherit a half-cleared claim.
    -----------------------------------------------------------------
    IF v_build_failed THEN
        -----------------------------------------------------------------
        -- Ownership stays with THIS invocation through both commits so a
        -- concurrent next invocation observes a consistent pause state
        -- instead of inheriting a stale claim. If either commit cannot be
        -- completed the advisory lock is released before re-raising the
        -- original error, replicating the pre-existing semantics (the
        -- claim remains abandoned-but-present, no coverage advance, and
        -- the hash-lock dependency remains symmetrical for the next
        -- session that takes it).
        -----------------------------------------------------------------
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
        BEGIN
            SET LOCAL lock_timeout = '1s';
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL,
                   claimed_through = NULL,
                   dirty_during_claim_from = NULL,
                   last_error = v_fault_sqlstate || ': ' || v_fault_message
             WHERE id;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- fault metadata and claim release landed before ownership drops
        PERFORM pg_advisory_unlock(v_lock_key);
        RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                              MESSAGE = v_fault_message;
    END IF;

    -----------------------------------------------------------------
    -- Success: commit the build + checkpoint, release the advisory lock.
    -----------------------------------------------------------------
    COMMIT;
    PERFORM pg_advisory_unlock(v_lock_key);
END;
$$;


ALTER PROCEDURE public.monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb) OWNER TO cea_user;

--
-- Name: PROCEDURE monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb); Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON PROCEDURE public.monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb) IS 'Pausable relay duration rebuild: one UTC day claim + pinned observation_id
watermark per invocation; the 24 hour slices run from the exact support
machine at that watermark; the checkpoint commits hourly rows, dirty bounds
and coverage rows atomically; unacknowledged failures gate before work and
pause only this job (scheduled=false) with the owner config preserved.';


--
-- Name: monitoring_refresh_overview_incremental(integer, jsonb); Type: PROCEDURE; Schema: public; Owner: cea_user
--

CREATE PROCEDURE public.monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb)
    LANGUAGE plpgsql
    AS $$
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

            -- The measurement hour must not outrun its 1min source frontier.
            v_hour_ready := FALSE;
            IF v_no_work IS NOT TRUE THEN
                v_hour_ready := EXISTS (
                    SELECT 1
                      FROM public.monitoring_history_cagg_watermark
                     WHERE cagg_name = v_hour_view
                       AND materialization_watermark >= v_hour_cand_end
                ) AND (
                    v_family IS DISTINCT FROM 'measurement'
                    OR EXISTS (
                        SELECT 1 FROM public.monitoring_history_cagg_watermark
                         WHERE cagg_name = 'monitoring_measurement_1min'
                           AND materialization_watermark >= v_hour_cand_end
                    )
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.monitoring_history_cagg_watermark
                     WHERE (
                               cagg_name = v_hour_view
                               OR (v_family IS NOT DISTINCT FROM 'measurement'
                                   AND cagg_name = 'monitoring_measurement_1min')
                           )
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
                               SELECT time_bucket(INTERVAL '1 hour', "time") AS bucket,
                                      location, cluster, device_name,
                                      count(effective_light_intensity) AS sample_count,
                                      sum(effective_light_intensity::NUMERIC) AS value_sum,
                                      sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS value_sum_squares,
                                      min(effective_light_intensity::NUMERIC) AS min_value,
                                      max(effective_light_intensity::NUMERIC) AS max_value,
                                      max("time") AS last_observed_at
                                 FROM public.effective_setpoints
                                WHERE device_name IS NOT NULL
                                  AND (effective_light_intensity IS NOT NULL
                                       OR nominal_light_intensity IS NOT NULL
                                       OR ramp_progress_light IS NOT NULL)
                                  AND "time" >= v_hour_front
                                  AND "time" < v_hour_cand_end
                                GROUP BY 1, 2, 3, 4
                           ) AS hour_raw)
                        UNION ALL
                        (SELECT hour_raw.bucket, hour_raw.location,
                                hour_raw.cluster, hour_raw.device_name,
                                hour_raw.sample_count, hour_raw.value_sum,
                                hour_raw.value_sum_squares, hour_raw.min_value,
                                hour_raw.max_value, hour_raw.last_observed_at
                           FROM (
                               SELECT time_bucket(INTERVAL '1 hour', "time") AS bucket,
                                      location, cluster, device_name,
                                      count(effective_light_intensity) AS sample_count,
                                      sum(effective_light_intensity::NUMERIC) AS value_sum,
                                      sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS value_sum_squares,
                                      min(effective_light_intensity::NUMERIC) AS min_value,
                                      max(effective_light_intensity::NUMERIC) AS max_value,
                                      max("time") AS last_observed_at
                                 FROM public.effective_setpoints
                                WHERE device_name IS NOT NULL
                                  AND (effective_light_intensity IS NOT NULL
                                       OR nominal_light_intensity IS NOT NULL
                                       OR ramp_progress_light IS NOT NULL)
                                  AND "time" >= v_hour_front
                                  AND "time" < v_hour_cand_end
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
                        WITH hour_raw AS (
                            SELECT time_bucket(INTERVAL '1 hour', "time") AS bucket,
                                   location, cluster, device_name,
                                   count(*)::BIGINT AS sample_count,
                                   min(round((pid_output::DOUBLE PRECISION
                                              * 100::DOUBLE PRECISION)::NUMERIC, 13))
                                       FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                               AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_min,
                                   max(round((pid_output::DOUBLE PRECISION
                                              * 100::DOUBLE PRECISION)::NUMERIC, 13))
                                       FILTER (WHERE pid_output::DOUBLE PRECISION >= 0::DOUBLE PRECISION
                                               AND pid_output::DOUBLE PRECISION <= 1::DOUBLE PRECISION) AS requested_max,
                                   max("time") AS last_observed_at
                              FROM public.automation_state
                             WHERE "time" >= v_hour_front
                               AND "time" < v_hour_cand_end
                             GROUP BY 1, 2, 3, 4
                        ),
                        differences AS (
                            (SELECT hour.bucket, hour.location, hour.cluster,
                                    hour.device_name, hour.sample_count,
                                    hour.requested_min, hour.requested_max,
                                    hour.last_observed_at
                               FROM public.monitoring_requested_pid_1hour AS hour
                              WHERE hour.bucket >= v_hour_front
                                AND hour.bucket < v_hour_cand_end
                             EXCEPT ALL
                             SELECT bucket, location, cluster, device_name,
                                    sample_count, requested_min, requested_max,
                                    last_observed_at
                               FROM hour_raw)
                            UNION ALL
                            (SELECT bucket, location, cluster, device_name,
                                    sample_count, requested_min, requested_max,
                                    last_observed_at
                               FROM hour_raw
                             EXCEPT ALL
                             SELECT hour.bucket, hour.location, hour.cluster,
                                    hour.device_name, hour.sample_count,
                                    hour.requested_min, hour.requested_max,
                                    hour.last_observed_at
                               FROM public.monitoring_requested_pid_1hour AS hour
                              WHERE hour.bucket >= v_hour_front
                                AND hour.bucket < v_hour_cand_end)
                        )
                        SELECT 1 FROM differences
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
            --     may not run past the previously verified hourly frontier,
            --     must sit at least 26 hours behind now(), and needs a clean
            --     native daily watermark. Measurement additionally requires
            --     clean hourly and 1min ancestor materialization. Pending
            --     invalidations on any tier block this candidate.
            -----------------------------------------------------------------
            v_day_cand_end := v_day_front + INTERVAL '24 hours';
            v_day_ready := v_day_cand_end <= v_hour_front
                AND v_day_cand_end <= now() - INTERVAL '26 hours'
                AND EXISTS (
                    SELECT 1 FROM public.monitoring_history_cagg_watermark
                     WHERE cagg_name = v_day_view
                       AND materialization_watermark >= v_day_cand_end
                ) AND (
                    v_family IS DISTINCT FROM 'measurement'
                    OR (
                        EXISTS (
                            SELECT 1 FROM public.monitoring_history_cagg_watermark
                             WHERE cagg_name = v_hour_view
                               AND materialization_watermark >= v_day_cand_end
                        )
                        AND EXISTS (
                            SELECT 1 FROM public.monitoring_history_cagg_watermark
                             WHERE cagg_name = 'monitoring_measurement_1min'
                               AND materialization_watermark >= v_day_cand_end
                        )
                    )
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.monitoring_history_cagg_watermark
                     WHERE (
                               cagg_name = v_day_view
                               OR cagg_name = v_hour_view
                               OR (v_family IS NOT DISTINCT FROM 'measurement'
                                   AND cagg_name = 'monitoring_measurement_1min')
                           )
                       AND invalidation_start < v_day_cand_end
                       AND invalidation_end >= v_day_front
                );


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
                        WITH hourly_rollup AS (
                            SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket,
                                   hourly.location, hourly.cluster, hourly.device_name,
                                   sum(hourly.sample_count)::BIGINT AS sample_count,
                                   min(hourly.requested_min) AS requested_min,
                                   max(hourly.requested_max) AS requested_max,
                                   max(hourly.last_observed_at) AS last_observed_at
                              FROM public.monitoring_requested_pid_1hour AS hourly
                             WHERE hourly.bucket >= v_day_front
                               AND hourly.bucket < v_day_cand_end
                             GROUP BY 1, 2, 3, 4
                        ),
                        differences AS (
                            (SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                    day.location, day.cluster, day.device_name,
                                    sum(day.sample_count)::BIGINT AS sample_count,
                                    min(day.requested_min) AS requested_min,
                                    max(day.requested_max) AS requested_max,
                                    max(day.last_observed_at) AS last_observed_at
                               FROM public.monitoring_requested_pid_1day AS day
                              WHERE day.bucket >= v_day_front
                                AND day.bucket < v_day_cand_end
                              GROUP BY 1, 2, 3, 4
                             EXCEPT ALL
                             SELECT bucket, location, cluster, device_name,
                                    sample_count, requested_min, requested_max,
                                    last_observed_at
                               FROM hourly_rollup)
                            UNION ALL
                            (SELECT bucket, location, cluster, device_name,
                                    sample_count, requested_min, requested_max,
                                    last_observed_at
                               FROM hourly_rollup
                             EXCEPT ALL
                             SELECT time_bucket(INTERVAL '1 day', day.bucket) AS bucket,
                                    day.location, day.cluster, day.device_name,
                                    sum(day.sample_count)::BIGINT,
                                    min(day.requested_min), max(day.requested_max),
                                    max(day.last_observed_at)
                               FROM public.monitoring_requested_pid_1day AS day
                              WHERE day.bucket >= v_day_front
                                AND day.bucket < v_day_cand_end
                              GROUP BY 1, 2, 3, 4)
                        )
                        SELECT 1 FROM differences
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
$$;


ALTER PROCEDURE public.monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb) OWNER TO cea_user;

--
-- Name: PROCEDURE monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb); Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON PROCEDURE public.monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb) IS 'Pausable bounded verified checkpoint: one UTC hour and at most one UTC day
per invocation per aggregate family (the family is read from the job''s config). The
native paused one-bucket policies advance the materialization and the
checkpoint verifies it against the raw source (symmetric EXCEPT ALL over
the candidate window) and the native per-catalog watermark/invalidation state
before advancing the hourly and (once the daily materialization catches
up) daily coverage rows atomically; missing/cancel/failure never advances
and unacknowledged failures pause only this family job (scheduled=false)
with owner config preserved.';


--
-- Name: monitoring_relay_duration_slices(timestamp with time zone, timestamp with time zone, bigint); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_duration_slices(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint) RETURNS TABLE(location text, cluster text, channel smallint, device_id integer, device_name text, device_type text, start_at timestamp with time zone, end_at timestamp with time zone, state text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
    v_start_us   BIGINT := COALESCE(public.monitoring_relay_epoch_us(range_start), NULL);
    v_end_us     BIGINT := COALESCE(public.monitoring_relay_epoch_us(range_end), NULL);
    v_wm         BIGINT := COALESCE(watermark, 0);
    v_gap_us     BIGINT := 60000000;  -- HEARTBEAT_MAX_GAP_MS = 60 s
    r            RECORD;
    v_t_us       BIGINT;
    i            INTEGER;
    ci           INTEGER;
    v_clipped_us BIGINT;
    v_state_int  TEXT;
    lane_count   INTEGER := 0;

    lane_location    TEXT[]     := '{}';
    lane_cluster     TEXT[]     := '{}';
    lane_channel     SMALLINT[] := '{}';
    lane_device_id   INTEGER[]  := '{}';
    lane_device_name TEXT[]     := '{}';
    lane_device_type TEXT[]     := '{}';
    lane_session     UUID[]     := '{}';  -- null: no session recorded yet
    lane_state       BOOLEAN[]  := '{}';  -- null means unknown
    lane_support     BIGINT[]   := '{}';  -- newest support instant (us)
    lane_cursor      BIGINT[]   := '{}';
    lane_pend_open   BOOLEAN[]  := '{}';  -- an un-emitted interval is pending
    lane_pend_start  BIGINT[]   := '{}';
    lane_pend_state  TEXT[]     := '{}';

    -- Per-channel anchor cache. ck_relay_observation_channel constrains
    -- channels to 0..15, so index 1..16 holds channel + 1.
    a_exists         BOOLEAN[16] := ARRAY_FILL(FALSE, ARRAY[16]);
    a_id             BIGINT[16]  := ARRAY_FILL(NULL::BIGINT, ARRAY[16]);
    a_us             BIGINT[16]  := ARRAY_FILL(NULL::BIGINT, ARRAY[16]);
    a_session        UUID[16]    := ARRAY_FILL(NULL::UUID, ARRAY[16]);
    a_state          BOOLEAN[16] := ARRAY_FILL(NULL::BOOLEAN, ARRAY[16]);
    a_reason         TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_loc            TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_cluster        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_devid          INTEGER[16] := ARRAY_FILL(NULL::INTEGER, ARRAY[16]);
    a_devname        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_devtype        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    v_last_support   BIGINT;
    v_hb_session     UUID;
    v_anchor_matches BOOLEAN;
    v_supports       BOOLEAN;
BEGIN
    IF v_start_us IS NULL OR v_end_us IS NULL OR v_start_us >= v_end_us THEN
        RETURN;  -- empty/malformed window: no fabricated spans
    END IF;

    -----------------------------------------------------------------
    -- Heartbeat anchor: the global latest heartbeat strictly before the
    -- window start, bounded to the snapshot watermark (the service's
    -- anchors payload delivers exactly this row to the TS engine).
    -----------------------------------------------------------------
    SELECT o.session_id
      INTO v_hb_session
      FROM public.relay_observation o
     WHERE o.observation_id <= v_wm
       AND o.reason = 'heartbeat'
       AND o."time" < range_start
     ORDER BY o."time" DESC, o.observation_id DESC
     LIMIT 1;

    -----------------------------------------------------------------
    -- Per-channel anchors: the true newest pre-window row per channel.
    -- Matching identity per lane below reproduces the service's
    -- location/cluster/'main'/not-'light' anchor filtering exactly: when a
    -- channel's newest pre-window row is another room's, a light's, or an
    -- unattributed row, every lane on that channel loses its baseline,
    -- which is what an absent filtered anchor produced in TS anyway.
    -----------------------------------------------------------------
    FOR r IN
        SELECT DISTINCT ON (o.channel)
               o.channel, o.observation_id, o."time", o.session_id,
               o.observed_state, o.reason,
               o.device_id, o.device_name, o.device_type,
               o.location, o.cluster
          FROM public.relay_observation o
         WHERE o.observation_id <= v_wm
           AND o.channel IS NOT NULL
           AND o."time" < range_start
         ORDER BY o.channel, o."time" DESC, o.observation_id DESC
    LOOP
        ci := (r.channel)::INT + 1;
        a_exists[ci]  := TRUE;
        a_id[ci]      := r.observation_id;
        a_us[ci]      := public.monitoring_relay_epoch_us(r."time");
        a_session[ci] := r.session_id;
        a_state[ci]   := r.observed_state;
        a_reason[ci]  := r.reason;
        a_loc[ci]     := r.location;
        a_cluster[ci] := r.cluster;
        a_devid[ci]   := r.device_id;
        a_devname[ci] := r.device_name;
        a_devtype[ci] := r.device_type;
    END LOOP;

    -----------------------------------------------------------------
    -- Lane enumeration. (a) any in-window full non-heartbeat identity;
    -- (b) the per-channel anchor identity when complete and not 'light'.
    -- Lanes dedupe so each identity owns exactly one machine.
    -----------------------------------------------------------------
    FOR r IN
        SELECT DISTINCT ro.location, ro.cluster, ro.channel,
               ro.device_id, ro.device_name, ro.device_type
          FROM public.relay_observation ro
         WHERE ro.observation_id <= v_wm
           AND ro."time" >= range_start
           AND ro."time" < range_end
           AND ro.reason <> 'heartbeat'
           AND ro.channel IS NOT NULL
           AND ro.location IS NOT NULL
           AND ro.cluster IS NOT NULL
           AND ro.device_id IS NOT NULL
           AND ro.device_name IS NOT NULL
           AND ro.device_type IS NOT NULL
           AND ro.device_type <> 'light'
    LOOP
        lane_count := lane_count + 1;
        lane_location[lane_count]    := r.location;
        lane_cluster[lane_count]     := r.cluster;
        lane_channel[lane_count]     := r.channel;
        lane_device_id[lane_count]   := r.device_id;
        lane_device_name[lane_count] := r.device_name;
        lane_device_type[lane_count] := r.device_type;
    END LOOP;

    FOR ci IN 1..16 LOOP
        IF a_exists[ci]
           AND a_loc[ci] IS NOT NULL
           AND a_cluster[ci] IS NOT NULL
           AND a_devid[ci] IS NOT NULL
           AND a_devname[ci] IS NOT NULL
           AND a_devtype[ci] IS NOT NULL
           AND a_devtype[ci] <> 'light'
           AND NOT EXISTS (
                SELECT 1
                  FROM generate_series(1, lane_count) AS known(idx)
                 WHERE lane_location[idx]    = a_loc[ci]
                   AND lane_cluster[idx]     = a_cluster[ci]
                   AND lane_channel[idx]     = (ci - 1)::SMALLINT
                   AND lane_device_id[idx]   = a_devid[ci]
                   AND lane_device_name[idx] = a_devname[ci]
                   AND lane_device_type[idx] = a_devtype[ci])
        THEN
            lane_count := lane_count + 1;
            lane_location[lane_count]    := a_loc[ci];
            lane_cluster[lane_count]     := a_cluster[ci];
            lane_channel[lane_count]     := (ci - 1)::SMALLINT;
            lane_device_id[lane_count]   := a_devid[ci];
            lane_device_name[lane_count] := a_devname[ci];
            lane_device_type[lane_count] := a_devtype[ci];
        END IF;
    END LOOP;

    IF lane_count = 0 THEN
        RETURN;  -- no recorded owners in the snapshot for this window
    END IF;

    -----------------------------------------------------------------
    -- Anchor seed per lane (the TS initial assignment), integer microseconds.
    -----------------------------------------------------------------
    FOR i IN 1..lane_count LOOP
        ci := (lane_channel[i])::INT + 1;
        v_anchor_matches := a_exists[ci]
            AND a_loc[ci]     = lane_location[i]
            AND a_cluster[ci] = lane_cluster[i]
            AND a_devid[ci]   = lane_device_id[i]
            AND a_devname[ci] = lane_device_name[i]
            AND a_devtype[ci] = lane_device_type[i]
            AND a_state[ci] IS NOT NULL
            AND a_reason[ci] IN ('initial', 'state_changed', 'recovered',
                                 'assignment_changed');
        v_supports := FALSE;
        v_last_support := NULL;
        IF v_anchor_matches THEN
            WITH heartbeats AS (
                SELECT o.session_id,
                       public.monitoring_relay_epoch_us(o."time") AS at_us,
                       lag(public.monitoring_relay_epoch_us(o."time"),
                           1, a_us[ci]) OVER (
                           ORDER BY o."time", o.observation_id) AS previous_us
                  FROM public.relay_observation AS o
                 WHERE o.observation_id <= v_wm
                   AND o.reason = 'heartbeat'
                   AND (o."time", o.observation_id) >
                       (public.monitoring_relay_ts_at_us(a_us[ci]), a_id[ci])
                   AND o."time" < range_start
            )
            SELECT COALESCE(bool_and(
                       session_id = a_session[ci] AND at_us - previous_us <= v_gap_us),
                       TRUE),
                   COALESCE(max(at_us), a_us[ci])
              INTO v_supports, v_last_support FROM heartbeats;
            v_supports := v_supports
                AND v_start_us - v_last_support <= v_gap_us;
        END IF;
        lane_state[i]      := CASE WHEN v_supports THEN a_state[ci] ELSE NULL END;
        lane_session[i]    := CASE WHEN v_supports THEN a_session[ci]
                                  ELSE COALESCE(v_hb_session, a_session[ci]) END;
        lane_support[i]    := CASE WHEN v_supports THEN v_last_support ELSE NULL END;
        lane_cursor[i]     := v_start_us;
        lane_pend_open[i]  := FALSE;
        lane_pend_start[i] := NULL;
        lane_pend_state[i] := NULL;
    END LOOP;

    -----------------------------------------------------------------
    -- Row stream in the global ("time", observation_id) order.
    -- Heartbeats apply to every lane; non-heartbeat rows apply to lanes on
    -- that row's channel (the TS filter reason='heartbeat' OR channel
    -- match). ck_relay_observation_channel_reason guarantees non-heartbeat
    -- rows carry a nonnull channel, so the lane filter is total.
    -----------------------------------------------------------------
    FOR r IN
        SELECT o."time", o.observation_id, o.session_id, o.reason,
               o.channel, o.observed_state,
               o.device_id, o.device_name, o.device_type,
               o.location, o.cluster
          FROM public.relay_observation o
         WHERE o.observation_id <= v_wm
           AND o."time" >= range_start
           AND o."time" < range_end
         ORDER BY o."time", o.observation_id
    LOOP
        v_t_us := public.monitoring_relay_epoch_us(r."time");
        IF v_t_us IS NULL OR v_t_us >= v_end_us THEN
            CONTINUE;  -- half-open window guard
        END IF;

        FOR i IN 1..lane_count LOOP
            IF r.reason <> 'heartbeat' AND lane_channel[i] <> r.channel THEN
                CONTINUE;
            END IF;

            -----------------------------------------------------------------
            -- expireBefore (reproduced identically at every enterUnknown)
            -- runs first: a known state expires exactly 60 s after the
            -- newest support record; unknown stays unknown — heartbeats
            -- after expiry never resurrect a state.
            -----------------------------------------------------------------
            IF lane_state[i] IS NOT NULL AND lane_support[i] IS NOT NULL
               AND v_t_us > lane_support[i] + v_gap_us THEN
                v_clipped_us := LEAST(lane_support[i] + v_gap_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
            END IF;

            -----------------------------------------------------------------
            -- Session tracking precedes every other branch (the TS runs it
            -- for heartbeats and payload rows alike), so a session change
            -- always ends the current run even from supported state.
            -----------------------------------------------------------------
            IF lane_session[i] IS NOT NULL
               AND r.session_id IS DISTINCT FROM lane_session[i] THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                lane_session[i] := r.session_id;
            ELSIF lane_session[i] IS NULL THEN
                lane_session[i] := r.session_id;
            END IF;

            IF r.reason = 'heartbeat' THEN
                IF lane_state[i] IS NOT NULL THEN
                    lane_support[i] := v_t_us;  -- support refresh while known
                END IF;
                CONTINUE;
            END IF;

            IF r.reason = 'stale' OR r.reason = 'recording_gap' THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                CONTINUE;
            END IF;

            -----------------------------------------------------------------
            -- Ownership: exact identity match across the whole six-tuple.
            -- A row with any NULL identity can never match (exactly like
            -- belongsToLane against a concrete identity), and 'light' rows
            -- disown every non-light lane sharing their channel instead of
            -- forming a lane themselves.
            -----------------------------------------------------------------
            IF r.location    IS DISTINCT FROM lane_location[i]
               OR r.cluster  IS DISTINCT FROM lane_cluster[i]
               OR r.device_id    IS DISTINCT FROM lane_device_id[i]
               OR r.device_name  IS DISTINCT FROM lane_device_name[i]
               OR r.device_type  IS DISTINCT FROM lane_device_type[i] THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);  -- assignment changed
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                CONTINUE;
            END IF;

            -- Owner row reached. assignment_changed / initial / recovered
            -- with a nonnull observed_state enter a baseline and with a
            -- null observed_state end the run; state_changed either
            -- refreshes support or switches the run.
            IF r.observed_state IS NULL THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
            ELSIF lane_state[i] IS NULL THEN
                -- enterBaseline with unknown state: FIRST the TS appends
                -- the unknown run from the cursor to the baseline instant
                -- (flushing any pending interval), THEN it moves the cursor
                -- forward (never backward) and adopts the new state. The
                -- pending unknown run stays open for the same merge/
                -- flush rule so the next append cuts it cleanly.
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := 'unknown';
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_cursor[i]  := GREATEST(lane_cursor[i], v_t_us);
                lane_state[i]   := r.observed_state;
                lane_support[i] := v_t_us;
            ELSIF lane_state[i] = r.observed_state THEN
                lane_support[i] := v_t_us;  -- same-state support refresh
            ELSE
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := r.observed_state;
                lane_support[i] := v_t_us;
            END IF;
        END LOOP;
    END LOOP;

    -----------------------------------------------------------------
    -- Tail: expire then clip at range_end. dataEnd equals range_end here
    -- (the caller reads its own bounded snapshot; there is no live edge),
    -- so the trailing "outside queried history" unknown run has zero
    -- width and no row.
    -----------------------------------------------------------------
    FOR i IN 1..lane_count LOOP
        IF lane_state[i] IS NOT NULL AND lane_support[i] IS NOT NULL
           AND v_end_us > lane_support[i] + v_gap_us THEN
            v_clipped_us := LEAST(lane_support[i] + v_gap_us, v_end_us);
            IF v_clipped_us > lane_cursor[i] THEN
                v_state_int := CASE
                    WHEN lane_state[i] IS NULL THEN 'unknown'
                    WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                IF NOT (lane_pend_open[i]
                        AND lane_pend_state[i] = 'unknown'
                        AND v_state_int = 'unknown') THEN
                    IF lane_pend_open[i] THEN
                        RETURN QUERY
                           SELECT lane_location[i], lane_cluster[i],
                                  lane_channel[i], lane_device_id[i],
                                  lane_device_name[i], lane_device_type[i],
                                  public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                  public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                  lane_pend_state[i];
                    END IF;
                    lane_pend_open[i]  := TRUE;
                    lane_pend_start[i] := lane_cursor[i];
                    lane_pend_state[i] := v_state_int;
                END IF;
                lane_cursor[i] := v_clipped_us;
            END IF;
            lane_state[i]   := NULL;
            lane_support[i] := NULL;
        END IF;

        v_clipped_us := v_end_us;
        IF v_clipped_us > lane_cursor[i] THEN
            v_state_int := CASE
                WHEN lane_state[i] IS NULL THEN 'unknown'
                WHEN lane_state[i] THEN 'on' ELSE 'off' END;
            IF NOT (lane_pend_open[i]
                    AND lane_pend_state[i] = 'unknown'
                    AND v_state_int = 'unknown') THEN
                IF lane_pend_open[i] THEN
                    RETURN QUERY
                       SELECT lane_location[i], lane_cluster[i],
                              lane_channel[i], lane_device_id[i],
                              lane_device_name[i], lane_device_type[i],
                              public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                              public.monitoring_relay_ts_at_us(lane_cursor[i]),
                              lane_pend_state[i];
                END IF;
                lane_pend_open[i]  := TRUE;
                lane_pend_start[i] := lane_cursor[i];
                lane_pend_state[i] := v_state_int;
            END IF;
            lane_cursor[i] := v_clipped_us;
        END IF;

        IF lane_pend_open[i] THEN
            RETURN QUERY
               SELECT lane_location[i], lane_cluster[i],
                      lane_channel[i], lane_device_id[i],
                      lane_device_name[i], lane_device_type[i],
                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                      lane_pend_state[i];
            lane_pend_open[i] := FALSE;
        END IF;
    END LOOP;
END;
$$;


ALTER FUNCTION public.monitoring_relay_duration_slices(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint) OWNER TO cea_user;

--
-- Name: FUNCTION monitoring_relay_duration_slices(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint); Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON FUNCTION public.monitoring_relay_duration_slices(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint) IS 'Exact SQL port of the canonical relay support machine
(features/relay-timeline/intervals.ts): half-open windows, integer
microsecond arithmetic, caller-supplied observation_id snapshot watermark,
60 s heartbeat expiry without resurrection, exact six-column ownership; no
live edge, registry or current-snapshot dependency.';


--
-- Name: monitoring_relay_empty_span_end(timestamp with time zone, timestamp with time zone, bigint); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_empty_span_end(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint) RETURNS timestamp with time zone
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
    WITH source_end AS (
        SELECT date_trunc('day', LEAST(range_end, COALESCE((
            SELECT "time" - INTERVAL '60 seconds'
              FROM public.relay_observation
             WHERE observation_id <= watermark
               AND "time" >= range_start - INTERVAL '60 seconds'
             ORDER BY "time", observation_id LIMIT 1
        ), range_end)) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS through
    )
    SELECT CASE WHEN through <= range_start + INTERVAL '24 hours' THEN through
        ELSE date_trunc('day', LEAST(through, COALESCE((
            SELECT bucket FROM public.monitoring_relay_duration_1hour
             WHERE active
               AND bucket >= range_start ORDER BY bucket LIMIT 1
        ), through)) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    END FROM source_end
$$;


ALTER FUNCTION public.monitoring_relay_empty_span_end(range_start timestamp with time zone, range_end timestamp with time zone, watermark bigint) OWNER TO cea_user;

--
-- Name: monitoring_relay_epoch_us(timestamp with time zone); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_epoch_us(ts timestamp with time zone) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
    SELECT
        (extract(epoch FROM date_trunc('second', ts)))::BIGINT * 1000000
        + round(extract(epoch
              FROM (ts - date_trunc('second', ts))) * 1000000)::BIGINT
$$;


ALTER FUNCTION public.monitoring_relay_epoch_us(ts timestamp with time zone) OWNER TO cea_user;

--
-- Name: monitoring_relay_overview_hourly_ready(timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_overview_hourly_ready(required_from timestamp with time zone, required_through timestamp with time zone) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT required_from IS NOT NULL
       AND required_through IS NOT NULL
       AND required_from <= required_through
       AND EXISTS (
            SELECT 1
              FROM public.monitoring_overview_coverage
             WHERE model_name = 'monitoring_relay_duration_1hour'
               AND covered_from <= required_from
               AND covered_through >= required_through
       )
       AND NOT EXISTS (
            SELECT 1
              FROM public.monitoring_relay_overview_progress
             WHERE dirty_from IS NOT NULL
               AND dirty_through IS NOT NULL
               AND dirty_from < required_through
               AND dirty_through > required_from
       )
$$;


ALTER FUNCTION public.monitoring_relay_overview_hourly_ready(required_from timestamp with time zone, required_through timestamp with time zone) OWNER TO cea_user;

--
-- Name: FUNCTION monitoring_relay_overview_hourly_ready(required_from timestamp with time zone, required_through timestamp with time zone); Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON FUNCTION public.monitoring_relay_overview_hourly_ready(required_from timestamp with time zone, required_through timestamp with time zone) IS 'Coverage/readiness gate for the relay duration hourly tier: verified
contiguous coverage must contain the requested span and no pending dirty
work may overlap it; the daily tier additionally consults the daily
coverage row plus native daily invalidation state.';


--
-- Name: monitoring_relay_overview_progress_dirty(); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_overview_progress_dirty() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    v_changed_min  TIMESTAMPTZ;
    v_changed_max  TIMESTAMPTZ;
    v_mark_from    TIMESTAMPTZ;
    v_mark_through TIMESTAMPTZ;
BEGIN
    IF TG_RELID <> 'public.relay_observation'::regclass THEN
        RAISE EXCEPTION 'relay dirty trigger may only observe public.relay_observation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT min("time"), max("time")
          INTO v_changed_min, v_changed_max
          FROM relay_observation_inserted;
    ELSIF TG_OP = 'UPDATE' THEN
        -- A mutated row owns both its old and new instants so neither side
        -- of a displaced identity ever drops out of the pending window.
        SELECT min("time"), max("time")
          INTO v_changed_min, v_changed_max
          FROM (
              SELECT "time" FROM relay_observation_update_old
              UNION ALL
              SELECT "time" FROM relay_observation_update_new
          ) AS changed;
    ELSE
        RETURN NULL;  -- unreachable: only INSERT/UPDATE triggers exist
    END IF;

    IF v_changed_min IS NULL OR v_changed_max IS NULL THEN
        RETURN NULL;  -- empty statement: nothing to mark
    END IF;

    v_mark_from := v_changed_min - INTERVAL '60 seconds';
    v_mark_through := GREATEST(v_changed_max + INTERVAL '60 seconds',
                               now() + INTERVAL '60 seconds');

    -- One upsert: the ON CONFLICT branch reads the CURRENT singleton row
    -- under the row lock, so concurrent statements serialize. Columns
    -- omitted from the SET list (covered_*, claim columns, last_error)
    -- pass through unchanged; only dirt bounds and the marker move.
    INSERT INTO public.monitoring_relay_overview_progress (
        id, covered_from, covered_through,
        dirty_from, dirty_through,
        claimed_from, claimed_through,
        dirty_during_claim_from, dirty_generation, last_error
    )
    VALUES (
        TRUE, NULL, NULL,
        v_mark_from, v_mark_through, NULL, NULL,
        NULL, 1, NULL
    )
    ON CONFLICT (id) DO UPDATE SET
        dirty_from = LEAST(
            COALESCE(monitoring_relay_overview_progress.dirty_from,
                     'infinity'::timestamptz),
            COALESCE(EXCLUDED.dirty_from, 'infinity'::timestamptz)),
        dirty_through = GREATEST(
            COALESCE(monitoring_relay_overview_progress.dirty_through,
                     '-infinity'::timestamptz),
            COALESCE(EXCLUDED.dirty_through, '-infinity'::timestamptz),
            COALESCE(monitoring_relay_overview_progress.covered_through,
                     '-infinity'::timestamptz)),
        dirty_during_claim_from = CASE
            WHEN monitoring_relay_overview_progress.claimed_from IS NOT NULL
                 AND monitoring_relay_overview_progress.claimed_through IS NOT NULL
            THEN LEAST(
                COALESCE(monitoring_relay_overview_progress.dirty_during_claim_from,
                         'infinity'::timestamptz),
                COALESCE(EXCLUDED.dirty_from, 'infinity'::timestamptz))
            ELSE NULL END,
        dirty_generation = monitoring_relay_overview_progress.dirty_generation + 1;
    RETURN NULL;  -- AFTER statement trigger return
END;
$$;


ALTER FUNCTION public.monitoring_relay_overview_progress_dirty() OWNER TO cea_user;

--
-- Name: monitoring_relay_ts_at_us(bigint); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.monitoring_relay_ts_at_us(epoch_us bigint) RETURNS timestamp with time zone
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
    SELECT TIMESTAMPTZ '1970-01-01 00:00:00+00'
         + (epoch_us * INTERVAL '1 microsecond')
$$;


ALTER FUNCTION public.monitoring_relay_ts_at_us(epoch_us bigint) OWNER TO cea_user;

--
-- Name: monitoring_restore_quiesced_jobs_0007(); Type: PROCEDURE; Schema: public; Owner: cea_user
--

CREATE PROCEDURE public.monitoring_restore_quiesced_jobs_0007()
    LANGUAGE plpgsql
    AS $$
DECLARE
    snapshot_row RECORD;
    local_job_id INTEGER;
    local_mat_hypertable_id INTEGER;
    local_config JSONB;
    local_check_config REGPROC;
BEGIN
    FOR snapshot_row IN
        SELECT *
        FROM public.monitoring_job_snapshot_0007
        WHERE restore_after_down
        ORDER BY job_id
    LOOP
        local_job_id := NULL;
        local_config := snapshot_row.config;
        local_check_config := CASE
            WHEN snapshot_row.check_schema IS NULL
              OR snapshot_row.check_name IS NULL THEN NULL::REGPROC
            ELSE format(
                '%I.%I', snapshot_row.check_schema, snapshot_row.check_name)::REGPROC
        END;

        IF snapshot_row.target_is_cagg THEN
            SELECT cagg.mat_hypertable_id
              INTO local_mat_hypertable_id
              FROM _timescaledb_catalog.continuous_agg AS cagg
             WHERE cagg.user_view_schema = snapshot_row.target_schema
               AND cagg.user_view_name = snapshot_row.target_name;
            IF local_mat_hypertable_id IS NULL THEN
                RAISE EXCEPTION 'cannot restore job %: continuous aggregate %.% is missing',
                    snapshot_row.job_id, snapshot_row.target_schema, snapshot_row.target_name;
            END IF;
            IF local_config ? 'mat_hypertable_id' THEN
                local_config := jsonb_set(
                    local_config, '{mat_hypertable_id}',
                    to_jsonb(local_mat_hypertable_id), TRUE);
            END IF;
            IF local_config ? 'hypertable_id' THEN
                local_config := jsonb_set(
                    local_config, '{hypertable_id}',
                    to_jsonb(local_mat_hypertable_id), TRUE);
            END IF;

            SELECT job.job_id
              INTO local_job_id
              FROM timescaledb_information.jobs AS job
             WHERE job.proc_schema = snapshot_row.proc_schema
               AND job.proc_name = snapshot_row.proc_name
               AND COALESCE(
                       NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                       NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
                   = local_mat_hypertable_id
             ORDER BY job.job_id
             LIMIT 1;
        ELSE
            SELECT job.job_id
              INTO local_job_id
              FROM timescaledb_information.jobs AS job
             WHERE job.job_id = snapshot_row.job_id
               AND job.proc_schema = snapshot_row.proc_schema
               AND job.proc_name = snapshot_row.proc_name;
        END IF;

        IF local_job_id IS NULL THEN
            IF NOT snapshot_row.target_is_cagg THEN
                RAISE EXCEPTION 'cannot restore missing pre-0007 job % (% %)',
                    snapshot_row.job_id, snapshot_row.proc_schema, snapshot_row.proc_name;
            END IF;
            local_job_id := add_job(
                format('%I.%I', snapshot_row.proc_schema, snapshot_row.proc_name)::REGPROC,
                schedule_interval => snapshot_row.schedule_interval,
                config => local_config,
                initial_start => snapshot_row.initial_start,
                scheduled => snapshot_row.scheduled,
                check_config => local_check_config,
                fixed_schedule => snapshot_row.fixed_schedule,
                timezone => snapshot_row.timezone);
            IF local_job_id IS NULL OR local_job_id < 1 THEN
                RAISE EXCEPTION 'could not recreate pre-0007 job % (% %)',
                    snapshot_row.job_id, snapshot_row.proc_schema, snapshot_row.proc_name;
            END IF;
        END IF;

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

    FOR local_job_id IN
        SELECT job.job_id
        FROM _timescaledb_catalog.continuous_agg AS cagg
        JOIN timescaledb_information.jobs AS job
          ON COALESCE(
                 NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                 NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
             = cagg.mat_hypertable_id
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
          AND job.proc_schema = '_timescaledb_functions'
          AND job.proc_name IN ('policy_compression', 'policy_columnstore')
          AND NOT EXISTS (
              SELECT 1
              FROM public.monitoring_job_snapshot_0007 AS snapshot
              WHERE snapshot.restore_after_down
                AND snapshot.target_is_cagg
                AND snapshot.target_schema = cagg.user_view_schema
                AND snapshot.target_name = cagg.user_view_name
                AND snapshot.proc_schema = job.proc_schema
                AND snapshot.proc_name = job.proc_name
          )
    LOOP
        PERFORM alter_job(local_job_id, scheduled => TRUE);
    END LOOP;
END
$$;


ALTER PROCEDURE public.monitoring_restore_quiesced_jobs_0007() OWNER TO cea_user;

--
-- Name: monitoring_restore_requested_pid_job_schedule_0008(); Type: PROCEDURE; Schema: public; Owner: cea_user
--

CREATE PROCEDURE public.monitoring_restore_requested_pid_job_schedule_0008()
    LANGUAGE plpgsql
    AS $$
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
$$;


ALTER PROCEDURE public.monitoring_restore_requested_pid_job_schedule_0008() OWNER TO cea_user;

--
-- Name: sj_calendar_bind_pending(); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.sj_calendar_bind_pending() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    v_changed BOOLEAN;
    v_active RECORD;
BEGIN
    IF TG_OP='INSERT' THEN
        v_changed := TRUE;
        NEW.local_revision := 1;
    ELSE
        v_changed := ROW(NEW.location,NEW.cluster,NEW.event_type,NEW.title,NEW.start_date,NEW.end_date,
            NEW.all_day,NEW.notes,NEW.metadata,NEW.crop_batch_id,NEW.grow_plan_id,NEW.ical_uid,
            NEW.recurrence_rule,NEW.recurrence_parent_id,NEW.deleted_at)
          IS DISTINCT FROM ROW(OLD.location,OLD.cluster,OLD.event_type,OLD.title,OLD.start_date,OLD.end_date,
            OLD.all_day,OLD.notes,OLD.metadata,OLD.crop_batch_id,OLD.grow_plan_id,OLD.ical_uid,
            OLD.recurrence_rule,OLD.recurrence_parent_id,OLD.deleted_at);
        v_changed := v_changed OR (NEW.updated_at IS DISTINCT FROM OLD.updated_at
            AND NEW.sync_status IN ('pending_push','pending_delete'));
        IF NOT v_changed THEN
            RETURN NEW;
        END IF;
        NEW.local_revision := OLD.local_revision+1;
    END IF;
    SELECT id,generation INTO v_active FROM public.calendar_sync_connection
    WHERE enabled AND files_root_url IS NOT NULL ORDER BY id DESC LIMIT 1;
    IF FOUND THEN
        IF TG_OP='UPDATE' THEN
            IF OLD.sync_connection_id IS DISTINCT FROM v_active.id OR OLD.sync_generation IS DISTINCT FROM v_active.generation THEN
                NEW.external_provider := NULL;
                NEW.external_event_id := NULL;
                NEW.external_etag := NULL;
                NEW.external_calendar_id := NULL;
            END IF;
        END IF;
        NEW.sync_connection_id := v_active.id;
        NEW.sync_generation := v_active.generation;
        NEW.sync_status := CASE WHEN NEW.deleted_at IS NULL THEN 'pending_push' ELSE 'pending_delete' END;
        NEW.sync_error_code := NULL;
    ELSE
        NEW.sync_connection_id := NULL;
        NEW.sync_generation := NULL;
        NEW.sync_status := 'error';
        NEW.sync_error_code := 'calendar_connection_unavailable';
    END IF;
    NEW.sync_lease_owner := NULL;
    NEW.sync_lease_until := NULL;
    NEW.sync_mirror_all := false;
    NEW.sync_attempts := 0;
    NEW.sync_next_attempt_at := clock_timestamp();
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.sj_calendar_bind_pending() OWNER TO cea_user;

--
-- Name: sj_history_room_lock_key(text); Type: FUNCTION; Schema: public; Owner: cea_user
--

CREATE FUNCTION public.sj_history_room_lock_key(room text) RETURNS bigint
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
    SELECT pg_catalog.hashtextextended('sj-history-room:' || room, 0)
$$;


ALTER FUNCTION public.sj_history_room_lock_key(room text) OWNER TO cea_user;

--
-- Name: validate_water_tank_monitor_config_sensor(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.validate_water_tank_monitor_config_sensor() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    sensor_unit TEXT;
BEGIN
    IF NEW.sensor_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT unit INTO sensor_unit
    FROM sensor
    WHERE sensor_id = NEW.sensor_id;

    IF sensor_unit IS NULL OR sensor_unit <> 'mm' THEN
        RAISE EXCEPTION 'water tank source sensor must report mm'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;


ALTER FUNCTION public.validate_water_tank_monitor_config_sensor() OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: _compressed_hypertable_10; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_10 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_10 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_559; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_559 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_559 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_609; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_609 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_609 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_610; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_610 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_610 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_612; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_612 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_612 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_624; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_624 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_624 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_625; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_625 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_625 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_626; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_626 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_626 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_627; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_627 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_627 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_628; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_628 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_628 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_629; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_629 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_629 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_630; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_630 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_630 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_631; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_631 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_631 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_632; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_632 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_632 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_633; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_633 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_633 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_636; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_636 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_636 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_639; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_639 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_639 OWNER TO cea_user;

--
-- Name: _compressed_hypertable_640; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._compressed_hypertable_640 (
);


ALTER TABLE _timescaledb_internal._compressed_hypertable_640 OWNER TO cea_user;

--
-- Name: measurement; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.measurement (
    "time" timestamp with time zone NOT NULL,
    sensor_id integer NOT NULL,
    value real NOT NULL,
    status text
);


ALTER TABLE public.measurement OWNER TO cea_user;

--
-- Name: _direct_view_590; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_590 AS
 SELECT public.time_bucket('00:01:00'::interval, measurement."time") AS bucket,
    measurement.sensor_id,
    count(measurement.value) AS sample_count,
    sum((measurement.value)::numeric) AS value_sum,
    sum(((measurement.value)::numeric * (measurement.value)::numeric)) AS value_sum_squares,
    min((measurement.value)::numeric) AS min_value,
    max((measurement.value)::numeric) AS max_value,
    max(measurement."time") AS last_observed_at
   FROM public.measurement
  GROUP BY (public.time_bucket('00:01:00'::interval, measurement."time")), measurement.sensor_id;


ALTER TABLE _timescaledb_internal._direct_view_590 OWNER TO cea_user;

--
-- Name: monitoring_relay_duration_1hour; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_relay_duration_1hour (
    bucket timestamp with time zone NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    channel smallint NOT NULL,
    device_id integer NOT NULL,
    device_name text NOT NULL,
    device_type text NOT NULL,
    on_microseconds bigint DEFAULT 0 NOT NULL,
    off_microseconds bigint DEFAULT 0 NOT NULL,
    last_observed_at timestamp with time zone NOT NULL,
    source_watermark bigint NOT NULL,
    active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_monitoring_relay_duration_1hour_nonnegative CHECK (((on_microseconds >= 0) AND (off_microseconds >= 0)))
);


ALTER TABLE public.monitoring_relay_duration_1hour OWNER TO cea_user;

--
-- Name: TABLE monitoring_relay_duration_1hour; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.monitoring_relay_duration_1hour IS 'Derived per-hour relay duration rollups keyed by recorded lane identity.';


--
-- Name: COLUMN monitoring_relay_duration_1hour.on_microseconds; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.monitoring_relay_duration_1hour.on_microseconds IS 'Exact known ON time of the bucket in integer microseconds.';


--
-- Name: COLUMN monitoring_relay_duration_1hour.off_microseconds; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.monitoring_relay_duration_1hour.off_microseconds IS 'Exact known OFF time in integer microseconds; bucket microseconds not
accounted for by on+off are unknown time, never fabricated.';


--
-- Name: COLUMN monitoring_relay_duration_1hour.last_observed_at; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.monitoring_relay_duration_1hour.last_observed_at IS 'time of the newest event-time owner row for this lane with
observation_id <= source_watermark and time < bucket end.';


--
-- Name: COLUMN monitoring_relay_duration_1hour.source_watermark; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.monitoring_relay_duration_1hour.source_watermark IS 'relay_observation.observation_id snapshot the row was verified with.';


--
-- Name: COLUMN monitoring_relay_duration_1hour.active; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.monitoring_relay_duration_1hour.active IS 'Derived-row replacement marker, not a lane state: the rebuild marks the
claimed day''s prior rows inactive, then upserts only the verified
snapshot''s own rows back to active, so no row or index is ever deleted.
The daily continuous aggregate and the API readers ignore inactive rows:
inactive means "this identity is not a recorded fact in the verified
snapshot" (unknown time), never an invented zero, and unknown-only
legitimate lanes stay active exactly as before.';


--
-- Name: _direct_view_608; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_608 AS
 SELECT public.time_bucket('1 day'::interval, hour.bucket) AS bucket,
    hour.location,
    hour.cluster,
    hour.channel,
    hour.device_id,
    hour.device_name,
    hour.device_type,
    (sum(hour.on_microseconds))::bigint AS on_microseconds,
    (sum(hour.off_microseconds))::bigint AS off_microseconds,
    max(hour.last_observed_at) AS last_observed_at,
    max(hour.source_watermark) AS source_watermark
   FROM public.monitoring_relay_duration_1hour hour
  WHERE hour.active
  GROUP BY (public.time_bucket('1 day'::interval, hour.bucket)), hour.location, hour.cluster, hour.channel, hour.device_id, hour.device_name, hour.device_type;


ALTER TABLE _timescaledb_internal._direct_view_608 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_590; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_590 (
    bucket timestamp with time zone NOT NULL,
    sensor_id integer,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_590 OWNER TO cea_user;

--
-- Name: monitoring_measurement_1min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_measurement_1min AS
 SELECT _materialized_hypertable_590.bucket,
    _materialized_hypertable_590.sensor_id,
    _materialized_hypertable_590.sample_count,
    _materialized_hypertable_590.value_sum,
    _materialized_hypertable_590.value_sum_squares,
    _materialized_hypertable_590.min_value,
    _materialized_hypertable_590.max_value,
    _materialized_hypertable_590.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_590;


ALTER TABLE public.monitoring_measurement_1min OWNER TO cea_user;

--
-- Name: _direct_view_613; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_613 AS
 SELECT public.time_bucket('00:05:00'::interval, minute.bucket) AS bucket,
    minute.sensor_id,
    (sum(minute.sample_count))::bigint AS sample_count,
    sum(minute.value_sum) AS value_sum,
    sum(minute.value_sum_squares) AS value_sum_squares,
    min(minute.min_value) AS min_value,
    max(minute.max_value) AS max_value,
    max(minute.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1min minute
  GROUP BY (public.time_bucket('00:05:00'::interval, minute.bucket)), minute.sensor_id;


ALTER TABLE _timescaledb_internal._direct_view_613 OWNER TO cea_user;

--
-- Name: effective_setpoints; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.effective_setpoints (
    "time" timestamp with time zone DEFAULT now() NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    mode text,
    effective_heating_setpoint real,
    effective_cooling_setpoint real,
    effective_humidity_setpoint real,
    effective_co2_setpoint real,
    effective_vpd_setpoint real,
    nominal_heating_setpoint real,
    nominal_cooling_setpoint real,
    nominal_humidity_setpoint real,
    nominal_co2_setpoint real,
    nominal_vpd_setpoint real,
    ramp_progress_heating real,
    ramp_progress_cooling real,
    ramp_progress_humidity real,
    ramp_progress_co2 real,
    ramp_progress_vpd real,
    device_name text,
    effective_light_intensity real,
    nominal_light_intensity real,
    ramp_progress_light real,
    monitoring_ingest_id bigint,
    ingested_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.effective_setpoints OWNER TO cea_user;

--
-- Name: _direct_view_614; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_614 AS
 SELECT public.time_bucket('00:01:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    effective_setpoints.mode,
    count(effective_setpoints.effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_sum,
    sum(((effective_setpoints.effective_heating_setpoint)::numeric * (effective_setpoints.effective_heating_setpoint)::numeric)) AS effective_heating_setpoint_sum_squares,
    min((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_min,
    max((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_max,
    public.last(effective_setpoints.effective_heating_setpoint, effective_setpoints."time") AS effective_heating_setpoint_last,
    count(effective_setpoints.effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_sum,
    sum(((effective_setpoints.effective_cooling_setpoint)::numeric * (effective_setpoints.effective_cooling_setpoint)::numeric)) AS effective_cooling_setpoint_sum_squares,
    min((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_min,
    max((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_max,
    public.last(effective_setpoints.effective_cooling_setpoint, effective_setpoints."time") AS effective_cooling_setpoint_last,
    count(effective_setpoints.effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_sum,
    sum(((effective_setpoints.effective_humidity_setpoint)::numeric * (effective_setpoints.effective_humidity_setpoint)::numeric)) AS effective_humidity_setpoint_sum_squares,
    min((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_min,
    max((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_max,
    public.last(effective_setpoints.effective_humidity_setpoint, effective_setpoints."time") AS effective_humidity_setpoint_last,
    count(effective_setpoints.effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_sum,
    sum(((effective_setpoints.effective_co2_setpoint)::numeric * (effective_setpoints.effective_co2_setpoint)::numeric)) AS effective_co2_setpoint_sum_squares,
    min((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_min,
    max((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_max,
    public.last(effective_setpoints.effective_co2_setpoint, effective_setpoints."time") AS effective_co2_setpoint_last,
    count(effective_setpoints.effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_sum,
    sum(((effective_setpoints.effective_vpd_setpoint)::numeric * (effective_setpoints.effective_vpd_setpoint)::numeric)) AS effective_vpd_setpoint_sum_squares,
    min((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_min,
    max((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_max,
    public.last(effective_setpoints.effective_vpd_setpoint, effective_setpoints."time") AS effective_vpd_setpoint_last,
    count(effective_setpoints.effective_light_intensity) AS effective_light_intensity_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS effective_light_intensity_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_min,
    max((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_max,
    public.last(effective_setpoints.effective_light_intensity, effective_setpoints."time") AS effective_light_intensity_last,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  GROUP BY (public.time_bucket('00:01:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name, effective_setpoints.mode;


ALTER TABLE _timescaledb_internal._direct_view_614 OWNER TO cea_user;

--
-- Name: _direct_view_615; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_615 AS
 SELECT public.time_bucket('00:05:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    effective_setpoints.mode,
    count(effective_setpoints.effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_sum,
    sum(((effective_setpoints.effective_heating_setpoint)::numeric * (effective_setpoints.effective_heating_setpoint)::numeric)) AS effective_heating_setpoint_sum_squares,
    min((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_min,
    max((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_max,
    public.last(effective_setpoints.effective_heating_setpoint, effective_setpoints."time") AS effective_heating_setpoint_last,
    count(effective_setpoints.effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_sum,
    sum(((effective_setpoints.effective_cooling_setpoint)::numeric * (effective_setpoints.effective_cooling_setpoint)::numeric)) AS effective_cooling_setpoint_sum_squares,
    min((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_min,
    max((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_max,
    public.last(effective_setpoints.effective_cooling_setpoint, effective_setpoints."time") AS effective_cooling_setpoint_last,
    count(effective_setpoints.effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_sum,
    sum(((effective_setpoints.effective_humidity_setpoint)::numeric * (effective_setpoints.effective_humidity_setpoint)::numeric)) AS effective_humidity_setpoint_sum_squares,
    min((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_min,
    max((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_max,
    public.last(effective_setpoints.effective_humidity_setpoint, effective_setpoints."time") AS effective_humidity_setpoint_last,
    count(effective_setpoints.effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_sum,
    sum(((effective_setpoints.effective_co2_setpoint)::numeric * (effective_setpoints.effective_co2_setpoint)::numeric)) AS effective_co2_setpoint_sum_squares,
    min((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_min,
    max((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_max,
    public.last(effective_setpoints.effective_co2_setpoint, effective_setpoints."time") AS effective_co2_setpoint_last,
    count(effective_setpoints.effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_sum,
    sum(((effective_setpoints.effective_vpd_setpoint)::numeric * (effective_setpoints.effective_vpd_setpoint)::numeric)) AS effective_vpd_setpoint_sum_squares,
    min((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_min,
    max((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_max,
    public.last(effective_setpoints.effective_vpd_setpoint, effective_setpoints."time") AS effective_vpd_setpoint_last,
    count(effective_setpoints.effective_light_intensity) AS effective_light_intensity_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS effective_light_intensity_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_min,
    max((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_max,
    public.last(effective_setpoints.effective_light_intensity, effective_setpoints."time") AS effective_light_intensity_last,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  GROUP BY (public.time_bucket('00:05:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name, effective_setpoints.mode;


ALTER TABLE _timescaledb_internal._direct_view_615 OWNER TO cea_user;

--
-- Name: automation_state; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.automation_state (
    id bigint NOT NULL,
    "time" timestamp with time zone DEFAULT now() NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    device_state integer NOT NULL,
    device_mode text NOT NULL,
    pid_output real,
    duty_cycle_percent real,
    active_rule_ids integer[],
    active_schedule_ids integer[],
    control_reason text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    schedule_ramp_up_duration integer,
    schedule_ramp_down_duration integer,
    schedule_photoperiod_hours real,
    pid_kp real,
    pid_ki real,
    pid_kd real
);


ALTER TABLE public.automation_state OWNER TO cea_user;

--
-- Name: _direct_view_616; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_616 AS
 SELECT public.time_bucket('00:01:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    min(automation_state.device_state) AS device_state_min,
    max(automation_state.device_state) AS device_state_max,
    public.last(automation_state.device_state, automation_state."time") AS device_state_last,
    public.last(automation_state.device_mode, automation_state."time") AS device_mode_last,
    count(automation_state.pid_output) AS pid_output_count,
    min(automation_state.pid_output) AS pid_output_min,
    max(automation_state.pid_output) AS pid_output_max,
    public.last(automation_state.pid_output, automation_state."time") AS pid_output_last,
    count(automation_state.duty_cycle_percent) AS duty_cycle_percent_count,
    min(automation_state.duty_cycle_percent) AS duty_cycle_percent_min,
    max(automation_state.duty_cycle_percent) AS duty_cycle_percent_max,
    public.last(automation_state.duty_cycle_percent, automation_state."time") AS duty_cycle_percent_last,
    public.last(automation_state.control_reason, automation_state."time") AS control_reason_last,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('00:01:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._direct_view_616 OWNER TO cea_user;

--
-- Name: _direct_view_617; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_617 AS
 SELECT public.time_bucket('00:05:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    min(automation_state.device_state) AS device_state_min,
    max(automation_state.device_state) AS device_state_max,
    public.last(automation_state.device_state, automation_state."time") AS device_state_last,
    public.last(automation_state.device_mode, automation_state."time") AS device_mode_last,
    count(automation_state.pid_output) AS pid_output_count,
    min(automation_state.pid_output) AS pid_output_min,
    max(automation_state.pid_output) AS pid_output_max,
    public.last(automation_state.pid_output, automation_state."time") AS pid_output_last,
    count(automation_state.duty_cycle_percent) AS duty_cycle_percent_count,
    min(automation_state.duty_cycle_percent) AS duty_cycle_percent_min,
    max(automation_state.duty_cycle_percent) AS duty_cycle_percent_max,
    public.last(automation_state.duty_cycle_percent, automation_state."time") AS duty_cycle_percent_last,
    public.last(automation_state.control_reason, automation_state."time") AS control_reason_last,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('00:05:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._direct_view_617 OWNER TO cea_user;

--
-- Name: _direct_view_618; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_618 AS
 SELECT public.time_bucket('01:00:00'::interval, minute.bucket) AS bucket,
    minute.sensor_id,
    (sum(minute.sample_count))::bigint AS sample_count,
    sum(minute.value_sum) AS value_sum,
    sum(minute.value_sum_squares) AS value_sum_squares,
    min(minute.min_value) AS min_value,
    max(minute.max_value) AS max_value,
    max(minute.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1min minute
  GROUP BY (public.time_bucket('01:00:00'::interval, minute.bucket)), minute.sensor_id;


ALTER TABLE _timescaledb_internal._direct_view_618 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_618; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_618 (
    bucket timestamp with time zone NOT NULL,
    sensor_id integer,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_618 OWNER TO cea_user;

--
-- Name: monitoring_measurement_1hour; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_measurement_1hour AS
 SELECT _materialized_hypertable_618.bucket,
    _materialized_hypertable_618.sensor_id,
    _materialized_hypertable_618.sample_count,
    _materialized_hypertable_618.value_sum,
    _materialized_hypertable_618.value_sum_squares,
    _materialized_hypertable_618.min_value,
    _materialized_hypertable_618.max_value,
    _materialized_hypertable_618.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_618;


ALTER TABLE public.monitoring_measurement_1hour OWNER TO cea_user;

--
-- Name: _direct_view_619; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_619 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.sensor_id,
    (sum(hourly.sample_count))::bigint AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.sensor_id;


ALTER TABLE _timescaledb_internal._direct_view_619 OWNER TO cea_user;

--
-- Name: _direct_view_620; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_620 AS
 SELECT public.time_bucket('01:00:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    count(effective_setpoints.effective_light_intensity) AS sample_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS value_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS value_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS min_value,
    max((effective_setpoints.effective_light_intensity)::numeric) AS max_value,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  WHERE ((effective_setpoints.device_name IS NOT NULL) AND ((effective_setpoints.effective_light_intensity IS NOT NULL) OR (effective_setpoints.nominal_light_intensity IS NOT NULL) OR (effective_setpoints.ramp_progress_light IS NOT NULL)))
  GROUP BY (public.time_bucket('01:00:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name;


ALTER TABLE _timescaledb_internal._direct_view_620 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_620; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_620 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_620 OWNER TO cea_user;

--
-- Name: monitoring_light_intensity_1hour; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_light_intensity_1hour AS
 SELECT _materialized_hypertable_620.bucket,
    _materialized_hypertable_620.location,
    _materialized_hypertable_620.cluster,
    _materialized_hypertable_620.device_name,
    _materialized_hypertable_620.sample_count,
    _materialized_hypertable_620.value_sum,
    _materialized_hypertable_620.value_sum_squares,
    _materialized_hypertable_620.min_value,
    _materialized_hypertable_620.max_value,
    _materialized_hypertable_620.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_620;


ALTER TABLE public.monitoring_light_intensity_1hour OWNER TO cea_user;

--
-- Name: _direct_view_621; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_621 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    (sum(hourly.sample_count))::bigint AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_light_intensity_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.location, hourly.cluster, hourly.device_name;


ALTER TABLE _timescaledb_internal._direct_view_621 OWNER TO cea_user;

--
-- Name: _direct_view_637; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_637 AS
 SELECT public.time_bucket('01:00:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    public.time_weight('LOCF'::text, automation_state."time", (round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13))::double precision) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_tw,
    min(round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13)) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_min,
    max(round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13)) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_max,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('01:00:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._direct_view_637 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_637; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_637 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    requested_tw public.timeweightsummary,
    requested_min numeric,
    requested_max numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_637 OWNER TO cea_user;

--
-- Name: monitoring_requested_pid_1hour; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_requested_pid_1hour AS
 SELECT _materialized_hypertable_637.bucket,
    _materialized_hypertable_637.location,
    _materialized_hypertable_637.cluster,
    _materialized_hypertable_637.device_name,
    _materialized_hypertable_637.sample_count,
    _materialized_hypertable_637.requested_tw,
    _materialized_hypertable_637.requested_min,
    _materialized_hypertable_637.requested_max,
    _materialized_hypertable_637.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_637;


ALTER TABLE public.monitoring_requested_pid_1hour OWNER TO cea_user;

--
-- Name: _direct_view_638; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._direct_view_638 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    (sum(hourly.sample_count))::bigint AS sample_count,
    public.rollup(hourly.requested_tw) AS requested_tw,
    min(hourly.requested_min) AS requested_min,
    max(hourly.requested_max) AS requested_max,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_requested_pid_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.location, hourly.cluster, hourly.device_name;


ALTER TABLE _timescaledb_internal._direct_view_638 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_608; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_608 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    channel smallint,
    device_id integer,
    device_name text,
    device_type text,
    on_microseconds bigint,
    off_microseconds bigint,
    last_observed_at timestamp with time zone,
    source_watermark bigint
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_608 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_613; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_613 (
    bucket timestamp with time zone NOT NULL,
    sensor_id integer,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_613 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_614; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_614 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    mode text,
    effective_heating_setpoint_count bigint,
    effective_heating_setpoint_sum numeric,
    effective_heating_setpoint_sum_squares numeric,
    effective_heating_setpoint_min numeric,
    effective_heating_setpoint_max numeric,
    effective_heating_setpoint_last real,
    effective_cooling_setpoint_count bigint,
    effective_cooling_setpoint_sum numeric,
    effective_cooling_setpoint_sum_squares numeric,
    effective_cooling_setpoint_min numeric,
    effective_cooling_setpoint_max numeric,
    effective_cooling_setpoint_last real,
    effective_humidity_setpoint_count bigint,
    effective_humidity_setpoint_sum numeric,
    effective_humidity_setpoint_sum_squares numeric,
    effective_humidity_setpoint_min numeric,
    effective_humidity_setpoint_max numeric,
    effective_humidity_setpoint_last real,
    effective_co2_setpoint_count bigint,
    effective_co2_setpoint_sum numeric,
    effective_co2_setpoint_sum_squares numeric,
    effective_co2_setpoint_min numeric,
    effective_co2_setpoint_max numeric,
    effective_co2_setpoint_last real,
    effective_vpd_setpoint_count bigint,
    effective_vpd_setpoint_sum numeric,
    effective_vpd_setpoint_sum_squares numeric,
    effective_vpd_setpoint_min numeric,
    effective_vpd_setpoint_max numeric,
    effective_vpd_setpoint_last real,
    effective_light_intensity_count bigint,
    effective_light_intensity_sum numeric,
    effective_light_intensity_sum_squares numeric,
    effective_light_intensity_min numeric,
    effective_light_intensity_max numeric,
    effective_light_intensity_last real,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_614 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_615; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_615 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    mode text,
    effective_heating_setpoint_count bigint,
    effective_heating_setpoint_sum numeric,
    effective_heating_setpoint_sum_squares numeric,
    effective_heating_setpoint_min numeric,
    effective_heating_setpoint_max numeric,
    effective_heating_setpoint_last real,
    effective_cooling_setpoint_count bigint,
    effective_cooling_setpoint_sum numeric,
    effective_cooling_setpoint_sum_squares numeric,
    effective_cooling_setpoint_min numeric,
    effective_cooling_setpoint_max numeric,
    effective_cooling_setpoint_last real,
    effective_humidity_setpoint_count bigint,
    effective_humidity_setpoint_sum numeric,
    effective_humidity_setpoint_sum_squares numeric,
    effective_humidity_setpoint_min numeric,
    effective_humidity_setpoint_max numeric,
    effective_humidity_setpoint_last real,
    effective_co2_setpoint_count bigint,
    effective_co2_setpoint_sum numeric,
    effective_co2_setpoint_sum_squares numeric,
    effective_co2_setpoint_min numeric,
    effective_co2_setpoint_max numeric,
    effective_co2_setpoint_last real,
    effective_vpd_setpoint_count bigint,
    effective_vpd_setpoint_sum numeric,
    effective_vpd_setpoint_sum_squares numeric,
    effective_vpd_setpoint_min numeric,
    effective_vpd_setpoint_max numeric,
    effective_vpd_setpoint_last real,
    effective_light_intensity_count bigint,
    effective_light_intensity_sum numeric,
    effective_light_intensity_sum_squares numeric,
    effective_light_intensity_min numeric,
    effective_light_intensity_max numeric,
    effective_light_intensity_last real,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_615 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_616; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_616 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    device_state_min integer,
    device_state_max integer,
    device_state_last integer,
    device_mode_last text,
    pid_output_count bigint,
    pid_output_min real,
    pid_output_max real,
    pid_output_last real,
    duty_cycle_percent_count bigint,
    duty_cycle_percent_min real,
    duty_cycle_percent_max real,
    duty_cycle_percent_last real,
    control_reason_last text,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_616 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_617; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_617 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    device_state_min integer,
    device_state_max integer,
    device_state_last integer,
    device_mode_last text,
    pid_output_count bigint,
    pid_output_min real,
    pid_output_max real,
    pid_output_last real,
    duty_cycle_percent_count bigint,
    duty_cycle_percent_min real,
    duty_cycle_percent_max real,
    duty_cycle_percent_last real,
    control_reason_last text,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_617 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_619; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_619 (
    bucket timestamp with time zone NOT NULL,
    sensor_id integer,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_619 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_621; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_621 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    value_sum numeric,
    value_sum_squares numeric,
    min_value numeric,
    max_value numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_621 OWNER TO cea_user;

--
-- Name: _materialized_hypertable_638; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal._materialized_hypertable_638 (
    bucket timestamp with time zone NOT NULL,
    location text,
    cluster text,
    device_name text,
    sample_count bigint,
    requested_tw public.timeweightsummary,
    requested_min numeric,
    requested_max numeric,
    last_observed_at timestamp with time zone
);


ALTER TABLE _timescaledb_internal._materialized_hypertable_638 OWNER TO cea_user;

--
-- Name: _partial_view_590; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_590 AS
 SELECT public.time_bucket('00:01:00'::interval, measurement."time") AS bucket,
    measurement.sensor_id,
    count(measurement.value) AS sample_count,
    sum((measurement.value)::numeric) AS value_sum,
    sum(((measurement.value)::numeric * (measurement.value)::numeric)) AS value_sum_squares,
    min((measurement.value)::numeric) AS min_value,
    max((measurement.value)::numeric) AS max_value,
    max(measurement."time") AS last_observed_at
   FROM public.measurement
  GROUP BY (public.time_bucket('00:01:00'::interval, measurement."time")), measurement.sensor_id;


ALTER TABLE _timescaledb_internal._partial_view_590 OWNER TO cea_user;

--
-- Name: _partial_view_608; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_608 AS
 SELECT public.time_bucket('1 day'::interval, hour.bucket) AS bucket,
    hour.location,
    hour.cluster,
    hour.channel,
    hour.device_id,
    hour.device_name,
    hour.device_type,
    (sum(hour.on_microseconds))::bigint AS on_microseconds,
    (sum(hour.off_microseconds))::bigint AS off_microseconds,
    max(hour.last_observed_at) AS last_observed_at,
    max(hour.source_watermark) AS source_watermark
   FROM public.monitoring_relay_duration_1hour hour
  WHERE hour.active
  GROUP BY (public.time_bucket('1 day'::interval, hour.bucket)), hour.location, hour.cluster, hour.channel, hour.device_id, hour.device_name, hour.device_type;


ALTER TABLE _timescaledb_internal._partial_view_608 OWNER TO cea_user;

--
-- Name: _partial_view_613; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_613 AS
 SELECT public.time_bucket('00:05:00'::interval, minute.bucket) AS bucket,
    minute.sensor_id,
    (sum(minute.sample_count))::bigint AS sample_count,
    sum(minute.value_sum) AS value_sum,
    sum(minute.value_sum_squares) AS value_sum_squares,
    min(minute.min_value) AS min_value,
    max(minute.max_value) AS max_value,
    max(minute.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1min minute
  GROUP BY (public.time_bucket('00:05:00'::interval, minute.bucket)), minute.sensor_id;


ALTER TABLE _timescaledb_internal._partial_view_613 OWNER TO cea_user;

--
-- Name: _partial_view_614; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_614 AS
 SELECT public.time_bucket('00:01:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    effective_setpoints.mode,
    count(effective_setpoints.effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_sum,
    sum(((effective_setpoints.effective_heating_setpoint)::numeric * (effective_setpoints.effective_heating_setpoint)::numeric)) AS effective_heating_setpoint_sum_squares,
    min((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_min,
    max((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_max,
    public.last(effective_setpoints.effective_heating_setpoint, effective_setpoints."time") AS effective_heating_setpoint_last,
    count(effective_setpoints.effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_sum,
    sum(((effective_setpoints.effective_cooling_setpoint)::numeric * (effective_setpoints.effective_cooling_setpoint)::numeric)) AS effective_cooling_setpoint_sum_squares,
    min((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_min,
    max((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_max,
    public.last(effective_setpoints.effective_cooling_setpoint, effective_setpoints."time") AS effective_cooling_setpoint_last,
    count(effective_setpoints.effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_sum,
    sum(((effective_setpoints.effective_humidity_setpoint)::numeric * (effective_setpoints.effective_humidity_setpoint)::numeric)) AS effective_humidity_setpoint_sum_squares,
    min((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_min,
    max((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_max,
    public.last(effective_setpoints.effective_humidity_setpoint, effective_setpoints."time") AS effective_humidity_setpoint_last,
    count(effective_setpoints.effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_sum,
    sum(((effective_setpoints.effective_co2_setpoint)::numeric * (effective_setpoints.effective_co2_setpoint)::numeric)) AS effective_co2_setpoint_sum_squares,
    min((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_min,
    max((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_max,
    public.last(effective_setpoints.effective_co2_setpoint, effective_setpoints."time") AS effective_co2_setpoint_last,
    count(effective_setpoints.effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_sum,
    sum(((effective_setpoints.effective_vpd_setpoint)::numeric * (effective_setpoints.effective_vpd_setpoint)::numeric)) AS effective_vpd_setpoint_sum_squares,
    min((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_min,
    max((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_max,
    public.last(effective_setpoints.effective_vpd_setpoint, effective_setpoints."time") AS effective_vpd_setpoint_last,
    count(effective_setpoints.effective_light_intensity) AS effective_light_intensity_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS effective_light_intensity_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_min,
    max((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_max,
    public.last(effective_setpoints.effective_light_intensity, effective_setpoints."time") AS effective_light_intensity_last,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  GROUP BY (public.time_bucket('00:01:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name, effective_setpoints.mode;


ALTER TABLE _timescaledb_internal._partial_view_614 OWNER TO cea_user;

--
-- Name: _partial_view_615; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_615 AS
 SELECT public.time_bucket('00:05:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    effective_setpoints.mode,
    count(effective_setpoints.effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_sum,
    sum(((effective_setpoints.effective_heating_setpoint)::numeric * (effective_setpoints.effective_heating_setpoint)::numeric)) AS effective_heating_setpoint_sum_squares,
    min((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_min,
    max((effective_setpoints.effective_heating_setpoint)::numeric) AS effective_heating_setpoint_max,
    public.last(effective_setpoints.effective_heating_setpoint, effective_setpoints."time") AS effective_heating_setpoint_last,
    count(effective_setpoints.effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_sum,
    sum(((effective_setpoints.effective_cooling_setpoint)::numeric * (effective_setpoints.effective_cooling_setpoint)::numeric)) AS effective_cooling_setpoint_sum_squares,
    min((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_min,
    max((effective_setpoints.effective_cooling_setpoint)::numeric) AS effective_cooling_setpoint_max,
    public.last(effective_setpoints.effective_cooling_setpoint, effective_setpoints."time") AS effective_cooling_setpoint_last,
    count(effective_setpoints.effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_sum,
    sum(((effective_setpoints.effective_humidity_setpoint)::numeric * (effective_setpoints.effective_humidity_setpoint)::numeric)) AS effective_humidity_setpoint_sum_squares,
    min((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_min,
    max((effective_setpoints.effective_humidity_setpoint)::numeric) AS effective_humidity_setpoint_max,
    public.last(effective_setpoints.effective_humidity_setpoint, effective_setpoints."time") AS effective_humidity_setpoint_last,
    count(effective_setpoints.effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_sum,
    sum(((effective_setpoints.effective_co2_setpoint)::numeric * (effective_setpoints.effective_co2_setpoint)::numeric)) AS effective_co2_setpoint_sum_squares,
    min((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_min,
    max((effective_setpoints.effective_co2_setpoint)::numeric) AS effective_co2_setpoint_max,
    public.last(effective_setpoints.effective_co2_setpoint, effective_setpoints."time") AS effective_co2_setpoint_last,
    count(effective_setpoints.effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_sum,
    sum(((effective_setpoints.effective_vpd_setpoint)::numeric * (effective_setpoints.effective_vpd_setpoint)::numeric)) AS effective_vpd_setpoint_sum_squares,
    min((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_min,
    max((effective_setpoints.effective_vpd_setpoint)::numeric) AS effective_vpd_setpoint_max,
    public.last(effective_setpoints.effective_vpd_setpoint, effective_setpoints."time") AS effective_vpd_setpoint_last,
    count(effective_setpoints.effective_light_intensity) AS effective_light_intensity_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS effective_light_intensity_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_min,
    max((effective_setpoints.effective_light_intensity)::numeric) AS effective_light_intensity_max,
    public.last(effective_setpoints.effective_light_intensity, effective_setpoints."time") AS effective_light_intensity_last,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  GROUP BY (public.time_bucket('00:05:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name, effective_setpoints.mode;


ALTER TABLE _timescaledb_internal._partial_view_615 OWNER TO cea_user;

--
-- Name: _partial_view_616; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_616 AS
 SELECT public.time_bucket('00:01:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    min(automation_state.device_state) AS device_state_min,
    max(automation_state.device_state) AS device_state_max,
    public.last(automation_state.device_state, automation_state."time") AS device_state_last,
    public.last(automation_state.device_mode, automation_state."time") AS device_mode_last,
    count(automation_state.pid_output) AS pid_output_count,
    min(automation_state.pid_output) AS pid_output_min,
    max(automation_state.pid_output) AS pid_output_max,
    public.last(automation_state.pid_output, automation_state."time") AS pid_output_last,
    count(automation_state.duty_cycle_percent) AS duty_cycle_percent_count,
    min(automation_state.duty_cycle_percent) AS duty_cycle_percent_min,
    max(automation_state.duty_cycle_percent) AS duty_cycle_percent_max,
    public.last(automation_state.duty_cycle_percent, automation_state."time") AS duty_cycle_percent_last,
    public.last(automation_state.control_reason, automation_state."time") AS control_reason_last,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('00:01:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._partial_view_616 OWNER TO cea_user;

--
-- Name: _partial_view_617; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_617 AS
 SELECT public.time_bucket('00:05:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    min(automation_state.device_state) AS device_state_min,
    max(automation_state.device_state) AS device_state_max,
    public.last(automation_state.device_state, automation_state."time") AS device_state_last,
    public.last(automation_state.device_mode, automation_state."time") AS device_mode_last,
    count(automation_state.pid_output) AS pid_output_count,
    min(automation_state.pid_output) AS pid_output_min,
    max(automation_state.pid_output) AS pid_output_max,
    public.last(automation_state.pid_output, automation_state."time") AS pid_output_last,
    count(automation_state.duty_cycle_percent) AS duty_cycle_percent_count,
    min(automation_state.duty_cycle_percent) AS duty_cycle_percent_min,
    max(automation_state.duty_cycle_percent) AS duty_cycle_percent_max,
    public.last(automation_state.duty_cycle_percent, automation_state."time") AS duty_cycle_percent_last,
    public.last(automation_state.control_reason, automation_state."time") AS control_reason_last,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('00:05:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._partial_view_617 OWNER TO cea_user;

--
-- Name: _partial_view_618; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_618 AS
 SELECT public.time_bucket('01:00:00'::interval, minute.bucket) AS bucket,
    minute.sensor_id,
    (sum(minute.sample_count))::bigint AS sample_count,
    sum(minute.value_sum) AS value_sum,
    sum(minute.value_sum_squares) AS value_sum_squares,
    min(minute.min_value) AS min_value,
    max(minute.max_value) AS max_value,
    max(minute.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1min minute
  GROUP BY (public.time_bucket('01:00:00'::interval, minute.bucket)), minute.sensor_id;


ALTER TABLE _timescaledb_internal._partial_view_618 OWNER TO cea_user;

--
-- Name: _partial_view_619; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_619 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.sensor_id,
    (sum(hourly.sample_count))::bigint AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_measurement_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.sensor_id;


ALTER TABLE _timescaledb_internal._partial_view_619 OWNER TO cea_user;

--
-- Name: _partial_view_620; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_620 AS
 SELECT public.time_bucket('01:00:00'::interval, effective_setpoints."time") AS bucket,
    effective_setpoints.location,
    effective_setpoints.cluster,
    effective_setpoints.device_name,
    count(effective_setpoints.effective_light_intensity) AS sample_count,
    sum((effective_setpoints.effective_light_intensity)::numeric) AS value_sum,
    sum(((effective_setpoints.effective_light_intensity)::numeric * (effective_setpoints.effective_light_intensity)::numeric)) AS value_sum_squares,
    min((effective_setpoints.effective_light_intensity)::numeric) AS min_value,
    max((effective_setpoints.effective_light_intensity)::numeric) AS max_value,
    max(effective_setpoints."time") AS last_observed_at
   FROM public.effective_setpoints
  WHERE ((effective_setpoints.device_name IS NOT NULL) AND ((effective_setpoints.effective_light_intensity IS NOT NULL) OR (effective_setpoints.nominal_light_intensity IS NOT NULL) OR (effective_setpoints.ramp_progress_light IS NOT NULL)))
  GROUP BY (public.time_bucket('01:00:00'::interval, effective_setpoints."time")), effective_setpoints.location, effective_setpoints.cluster, effective_setpoints.device_name;


ALTER TABLE _timescaledb_internal._partial_view_620 OWNER TO cea_user;

--
-- Name: _partial_view_621; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_621 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    (sum(hourly.sample_count))::bigint AS sample_count,
    sum(hourly.value_sum) AS value_sum,
    sum(hourly.value_sum_squares) AS value_sum_squares,
    min(hourly.min_value) AS min_value,
    max(hourly.max_value) AS max_value,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_light_intensity_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.location, hourly.cluster, hourly.device_name;


ALTER TABLE _timescaledb_internal._partial_view_621 OWNER TO cea_user;

--
-- Name: _partial_view_637; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_637 AS
 SELECT public.time_bucket('01:00:00'::interval, automation_state."time") AS bucket,
    automation_state.location,
    automation_state.cluster,
    automation_state.device_name,
    count(*) AS sample_count,
    public.time_weight('LOCF'::text, automation_state."time", (round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13))::double precision) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_tw,
    min(round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13)) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_min,
    max(round((((automation_state.pid_output)::double precision * (100)::double precision))::numeric, 13)) FILTER (WHERE (((automation_state.pid_output)::double precision >= (0)::double precision) AND ((automation_state.pid_output)::double precision <= (1)::double precision))) AS requested_max,
    max(automation_state."time") AS last_observed_at
   FROM public.automation_state
  GROUP BY (public.time_bucket('01:00:00'::interval, automation_state."time")), automation_state.location, automation_state.cluster, automation_state.device_name;


ALTER TABLE _timescaledb_internal._partial_view_637 OWNER TO cea_user;

--
-- Name: _partial_view_638; Type: VIEW; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE VIEW _timescaledb_internal._partial_view_638 AS
 SELECT public.time_bucket('1 day'::interval, hourly.bucket) AS bucket,
    hourly.location,
    hourly.cluster,
    hourly.device_name,
    (sum(hourly.sample_count))::bigint AS sample_count,
    public.rollup(hourly.requested_tw) AS requested_tw,
    min(hourly.requested_min) AS requested_min,
    max(hourly.requested_max) AS requested_max,
    max(hourly.last_observed_at) AS last_observed_at
   FROM public.monitoring_requested_pid_1hour hourly
  GROUP BY (public.time_bucket('1 day'::interval, hourly.bucket)), hourly.location, hourly.cluster, hourly.device_name;


ALTER TABLE _timescaledb_internal._partial_view_638 OWNER TO cea_user;

--
-- Name: compress_hyper_634_1276_chunk; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal.compress_hyper_634_1276_chunk (
    _ts_meta_count integer,
    _ts_meta_min_1 timestamp with time zone,
    _ts_meta_max_1 timestamp with time zone,
    _ts_meta_v2_first_bucket timestamp with time zone,
    _ts_meta_v2_last_bucket timestamp with time zone,
    bucket _timescaledb_internal.compressed_data,
    _ts_meta_min_2 text,
    _ts_meta_max_2 text,
    _ts_meta_v2_first_location text,
    _ts_meta_v2_last_location text,
    location _timescaledb_internal.compressed_data,
    _ts_meta_min_3 text,
    _ts_meta_max_3 text,
    _ts_meta_v2_first_cluster text,
    _ts_meta_v2_last_cluster text,
    cluster _timescaledb_internal.compressed_data,
    _ts_meta_min_4 text,
    _ts_meta_max_4 text,
    _ts_meta_v2_first_device_name text,
    _ts_meta_v2_last_device_name text,
    device_name _timescaledb_internal.compressed_data,
    sample_count _timescaledb_internal.compressed_data,
    requested_count _timescaledb_internal.compressed_data,
    requested_sum _timescaledb_internal.compressed_data,
    requested_sum_squares _timescaledb_internal.compressed_data,
    requested_min _timescaledb_internal.compressed_data,
    requested_max _timescaledb_internal.compressed_data,
    last_observed_at _timescaledb_internal.compressed_data,
    _ts_meta_v2_bloomh_efbd_bucket_location _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_ce24_bucket_cluster _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_22b7_bucket_device_name _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name _timescaledb_internal.bloom1
)
WITH (toast_tuple_target='128');
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_count SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN bucket SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN location SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN location SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN cluster SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN cluster SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_min_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_max_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN device_name SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN device_name SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN sample_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_sum SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_sum SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_sum_squares SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_sum_squares SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_min SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_min SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_max SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN requested_max SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN last_observed_at SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1276_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STORAGE MAIN;


ALTER TABLE _timescaledb_internal.compress_hyper_634_1276_chunk OWNER TO cea_user;

--
-- Name: compress_hyper_634_1284_chunk; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal.compress_hyper_634_1284_chunk (
    _ts_meta_count integer,
    _ts_meta_min_1 timestamp with time zone,
    _ts_meta_max_1 timestamp with time zone,
    _ts_meta_v2_first_bucket timestamp with time zone,
    _ts_meta_v2_last_bucket timestamp with time zone,
    bucket _timescaledb_internal.compressed_data,
    _ts_meta_min_2 text,
    _ts_meta_max_2 text,
    _ts_meta_v2_first_location text,
    _ts_meta_v2_last_location text,
    location _timescaledb_internal.compressed_data,
    _ts_meta_min_3 text,
    _ts_meta_max_3 text,
    _ts_meta_v2_first_cluster text,
    _ts_meta_v2_last_cluster text,
    cluster _timescaledb_internal.compressed_data,
    _ts_meta_min_4 text,
    _ts_meta_max_4 text,
    _ts_meta_v2_first_device_name text,
    _ts_meta_v2_last_device_name text,
    device_name _timescaledb_internal.compressed_data,
    sample_count _timescaledb_internal.compressed_data,
    requested_count _timescaledb_internal.compressed_data,
    requested_sum _timescaledb_internal.compressed_data,
    requested_sum_squares _timescaledb_internal.compressed_data,
    requested_min _timescaledb_internal.compressed_data,
    requested_max _timescaledb_internal.compressed_data,
    last_observed_at _timescaledb_internal.compressed_data,
    _ts_meta_v2_bloomh_efbd_bucket_location _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_ce24_bucket_cluster _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_22b7_bucket_device_name _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name _timescaledb_internal.bloom1
)
WITH (toast_tuple_target='128');
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_count SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN bucket SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN location SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN location SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN cluster SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN cluster SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_min_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_max_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN device_name SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN device_name SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN sample_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_sum SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_sum SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_sum_squares SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_sum_squares SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_min SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_min SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_max SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN requested_max SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN last_observed_at SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_634_1284_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STORAGE MAIN;


ALTER TABLE _timescaledb_internal.compress_hyper_634_1284_chunk OWNER TO cea_user;

--
-- Name: compress_hyper_635_1280_chunk; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal.compress_hyper_635_1280_chunk (
    _ts_meta_count integer,
    _ts_meta_min_1 timestamp with time zone,
    _ts_meta_max_1 timestamp with time zone,
    _ts_meta_v2_first_bucket timestamp with time zone,
    _ts_meta_v2_last_bucket timestamp with time zone,
    bucket _timescaledb_internal.compressed_data,
    _ts_meta_min_2 text,
    _ts_meta_max_2 text,
    _ts_meta_v2_first_location text,
    _ts_meta_v2_last_location text,
    location _timescaledb_internal.compressed_data,
    _ts_meta_min_3 text,
    _ts_meta_max_3 text,
    _ts_meta_v2_first_cluster text,
    _ts_meta_v2_last_cluster text,
    cluster _timescaledb_internal.compressed_data,
    _ts_meta_min_4 text,
    _ts_meta_max_4 text,
    _ts_meta_v2_first_device_name text,
    _ts_meta_v2_last_device_name text,
    device_name _timescaledb_internal.compressed_data,
    sample_count _timescaledb_internal.compressed_data,
    requested_count _timescaledb_internal.compressed_data,
    requested_sum _timescaledb_internal.compressed_data,
    requested_sum_squares _timescaledb_internal.compressed_data,
    requested_min _timescaledb_internal.compressed_data,
    requested_max _timescaledb_internal.compressed_data,
    last_observed_at _timescaledb_internal.compressed_data,
    _ts_meta_v2_bloomh_efbd_bucket_location _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_ce24_bucket_cluster _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_22b7_bucket_device_name _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name _timescaledb_internal.bloom1
)
WITH (toast_tuple_target='128');
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_count SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN bucket SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN location SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN location SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN cluster SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN cluster SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_min_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_max_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN device_name SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN device_name SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN sample_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_sum SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_sum SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_sum_squares SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_sum_squares SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_min SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_min SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_max SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN requested_max SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN last_observed_at SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1280_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STORAGE MAIN;


ALTER TABLE _timescaledb_internal.compress_hyper_635_1280_chunk OWNER TO cea_user;

--
-- Name: compress_hyper_635_1285_chunk; Type: TABLE; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE TABLE _timescaledb_internal.compress_hyper_635_1285_chunk (
    _ts_meta_count integer,
    _ts_meta_min_1 timestamp with time zone,
    _ts_meta_max_1 timestamp with time zone,
    _ts_meta_v2_first_bucket timestamp with time zone,
    _ts_meta_v2_last_bucket timestamp with time zone,
    bucket _timescaledb_internal.compressed_data,
    _ts_meta_min_2 text,
    _ts_meta_max_2 text,
    _ts_meta_v2_first_location text,
    _ts_meta_v2_last_location text,
    location _timescaledb_internal.compressed_data,
    _ts_meta_min_3 text,
    _ts_meta_max_3 text,
    _ts_meta_v2_first_cluster text,
    _ts_meta_v2_last_cluster text,
    cluster _timescaledb_internal.compressed_data,
    _ts_meta_min_4 text,
    _ts_meta_max_4 text,
    _ts_meta_v2_first_device_name text,
    _ts_meta_v2_last_device_name text,
    device_name _timescaledb_internal.compressed_data,
    sample_count _timescaledb_internal.compressed_data,
    requested_count _timescaledb_internal.compressed_data,
    requested_sum _timescaledb_internal.compressed_data,
    requested_sum_squares _timescaledb_internal.compressed_data,
    requested_min _timescaledb_internal.compressed_data,
    requested_max _timescaledb_internal.compressed_data,
    last_observed_at _timescaledb_internal.compressed_data,
    _ts_meta_v2_bloomh_efbd_bucket_location _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_ce24_bucket_cluster _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_22b7_bucket_device_name _timescaledb_internal.bloom1,
    _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name _timescaledb_internal.bloom1
)
WITH (toast_tuple_target='128');
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_count SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_1 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_bucket SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN bucket SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_2 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_2 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN location SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN location SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_3 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_3 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN cluster SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN cluster SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_min_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_4 SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_max_4 SET STORAGE PLAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_first_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_last_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN device_name SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN device_name SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN sample_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_count SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_sum SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_sum SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_sum_squares SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_sum_squares SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_min SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_min SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_max SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN requested_max SET STORAGE EXTENDED;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN last_observed_at SET STATISTICS 0;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_efbd_bucket_location SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_ce24_bucket_cluster SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_22b7_bucket_device_name SET STORAGE MAIN;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STATISTICS 1000;
ALTER TABLE ONLY _timescaledb_internal.compress_hyper_635_1285_chunk ALTER COLUMN _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name SET STORAGE MAIN;


ALTER TABLE _timescaledb_internal.compress_hyper_635_1285_chunk OWNER TO cea_user;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: archive_artifact; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_artifact (
    artifact_id uuid DEFAULT gen_random_uuid() NOT NULL,
    dataset_id uuid NOT NULL,
    revision bigint NOT NULL,
    state text DEFAULT 'staging'::text NOT NULL,
    remote_instance_id uuid,
    remote_path text,
    sha256 text,
    byte_size bigint,
    verified_at timestamp with time zone,
    local_path text,
    local_verified_at timestamp with time zone,
    last_accessed_at timestamp with time zone,
    manifest_sha256 text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_artifact_local_verified CHECK (((local_verified_at IS NULL) OR (local_path IS NOT NULL))),
    CONSTRAINT archive_artifact_manifest_sha256_check CHECK (((manifest_sha256 IS NULL) OR (manifest_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT archive_artifact_revision_check CHECK ((revision > 0)),
    CONSTRAINT archive_artifact_sha256_check CHECK (((sha256 IS NULL) OR (sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT archive_artifact_size_check CHECK (((byte_size IS NULL) OR (byte_size >= 0))),
    CONSTRAINT archive_artifact_state_check CHECK ((state = ANY (ARRAY['staging'::text, 'verified'::text, 'superseded'::text]))),
    CONSTRAINT archive_artifact_verified_complete CHECK (((state <> 'verified'::text) OR ((remote_path IS NOT NULL) AND (sha256 IS NOT NULL) AND (byte_size IS NOT NULL) AND (verified_at IS NOT NULL))))
);


ALTER TABLE public.archive_artifact OWNER TO cea_user;

--
-- Name: TABLE archive_artifact; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_artifact IS 'Immutable ZIP artifacts per dataset revision; only one verified artifact per dataset revision at a time.';


--
-- Name: archive_availability; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_availability (
    availability_id bigint NOT NULL,
    dataset_id uuid,
    revision bigint,
    source_table text NOT NULL,
    room text,
    start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    source_rows_removed boolean DEFAULT false NOT NULL,
    source_removed_at timestamp with time zone,
    aggregates_refreshed boolean DEFAULT false NOT NULL,
    aggregates_refreshed_at timestamp with time zone,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_availability_aggregate_status CHECK ((aggregates_refreshed = (aggregates_refreshed_at IS NOT NULL))),
    CONSTRAINT archive_availability_bounds CHECK ((start_at < end_at)),
    CONSTRAINT archive_availability_dataset_binding CHECK ((((state = 'captured'::text) AND (dataset_id IS NOT NULL) AND (revision IS NOT NULL) AND (revision > 0)) OR ((state = 'expired'::text) AND (dataset_id IS NULL) AND (revision IS NULL)))),
    CONSTRAINT archive_availability_refresh_order CHECK (((NOT aggregates_refreshed) OR source_rows_removed)),
    CONSTRAINT archive_availability_removal_status CHECK ((source_rows_removed = (source_removed_at IS NOT NULL))),
    CONSTRAINT archive_availability_state_check CHECK ((state = ANY (ARRAY['captured'::text, 'expired'::text])))
);


ALTER TABLE public.archive_availability OWNER TO cea_user;

--
-- Name: TABLE archive_availability; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_availability IS 'Per source table/room intervals that a dataset captured (or that expired): the local-history guard reads these instead of trusting closure alone.';


--
-- Name: archive_availability_availability_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.archive_availability ALTER COLUMN availability_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.archive_availability_availability_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: archive_component; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_component (
    parent_artifact_id uuid NOT NULL,
    child_dataset_id uuid NOT NULL,
    child_revision bigint NOT NULL,
    member_prefix text NOT NULL,
    manifest_sha256 text NOT NULL,
    observation_counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_component_manifest_sha256_check CHECK ((manifest_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT archive_component_revision_check CHECK ((child_revision > 0))
);


ALTER TABLE public.archive_component OWNER TO cea_user;

--
-- Name: TABLE archive_component; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_component IS 'Annual containers pin each child dataset identity, revision, counts and hashes; not just paths.';


--
-- Name: archive_dataset; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_dataset (
    dataset_id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    grow_plan_id uuid,
    calendar_year integer,
    quarter integer,
    room text NOT NULL,
    calendar_revision bigint DEFAULT 1 NOT NULL,
    data_revision bigint DEFAULT 1 NOT NULL,
    published_revision bigint,
    state text DEFAULT 'pending'::text NOT NULL,
    available_from timestamp with time zone,
    available_to timestamp with time zone,
    anniversary_at timestamp with time zone,
    manifest jsonb,
    last_error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_dataset_availability_bounds CHECK (((available_from IS NULL) OR (available_to IS NULL) OR (available_from < available_to))),
    CONSTRAINT archive_dataset_kind_check CHECK ((kind = ANY (ARRAY['grow'::text, 'veg_quarter'::text, 'veg_year'::text]))),
    CONSTRAINT archive_dataset_kind_linkage CHECK ((((kind = 'grow'::text) AND (grow_plan_id IS NOT NULL) AND (calendar_year IS NULL) AND (quarter IS NULL)) OR ((kind = 'veg_quarter'::text) AND (grow_plan_id IS NULL) AND (calendar_year IS NOT NULL) AND (quarter IS NOT NULL)) OR ((kind = 'veg_year'::text) AND (grow_plan_id IS NULL) AND (calendar_year IS NOT NULL) AND (quarter IS NULL)))),
    CONSTRAINT archive_dataset_quarter_check CHECK (((quarter IS NULL) OR ((quarter >= 1) AND (quarter <= 4)))),
    CONSTRAINT archive_dataset_revisions_check CHECK (((calendar_revision > 0) AND (data_revision > 0) AND ((published_revision IS NULL) OR ((published_revision > 0) AND (published_revision <= data_revision))))),
    CONSTRAINT archive_dataset_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'copying'::text, 'ready'::text, 'dirty'::text, 'blocked'::text, 'zip_only'::text])))
);


ALTER TABLE public.archive_dataset OWNER TO cea_user;

--
-- Name: TABLE archive_dataset; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_dataset IS 'Archive catalog: one grow dataset per closed grow, one dataset per Veg calendar quarter and year.';


--
-- Name: archive_expiry_cursor; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_expiry_cursor (
    source_table text NOT NULL,
    room text NOT NULL,
    processed_before timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT archive_expiry_cursor_room_nonempty CHECK ((btrim(room) <> ''::text)),
    CONSTRAINT archive_expiry_cursor_source_nonempty CHECK ((btrim(source_table) <> ''::text))
);


ALTER TABLE public.archive_expiry_cursor OWNER TO cea_user;

--
-- Name: TABLE archive_expiry_cursor; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_expiry_cursor IS 'Forward-only per-source/room nongrow expiry frontier; active/open/captured and unresolved rows are never deleted by the frontier.';


--
-- Name: archive_job; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_job (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    dataset_id uuid NOT NULL,
    kind text NOT NULL,
    requested_revision bigint NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    checkpoint jsonb DEFAULT '{}'::jsonb NOT NULL,
    lease_owner uuid,
    lease_token uuid,
    lease_until timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_job_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT archive_job_kind_check CHECK ((kind = ANY (ARRAY['export'::text, 'annual_merge'::text, 'load'::text, 'expire'::text, 'artifact_cleanup'::text]))),
    CONSTRAINT archive_job_lease_complete CHECK ((((lease_owner IS NULL) = (lease_token IS NULL)) AND ((lease_owner IS NULL) = (lease_until IS NULL)))),
    CONSTRAINT archive_job_requested_revision_check CHECK ((requested_revision > 0)),
    CONSTRAINT archive_job_running_requires_lease CHECK (((state <> 'running'::text) OR (lease_owner IS NOT NULL))),
    CONSTRAINT archive_job_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'blocked'::text, 'failed'::text])))
);


ALTER TABLE public.archive_job OWNER TO cea_user;

--
-- Name: TABLE archive_job; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_job IS 'Durable archive queue with renewable 60 second leases; idempotent per dataset, kind and requested revision.';


--
-- Name: archive_load; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_load (
    load_id uuid DEFAULT gen_random_uuid() NOT NULL,
    dataset_id uuid NOT NULL,
    revision bigint NOT NULL,
    source_kind text NOT NULL,
    artifact_id uuid,
    member_prefix text,
    staging_path text,
    last_accessed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_load_revision_check CHECK ((revision > 0)),
    CONSTRAINT archive_load_source_kind_check CHECK ((source_kind = 'zip'::text)),
    CONSTRAINT archive_load_zip_requires_artifact CHECK (((source_kind <> 'zip'::text) OR (artifact_id IS NOT NULL)))
);


ALTER TABLE public.archive_load OWNER TO cea_user;

--
-- Name: TABLE archive_load; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_load IS 'Explicit Historical selection pin: one row per user-selected load, expiring after idle time.';


--
-- Name: archive_scope_override; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_scope_override (
    override_id bigint NOT NULL,
    source_table text NOT NULL,
    source_identity jsonb NOT NULL,
    start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    room text NOT NULL,
    confirmed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT archive_scope_override_bounds CHECK ((start_at < end_at)),
    CONSTRAINT archive_scope_override_identity_positive CHECK ((((source_identity ->> 'sensor_id'::text))::bigint > 0)),
    CONSTRAINT archive_scope_override_identity_shape CHECK (((source_identity ? 'sensor_id'::text) AND (jsonb_typeof((source_identity -> 'sensor_id'::text)) = 'number'::text) AND ((source_identity - 'sensor_id'::text) = '{}'::jsonb))),
    CONSTRAINT archive_scope_override_room_check CHECK ((btrim(room) <> ''::text)),
    CONSTRAINT archive_scope_override_source_table_check CHECK ((source_table = 'measurement'::text))
);


ALTER TABLE public.archive_scope_override OWNER TO cea_user;

--
-- Name: TABLE archive_scope_override; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_scope_override IS 'Owner-confirmed dated room assignment for unresolved measurement identities; never a general room-history editor.';


--
-- Name: archive_scope_override_override_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.archive_scope_override ALTER COLUMN override_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.archive_scope_override_override_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: archive_window; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.archive_window (
    dataset_id uuid NOT NULL,
    calendar_revision bigint NOT NULL,
    event_id integer NOT NULL,
    phase_type text NOT NULL,
    room text NOT NULL,
    start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    CONSTRAINT archive_window_half_open CHECK ((start_at < end_at)),
    CONSTRAINT archive_window_revision_check CHECK ((calendar_revision > 0))
);


ALTER TABLE public.archive_window OWNER TO cea_user;

--
-- Name: TABLE archive_window; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.archive_window IS 'Per-phase half-open selection windows; intersecting same-grow/same-room windows merge for row selection.';


--
-- Name: automation_state_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.automation_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.automation_state_id_seq OWNER TO cea_user;

--
-- Name: automation_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.automation_state_id_seq OWNED BY public.automation_state.id;


--
-- Name: calendar_event; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.calendar_event (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text DEFAULT 'main'::text NOT NULL,
    event_type text NOT NULL,
    title text NOT NULL,
    start_date date NOT NULL,
    end_date date,
    all_day boolean DEFAULT true NOT NULL,
    notes text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    crop_batch_id integer,
    grow_plan_id uuid,
    recurrence_rule text,
    recurrence_parent_id integer,
    ical_uid text NOT NULL,
    external_provider text,
    external_calendar_id text,
    external_event_id text,
    external_etag text,
    last_synced_at timestamp with time zone,
    sync_status text DEFAULT 'synced'::text NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text,
    local_revision bigint DEFAULT 1 NOT NULL,
    sync_connection_id integer,
    sync_generation bigint,
    sync_error_code text,
    sync_next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    sync_attempts integer DEFAULT 0 NOT NULL,
    sync_lease_owner uuid,
    sync_lease_until timestamp with time zone,
    sync_mirror_all boolean DEFAULT false NOT NULL,
    CONSTRAINT calendar_event_local_revision_positive CHECK ((local_revision > 0)),
    CONSTRAINT calendar_event_sync_attempts_nonnegative CHECK ((sync_attempts >= 0)),
    CONSTRAINT calendar_event_sync_lease_pair CHECK (((sync_lease_owner IS NULL) = (sync_lease_until IS NULL)))
);


ALTER TABLE public.calendar_event OWNER TO cea_user;

--
-- Name: calendar_event_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.calendar_event_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.calendar_event_id_seq OWNER TO cea_user;

--
-- Name: calendar_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.calendar_event_id_seq OWNED BY public.calendar_event.id;


--
-- Name: calendar_mode_application; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.calendar_mode_application (
    id integer NOT NULL,
    event_id integer NOT NULL,
    applied_date date NOT NULL,
    mode_id integer,
    submode_id integer,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    triggered_by text NOT NULL
);


ALTER TABLE public.calendar_mode_application OWNER TO cea_user;

--
-- Name: calendar_mode_application_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.calendar_mode_application_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.calendar_mode_application_id_seq OWNER TO cea_user;

--
-- Name: calendar_mode_application_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.calendar_mode_application_id_seq OWNED BY public.calendar_mode_application.id;


--
-- Name: calendar_room_profile; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.calendar_room_profile (
    location text NOT NULL,
    display_name text NOT NULL,
    color_key text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    nextcloud_calendar_url text,
    caldav_sync_token text,
    enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    calendar_mode_transitions_enabled boolean DEFAULT true NOT NULL
);


ALTER TABLE public.calendar_room_profile OWNER TO cea_user;

--
-- Name: calendar_sync_connection; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.calendar_sync_connection (
    id integer NOT NULL,
    provider text DEFAULT 'nextcloud'::text NOT NULL,
    display_name text,
    account_email text,
    caldav_base_url text NOT NULL,
    credentials_encrypted bytea NOT NULL,
    target_calendar_url text NOT NULL,
    sync_token text,
    last_sync_at timestamp with time zone,
    last_error text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    instance_id uuid DEFAULT gen_random_uuid() NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    files_root_url text,
    CONSTRAINT calendar_sync_connection_generation_positive CHECK ((generation > 0)),
    CONSTRAINT calendar_sync_connection_provider_check CHECK ((provider = 'nextcloud'::text))
);


ALTER TABLE public.calendar_sync_connection OWNER TO cea_user;

--
-- Name: calendar_sync_connection_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.calendar_sync_connection_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.calendar_sync_connection_id_seq OWNER TO cea_user;

--
-- Name: calendar_sync_connection_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.calendar_sync_connection_id_seq OWNED BY public.calendar_sync_connection.id;


--
-- Name: climate_periods; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.climate_periods (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    mode_id integer,
    submode_id integer,
    period_name text NOT NULL,
    start_time time without time zone NOT NULL,
    end_time time without time zone NOT NULL,
    ramp_minutes integer DEFAULT 0 NOT NULL,
    heating_setpoint real,
    cooling_setpoint real,
    vpd_setpoint real,
    co2_setpoint integer,
    details text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.climate_periods OWNER TO postgres;

--
-- Name: climate_periods_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.climate_periods_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.climate_periods_id_seq OWNER TO postgres;

--
-- Name: climate_periods_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.climate_periods_id_seq OWNED BY public.climate_periods.id;


--
-- Name: config_versions; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.config_versions (
    version_id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    author text,
    comment text,
    config_type text NOT NULL,
    location text,
    cluster text,
    changes jsonb
);


ALTER TABLE public.config_versions OWNER TO cea_user;

--
-- Name: config_versions_version_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.config_versions_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.config_versions_version_id_seq OWNER TO cea_user;

--
-- Name: config_versions_version_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.config_versions_version_id_seq OWNED BY public.config_versions.version_id;


--
-- Name: control_history; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.control_history (
    id bigint NOT NULL,
    "time" timestamp with time zone DEFAULT now() NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    channel integer NOT NULL,
    old_state integer,
    new_state integer,
    mode text,
    reason text,
    sensor_value real,
    setpoint real,
    load_percent real,
    manual_expires_at timestamp with time zone
);


ALTER TABLE public.control_history OWNER TO cea_user;

--
-- Name: control_history_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.control_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.control_history_id_seq OWNER TO cea_user;

--
-- Name: control_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.control_history_id_seq OWNED BY public.control_history.id;


--
-- Name: crop_batch; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.crop_batch (
    batch_id integer NOT NULL,
    crop_name text NOT NULL,
    start_date date NOT NULL,
    end_date date,
    room_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.crop_batch OWNER TO cea_user;

--
-- Name: crop_batch_batch_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.crop_batch_batch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.crop_batch_batch_id_seq OWNER TO cea_user;

--
-- Name: crop_batch_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.crop_batch_batch_id_seq OWNED BY public.crop_batch.batch_id;


--
-- Name: device; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.device (
    device_id integer NOT NULL,
    rack_id integer,
    name text NOT NULL,
    type text NOT NULL,
    ip_address text,
    serial_number text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.device OWNER TO cea_user;

--
-- Name: device_device_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.device_device_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.device_device_id_seq OWNER TO cea_user;

--
-- Name: device_device_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.device_device_id_seq OWNED BY public.device.device_id;


--
-- Name: device_mappings; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.device_mappings (
    id bigint NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    channel integer NOT NULL,
    active_high boolean DEFAULT true NOT NULL,
    safe_state integer NOT NULL,
    mcp_board_id integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.device_mappings OWNER TO cea_user;

--
-- Name: device_mappings_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.device_mappings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.device_mappings_id_seq OWNER TO cea_user;

--
-- Name: device_mappings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.device_mappings_id_seq OWNED BY public.device_mappings.id;


--
-- Name: device_registry; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.device_registry (
    device_id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    display_name text,
    device_type text NOT NULL,
    channel integer,
    dimming_enabled boolean,
    dimming_type text,
    dimming_board_id integer,
    dimming_channel smallint,
    safety_level integer,
    pid_enabled boolean,
    interlock_with jsonb,
    pid_setpoints jsonb,
    per_room_index integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_device_registry_cluster_main CHECK ((cluster = 'main'::text))
);


ALTER TABLE public.device_registry OWNER TO cea_user;

--
-- Name: device_registry_device_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.device_registry ALTER COLUMN device_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.device_registry_device_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: device_states; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.device_states (
    id bigint NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    channel integer NOT NULL,
    state integer NOT NULL,
    mode text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.device_states OWNER TO cea_user;

--
-- Name: device_states_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.device_states_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.device_states_id_seq OWNER TO cea_user;

--
-- Name: device_states_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.device_states_id_seq OWNED BY public.device_states.id;


--
-- Name: flower_submodes; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.flower_submodes (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    week_start integer,
    week_end integer,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT flower_submodes_week_end_check CHECK ((week_end >= 1)),
    CONSTRAINT flower_submodes_week_start_check CHECK ((week_start >= 1))
);


ALTER TABLE public.flower_submodes OWNER TO cea_user;

--
-- Name: flower_submodes_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.flower_submodes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.flower_submodes_id_seq OWNER TO cea_user;

--
-- Name: flower_submodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.flower_submodes_id_seq OWNED BY public.flower_submodes.id;


--
-- Name: grow_lifecycle; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.grow_lifecycle (
    grow_plan_id uuid NOT NULL,
    crop_batch_id integer NOT NULL,
    state text DEFAULT 'open'::text NOT NULL,
    calendar_revision bigint DEFAULT 1 NOT NULL,
    original_drying_end date,
    first_closed_at timestamp with time zone,
    closed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT grow_lifecycle_calendar_revision_check CHECK ((calendar_revision > 0)),
    CONSTRAINT grow_lifecycle_closed_at_required CHECK (((state <> 'closed'::text) OR (closed_at IS NOT NULL))),
    CONSTRAINT grow_lifecycle_first_closure_complete CHECK (((first_closed_at IS NULL) = (original_drying_end IS NULL))),
    CONSTRAINT grow_lifecycle_state_check CHECK ((state = ANY (ARRAY['open'::text, 'closed'::text])))
);


ALTER TABLE public.grow_lifecycle OWNER TO cea_user;

--
-- Name: TABLE grow_lifecycle; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.grow_lifecycle IS 'Explicit grow lifecycle: one row per top-level Calendar grow UUID mapped to exactly one crop batch.';


--
-- Name: grow_lifecycle_backfill_report; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.grow_lifecycle_backfill_report AS
 WITH grow_batches AS (
         SELECT e.grow_plan_id,
            count(*) AS phase_events,
            count(*) FILTER (WHERE (e.crop_batch_id IS NULL)) AS events_without_batch,
            count(DISTINCT e.crop_batch_id) AS distinct_batches,
            min(e.crop_batch_id) AS batch_id
           FROM public.calendar_event e
          WHERE ((e.grow_plan_id IS NOT NULL) AND (e.deleted_at IS NULL))
          GROUP BY e.grow_plan_id
        ), batch_owners AS (
         SELECT gb_1.batch_id,
            count(DISTINCT gb_1.grow_plan_id) AS owner_count
           FROM grow_batches gb_1
          WHERE (gb_1.batch_id IS NOT NULL)
          GROUP BY gb_1.batch_id
        )
 SELECT gb.grow_plan_id,
    gb.batch_id,
    gb.phase_events,
    gb.events_without_batch,
    gb.distinct_batches,
        CASE
            WHEN (gb.batch_id IS NULL) THEN 'missing_batch'::text
            WHEN (gb.distinct_batches > 1) THEN 'ambiguous_batch'::text
            WHEN (COALESCE(bo.owner_count, (0)::bigint) > 1) THEN 'shared_batch'::text
            ELSE 'backfill_pending'::text
        END AS linkage_state
   FROM ((grow_batches gb
     LEFT JOIN batch_owners bo ON ((bo.batch_id = gb.batch_id)))
     LEFT JOIN public.grow_lifecycle gl ON ((gl.grow_plan_id = gb.grow_plan_id)))
  WHERE (gl.grow_plan_id IS NULL);


ALTER TABLE public.grow_lifecycle_backfill_report OWNER TO cea_user;

--
-- Name: VIEW grow_lifecycle_backfill_report; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON VIEW public.grow_lifecycle_backfill_report IS 'Blocking grow-linkage preflight report: active top-level grow UUIDs without an unambiguous crop batch.';


--
-- Name: grow_plan_idempotency; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.grow_plan_idempotency (
    idempotency_key uuid NOT NULL,
    grow_plan_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


ALTER TABLE public.grow_plan_idempotency OWNER TO cea_user;

--
-- Name: light_presets; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.light_presets (
    id integer NOT NULL,
    mode_id integer,
    submode_id integer,
    lights_on_hour integer,
    lights_off_hour integer,
    intensity_day integer,
    intensity_night integer,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT light_presets_intensity_day_check CHECK (((intensity_day >= 0) AND (intensity_day <= 100))),
    CONSTRAINT light_presets_intensity_night_check CHECK (((intensity_night >= 0) AND (intensity_night <= 100))),
    CONSTRAINT light_presets_lights_off_hour_check CHECK (((lights_off_hour >= 0) AND (lights_off_hour < 24))),
    CONSTRAINT light_presets_lights_on_hour_check CHECK (((lights_on_hour >= 0) AND (lights_on_hour < 24)))
);


ALTER TABLE public.light_presets OWNER TO cea_user;

--
-- Name: light_presets_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.light_presets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.light_presets_id_seq OWNER TO cea_user;

--
-- Name: light_presets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.light_presets_id_seq OWNED BY public.light_presets.id;


--
-- Name: light_programs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.light_programs (
    id integer NOT NULL,
    device_id integer,
    location text NOT NULL,
    cluster text DEFAULT 'main'::text NOT NULL,
    mode_id integer,
    name text NOT NULL,
    program_type text NOT NULL,
    start_time time without time zone NOT NULL,
    end_time time without time zone NOT NULL,
    cycle_enabled boolean DEFAULT false NOT NULL,
    cycle_on_seconds integer,
    cycle_off_seconds integer,
    target_intensity real DEFAULT 10.0 NOT NULL,
    ramp_up_minutes integer DEFAULT 0 NOT NULL,
    ramp_down_minutes integer DEFAULT 0 NOT NULL,
    day_of_week integer,
    enabled boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_light_programs_intensity_range CHECK (((target_intensity >= (0)::double precision) AND (target_intensity <= (100)::double precision))),
    CONSTRAINT ck_light_programs_type CHECK ((program_type = ANY (ARRAY['supplemental'::text, 'override'::text])))
);


ALTER TABLE public.light_programs OWNER TO postgres;

--
-- Name: light_programs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

ALTER TABLE public.light_programs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.light_programs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: light_state_history; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.light_state_history (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    old_state text,
    new_state text NOT NULL,
    intensity real,
    ramp_progress real,
    triggered_by text NOT NULL,
    triggered_at timestamp with time zone DEFAULT now(),
    CONSTRAINT light_state_history_intensity_check CHECK (((intensity >= (0)::double precision) AND (intensity <= (100)::double precision))),
    CONSTRAINT light_state_history_new_state_check CHECK ((new_state = ANY (ARRAY['sun'::text, 'moon'::text, 'ramping_up'::text, 'ramping_down'::text]))),
    CONSTRAINT light_state_history_old_state_check CHECK (((old_state IS NULL) OR (old_state = ANY (ARRAY['sun'::text, 'moon'::text, 'ramping_up'::text, 'ramping_down'::text, 'unknown'::text])))),
    CONSTRAINT light_state_history_ramp_progress_check CHECK (((ramp_progress IS NULL) OR ((ramp_progress >= (0)::double precision) AND (ramp_progress <= (1)::double precision)))),
    CONSTRAINT light_state_history_triggered_by_check CHECK ((triggered_by = ANY (ARRAY['schedule'::text, 'manual'::text, 'system'::text])))
);


ALTER TABLE public.light_state_history OWNER TO postgres;

--
-- Name: TABLE light_state_history; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.light_state_history IS 'Audit trail for light state transitions (sun/moon/ramping) per device';


--
-- Name: light_state_history_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.light_state_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.light_state_history_id_seq OWNER TO postgres;

--
-- Name: light_state_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.light_state_history_id_seq OWNED BY public.light_state_history.id;


--
-- Name: light_target_intensity; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.light_target_intensity (
    device_id integer NOT NULL,
    mode_id integer NOT NULL,
    target_intensity real DEFAULT 10.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_light_target_intensity_range CHECK (((target_intensity >= (0)::double precision) AND (target_intensity <= (100)::double precision)))
);


ALTER TABLE public.light_target_intensity OWNER TO postgres;

--
-- Name: rack; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.rack (
    rack_id integer NOT NULL,
    room_id integer NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.rack OWNER TO cea_user;

--
-- Name: room; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.room (
    room_id integer NOT NULL,
    name text NOT NULL,
    target_vpd real,
    target_temp real,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.room OWNER TO cea_user;

--
-- Name: sensor; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.sensor (
    sensor_id integer NOT NULL,
    device_id integer NOT NULL,
    name text NOT NULL,
    unit text NOT NULL,
    data_type text NOT NULL,
    channel integer,
    calibration_offset real DEFAULT 0.0,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.sensor OWNER TO cea_user;

--
-- Name: measurement_timeseries; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.measurement_timeseries AS
 SELECT m."time",
    m.sensor_id,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.device_id,
    d.name AS device_name,
    r.room_id,
    r.name AS room_name
   FROM ((((public.measurement m
     JOIN public.sensor s ON ((m.sensor_id = s.sensor_id)))
     JOIN public.device d ON ((s.device_id = d.device_id)))
     LEFT JOIN public.rack rk ON ((d.rack_id = rk.rack_id)))
     LEFT JOIN public.room r ON ((rk.room_id = r.room_id)));


ALTER TABLE public.measurement_timeseries OWNER TO cea_user;

--
-- Name: measurement_with_metadata; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.measurement_with_metadata AS
 SELECT m."time",
    m.sensor_id,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    s.data_type AS sensor_data_type,
    d.device_id,
    d.name AS device_name,
    d.type AS device_type,
    r.room_id,
    r.name AS room_name,
    r.target_vpd,
    r.target_temp
   FROM ((((public.measurement m
     JOIN public.sensor s ON ((m.sensor_id = s.sensor_id)))
     JOIN public.device d ON ((s.device_id = d.device_id)))
     LEFT JOIN public.rack rk ON ((d.rack_id = rk.rack_id)))
     LEFT JOIN public.room r ON ((rk.room_id = r.room_id)));


ALTER TABLE public.measurement_with_metadata OWNER TO cea_user;

--
-- Name: migration_0012_state; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.migration_0012_state (
    created_table boolean NOT NULL,
    had_view boolean NOT NULL
);


ALTER TABLE public.migration_0012_state OWNER TO cea_user;

--
-- Name: mode_notes; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.mode_notes (
    id integer NOT NULL,
    location character varying(50) NOT NULL,
    mode character varying(20) NOT NULL,
    content text,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.mode_notes OWNER TO cea_user;

--
-- Name: mode_notes_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.mode_notes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.mode_notes_id_seq OWNER TO cea_user;

--
-- Name: mode_notes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.mode_notes_id_seq OWNED BY public.mode_notes.id;


--
-- Name: mode_parameters; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.mode_parameters (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    mode_id integer NOT NULL,
    submode_id integer,
    day_start_time time without time zone DEFAULT '17:00:00'::time without time zone NOT NULL,
    night_start_time time without time zone DEFAULT '11:00:00'::time without time zone NOT NULL,
    ramp_up_minutes integer DEFAULT 30 NOT NULL,
    ramp_down_minutes integer DEFAULT 30 NOT NULL,
    pre_day_minutes integer DEFAULT 30 NOT NULL,
    pre_night_minutes integer DEFAULT 30 NOT NULL,
    day_heat_temp real DEFAULT 24.0 NOT NULL,
    day_cool_temp real DEFAULT 28.0 NOT NULL,
    day_vpd real DEFAULT 1.0 NOT NULL,
    day_co2 integer DEFAULT 800 NOT NULL,
    day_leaf_delta real DEFAULT '-2.0'::numeric NOT NULL,
    night_heat_temp real DEFAULT 20.0 NOT NULL,
    night_cool_temp real DEFAULT 24.0 NOT NULL,
    night_vpd real DEFAULT 0.8 NOT NULL,
    night_co2 integer DEFAULT 600 NOT NULL,
    night_leaf_delta real DEFAULT '-1.0'::numeric NOT NULL,
    main_light_intensity integer DEFAULT 100 NOT NULL,
    supplemental_light_intensity integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    pre_day_heat_temp real DEFAULT 22.0 NOT NULL,
    pre_day_cool_temp real DEFAULT 26.0 NOT NULL,
    pre_day_vpd real DEFAULT 0.9 NOT NULL,
    pre_day_co2 integer DEFAULT 700 NOT NULL,
    pre_night_heat_temp real DEFAULT 22.0 NOT NULL,
    pre_night_cool_temp real DEFAULT 26.0 NOT NULL,
    pre_night_vpd real DEFAULT 0.9 NOT NULL,
    pre_night_co2 integer DEFAULT 700 NOT NULL,
    pre_day_ramp_minutes integer DEFAULT 30,
    pre_night_ramp_minutes integer DEFAULT 30,
    light_ramp_up_minutes integer DEFAULT 15 NOT NULL,
    light_ramp_down_minutes integer DEFAULT 15 NOT NULL
);


ALTER TABLE public.mode_parameters OWNER TO cea_user;

--
-- Name: mode_parameters_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.mode_parameters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.mode_parameters_id_seq OWNER TO cea_user;

--
-- Name: mode_parameters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.mode_parameters_id_seq OWNED BY public.mode_parameters.id;


--
-- Name: mode_transition_history; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.mode_transition_history (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    old_mode_id integer,
    old_submode_id integer,
    new_mode_id integer NOT NULL,
    new_submode_id integer,
    triggered_by text NOT NULL,
    "time" timestamp with time zone DEFAULT now(),
    parameters_synced jsonb,
    success boolean DEFAULT true,
    error_message text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT mode_transition_history_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))),
    CONSTRAINT mode_transition_history_triggered_by_check CHECK ((triggered_by = ANY (ARRAY['api'::text, 'schedule'::text, 'system'::text])))
);


ALTER TABLE public.mode_transition_history OWNER TO cea_user;

--
-- Name: TABLE mode_transition_history; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.mode_transition_history IS 'Audit trail for environmental mode transitions in rooms/clusters';


--
-- Name: COLUMN mode_transition_history.old_mode_id; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.mode_transition_history.old_mode_id IS 'Previous room_modes.id (nullable)';


--
-- Name: COLUMN mode_transition_history.new_mode_id; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.mode_transition_history.new_mode_id IS 'New room_modes.id';


--
-- Name: COLUMN mode_transition_history.parameters_synced; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.mode_transition_history.parameters_synced IS 'JSON: old_mode_name, old_submode_name, new_mode_name, new_submode_name, schedule_sync (from ModeTransitionService)';


--
-- Name: mode_transition_history_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.mode_transition_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.mode_transition_history_id_seq OWNER TO cea_user;

--
-- Name: mode_transition_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.mode_transition_history_id_seq OWNED BY public.mode_transition_history.id;


--
-- Name: monitoring_automation_state_1min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_automation_state_1min AS
 SELECT _materialized_hypertable_616.bucket,
    _materialized_hypertable_616.location,
    _materialized_hypertable_616.cluster,
    _materialized_hypertable_616.device_name,
    _materialized_hypertable_616.sample_count,
    _materialized_hypertable_616.device_state_min,
    _materialized_hypertable_616.device_state_max,
    _materialized_hypertable_616.device_state_last,
    _materialized_hypertable_616.device_mode_last,
    _materialized_hypertable_616.pid_output_count,
    _materialized_hypertable_616.pid_output_min,
    _materialized_hypertable_616.pid_output_max,
    _materialized_hypertable_616.pid_output_last,
    _materialized_hypertable_616.duty_cycle_percent_count,
    _materialized_hypertable_616.duty_cycle_percent_min,
    _materialized_hypertable_616.duty_cycle_percent_max,
    _materialized_hypertable_616.duty_cycle_percent_last,
    _materialized_hypertable_616.control_reason_last,
    _materialized_hypertable_616.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_616;


ALTER TABLE public.monitoring_automation_state_1min OWNER TO cea_user;

--
-- Name: monitoring_automation_state_5min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_automation_state_5min AS
 SELECT _materialized_hypertable_617.bucket,
    _materialized_hypertable_617.location,
    _materialized_hypertable_617.cluster,
    _materialized_hypertable_617.device_name,
    _materialized_hypertable_617.sample_count,
    _materialized_hypertable_617.device_state_min,
    _materialized_hypertable_617.device_state_max,
    _materialized_hypertable_617.device_state_last,
    _materialized_hypertable_617.device_mode_last,
    _materialized_hypertable_617.pid_output_count,
    _materialized_hypertable_617.pid_output_min,
    _materialized_hypertable_617.pid_output_max,
    _materialized_hypertable_617.pid_output_last,
    _materialized_hypertable_617.duty_cycle_percent_count,
    _materialized_hypertable_617.duty_cycle_percent_min,
    _materialized_hypertable_617.duty_cycle_percent_max,
    _materialized_hypertable_617.duty_cycle_percent_last,
    _materialized_hypertable_617.control_reason_last,
    _materialized_hypertable_617.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_617;


ALTER TABLE public.monitoring_automation_state_5min OWNER TO cea_user;

--
-- Name: monitoring_cagg_backfill_marker; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_cagg_backfill_marker (
    cagg_name text NOT NULL,
    covered_through timestamp with time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL,
    completed_by text NOT NULL,
    CONSTRAINT monitoring_cagg_backfill_marker_completed_by_check CHECK ((btrim(completed_by) <> ''::text))
);


ALTER TABLE public.monitoring_cagg_backfill_marker OWNER TO cea_user;

--
-- Name: monitoring_cagg_watermark; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_cagg_watermark AS
 SELECT (continuous_agg.user_view_name)::text AS cagg_name,
    _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(continuous_agg.mat_hypertable_id)) AS materialization_watermark,
    invalidation.invalidation_source,
    _timescaledb_functions.to_timestamp(invalidation.lowest_modified_value) AS invalidation_start,
    _timescaledb_functions.to_timestamp(invalidation.greatest_modified_value) AS invalidation_end
   FROM (_timescaledb_catalog.continuous_agg continuous_agg
     LEFT JOIN LATERAL ( SELECT 'materialization'::text AS invalidation_source,
            materialization_log.lowest_modified_value,
            materialization_log.greatest_modified_value
           FROM _timescaledb_catalog.continuous_aggs_materialization_invalidation_log materialization_log
          WHERE (materialization_log.materialization_id = continuous_agg.mat_hypertable_id)
        UNION ALL
         SELECT 'hypertable'::text AS invalidation_source,
            hypertable_log.lowest_modified_value,
            hypertable_log.greatest_modified_value
           FROM _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log hypertable_log
          WHERE (hypertable_log.hypertable_id = continuous_agg.raw_hypertable_id)) invalidation ON (true))
  WHERE ((continuous_agg.user_view_schema = 'public'::name) AND (continuous_agg.user_view_name ~~ like_escape('monitoring\_%'::text, '\'::text)));


ALTER TABLE public.monitoring_cagg_watermark OWNER TO cea_user;

--
-- Name: monitoring_dependent_view_snapshot_0007; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_dependent_view_snapshot_0007 (
    view_schema text NOT NULL,
    view_name text NOT NULL,
    view_definition text NOT NULL,
    dependency_depth integer NOT NULL,
    view_owner text NOT NULL,
    view_owner_oid oid NOT NULL,
    view_acl aclitem[],
    CONSTRAINT monitoring_dependent_view_snapshot_0007_dependency_depth_check CHECK ((dependency_depth > 0))
);


ALTER TABLE public.monitoring_dependent_view_snapshot_0007 OWNER TO cea_user;

--
-- Name: monitoring_effective_setpoints_1min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_effective_setpoints_1min AS
 SELECT _materialized_hypertable_614.bucket,
    _materialized_hypertable_614.location,
    _materialized_hypertable_614.cluster,
    _materialized_hypertable_614.device_name,
    _materialized_hypertable_614.mode,
    _materialized_hypertable_614.effective_heating_setpoint_count,
    _materialized_hypertable_614.effective_heating_setpoint_sum,
    _materialized_hypertable_614.effective_heating_setpoint_sum_squares,
    _materialized_hypertable_614.effective_heating_setpoint_min,
    _materialized_hypertable_614.effective_heating_setpoint_max,
    _materialized_hypertable_614.effective_heating_setpoint_last,
    _materialized_hypertable_614.effective_cooling_setpoint_count,
    _materialized_hypertable_614.effective_cooling_setpoint_sum,
    _materialized_hypertable_614.effective_cooling_setpoint_sum_squares,
    _materialized_hypertable_614.effective_cooling_setpoint_min,
    _materialized_hypertable_614.effective_cooling_setpoint_max,
    _materialized_hypertable_614.effective_cooling_setpoint_last,
    _materialized_hypertable_614.effective_humidity_setpoint_count,
    _materialized_hypertable_614.effective_humidity_setpoint_sum,
    _materialized_hypertable_614.effective_humidity_setpoint_sum_squares,
    _materialized_hypertable_614.effective_humidity_setpoint_min,
    _materialized_hypertable_614.effective_humidity_setpoint_max,
    _materialized_hypertable_614.effective_humidity_setpoint_last,
    _materialized_hypertable_614.effective_co2_setpoint_count,
    _materialized_hypertable_614.effective_co2_setpoint_sum,
    _materialized_hypertable_614.effective_co2_setpoint_sum_squares,
    _materialized_hypertable_614.effective_co2_setpoint_min,
    _materialized_hypertable_614.effective_co2_setpoint_max,
    _materialized_hypertable_614.effective_co2_setpoint_last,
    _materialized_hypertable_614.effective_vpd_setpoint_count,
    _materialized_hypertable_614.effective_vpd_setpoint_sum,
    _materialized_hypertable_614.effective_vpd_setpoint_sum_squares,
    _materialized_hypertable_614.effective_vpd_setpoint_min,
    _materialized_hypertable_614.effective_vpd_setpoint_max,
    _materialized_hypertable_614.effective_vpd_setpoint_last,
    _materialized_hypertable_614.effective_light_intensity_count,
    _materialized_hypertable_614.effective_light_intensity_sum,
    _materialized_hypertable_614.effective_light_intensity_sum_squares,
    _materialized_hypertable_614.effective_light_intensity_min,
    _materialized_hypertable_614.effective_light_intensity_max,
    _materialized_hypertable_614.effective_light_intensity_last,
    _materialized_hypertable_614.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_614;


ALTER TABLE public.monitoring_effective_setpoints_1min OWNER TO cea_user;

--
-- Name: monitoring_effective_setpoints_5min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_effective_setpoints_5min AS
 SELECT _materialized_hypertable_615.bucket,
    _materialized_hypertable_615.location,
    _materialized_hypertable_615.cluster,
    _materialized_hypertable_615.device_name,
    _materialized_hypertable_615.mode,
    _materialized_hypertable_615.effective_heating_setpoint_count,
    _materialized_hypertable_615.effective_heating_setpoint_sum,
    _materialized_hypertable_615.effective_heating_setpoint_sum_squares,
    _materialized_hypertable_615.effective_heating_setpoint_min,
    _materialized_hypertable_615.effective_heating_setpoint_max,
    _materialized_hypertable_615.effective_heating_setpoint_last,
    _materialized_hypertable_615.effective_cooling_setpoint_count,
    _materialized_hypertable_615.effective_cooling_setpoint_sum,
    _materialized_hypertable_615.effective_cooling_setpoint_sum_squares,
    _materialized_hypertable_615.effective_cooling_setpoint_min,
    _materialized_hypertable_615.effective_cooling_setpoint_max,
    _materialized_hypertable_615.effective_cooling_setpoint_last,
    _materialized_hypertable_615.effective_humidity_setpoint_count,
    _materialized_hypertable_615.effective_humidity_setpoint_sum,
    _materialized_hypertable_615.effective_humidity_setpoint_sum_squares,
    _materialized_hypertable_615.effective_humidity_setpoint_min,
    _materialized_hypertable_615.effective_humidity_setpoint_max,
    _materialized_hypertable_615.effective_humidity_setpoint_last,
    _materialized_hypertable_615.effective_co2_setpoint_count,
    _materialized_hypertable_615.effective_co2_setpoint_sum,
    _materialized_hypertable_615.effective_co2_setpoint_sum_squares,
    _materialized_hypertable_615.effective_co2_setpoint_min,
    _materialized_hypertable_615.effective_co2_setpoint_max,
    _materialized_hypertable_615.effective_co2_setpoint_last,
    _materialized_hypertable_615.effective_vpd_setpoint_count,
    _materialized_hypertable_615.effective_vpd_setpoint_sum,
    _materialized_hypertable_615.effective_vpd_setpoint_sum_squares,
    _materialized_hypertable_615.effective_vpd_setpoint_min,
    _materialized_hypertable_615.effective_vpd_setpoint_max,
    _materialized_hypertable_615.effective_vpd_setpoint_last,
    _materialized_hypertable_615.effective_light_intensity_count,
    _materialized_hypertable_615.effective_light_intensity_sum,
    _materialized_hypertable_615.effective_light_intensity_sum_squares,
    _materialized_hypertable_615.effective_light_intensity_min,
    _materialized_hypertable_615.effective_light_intensity_max,
    _materialized_hypertable_615.effective_light_intensity_last,
    _materialized_hypertable_615.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_615;


ALTER TABLE public.monitoring_effective_setpoints_5min OWNER TO cea_user;

--
-- Name: monitoring_effective_setpoints_ingest_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.monitoring_effective_setpoints_ingest_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.monitoring_effective_setpoints_ingest_id_seq OWNER TO cea_user;

--
-- Name: monitoring_effective_setpoints_ingest_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.monitoring_effective_setpoints_ingest_id_seq OWNED BY public.effective_setpoints.monitoring_ingest_id;


--
-- Name: monitoring_history_cagg_watermark; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_history_cagg_watermark AS
 WITH native AS (
         SELECT (cagg.user_view_name)::text AS cagg_name,
            _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(cagg.mat_hypertable_id)) AS materialization_watermark,
            invalidation.invalidation_source,
                CASE
                    WHEN (invalidation.lowest_modified_value < (- '210866803200000000'::bigint)) THEN '-infinity'::timestamp with time zone
                    ELSE _timescaledb_functions.to_timestamp(invalidation.lowest_modified_value)
                END AS invalidation_start,
                CASE
                    WHEN (invalidation.greatest_modified_value < (- '210866803200000000'::bigint)) THEN '-infinity'::timestamp with time zone
                    ELSE _timescaledb_functions.to_timestamp(invalidation.greatest_modified_value)
                END AS invalidation_end
           FROM (_timescaledb_catalog.continuous_agg cagg
             LEFT JOIN LATERAL ( SELECT 'materialization'::text AS invalidation_source,
                    log.lowest_modified_value,
                    log.greatest_modified_value
                   FROM _timescaledb_catalog.continuous_aggs_materialization_invalidation_log log
                  WHERE (log.materialization_id = cagg.mat_hypertable_id)
                UNION ALL
                 SELECT 'hypertable'::text AS invalidation_source,
                    log.lowest_modified_value,
                    log.greatest_modified_value
                   FROM _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log log
                  WHERE (log.hypertable_id = cagg.raw_hypertable_id)) invalidation ON (true))
          WHERE ((cagg.user_view_schema = 'public'::name) AND (cagg.user_view_name = ANY (ARRAY['monitoring_measurement_1min'::name, 'monitoring_measurement_1hour'::name, 'monitoring_measurement_1day'::name, 'monitoring_light_intensity_1hour'::name, 'monitoring_light_intensity_1day'::name, 'monitoring_requested_pid_1hour'::name, 'monitoring_requested_pid_1day'::name, 'monitoring_relay_duration_1day'::name])))
        )
 SELECT native.cagg_name,
    native.materialization_watermark,
    native.invalidation_source,
    native.invalidation_start,
    native.invalidation_end
   FROM native;


ALTER TABLE public.monitoring_history_cagg_watermark OWNER TO cea_user;

--
-- Name: monitoring_index_snapshot_0007; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_index_snapshot_0007 (
    table_name text NOT NULL,
    old_index_name text NOT NULL,
    new_index_name text NOT NULL,
    old_constraint_name text
);


ALTER TABLE public.monitoring_index_snapshot_0007 OWNER TO cea_user;

--
-- Name: monitoring_job_snapshot_0007; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_job_snapshot_0007 (
    job_id integer NOT NULL,
    proc_schema text NOT NULL,
    proc_name text NOT NULL,
    scheduled boolean NOT NULL,
    schedule_interval interval NOT NULL,
    max_runtime interval,
    max_retries integer,
    retry_period interval,
    config jsonb NOT NULL,
    next_start timestamp with time zone,
    target_schema text,
    target_name text,
    target_is_cagg boolean DEFAULT false NOT NULL,
    restore_after_down boolean DEFAULT true NOT NULL,
    check_schema text,
    check_name text,
    fixed_schedule boolean,
    initial_start timestamp with time zone,
    timezone text
);


ALTER TABLE public.monitoring_job_snapshot_0007 OWNER TO cea_user;

--
-- Name: monitoring_job_snapshot_0008; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_job_snapshot_0008 (
    job_id integer NOT NULL,
    proc_schema text NOT NULL,
    proc_name text NOT NULL,
    scheduled boolean NOT NULL,
    target_schema text,
    target_name text,
    target_is_cagg boolean NOT NULL,
    restore_after_backfill boolean DEFAULT true NOT NULL,
    drop_after_backfill boolean DEFAULT false NOT NULL
);


ALTER TABLE public.monitoring_job_snapshot_0008 OWNER TO cea_user;

--
-- Name: monitoring_light_intensity_1day; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_light_intensity_1day AS
 SELECT _materialized_hypertable_621.bucket,
    _materialized_hypertable_621.location,
    _materialized_hypertable_621.cluster,
    _materialized_hypertable_621.device_name,
    _materialized_hypertable_621.sample_count,
    _materialized_hypertable_621.value_sum,
    _materialized_hypertable_621.value_sum_squares,
    _materialized_hypertable_621.min_value,
    _materialized_hypertable_621.max_value,
    _materialized_hypertable_621.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_621;


ALTER TABLE public.monitoring_light_intensity_1day OWNER TO cea_user;

--
-- Name: monitoring_measurement_1day; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_measurement_1day AS
 SELECT _materialized_hypertable_619.bucket,
    _materialized_hypertable_619.sensor_id,
    _materialized_hypertable_619.sample_count,
    _materialized_hypertable_619.value_sum,
    _materialized_hypertable_619.value_sum_squares,
    _materialized_hypertable_619.min_value,
    _materialized_hypertable_619.max_value,
    _materialized_hypertable_619.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_619;


ALTER TABLE public.monitoring_measurement_1day OWNER TO cea_user;

--
-- Name: monitoring_measurement_5min; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_measurement_5min AS
 SELECT _materialized_hypertable_613.bucket,
    _materialized_hypertable_613.sensor_id,
    _materialized_hypertable_613.sample_count,
    _materialized_hypertable_613.value_sum,
    _materialized_hypertable_613.value_sum_squares,
    _materialized_hypertable_613.min_value,
    _materialized_hypertable_613.max_value,
    _materialized_hypertable_613.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_613;


ALTER TABLE public.monitoring_measurement_5min OWNER TO cea_user;

--
-- Name: monitoring_overview_coverage; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_overview_coverage (
    model_name text NOT NULL,
    covered_from timestamp with time zone NOT NULL,
    covered_through timestamp with time zone NOT NULL
);


ALTER TABLE public.monitoring_overview_coverage OWNER TO cea_user;

--
-- Name: monitoring_photoperiod_coverage; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_photoperiod_coverage (
    id bigint NOT NULL,
    "time" timestamp with time zone NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    state text NOT NULL,
    reason text NOT NULL,
    runtime_snapshot_version bigint NOT NULL,
    CONSTRAINT monitoring_photoperiod_coverage_reason_check CHECK ((reason = ANY (ARRAY['initial'::text, 'started'::text, 'stopped'::text, 'control_failure'::text, 'recording_gap'::text, 'recovered'::text, 'unclean_restart'::text]))),
    CONSTRAINT monitoring_photoperiod_coverage_runtime_snapshot_version_check CHECK ((runtime_snapshot_version >= 0)),
    CONSTRAINT monitoring_photoperiod_coverage_state_check CHECK ((state = ANY (ARRAY['available'::text, 'unavailable'::text]))),
    CONSTRAINT monitoring_photoperiod_coverage_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval))))
);


ALTER TABLE public.monitoring_photoperiod_coverage OWNER TO cea_user;

--
-- Name: monitoring_photoperiod_coverage_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.monitoring_photoperiod_coverage ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.monitoring_photoperiod_coverage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: monitoring_refresh_policy_snapshot_0007; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_refresh_policy_snapshot_0007 (
    view_name text NOT NULL,
    job_id integer NOT NULL,
    scheduled boolean NOT NULL,
    schedule_interval interval NOT NULL,
    max_runtime interval,
    max_retries integer,
    retry_period interval,
    config jsonb NOT NULL,
    next_start timestamp with time zone,
    check_schema text,
    check_name text,
    fixed_schedule boolean,
    initial_start timestamp with time zone,
    timezone text
);


ALTER TABLE public.monitoring_refresh_policy_snapshot_0007 OWNER TO cea_user;

--
-- Name: monitoring_relay_duration_1day; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_relay_duration_1day AS
 SELECT _materialized_hypertable_608.bucket,
    _materialized_hypertable_608.location,
    _materialized_hypertable_608.cluster,
    _materialized_hypertable_608.channel,
    _materialized_hypertable_608.device_id,
    _materialized_hypertable_608.device_name,
    _materialized_hypertable_608.device_type,
    _materialized_hypertable_608.on_microseconds,
    _materialized_hypertable_608.off_microseconds,
    _materialized_hypertable_608.last_observed_at,
    _materialized_hypertable_608.source_watermark
   FROM _timescaledb_internal._materialized_hypertable_608;


ALTER TABLE public.monitoring_relay_duration_1day OWNER TO cea_user;

--
-- Name: VIEW monitoring_relay_duration_1day; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON VIEW public.monitoring_relay_duration_1day IS 'Materialized-only daily rollup of the relay duration hourly tier; the daily
readiness claim is verified per day against the hourly derive by the rebuild
checkpoints and lives in monitoring_overview_coverage.';


--
-- Name: monitoring_relay_overview_progress; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_relay_overview_progress (
    id boolean NOT NULL,
    covered_from timestamp with time zone,
    covered_through timestamp with time zone,
    dirty_from timestamp with time zone,
    dirty_through timestamp with time zone,
    claimed_from timestamp with time zone,
    claimed_through timestamp with time zone,
    dirty_during_claim_from timestamp with time zone,
    dirty_generation bigint DEFAULT 0 NOT NULL,
    last_error text,
    CONSTRAINT ck_relay_overview_progress_claim_pair CHECK ((((claimed_from IS NULL) = (claimed_through IS NULL)) AND ((claimed_from IS NULL) OR (claimed_from < claimed_through)))),
    CONSTRAINT ck_relay_overview_progress_covered_pair CHECK ((((covered_from IS NULL) = (covered_through IS NULL)) AND ((covered_from IS NULL) OR (covered_from <= covered_through)))),
    CONSTRAINT ck_relay_overview_progress_dirty_pair CHECK ((((dirty_from IS NULL) = (dirty_through IS NULL)) AND ((dirty_from IS NULL) OR (dirty_from < dirty_through)))),
    CONSTRAINT monitoring_relay_overview_progress_id_check CHECK (id)
);


ALTER TABLE public.monitoring_relay_overview_progress OWNER TO cea_user;

--
-- Name: TABLE monitoring_relay_overview_progress; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.monitoring_relay_overview_progress IS 'One-row dirty/claim/coverage state machine for the relay duration rebuild
and its statement-level dirty triggers; NULL pairs are legal only while the
corresponding concept has no work yet.';


--
-- Name: monitoring_requested_pid_1day; Type: VIEW; Schema: public; Owner: cea_user
--

CREATE VIEW public.monitoring_requested_pid_1day AS
 SELECT _materialized_hypertable_638.bucket,
    _materialized_hypertable_638.location,
    _materialized_hypertable_638.cluster,
    _materialized_hypertable_638.device_name,
    _materialized_hypertable_638.sample_count,
    _materialized_hypertable_638.requested_tw,
    _materialized_hypertable_638.requested_min,
    _materialized_hypertable_638.requested_max,
    _materialized_hypertable_638.last_observed_at
   FROM _timescaledb_internal._materialized_hypertable_638;


ALTER TABLE public.monitoring_requested_pid_1day OWNER TO cea_user;

--
-- Name: monitoring_room_photoperiod; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_room_photoperiod (
    id bigint NOT NULL,
    "time" timestamp with time zone NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    phase text NOT NULL,
    mode_id integer,
    submode_id integer,
    runtime_snapshot_version bigint NOT NULL,
    source text NOT NULL,
    CONSTRAINT monitoring_room_photoperiod_phase_check CHECK ((phase = ANY (ARRAY['SUN'::text, 'MOON'::text, 'UNKNOWN'::text]))),
    CONSTRAINT monitoring_room_photoperiod_runtime_snapshot_version_check CHECK ((runtime_snapshot_version >= 0)),
    CONSTRAINT monitoring_room_photoperiod_source_check CHECK ((btrim(source) <> ''::text)),
    CONSTRAINT monitoring_room_photoperiod_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval))))
);


ALTER TABLE public.monitoring_room_photoperiod OWNER TO cea_user;

--
-- Name: monitoring_room_photoperiod_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.monitoring_room_photoperiod ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.monitoring_room_photoperiod_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: monitoring_toolkit_snapshot_0008; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.monitoring_toolkit_snapshot_0008 (
    singleton boolean DEFAULT true NOT NULL,
    extension_preexisting boolean NOT NULL,
    CONSTRAINT monitoring_toolkit_snapshot_0008_singleton_check CHECK (singleton)
);


ALTER TABLE public.monitoring_toolkit_snapshot_0008 OWNER TO cea_user;

--
-- Name: notes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.notes (
    note_id integer NOT NULL,
    location character varying(100) NOT NULL,
    cluster character varying(100) NOT NULL,
    mode character varying(50) DEFAULT 'default'::character varying NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.notes OWNER TO postgres;

--
-- Name: notes_note_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.notes_note_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.notes_note_id_seq OWNER TO postgres;

--
-- Name: notes_note_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.notes_note_id_seq OWNED BY public.notes.note_id;


--
-- Name: pid_adjustments; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.pid_adjustments (
    id integer NOT NULL,
    location character varying(50) NOT NULL,
    cluster character varying(50) NOT NULL,
    device_type character varying(20) NOT NULL,
    parameter character varying(10) NOT NULL,
    old_value double precision NOT NULL,
    new_value double precision NOT NULL,
    adjustment_type character varying(10) NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.pid_adjustments OWNER TO cea_user;

--
-- Name: pid_adjustments_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.pid_adjustments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.pid_adjustments_id_seq OWNER TO cea_user;

--
-- Name: pid_adjustments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.pid_adjustments_id_seq OWNED BY public.pid_adjustments.id;


--
-- Name: pid_autotune_state; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.pid_autotune_state (
    device_type text NOT NULL,
    is_active boolean DEFAULT false,
    started_at timestamp with time zone,
    cycles_completed integer DEFAULT 0,
    current_amplitude real,
    current_period real,
    current_ku real,
    current_tu real,
    suggested_kp real,
    suggested_ki real,
    suggested_kd real,
    last_change_reason text,
    last_update timestamp with time zone,
    status text DEFAULT 'idle'::text,
    CONSTRAINT pid_autotune_state_status_check CHECK ((status = ANY (ARRAY['idle'::text, 'running'::text, 'calculating'::text, 'complete'::text, 'error'::text])))
);


ALTER TABLE public.pid_autotune_state OWNER TO cea_user;

--
-- Name: pid_parameter_history; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.pid_parameter_history (
    id bigint NOT NULL,
    "time" timestamp with time zone DEFAULT now() NOT NULL,
    device_type text NOT NULL,
    kp real NOT NULL,
    ki real NOT NULL,
    kd real NOT NULL,
    updated_by text,
    source text,
    change_reason text,
    changed_at timestamp with time zone DEFAULT now(),
    location text,
    cluster text,
    binary_hysteresis real,
    CONSTRAINT pid_parameter_history_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval))))
);


ALTER TABLE public.pid_parameter_history OWNER TO cea_user;

--
-- Name: pid_parameter_history_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.pid_parameter_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.pid_parameter_history_id_seq OWNER TO cea_user;

--
-- Name: pid_parameter_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.pid_parameter_history_id_seq OWNED BY public.pid_parameter_history.id;


--
-- Name: pid_parameters; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.pid_parameters (
    device_type text NOT NULL,
    kp real NOT NULL,
    ki real NOT NULL,
    kd real NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text,
    source text,
    control_mode text DEFAULT 'pid'::text,
    hysteresis_high real DEFAULT 1.0,
    hysteresis_low real DEFAULT 0.5,
    location text NOT NULL,
    cluster text NOT NULL,
    binary_hysteresis real,
    CONSTRAINT pid_parameters_control_mode_check CHECK ((control_mode = ANY (ARRAY['auto_pid'::text, 'pid'::text, 'on_off'::text])))
);


ALTER TABLE public.pid_parameters OWNER TO cea_user;

--
-- Name: rack_rack_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.rack_rack_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.rack_rack_id_seq OWNER TO cea_user;

--
-- Name: rack_rack_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.rack_rack_id_seq OWNED BY public.rack.rack_id;


--
-- Name: relay_observation; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.relay_observation (
    observation_id bigint NOT NULL,
    "time" timestamp with time zone NOT NULL,
    session_id uuid NOT NULL,
    channel smallint,
    observed_state boolean,
    device_id integer,
    device_name text,
    device_type text,
    location text,
    cluster text,
    registry_version bigint NOT NULL,
    reason text NOT NULL,
    CONSTRAINT ck_relay_observation_channel CHECK (((channel IS NULL) OR ((channel >= 0) AND (channel <= 15)))),
    CONSTRAINT ck_relay_observation_channel_reason CHECK (((channel IS NULL) = (reason = 'heartbeat'::text))),
    CONSTRAINT ck_relay_observation_heartbeat_payload CHECK (((reason <> 'heartbeat'::text) OR ((observed_state IS NULL) AND (device_id IS NULL) AND (device_name IS NULL) AND (device_type IS NULL) AND (location IS NULL) AND (cluster IS NULL)))),
    CONSTRAINT ck_relay_observation_reason CHECK ((reason = ANY (ARRAY['initial'::text, 'state_changed'::text, 'stale'::text, 'recovered'::text, 'assignment_changed'::text, 'recording_gap'::text, 'heartbeat'::text])))
);


ALTER TABLE public.relay_observation OWNER TO cea_user;

--
-- Name: relay_observation_observation_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

ALTER TABLE public.relay_observation ALTER COLUMN observation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.relay_observation_observation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rollout_events; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.rollout_events (
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    system text NOT NULL,
    phase text NOT NULL,
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    event_type text NOT NULL,
    event_reason text DEFAULT ''::text NOT NULL,
    release_id text DEFAULT ''::text NOT NULL,
    gate_snapshot_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    action_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    operator text DEFAULT 'system'::text NOT NULL
);


ALTER TABLE public.rollout_events OWNER TO cea_user;

--
-- Name: room_active_mode; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.room_active_mode (
    id integer NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    mode_id integer,
    submode_id integer,
    activated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.room_active_mode OWNER TO cea_user;

--
-- Name: room_active_mode_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.room_active_mode_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.room_active_mode_id_seq OWNER TO cea_user;

--
-- Name: room_active_mode_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.room_active_mode_id_seq OWNED BY public.room_active_mode.id;


--
-- Name: room_light_schedule; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.room_light_schedule (
    id integer NOT NULL,
    room_name text NOT NULL,
    lights_on time without time zone NOT NULL,
    lights_off time without time zone NOT NULL,
    effective_from date DEFAULT CURRENT_DATE NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.room_light_schedule OWNER TO postgres;

--
-- Name: room_light_schedule_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.room_light_schedule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.room_light_schedule_id_seq OWNER TO postgres;

--
-- Name: room_light_schedule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.room_light_schedule_id_seq OWNED BY public.room_light_schedule.id;


--
-- Name: room_modes; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.room_modes (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    photoperiod_hours integer,
    is_constant boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT room_modes_photoperiod_hours_check CHECK (((photoperiod_hours >= 0) AND (photoperiod_hours <= 24)))
);


ALTER TABLE public.room_modes OWNER TO cea_user;

--
-- Name: room_modes_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.room_modes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.room_modes_id_seq OWNER TO cea_user;

--
-- Name: room_modes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.room_modes_id_seq OWNED BY public.room_modes.id;


--
-- Name: room_room_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.room_room_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.room_room_id_seq OWNER TO cea_user;

--
-- Name: room_room_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.room_room_id_seq OWNED BY public.room.room_id;


--
-- Name: rules; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.rules (
    id bigint NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true,
    location text NOT NULL,
    cluster text NOT NULL,
    condition_sensor text NOT NULL,
    condition_operator text NOT NULL,
    condition_value real NOT NULL,
    action_device text NOT NULL,
    action_state integer NOT NULL,
    priority integer DEFAULT 0,
    schedule_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.rules OWNER TO cea_user;

--
-- Name: rules_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.rules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.rules_id_seq OWNER TO cea_user;

--
-- Name: rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.rules_id_seq OWNED BY public.rules.id;


--
-- Name: schedules; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.schedules (
    id bigint NOT NULL,
    name text NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    device_name text NOT NULL,
    day_of_week integer,
    start_time time without time zone NOT NULL,
    end_time time without time zone NOT NULL,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    mode text,
    target_intensity real,
    ramp_up_duration integer,
    ramp_down_duration integer,
    pre_day_duration integer,
    pre_night_duration integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schedules_pre_day_duration_check CHECK (((pre_day_duration IS NULL) OR ((pre_day_duration >= 0) AND (pre_day_duration <= 240)))),
    CONSTRAINT schedules_pre_night_duration_check CHECK (((pre_night_duration IS NULL) OR ((pre_night_duration >= 0) AND (pre_night_duration <= 240))))
);


ALTER TABLE public.schedules OWNER TO cea_user;

--
-- Name: schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.schedules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.schedules_id_seq OWNER TO cea_user;

--
-- Name: schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.schedules_id_seq OWNED BY public.schedules.id;


--
-- Name: schema_migration; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.schema_migration (
    version text NOT NULL,
    sha256 text NOT NULL,
    downtime boolean NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


ALTER TABLE public.schema_migration OWNER TO cea_user;

--
-- Name: sensor_address_history; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.sensor_address_history (
    registry_id bigint NOT NULL,
    bus text NOT NULL,
    hardware_address integer NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone
);


ALTER TABLE public.sensor_address_history OWNER TO cea_user;

--
-- Name: sensor_registry; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.sensor_registry (
    registry_id bigint NOT NULL,
    bus text NOT NULL,
    hardware_address integer,
    device_id integer,
    display_name text NOT NULL,
    room_id integer,
    rack_id integer,
    location_in_room text,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    installation_depth_cm numeric(5,1),
    installation_orientation text,
    blusoak_section smallint,
    installed_at timestamp with time zone,
    calibration_profile text,
    calibrated_at timestamp with time zone,
    last_maintained_at timestamp with time zone,
    notes text,
    sensor_uid uuid DEFAULT gen_random_uuid() NOT NULL,
    identity_status text DEFAULT 'confirmed'::text NOT NULL,
    pending_candidate_registry_id bigint,
    pending_since timestamp with time zone,
    CONSTRAINT sensor_registry_blusoak_section_valid CHECK (((blusoak_section IS NULL) OR (blusoak_section = ANY (ARRAY[1, 2])))),
    CONSTRAINT sensor_registry_bus_check CHECK ((bus = ANY (ARRAY['can'::text, 'rs485'::text]))),
    CONSTRAINT sensor_registry_detached_address_check CHECK (((identity_status = 'detached'::text) = (hardware_address IS NULL))),
    CONSTRAINT sensor_registry_display_name_check CHECK ((length(btrim(display_name)) > 0)),
    CONSTRAINT sensor_registry_hardware_address_check CHECK ((hardware_address > 0)),
    CONSTRAINT sensor_registry_identity_status_check CHECK ((identity_status = ANY (ARRAY['confirmed'::text, 'pending'::text, 'detached'::text]))),
    CONSTRAINT sensor_registry_installation_depth_positive CHECK (((installation_depth_cm IS NULL) OR (installation_depth_cm > (0)::numeric))),
    CONSTRAINT sensor_registry_installation_orientation_valid CHECK (((installation_orientation IS NULL) OR (installation_orientation = ANY (ARRAY['vertical'::text, 'horizontal'::text, 'angled'::text])))),
    CONSTRAINT sensor_registry_legal_state CHECK ((((room_id IS NULL) AND (rack_id IS NULL) AND (location_in_room IS NULL)) OR ((bus = 'can'::text) AND (room_id IS NOT NULL) AND (location_in_room IS NOT NULL) AND (rack_id IS NULL)) OR ((bus = 'rs485'::text) AND (rack_id IS NOT NULL) AND (room_id IS NULL) AND (location_in_room IS NULL)))),
    CONSTRAINT sensor_registry_location_in_room_check CHECK ((location_in_room = ANY (ARRAY['front'::text, 'back'::text, 'main'::text]))),
    CONSTRAINT sensor_registry_pending_since_check CHECK (((identity_status = 'pending'::text) = (pending_since IS NOT NULL))),
    CONSTRAINT sensor_registry_soil_metadata_rs485_only CHECK (((bus = 'rs485'::text) OR ((installation_depth_cm IS NULL) AND (installation_orientation IS NULL) AND (blusoak_section IS NULL) AND (installed_at IS NULL) AND (calibration_profile IS NULL) AND (calibrated_at IS NULL) AND (last_maintained_at IS NULL) AND (notes IS NULL))))
);


ALTER TABLE public.sensor_registry OWNER TO cea_user;

--
-- Name: TABLE sensor_registry; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON TABLE public.sensor_registry IS 'Physical sensor units (CAN nodes, RS-485 probes) with commissioned room/bed placement; links to device rows for time-series data';


--
-- Name: sensor_registry_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.sensor_registry_registry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sensor_registry_registry_id_seq OWNER TO cea_user;

--
-- Name: sensor_registry_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.sensor_registry_registry_id_seq OWNED BY public.sensor_registry.registry_id;


--
-- Name: sensor_sensor_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.sensor_sensor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sensor_sensor_id_seq OWNER TO cea_user;

--
-- Name: sensor_sensor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.sensor_sensor_id_seq OWNED BY public.sensor.sensor_id;


--
-- Name: service_setting; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.service_setting (
    service text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_by text NOT NULL,
    CONSTRAINT service_setting_service_check CHECK ((service = ANY (ARRAY['automation'::text, 'soil'::text, 'onewire'::text, 'weather'::text])))
);


ALTER TABLE public.service_setting OWNER TO cea_user;

--
-- Name: setpoint_history; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.setpoint_history (
    id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    mode text,
    heating_setpoint real,
    humidity real,
    co2 real,
    vpd real,
    cooling_setpoint real,
    CONSTRAINT setpoint_history_co2_check CHECK (((co2 IS NULL) OR ((co2 >= (0)::double precision) AND (co2 <= (10000)::double precision)))),
    CONSTRAINT setpoint_history_cooling_setpoint_check CHECK (((cooling_setpoint IS NULL) OR ((cooling_setpoint >= ('-50'::integer)::double precision) AND (cooling_setpoint <= (100)::double precision)))),
    CONSTRAINT setpoint_history_humidity_check CHECK (((humidity IS NULL) OR ((humidity >= (0)::double precision) AND (humidity <= (100)::double precision)))),
    CONSTRAINT setpoint_history_mode_check CHECK (((mode IS NULL) OR (mode = ANY (ARRAY['DAY'::text, 'NIGHT'::text, 'PRE_DAY'::text, 'PRE_NIGHT'::text, 'TRANSITION'::text])))),
    CONSTRAINT setpoint_history_temperature_check CHECK (((heating_setpoint IS NULL) OR ((heating_setpoint >= ('-50'::integer)::double precision) AND (heating_setpoint <= (100)::double precision)))),
    CONSTRAINT setpoint_history_timestamp_check CHECK ((("timestamp" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("timestamp" <= (now() + '1 day'::interval)))),
    CONSTRAINT setpoint_history_vpd_check CHECK (((vpd IS NULL) OR ((vpd >= (0)::double precision) AND (vpd <= (10)::double precision))))
);


ALTER TABLE public.setpoint_history OWNER TO cea_user;

--
-- Name: setpoint_history_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.setpoint_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.setpoint_history_id_seq OWNER TO cea_user;

--
-- Name: setpoint_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.setpoint_history_id_seq OWNED BY public.setpoint_history.id;


--
-- Name: setpoints; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.setpoints (
    id bigint NOT NULL,
    location text NOT NULL,
    cluster text NOT NULL,
    heating_setpoint real,
    humidity real,
    co2 real,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    mode text,
    vpd real,
    cooling_setpoint real,
    ramp_in_duration integer,
    leaf_delta_day real DEFAULT '-2.0'::numeric,
    leaf_delta_night real DEFAULT '-1.0'::numeric,
    CONSTRAINT setpoints_cooling_setpoint_check CHECK (((cooling_setpoint IS NULL) OR ((cooling_setpoint >= ('-50'::integer)::double precision) AND (cooling_setpoint <= (100)::double precision)))),
    CONSTRAINT setpoints_mode_check CHECK (((mode IS NULL) OR (mode = ANY (ARRAY['DAY'::text, 'NIGHT'::text, 'TRANSITION'::text, 'PRE_DAY'::text, 'PRE_NIGHT'::text])))),
    CONSTRAINT setpoints_ramp_in_duration_check CHECK (((ramp_in_duration IS NULL) OR ((ramp_in_duration >= 0) AND (ramp_in_duration <= 240))))
);


ALTER TABLE public.setpoints OWNER TO cea_user;

--
-- Name: COLUMN setpoints.leaf_delta_day; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.setpoints.leaf_delta_day IS 'Leaf-air temp delta during day (°C), typically -1 to -3';


--
-- Name: COLUMN setpoints.leaf_delta_night; Type: COMMENT; Schema: public; Owner: cea_user
--

COMMENT ON COLUMN public.setpoints.leaf_delta_night IS 'Leaf-air temp delta during night (°C), typically -0.5 to -2';


--
-- Name: setpoints_id_seq; Type: SEQUENCE; Schema: public; Owner: cea_user
--

CREATE SEQUENCE public.setpoints_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.setpoints_id_seq OWNER TO cea_user;

--
-- Name: setpoints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cea_user
--

ALTER SEQUENCE public.setpoints_id_seq OWNED BY public.setpoints.id;


--
-- Name: soil_bed_profile; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.soil_bed_profile (
    rack_id integer NOT NULL,
    blusoak_total_length_ft numeric(6,2) NOT NULL,
    section_count smallint NOT NULL,
    section_length_ft numeric(6,2) NOT NULL,
    standard_carrots_per_section smallint NOT NULL,
    maxi_carrots_per_section smallint NOT NULL,
    substrate text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT soil_bed_profile_blusoak_total_length_ft_check CHECK ((blusoak_total_length_ft > (0)::numeric)),
    CONSTRAINT soil_bed_profile_maxi_carrots_per_section_check CHECK ((maxi_carrots_per_section > 0)),
    CONSTRAINT soil_bed_profile_section_count_check CHECK ((section_count > 0)),
    CONSTRAINT soil_bed_profile_section_length_ft_check CHECK ((section_length_ft > (0)::numeric)),
    CONSTRAINT soil_bed_profile_standard_carrots_per_section_check CHECK ((standard_carrots_per_section > 0)),
    CONSTRAINT soil_bed_profile_substrate_check CHECK ((length(btrim(substrate)) > 0))
);


ALTER TABLE public.soil_bed_profile OWNER TO cea_user;

--
-- Name: soil_phase_target; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.soil_phase_target (
    event_type text NOT NULL,
    lower_mbar numeric(7,2) NOT NULL,
    upper_mbar numeric(7,2) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT soil_phase_target_check CHECK ((upper_mbar > lower_mbar)),
    CONSTRAINT soil_phase_target_event_type_check CHECK ((event_type = ANY (ARRAY['bed_veg'::text, 'flower_bulk'::text, 'flower_stretch'::text, 'flower_ripen'::text]))),
    CONSTRAINT soil_phase_target_lower_mbar_check CHECK ((lower_mbar > (0)::numeric))
);


ALTER TABLE public.soil_phase_target OWNER TO cea_user;

--
-- Name: water_tank_monitor_config; Type: TABLE; Schema: public; Owner: cea_user
--

CREATE TABLE public.water_tank_monitor_config (
    config_id smallint NOT NULL,
    sensor_id integer,
    full_distance_mm numeric(8,2),
    empty_distance_mm numeric(8,2),
    capacity_liters numeric(10,2),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT water_tank_monitor_config_config_id_check CHECK ((config_id = 1)),
    CONSTRAINT water_tank_monitor_config_shape CHECK ((((sensor_id IS NULL) AND (full_distance_mm IS NULL) AND (empty_distance_mm IS NULL) AND (capacity_liters IS NULL)) OR ((sensor_id IS NOT NULL) AND (full_distance_mm IS NOT NULL) AND (empty_distance_mm IS NOT NULL) AND (full_distance_mm >= (0)::numeric) AND (empty_distance_mm > full_distance_mm) AND ((capacity_liters IS NULL) OR (capacity_liters > (0)::numeric)))))
);


ALTER TABLE public.water_tank_monitor_config OWNER TO cea_user;

--
-- Name: automation_state id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.automation_state ALTER COLUMN id SET DEFAULT nextval('public.automation_state_id_seq'::regclass);


--
-- Name: calendar_event id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_event ALTER COLUMN id SET DEFAULT nextval('public.calendar_event_id_seq'::regclass);


--
-- Name: calendar_mode_application id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_mode_application ALTER COLUMN id SET DEFAULT nextval('public.calendar_mode_application_id_seq'::regclass);


--
-- Name: calendar_sync_connection id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_sync_connection ALTER COLUMN id SET DEFAULT nextval('public.calendar_sync_connection_id_seq'::regclass);


--
-- Name: climate_periods id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.climate_periods ALTER COLUMN id SET DEFAULT nextval('public.climate_periods_id_seq'::regclass);


--
-- Name: config_versions version_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.config_versions ALTER COLUMN version_id SET DEFAULT nextval('public.config_versions_version_id_seq'::regclass);


--
-- Name: control_history id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.control_history ALTER COLUMN id SET DEFAULT nextval('public.control_history_id_seq'::regclass);


--
-- Name: crop_batch batch_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.crop_batch ALTER COLUMN batch_id SET DEFAULT nextval('public.crop_batch_batch_id_seq'::regclass);


--
-- Name: device device_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device ALTER COLUMN device_id SET DEFAULT nextval('public.device_device_id_seq'::regclass);


--
-- Name: device_mappings id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_mappings ALTER COLUMN id SET DEFAULT nextval('public.device_mappings_id_seq'::regclass);


--
-- Name: device_states id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_states ALTER COLUMN id SET DEFAULT nextval('public.device_states_id_seq'::regclass);


--
-- Name: effective_setpoints monitoring_ingest_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.effective_setpoints ALTER COLUMN monitoring_ingest_id SET DEFAULT nextval('public.monitoring_effective_setpoints_ingest_id_seq'::regclass);


--
-- Name: flower_submodes id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.flower_submodes ALTER COLUMN id SET DEFAULT nextval('public.flower_submodes_id_seq'::regclass);


--
-- Name: light_presets id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.light_presets ALTER COLUMN id SET DEFAULT nextval('public.light_presets_id_seq'::regclass);


--
-- Name: light_state_history id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_state_history ALTER COLUMN id SET DEFAULT nextval('public.light_state_history_id_seq'::regclass);


--
-- Name: mode_notes id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_notes ALTER COLUMN id SET DEFAULT nextval('public.mode_notes_id_seq'::regclass);


--
-- Name: mode_parameters id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_parameters ALTER COLUMN id SET DEFAULT nextval('public.mode_parameters_id_seq'::regclass);


--
-- Name: mode_transition_history id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_transition_history ALTER COLUMN id SET DEFAULT nextval('public.mode_transition_history_id_seq'::regclass);


--
-- Name: notes note_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notes ALTER COLUMN note_id SET DEFAULT nextval('public.notes_note_id_seq'::regclass);


--
-- Name: pid_adjustments id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_adjustments ALTER COLUMN id SET DEFAULT nextval('public.pid_adjustments_id_seq'::regclass);


--
-- Name: pid_parameter_history id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_parameter_history ALTER COLUMN id SET DEFAULT nextval('public.pid_parameter_history_id_seq'::regclass);


--
-- Name: rack rack_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rack ALTER COLUMN rack_id SET DEFAULT nextval('public.rack_rack_id_seq'::regclass);


--
-- Name: room room_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room ALTER COLUMN room_id SET DEFAULT nextval('public.room_room_id_seq'::regclass);


--
-- Name: room_active_mode id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_active_mode ALTER COLUMN id SET DEFAULT nextval('public.room_active_mode_id_seq'::regclass);


--
-- Name: room_light_schedule id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_light_schedule ALTER COLUMN id SET DEFAULT nextval('public.room_light_schedule_id_seq'::regclass);


--
-- Name: room_modes id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_modes ALTER COLUMN id SET DEFAULT nextval('public.room_modes_id_seq'::regclass);


--
-- Name: rules id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rules ALTER COLUMN id SET DEFAULT nextval('public.rules_id_seq'::regclass);


--
-- Name: schedules id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.schedules ALTER COLUMN id SET DEFAULT nextval('public.schedules_id_seq'::regclass);


--
-- Name: sensor sensor_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor ALTER COLUMN sensor_id SET DEFAULT nextval('public.sensor_sensor_id_seq'::regclass);


--
-- Name: sensor_registry registry_id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry ALTER COLUMN registry_id SET DEFAULT nextval('public.sensor_registry_registry_id_seq'::regclass);


--
-- Name: setpoint_history id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.setpoint_history ALTER COLUMN id SET DEFAULT nextval('public.setpoint_history_id_seq'::regclass);


--
-- Name: setpoints id; Type: DEFAULT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.setpoints ALTER COLUMN id SET DEFAULT nextval('public.setpoints_id_seq'::regclass);


--
-- Data for Name: hypertable; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.hypertable (id, schema_name, table_name, associated_schema_name, associated_table_prefix, num_dimensions, chunk_sizing_func_schema, chunk_sizing_func_name, chunk_target_size, compression_state, compressed_hypertable_id, status) FROM stdin;
10	_timescaledb_internal	_compressed_hypertable_10	_timescaledb_internal	_hyper_10	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
9	public	measurement	_timescaledb_internal	_hyper_9	1	_timescaledb_functions	calculate_chunk_interval	0	1	10	0
559	_timescaledb_internal	_compressed_hypertable_559	_timescaledb_internal	_hyper_559	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
423	public	effective_setpoints	_timescaledb_internal	_hyper_423	1	_timescaledb_functions	calculate_chunk_interval	0	1	559	0
607	public	monitoring_relay_duration_1hour	_timescaledb_internal	_hyper_607	1	_timescaledb_functions	calculate_chunk_interval	0	0	\N	0
609	_timescaledb_internal	_compressed_hypertable_609	_timescaledb_internal	_hyper_609	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
565	public	automation_state	_timescaledb_internal	_hyper_565	1	_timescaledb_functions	calculate_chunk_interval	0	1	609	0
610	_timescaledb_internal	_compressed_hypertable_610	_timescaledb_internal	_hyper_610	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
590	_timescaledb_internal	_materialized_hypertable_590	_timescaledb_internal	_hyper_590	1	_timescaledb_functions	calculate_chunk_interval	0	1	624	0
600	public	relay_observation	_timescaledb_internal	_hyper_600	1	_timescaledb_functions	calculate_chunk_interval	0	1	610	0
612	_timescaledb_internal	_compressed_hypertable_612	_timescaledb_internal	_hyper_612	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
611	public	control_history	_timescaledb_internal	_hyper_611	1	_timescaledb_functions	calculate_chunk_interval	0	1	612	0
624	_timescaledb_internal	_compressed_hypertable_624	_timescaledb_internal	_hyper_624	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
625	_timescaledb_internal	_compressed_hypertable_625	_timescaledb_internal	_hyper_625	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
613	_timescaledb_internal	_materialized_hypertable_613	_timescaledb_internal	_hyper_613	1	_timescaledb_functions	calculate_chunk_interval	0	1	625	0
626	_timescaledb_internal	_compressed_hypertable_626	_timescaledb_internal	_hyper_626	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
618	_timescaledb_internal	_materialized_hypertable_618	_timescaledb_internal	_hyper_618	1	_timescaledb_functions	calculate_chunk_interval	0	1	626	0
627	_timescaledb_internal	_compressed_hypertable_627	_timescaledb_internal	_hyper_627	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
619	_timescaledb_internal	_materialized_hypertable_619	_timescaledb_internal	_hyper_619	1	_timescaledb_functions	calculate_chunk_interval	0	1	627	0
628	_timescaledb_internal	_compressed_hypertable_628	_timescaledb_internal	_hyper_628	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
614	_timescaledb_internal	_materialized_hypertable_614	_timescaledb_internal	_hyper_614	1	_timescaledb_functions	calculate_chunk_interval	0	1	628	0
629	_timescaledb_internal	_compressed_hypertable_629	_timescaledb_internal	_hyper_629	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
615	_timescaledb_internal	_materialized_hypertable_615	_timescaledb_internal	_hyper_615	1	_timescaledb_functions	calculate_chunk_interval	0	1	629	0
630	_timescaledb_internal	_compressed_hypertable_630	_timescaledb_internal	_hyper_630	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
616	_timescaledb_internal	_materialized_hypertable_616	_timescaledb_internal	_hyper_616	1	_timescaledb_functions	calculate_chunk_interval	0	1	630	0
631	_timescaledb_internal	_compressed_hypertable_631	_timescaledb_internal	_hyper_631	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
617	_timescaledb_internal	_materialized_hypertable_617	_timescaledb_internal	_hyper_617	1	_timescaledb_functions	calculate_chunk_interval	0	1	631	0
632	_timescaledb_internal	_compressed_hypertable_632	_timescaledb_internal	_hyper_632	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
620	_timescaledb_internal	_materialized_hypertable_620	_timescaledb_internal	_hyper_620	1	_timescaledb_functions	calculate_chunk_interval	0	1	632	0
633	_timescaledb_internal	_compressed_hypertable_633	_timescaledb_internal	_hyper_633	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
621	_timescaledb_internal	_materialized_hypertable_621	_timescaledb_internal	_hyper_621	1	_timescaledb_functions	calculate_chunk_interval	0	1	633	0
636	_timescaledb_internal	_compressed_hypertable_636	_timescaledb_internal	_hyper_636	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
608	_timescaledb_internal	_materialized_hypertable_608	_timescaledb_internal	_hyper_608	1	_timescaledb_functions	calculate_chunk_interval	0	1	636	0
639	_timescaledb_internal	_compressed_hypertable_639	_timescaledb_internal	_hyper_639	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
637	_timescaledb_internal	_materialized_hypertable_637	_timescaledb_internal	_hyper_637	1	_timescaledb_functions	calculate_chunk_interval	0	1	639	0
640	_timescaledb_internal	_compressed_hypertable_640	_timescaledb_internal	_hyper_640	0	_timescaledb_functions	calculate_chunk_interval	0	2	\N	0
638	_timescaledb_internal	_materialized_hypertable_638	_timescaledb_internal	_hyper_638	1	_timescaledb_functions	calculate_chunk_interval	0	1	640	0
\.


--
-- Data for Name: bgw_job; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.bgw_job (id, application_name, schedule_interval, max_runtime, max_retries, retry_period, proc_schema, proc_name, owner, scheduled, fixed_schedule, initial_start, hypertable_id, config, check_schema, check_name, timezone) FROM stdin;
1008	Refresh Continuous Aggregate Policy [1008]	00:01:00	00:00:00	-1	00:01:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	590	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 590}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1049	Columnstore Policy [1049]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	616	{"hypertable_id": 616, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1022	overview_incremental_requested_pid [1022]	00:05:00	00:01:00	0	00:05:00	public	monitoring_refresh_overview_incremental	cea_user	t	t	2026-10-12 14:44:47.669816-04	\N	{"family": "requested_pid", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	\N	\N	\N
1024	monitoring_rebuild_relay_overview [1024]	00:05:00	00:01:00	0	00:05:00	public	monitoring_rebuild_relay_overview	cea_user	t	t	2026-10-12 14:44:47.765936-04	\N	{"enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	\N	\N	\N
1057	Refresh Continuous Aggregate Policy [1057]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	637	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 637, "refresh_newest_first": false, "max_batches_per_execution": 0}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1058	Refresh Continuous Aggregate Policy [1058]	01:00:00	00:01:00	0	01:00:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	638	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 638, "refresh_newest_first": false, "max_batches_per_execution": 1}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1059	Columnstore Policy [1059]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	637	{"hypertable_id": 637, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1060	Columnstore Policy [1060]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	638	{"hypertable_id": 638, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1023	Refresh Continuous Aggregate Policy [1023]	01:00:00	00:01:00	0	01:00:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	f	f	\N	608	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 608, "refresh_newest_first": false, "max_batches_per_execution": 1}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1026	Columnstore Policy [1026]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	565	{"hypertable_id": 565, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1020	overview_incremental_measurement [1020]	00:05:00	00:01:00	0	00:05:00	public	monitoring_refresh_overview_incremental	cea_user	t	t	2026-10-12 14:44:47.666536-04	\N	{"family": "measurement", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	\N	\N	\N
1021	overview_incremental_light_intensity [1021]	00:05:00	00:01:00	0	00:05:00	public	monitoring_refresh_overview_incremental	cea_user	t	t	2026-10-12 14:44:47.667988-04	\N	{"family": "light_intensity", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	\N	\N	\N
1030	Columnstore Policy [1030]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	9	{"hypertable_id": 9, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1032	Refresh Continuous Aggregate Policy [1032]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	618	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 618, "refresh_newest_first": false, "max_batches_per_execution": 0}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1033	Refresh Continuous Aggregate Policy [1033]	01:00:00	00:01:00	0	01:00:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	619	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 619, "refresh_newest_first": false, "max_batches_per_execution": 1}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1034	Refresh Continuous Aggregate Policy [1034]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	620	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 620, "refresh_newest_first": false, "max_batches_per_execution": 0}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1028	Columnstore Policy [1028]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	600	{"hypertable_id": 600, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1029	Columnstore Policy [1029]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	611	{"hypertable_id": 611, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1031	Columnstore Policy [1031]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	423	{"hypertable_id": 423, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1035	Refresh Continuous Aggregate Policy [1035]	01:00:00	00:01:00	0	01:00:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	621	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 621, "refresh_newest_first": false, "max_batches_per_execution": 1}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1038	Refresh Continuous Aggregate Policy [1038]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	613	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 613}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1039	Refresh Continuous Aggregate Policy [1039]	00:01:00	00:01:00	0	00:01:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	614	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 614}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1040	Refresh Continuous Aggregate Policy [1040]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	615	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 615}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1041	Refresh Continuous Aggregate Policy [1041]	00:01:00	00:01:00	0	00:01:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	616	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 616}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1042	Refresh Continuous Aggregate Policy [1042]	00:05:00	00:01:00	0	00:05:00	_timescaledb_functions	policy_refresh_continuous_aggregate	cea_user	t	f	\N	617	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 617}	_timescaledb_functions	policy_refresh_continuous_aggregate_check	\N
1043	Columnstore Policy [1043]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	590	{"hypertable_id": 590, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1044	Columnstore Policy [1044]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	613	{"hypertable_id": 613, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1045	Columnstore Policy [1045]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	618	{"hypertable_id": 618, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1046	Columnstore Policy [1046]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	619	{"hypertable_id": 619, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1047	Columnstore Policy [1047]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	614	{"hypertable_id": 614, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1048	Columnstore Policy [1048]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	615	{"hypertable_id": 615, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1050	Columnstore Policy [1050]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	617	{"hypertable_id": 617, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1051	Columnstore Policy [1051]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	620	{"hypertable_id": 620, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1052	Columnstore Policy [1052]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	621	{"hypertable_id": 621, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1055	Columnstore Policy [1055]	12:00:00	00:00:00	-1	01:00:00	_timescaledb_functions	policy_compression	cea_user	t	f	\N	608	{"hypertable_id": 608, "compress_after": "3 mons"}	_timescaledb_functions	policy_compression_check	\N
1056	monitoring_backfill_aggregates_0007 [1056]	00:01:00	00:10:00	-1	00:05:00	public	monitoring_backfill_aggregates	cea_user	f	t	2026-10-20 12:20:11.743704-04	\N	{"done": true, "start": "2026-02-28T19:00:00-05:00", "views": ["monitoring_measurement_5min", "monitoring_measurement_1hour", "monitoring_measurement_1day", "monitoring_effective_setpoints_1min", "monitoring_effective_setpoints_5min", "monitoring_automation_state_1min", "monitoring_automation_state_5min", "monitoring_light_intensity_1hour", "monitoring_light_intensity_1day", "monitoring_requested_pid_1hour", "monitoring_requested_pid_1day"], "cursor": "2026-10-20T20:00:00-04:00"}	\N	\N	\N
\.


--
-- Data for Name: chunk; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.chunk (id, hypertable_id, schema_name, table_name, compressed_chunk_id, status, osm_chunk, creation_time) FROM stdin;
\.


--
-- Data for Name: chunk_column_stats; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.chunk_column_stats (id, hypertable_id, chunk_id, column_name, range_start, range_end, valid) FROM stdin;
\.


--
-- Data for Name: compression_chunk_size; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.compression_chunk_size (chunk_id, compressed_chunk_id, uncompressed_heap_size, uncompressed_toast_size, uncompressed_index_size, compressed_heap_size, compressed_toast_size, compressed_index_size, numrows_pre_compression, numrows_post_compression, numrows_frozen_immediately) FROM stdin;
\.


--
-- Data for Name: compression_settings; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.compression_settings (relid, compress_relid, segmentby, orderby, orderby_desc, orderby_nullsfirst, index) FROM stdin;
public.measurement	\N	{sensor_id}	\N	\N	\N	\N
public.automation_state	\N	{location,cluster,device_name}	{time}	{t}	{t}	[{"type": "minmax", "column": "time", "source": "orderby"}, {"type": "firstlast", "column": "time", "source": "orderby"}]
public.effective_setpoints	\N	{location,cluster}	\N	\N	\N	\N
public.control_history	\N	{location,cluster,device_name,channel}	{time}	{t}	{t}	[{"type": "minmax", "column": "time", "source": "orderby"}, {"type": "firstlast", "column": "time", "source": "orderby"}]
public.relay_observation	\N	{channel}	{time}	{t}	{t}	[{"type": "minmax", "column": "time", "source": "orderby"}, {"type": "firstlast", "column": "time", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_615	\N	\N	{bucket,location,cluster,device_name,mode}	{f,f,f,f,f}	{f,f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}, {"type": "minmax", "column": "mode", "source": "orderby"}, {"type": "firstlast", "column": "mode", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_616	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_617	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_620	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_621	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_638	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_637	\N	\N	{bucket,location,cluster,device_name}	{f,f,f,f}	{f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_590	\N	\N	{bucket,sensor_id}	{f,f}	{f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "sensor_id", "source": "orderby"}, {"type": "firstlast", "column": "sensor_id", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_613	\N	\N	{bucket,sensor_id}	{f,f}	{f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "sensor_id", "source": "orderby"}, {"type": "firstlast", "column": "sensor_id", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_618	\N	\N	{bucket,sensor_id}	{f,f}	{f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "sensor_id", "source": "orderby"}, {"type": "firstlast", "column": "sensor_id", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_619	\N	\N	{bucket,sensor_id}	{f,f}	{f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "sensor_id", "source": "orderby"}, {"type": "firstlast", "column": "sensor_id", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_614	\N	\N	{bucket,location,cluster,device_name,mode}	{f,f,f,f,f}	{f,f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}, {"type": "minmax", "column": "mode", "source": "orderby"}, {"type": "firstlast", "column": "mode", "source": "orderby"}]
_timescaledb_internal._materialized_hypertable_608	\N	\N	{bucket,location,cluster,channel,device_id,device_name,device_type}	{f,f,f,f,f,f,f}	{f,f,f,f,f,f,f}	[{"type": "minmax", "column": "bucket", "source": "orderby"}, {"type": "firstlast", "column": "bucket", "source": "orderby"}, {"type": "minmax", "column": "location", "source": "orderby"}, {"type": "firstlast", "column": "location", "source": "orderby"}, {"type": "minmax", "column": "cluster", "source": "orderby"}, {"type": "firstlast", "column": "cluster", "source": "orderby"}, {"type": "minmax", "column": "channel", "source": "orderby"}, {"type": "firstlast", "column": "channel", "source": "orderby"}, {"type": "minmax", "column": "device_id", "source": "orderby"}, {"type": "firstlast", "column": "device_id", "source": "orderby"}, {"type": "minmax", "column": "device_name", "source": "orderby"}, {"type": "firstlast", "column": "device_name", "source": "orderby"}, {"type": "minmax", "column": "device_type", "source": "orderby"}, {"type": "firstlast", "column": "device_type", "source": "orderby"}]
\.


--
-- Data for Name: continuous_agg; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_agg (mat_hypertable_id, raw_hypertable_id, parent_mat_hypertable_id, user_view_schema, user_view_name, partial_view_schema, partial_view_name, direct_view_schema, direct_view_name, materialized_only, schema_change_timestamp) FROM stdin;
590	9	\N	public	monitoring_measurement_1min	_timescaledb_internal	_partial_view_590	_timescaledb_internal	_direct_view_590	t	\N
608	607	\N	public	monitoring_relay_duration_1day	_timescaledb_internal	_partial_view_608	_timescaledb_internal	_direct_view_608	t	\N
613	590	590	public	monitoring_measurement_5min	_timescaledb_internal	_partial_view_613	_timescaledb_internal	_direct_view_613	t	\N
614	423	\N	public	monitoring_effective_setpoints_1min	_timescaledb_internal	_partial_view_614	_timescaledb_internal	_direct_view_614	t	\N
615	423	\N	public	monitoring_effective_setpoints_5min	_timescaledb_internal	_partial_view_615	_timescaledb_internal	_direct_view_615	t	\N
616	565	\N	public	monitoring_automation_state_1min	_timescaledb_internal	_partial_view_616	_timescaledb_internal	_direct_view_616	t	\N
617	565	\N	public	monitoring_automation_state_5min	_timescaledb_internal	_partial_view_617	_timescaledb_internal	_direct_view_617	t	\N
618	590	590	public	monitoring_measurement_1hour	_timescaledb_internal	_partial_view_618	_timescaledb_internal	_direct_view_618	t	\N
619	618	618	public	monitoring_measurement_1day	_timescaledb_internal	_partial_view_619	_timescaledb_internal	_direct_view_619	t	\N
620	423	\N	public	monitoring_light_intensity_1hour	_timescaledb_internal	_partial_view_620	_timescaledb_internal	_direct_view_620	t	\N
621	620	620	public	monitoring_light_intensity_1day	_timescaledb_internal	_partial_view_621	_timescaledb_internal	_direct_view_621	t	\N
637	565	\N	public	monitoring_requested_pid_1hour	_timescaledb_internal	_partial_view_637	_timescaledb_internal	_direct_view_637	t	\N
638	637	637	public	monitoring_requested_pid_1day	_timescaledb_internal	_partial_view_638	_timescaledb_internal	_direct_view_638	t	\N
\.


--
-- Data for Name: continuous_aggs_bucket_function; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_bucket_function (mat_hypertable_id, bucket_func, bucket_width, bucket_origin, bucket_offset, bucket_timezone, bucket_fixed_width) FROM stdin;
590	public.time_bucket(interval,timestamp with time zone)	00:01:00	\N	\N	\N	t
608	public.time_bucket(interval,timestamp with time zone)	1 day	\N	\N	\N	t
613	public.time_bucket(interval,timestamp with time zone)	00:05:00	\N	\N	\N	t
614	public.time_bucket(interval,timestamp with time zone)	00:01:00	\N	\N	\N	t
615	public.time_bucket(interval,timestamp with time zone)	00:05:00	\N	\N	\N	t
616	public.time_bucket(interval,timestamp with time zone)	00:01:00	\N	\N	\N	t
617	public.time_bucket(interval,timestamp with time zone)	00:05:00	\N	\N	\N	t
618	public.time_bucket(interval,timestamp with time zone)	01:00:00	\N	\N	\N	t
619	public.time_bucket(interval,timestamp with time zone)	1 day	\N	\N	\N	t
620	public.time_bucket(interval,timestamp with time zone)	01:00:00	\N	\N	\N	t
621	public.time_bucket(interval,timestamp with time zone)	1 day	\N	\N	\N	t
637	public.time_bucket(interval,timestamp with time zone)	01:00:00	\N	\N	\N	t
638	public.time_bucket(interval,timestamp with time zone)	1 day	\N	\N	\N	t
\.


--
-- Data for Name: continuous_aggs_hypertable_invalidation_log; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log (hypertable_id, lowest_modified_value, greatest_modified_value) FROM stdin;
565	-9223372036854775808	9223372036854775807
423	-9223372036854775808	9223372036854775807
9	-9223372036854775808	9223372036854775807
607	-9223372036854775808	9223372036854775807
590	-9223372036854775808	9223372036854775807
618	-9223372036854775808	9223372036854775807
620	-9223372036854775808	9223372036854775807
637	-9223372036854775808	9223372036854775807
\.


--
-- Data for Name: continuous_aggs_invalidation_threshold; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_invalidation_threshold (hypertable_id, watermark) FROM stdin;
607	-210866803200000000
637	1792540800000000
618	1792540800000000
620	1792540800000000
9	1792606200000000
423	1792606200000000
590	1792605900000000
565	1792606200000000
\.


--
-- Data for Name: continuous_aggs_jobs_refresh_ranges; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_jobs_refresh_ranges (materialization_id, start_range, end_range, pid, job_id, created_at) FROM stdin;
\.


--
-- Data for Name: continuous_aggs_materialization_invalidation_log; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_materialization_invalidation_log (materialization_id, lowest_modified_value, greatest_modified_value) FROM stdin;
619	1792540800000000	9223372036854775807
621	1792540800000000	9223372036854775807
638	1792540800000000	9223372036854775807
613	-9223372036854775808	1772323199999999
614	-9223372036854775808	1772323199999999
615	-9223372036854775808	1772323199999999
616	-9223372036854775808	1772323199999999
617	-9223372036854775808	1772323199999999
618	-9223372036854775808	-210866803200000001
619	-9223372036854775808	-210866803200000001
620	-9223372036854775808	-210866803200000001
621	-9223372036854775808	-210866803200000001
618	1792602000000000	1792605599999999
590	1772668800000000	1772841659999999
637	-9223372036854775808	-210866803200000001
638	-9223372036854775808	-210866803200000001
590	1772841600000000	1774742459999999
618	1792602000000000	1792605599999999
590	1774742400000000	1777766459999999
590	1777766400000000	1779494459999999
590	1779494400000000	1779753659999999
590	32472144000000000	32472230459999999
608	-9223372036854775808	9223372036854775807
590	-9223372036854775808	1785455999999999
590	1779753600000000	1781136059999999
590	1781136000000000	1782691259999999
590	1782691200000000	1784592059999999
590	1792606200000000	9223372036854775807
637	1792602000000000	9223372036854775807
614	1792606200000000	9223372036854775807
615	1792605900000000	9223372036854775807
613	1792605900000000	9223372036854775807
616	1792606200000000	9223372036854775807
617	1792605900000000	9223372036854775807
618	1792602000000000	9223372036854775807
620	1792602000000000	9223372036854775807
590	-9223372036854775808	9223372036854775807
608	-9223372036854775808	9223372036854775807
613	-9223372036854775808	9223372036854775807
614	-9223372036854775808	9223372036854775807
615	-9223372036854775808	9223372036854775807
616	-9223372036854775808	9223372036854775807
617	-9223372036854775808	9223372036854775807
618	-9223372036854775808	9223372036854775807
619	-9223372036854775808	9223372036854775807
620	-9223372036854775808	9223372036854775807
621	-9223372036854775808	9223372036854775807
637	-9223372036854775808	9223372036854775807
638	-9223372036854775808	9223372036854775807
\.


--
-- Data for Name: continuous_aggs_materialization_ranges; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_materialization_ranges (materialization_id, lowest_modified_value, greatest_modified_value) FROM stdin;
\.


--
-- Data for Name: continuous_aggs_watermark; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.continuous_aggs_watermark (mat_hypertable_id, watermark) FROM stdin;
590	-210866803200000000
608	-210866803200000000
613	-210866803200000000
614	-210866803200000000
615	-210866803200000000
616	-210866803200000000
617	-210866803200000000
618	-210866803200000000
619	-210866803200000000
620	-210866803200000000
621	-210866803200000000
637	-210866803200000000
638	-210866803200000000
\.


--
-- Data for Name: dimension; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.dimension (id, hypertable_id, column_name, column_type, aligned, num_slices, partitioning_func_schema, partitioning_func, interval_length, compress_interval_length, integer_now_func_schema, integer_now_func) FROM stdin;
9	9	time	timestamp with time zone	t	\N	\N	\N	86400000000	\N	\N	\N
563	565	time	timestamp with time zone	t	\N	\N	\N	86400000000	\N	\N	\N
422	423	time	timestamp with time zone	t	\N	\N	\N	604800000000	\N	\N	\N
588	590	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
605	607	bucket	timestamp with time zone	t	\N	\N	\N	86400000000	\N	\N	\N
606	608	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
607	611	time	timestamp with time zone	t	\N	\N	\N	604800000000	\N	\N	\N
598	600	time	timestamp with time zone	t	\N	\N	\N	604800000000	\N	\N	\N
608	613	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
609	614	bucket	timestamp with time zone	t	\N	\N	\N	6048000000000	\N	\N	\N
610	615	bucket	timestamp with time zone	t	\N	\N	\N	6048000000000	\N	\N	\N
611	616	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
612	617	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
613	618	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
614	619	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
615	620	bucket	timestamp with time zone	t	\N	\N	\N	6048000000000	\N	\N	\N
616	621	bucket	timestamp with time zone	t	\N	\N	\N	6048000000000	\N	\N	\N
619	637	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
620	638	bucket	timestamp with time zone	t	\N	\N	\N	864000000000	\N	\N	\N
\.


--
-- Data for Name: dimension_slice; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.dimension_slice (id, chunk_id, dimension_id, range_start, range_end) FROM stdin;
\.


--
-- Data for Name: metadata; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.metadata (key, value, include_in_telemetry) FROM stdin;
install_timestamp	2025-12-11 11:19:29.247082-05	t
exported_uuid	6651c690-d290-4168-910b-68f19efe87e9	t
timescaledb_version	2.28.3	f
\.


--
-- Data for Name: tablespace; Type: TABLE DATA; Schema: _timescaledb_catalog; Owner: postgres
--

COPY _timescaledb_catalog.tablespace (id, hypertable_id, tablespace_name) FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_10; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_10  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_559; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_559  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_609; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_609  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_610; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_610  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_612; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_612  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_624; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_624  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_625; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_625  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_626; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_626  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_627; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_627  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_628; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_628  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_629; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_629  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_630; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_630  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_631; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_631  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_632; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_632  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_633; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_633  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_636; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_636  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_639; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_639  FROM stdin;
\.


--
-- Data for Name: _compressed_hypertable_640; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._compressed_hypertable_640  FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_590; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_590 (bucket, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_608; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_608 (bucket, location, cluster, channel, device_id, device_name, device_type, on_microseconds, off_microseconds, last_observed_at, source_watermark) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_613; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_613 (bucket, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_614; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_614 (bucket, location, cluster, device_name, mode, effective_heating_setpoint_count, effective_heating_setpoint_sum, effective_heating_setpoint_sum_squares, effective_heating_setpoint_min, effective_heating_setpoint_max, effective_heating_setpoint_last, effective_cooling_setpoint_count, effective_cooling_setpoint_sum, effective_cooling_setpoint_sum_squares, effective_cooling_setpoint_min, effective_cooling_setpoint_max, effective_cooling_setpoint_last, effective_humidity_setpoint_count, effective_humidity_setpoint_sum, effective_humidity_setpoint_sum_squares, effective_humidity_setpoint_min, effective_humidity_setpoint_max, effective_humidity_setpoint_last, effective_co2_setpoint_count, effective_co2_setpoint_sum, effective_co2_setpoint_sum_squares, effective_co2_setpoint_min, effective_co2_setpoint_max, effective_co2_setpoint_last, effective_vpd_setpoint_count, effective_vpd_setpoint_sum, effective_vpd_setpoint_sum_squares, effective_vpd_setpoint_min, effective_vpd_setpoint_max, effective_vpd_setpoint_last, effective_light_intensity_count, effective_light_intensity_sum, effective_light_intensity_sum_squares, effective_light_intensity_min, effective_light_intensity_max, effective_light_intensity_last, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_615; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_615 (bucket, location, cluster, device_name, mode, effective_heating_setpoint_count, effective_heating_setpoint_sum, effective_heating_setpoint_sum_squares, effective_heating_setpoint_min, effective_heating_setpoint_max, effective_heating_setpoint_last, effective_cooling_setpoint_count, effective_cooling_setpoint_sum, effective_cooling_setpoint_sum_squares, effective_cooling_setpoint_min, effective_cooling_setpoint_max, effective_cooling_setpoint_last, effective_humidity_setpoint_count, effective_humidity_setpoint_sum, effective_humidity_setpoint_sum_squares, effective_humidity_setpoint_min, effective_humidity_setpoint_max, effective_humidity_setpoint_last, effective_co2_setpoint_count, effective_co2_setpoint_sum, effective_co2_setpoint_sum_squares, effective_co2_setpoint_min, effective_co2_setpoint_max, effective_co2_setpoint_last, effective_vpd_setpoint_count, effective_vpd_setpoint_sum, effective_vpd_setpoint_sum_squares, effective_vpd_setpoint_min, effective_vpd_setpoint_max, effective_vpd_setpoint_last, effective_light_intensity_count, effective_light_intensity_sum, effective_light_intensity_sum_squares, effective_light_intensity_min, effective_light_intensity_max, effective_light_intensity_last, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_616; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_616 (bucket, location, cluster, device_name, sample_count, device_state_min, device_state_max, device_state_last, device_mode_last, pid_output_count, pid_output_min, pid_output_max, pid_output_last, duty_cycle_percent_count, duty_cycle_percent_min, duty_cycle_percent_max, duty_cycle_percent_last, control_reason_last, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_617; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_617 (bucket, location, cluster, device_name, sample_count, device_state_min, device_state_max, device_state_last, device_mode_last, pid_output_count, pid_output_min, pid_output_max, pid_output_last, duty_cycle_percent_count, duty_cycle_percent_min, duty_cycle_percent_max, duty_cycle_percent_last, control_reason_last, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_618; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_618 (bucket, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_619; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_619 (bucket, sensor_id, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_620; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_620 (bucket, location, cluster, device_name, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_621; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_621 (bucket, location, cluster, device_name, sample_count, value_sum, value_sum_squares, min_value, max_value, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_637; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_637 (bucket, location, cluster, device_name, sample_count, requested_tw, requested_min, requested_max, last_observed_at) FROM stdin;
\.


--
-- Data for Name: _materialized_hypertable_638; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal._materialized_hypertable_638 (bucket, location, cluster, device_name, sample_count, requested_tw, requested_min, requested_max, last_observed_at) FROM stdin;
\.


--
-- Data for Name: compress_hyper_634_1276_chunk; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal.compress_hyper_634_1276_chunk (_ts_meta_count, _ts_meta_min_1, _ts_meta_max_1, _ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, bucket, _ts_meta_min_2, _ts_meta_max_2, _ts_meta_v2_first_location, _ts_meta_v2_last_location, location, _ts_meta_min_3, _ts_meta_max_3, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, cluster, _ts_meta_min_4, _ts_meta_max_4, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name, device_name, sample_count, requested_count, requested_sum, requested_sum_squares, requested_min, requested_max, last_observed_at, _ts_meta_v2_bloomh_efbd_bucket_location, _ts_meta_v2_bloomh_ce24_bucket_cluster, _ts_meta_v2_bloomh_22b7_bucket_device_name, _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name) FROM stdin;
4	2026-03-01 16:00:00-05	2026-03-01 16:00:00-05	2026-03-01 16:00:00-05	2026-03-01 16:00:00-05	BAAAAu77haf0AAAAAAAAAAAAAAAABAAAAAMAAAAAAAAB7gAF3fcLT+gAAAXd9wtP5/8AAAAAAAAAAA==	Flower Room	Veg Room	Flower Room	Veg Room	AgBwZ19jYXRhbG9nAHRleHQAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAOAAEAAAACAAAAC0Zsb3dlciBSb29tAAAACFZlZyBSb29t	main	main	main	main	AgBwZ19jYXRhbG9nAHRleHQAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAAAAEAAAABAAAABG1haW4=	Apache	Ridgetop Bottom Right	Apache	Ridgetop Bottom Right	AQBwZ19jYXRhbG9nAHRleHQAAAEAAAAEAAAABkFwYWNoZQAAAA1FeWVmaW5pdHkgVG9wAAAAFFJpZGdldG9wIEJvdHRvbSBMZWZ0AAAAFVJpZGdldG9wIEJvdHRvbSBSaWdodA==	BAAAAAAAAAAAAQAAAAAAAAAAAAAABAAAAAEAAAAAAAAAAgAAAAAAAAAG	BAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAA	Bg==	Bg==	Bg==	Bg==	BAAAAu78MTPyKP///////78FAAAABAAAAAMAAAAAAAAL7gAF3fhiZ4g+AAXd+GJmvUMAAAAAlQO36w==	\\x0900181481001014	\\x00008400800004c0	\\x932e039209922106	\\x0e06604b240a2614
\.


--
-- Data for Name: compress_hyper_634_1284_chunk; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal.compress_hyper_634_1284_chunk (_ts_meta_count, _ts_meta_min_1, _ts_meta_max_1, _ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, bucket, _ts_meta_min_2, _ts_meta_max_2, _ts_meta_v2_first_location, _ts_meta_v2_last_location, location, _ts_meta_min_3, _ts_meta_max_3, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, cluster, _ts_meta_min_4, _ts_meta_max_4, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name, device_name, sample_count, requested_count, requested_sum, requested_sum_squares, requested_min, requested_max, last_observed_at, _ts_meta_v2_bloomh_efbd_bucket_location, _ts_meta_v2_bloomh_ce24_bucket_cluster, _ts_meta_v2_bloomh_22b7_bucket_device_name, _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name) FROM stdin;
21	2026-07-06 18:00:00-04	2026-07-12 17:00:00-04	2026-07-06 18:00:00-04	2026-07-12 17:00:00-04	BAAAAvlvBozUAAAAAAAAAAAAAAAAFQAAAA0AAf7s7/7r7gAF8e5UKHAAAAXx7lQob/8AAAAAAAAAAAAAACno1ggAAAAAKDuuv/8AAAAfFmGIAAAAABNaTpAAAAAAFB3XX/8AAAAAAAAAAAAAAJvnRSgAAAAAmIz2l/8AAAATWk6P/wAAAAAAAAAA	Flower Room	Veg Room	Flower Room	Veg Room	AgBwZ19jYXRhbG9nAHRleHQAAAAAFQAAAAEAAAAAAAAAAQAAAAAAHHg4AAEAAAACAAAAC0Zsb3dlciBSb29tAAAACFZlZyBSb29t	main	main	main	main	AgBwZ19jYXRhbG9nAHRleHQAAAAAFQAAAAEAAAAAAAAAAQAAAAAAAAAAAAEAAAABAAAABG1haW4=	cooling_f_1	light_f_4	light_1	heater_v_1	AQBwZ19jYXRhbG9nAHRleHQAAAEAAAAVAAAAB2xpZ2h0XzEAAAAHbGlnaHRfMgAAAAdsaWdodF8zAAAAB2xpZ2h0XzEAAAAHbGlnaHRfMgAAAAdsaWdodF8zAAAAC2Nvb2xpbmdfZl8xAAAACWxpZ2h0X2ZfNAAAAAtoZWF0aW5nX2ZfMgAAAAtleGhhdXN0X2ZfMQAAAAtoZWF0aW5nX2ZfMQAAAAtjb29saW5nX3ZfMQAAAAtleGhhdXN0X3ZfMQAAAAtoZWF0aW5nX3ZfMQAAAAdmYW5fdl8xAAAAEGRlaHVtaWRpZmllcl9mXzEAAAAHZmFuX2ZfMQAAAApoZWF0ZXJfZl8xAAAAEGRlaHVtaWRpZmllcl92XzEAAAAHZmFuX3ZfMgAAAApoZWF0ZXJfdl8x	BAAAAAAAAAAAAQAAAAAAAAAAAAAAFQAAAAEAAAAAAAAAAgAAAAAAAAAG	BAAAAAAAAAAAAAAAAAAAAAAAAAAAFQAAAAEAAAAAAAAAAQAAAAAAAAAA	Bg==	Bg==	Bg==	Bg==	BAAAAvlvQlLdNQAAAAAAAAAAAAAAFQAAAA0AAf7s7/7r7gAF8e+4XVFKAAXx77hdUUkAAAAAAAAAAAAAACmMHDYkAAAAJ7ogrc8AAAAe49MVwgAAABK4xDRIAAAAE26S0l0AAAAAAAAAAAAAAJxeRbg2AAAAmXK8NjcAAAAS64mB/QAAAAAAAAAA	\\xbc2448e4204e985b601861704a87a328	\\xba0e2bb21b99ab1f	\\x553835490e04ecf4102a0100498a01892e1a0316504232a2300300a91fa810a4	\\x66044a1315b602c38c84e3a6410fea14c00600676ac2450c2112583e58143851
\.


--
-- Data for Name: compress_hyper_635_1280_chunk; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal.compress_hyper_635_1280_chunk (_ts_meta_count, _ts_meta_min_1, _ts_meta_max_1, _ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, bucket, _ts_meta_min_2, _ts_meta_max_2, _ts_meta_v2_first_location, _ts_meta_v2_last_location, location, _ts_meta_min_3, _ts_meta_max_3, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, cluster, _ts_meta_min_4, _ts_meta_max_4, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name, device_name, sample_count, requested_count, requested_sum, requested_sum_squares, requested_min, requested_max, last_observed_at, _ts_meta_v2_bloomh_efbd_bucket_location, _ts_meta_v2_bloomh_ce24_bucket_cluster, _ts_meta_v2_bloomh_22b7_bucket_device_name, _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name) FROM stdin;
4	2026-02-28 19:00:00-05	2026-02-28 19:00:00-05	2026-02-28 19:00:00-05	2026-02-28 19:00:00-05	BAAAAu7p64uAAAAAAAAAAAAAAAAABAAAAAMAAAAAAAAB7gAF3dPXFwAAAAXd09cW//8AAAAAAAAAAA==	Flower Room	Veg Room	Flower Room	Veg Room	AgBwZ19jYXRhbG9nAHRleHQAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAOAAEAAAACAAAAC0Zsb3dlciBSb29tAAAACFZlZyBSb29t	main	main	main	main	AgBwZ19jYXRhbG9nAHRleHQAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAAAAEAAAABAAAABG1haW4=	Apache	Ridgetop Bottom Right	Apache	Ridgetop Bottom Right	AQBwZ19jYXRhbG9nAHRleHQAAAEAAAAEAAAABkFwYWNoZQAAAA1FeWVmaW5pdHkgVG9wAAAAFFJpZGdldG9wIEJvdHRvbSBMZWZ0AAAAFVJpZGdldG9wIEJvdHRvbSBSaWdodA==	BAAAAAAAAAAAAQAAAAAAAAAAAAAABAAAAAEAAAAAAAAAAgAAAAAAAAAG	BAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAEAAAAAAAAAAQAAAAAAAAAA	Bg==	Bg==	Bg==	Bg==	BAAAAu78MTPyKP///////78FAAAABAAAAAMAAAAAAAAL7gAF3fhiZ4g+AAXd+GJmvUMAAAAAlQO36w==	\\x1222088000203029	\\x6000004200402000	\\xc485500891800119	\\x5414041133411651
\.


--
-- Data for Name: compress_hyper_635_1285_chunk; Type: TABLE DATA; Schema: _timescaledb_internal; Owner: cea_user
--

COPY _timescaledb_internal.compress_hyper_635_1285_chunk (_ts_meta_count, _ts_meta_min_1, _ts_meta_max_1, _ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, bucket, _ts_meta_min_2, _ts_meta_max_2, _ts_meta_v2_first_location, _ts_meta_v2_last_location, location, _ts_meta_min_3, _ts_meta_max_3, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, cluster, _ts_meta_min_4, _ts_meta_max_4, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name, device_name, sample_count, requested_count, requested_sum, requested_sum_squares, requested_min, requested_max, last_observed_at, _ts_meta_v2_bloomh_efbd_bucket_location, _ts_meta_v2_bloomh_ce24_bucket_cluster, _ts_meta_v2_bloomh_22b7_bucket_device_name, _ts_meta_v2_bloomh_9397_bucket_location_cluster_device_name) FROM stdin;
21	2026-07-05 20:00:00-04	2026-07-11 20:00:00-04	2026-07-05 20:00:00-04	2026-07-11 20:00:00-04	BAAAAvldbHBgAAAAAAAAAAAAAAAAFQAAAAoAAAAe6u7r7gAF8clyyEAAAAXxyXLIP/8AAAAAAAAAAAAAACg7rsAAAAAAAAAAAAAAAAAoO66//wAAAAAAAAAAAAAAoO67AAAAAACg7rr//wAAAAAAAAAA	Flower Room	Veg Room	Flower Room	Veg Room	AgBwZ19jYXRhbG9nAHRleHQAAAAAFQAAAAEAAAAAAAAAAQAAAAAAHjg4AAEAAAACAAAAC0Zsb3dlciBSb29tAAAACFZlZyBSb29t	main	main	main	main	AgBwZ19jYXRhbG9nAHRleHQAAAAAFQAAAAEAAAAAAAAAAQAAAAAAAAAAAAEAAAABAAAABG1haW4=	cooling_f_1	light_f_4	light_1	heater_v_1	AQBwZ19jYXRhbG9nAHRleHQAAAEAAAAVAAAAB2xpZ2h0XzEAAAAHbGlnaHRfMgAAAAdsaWdodF8zAAAAB2xpZ2h0XzEAAAAHbGlnaHRfMgAAAAdsaWdodF8zAAAAC2Nvb2xpbmdfZl8xAAAAC2V4aGF1c3RfZl8xAAAAC2hlYXRpbmdfZl8xAAAAC2hlYXRpbmdfZl8yAAAACWxpZ2h0X2ZfNAAAAAtjb29saW5nX3ZfMQAAAAtleGhhdXN0X3ZfMQAAAAtoZWF0aW5nX3ZfMQAAABBkZWh1bWlkaWZpZXJfZl8xAAAAB2Zhbl9mXzEAAAAKaGVhdGVyX2ZfMQAAABBkZWh1bWlkaWZpZXJfdl8xAAAAB2Zhbl92XzEAAAAHZmFuX3ZfMgAAAApoZWF0ZXJfdl8x	BAAAAAAAAAAAAQAAAAAAAAAAAAAAFQAAAAEAAAAAAAAAAgAAAAAAAAAG	BAAAAAAAAAAAAAAAAAAAAAAAAAAAFQAAAAEAAAAAAAAAAQAAAAAAAAAA	Bg==	Bg==	Bg==	Bg==	BAAAAvlvQlLdNQAAAAAAAAAAAAAAFQAAABH/3u7u/u/r7gAAAAAAAAAPAAXx77hdUUoABfHvuF1RSQAAAAAAAAAAAAAAKYwcNiQAAAATlb89WwAAACX2XPjHAAAAE26S0l0AAAASuMQ0SAAAADTaMA6KAAAAJCRhcHMAAAAAAAAAAAAAAJ9Jzzo0AAAAn0nPOjMAAAAAAAAAAAAAABLriYH9AAAAFdcTA/wAAAAS64mB/Q==	\\x748af33dd382bc90	\\x2101092099d028d4	\\x6160f81939055c9019c612080059885f0024e18372f014340ac2350628a0c411	\\xec408cfd48756ec01963189497496500040768c37da194710023340ec2630131
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
011_merge_heads
\.


--
-- Data for Name: archive_artifact; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_artifact (artifact_id, dataset_id, revision, state, remote_instance_id, remote_path, sha256, byte_size, verified_at, local_path, local_verified_at, last_accessed_at, manifest_sha256, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: archive_availability; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_availability (availability_id, dataset_id, revision, source_table, room, start_at, end_at, state, source_rows_removed, source_removed_at, aggregates_refreshed, aggregates_refreshed_at, recorded_at, updated_at) FROM stdin;
\.


--
-- Data for Name: archive_component; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_component (parent_artifact_id, child_dataset_id, child_revision, member_prefix, manifest_sha256, observation_counts, created_at) FROM stdin;
\.


--
-- Data for Name: archive_dataset; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_dataset (dataset_id, kind, grow_plan_id, calendar_year, quarter, room, calendar_revision, data_revision, published_revision, state, available_from, available_to, anniversary_at, manifest, last_error_code, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: archive_expiry_cursor; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_expiry_cursor (source_table, room, processed_before, updated_at) FROM stdin;
\.


--
-- Data for Name: archive_job; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_job (job_id, dataset_id, kind, requested_revision, state, checkpoint, lease_owner, lease_token, lease_until, attempts, next_attempt_at, last_error_code, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: archive_load; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_load (load_id, dataset_id, revision, source_kind, artifact_id, member_prefix, staging_path, last_accessed_at, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: archive_scope_override; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_scope_override (override_id, source_table, source_identity, start_at, end_at, room, confirmed_at) FROM stdin;
\.


--
-- Data for Name: archive_window; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.archive_window (dataset_id, calendar_revision, event_id, phase_type, room, start_at, end_at) FROM stdin;
\.


--
-- Data for Name: automation_state; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.automation_state (id, "time", location, cluster, device_name, device_state, device_mode, pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids, control_reason, updated_at, schedule_ramp_up_duration, schedule_ramp_down_duration, schedule_photoperiod_hours, pid_kp, pid_ki, pid_kd) FROM stdin;
\.


--
-- Data for Name: calendar_event; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.calendar_event (id, location, cluster, event_type, title, start_date, end_date, all_day, notes, metadata, crop_batch_id, grow_plan_id, recurrence_rule, recurrence_parent_id, ical_uid, external_provider, external_calendar_id, external_event_id, external_etag, last_synced_at, sync_status, deleted_at, created_at, updated_at, created_by, local_revision, sync_connection_id, sync_generation, sync_error_code, sync_next_attempt_at, sync_attempts, sync_lease_owner, sync_lease_until, sync_mirror_all) FROM stdin;
1	Flower Room	main	planned_task	Plantation	2026-10-03	\N	t	\N	{}	\N	\N	\N	\N	cea-cal-flower-room-3f7ffc9f601b@siberianjungle.local	nextcloud	https://iskradocker.tail7a351e.ts.net/remote.php/dav/calendars/antoine/cultivation/	https://iskradocker.tail7a351e.ts.net/remote.php/dav/calendars/antoine/cultivation/cea-cal-flower-room-3f7ffc9f601b%40siberianjungle.local.ics	"e62f93d3dab79dcf5d4c5aa5ca320541"	2026-10-19 16:04:25.722689-04	synced	\N	2026-10-10 09:42:41.438328-04	2026-10-10 09:42:43.506318-04	\N	2	\N	2	\N	2026-10-19 16:04:25.422025-04	1	\N	\N	f
\.


--
-- Data for Name: calendar_mode_application; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.calendar_mode_application (id, event_id, applied_date, mode_id, submode_id, applied_at, triggered_by) FROM stdin;
\.


--
-- Data for Name: calendar_room_profile; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.calendar_room_profile (location, display_name, color_key, sort_order, nextcloud_calendar_url, caldav_sync_token, enabled, updated_at, calendar_mode_transitions_enabled) FROM stdin;
Flower Room	Flower	flower	0	\N	\N	t	2026-05-17 15:23:52.746326-04	t
Veg Room	Vegetation	veg	1	\N	\N	t	2026-05-17 15:23:52.746326-04	t
Lab	Laboratory	lab	2	\N	\N	t	2026-05-17 15:23:52.746326-04	t
\.


--
-- Data for Name: calendar_sync_connection; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.calendar_sync_connection (id, provider, display_name, account_email, caldav_base_url, credentials_encrypted, target_calendar_url, sync_token, last_sync_at, last_error, enabled, created_at, instance_id, generation, files_root_url) FROM stdin;
\.


--
-- Data for Name: climate_periods; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.climate_periods (id, location, cluster, mode_id, submode_id, period_name, start_time, end_time, ramp_minutes, heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint, details, created_at, updated_at) FROM stdin;
94	Flower Room	main	4	\N	Sleep Constant	00:00:00	00:00:00	0	10	50	1	\N	24h constant setpoints	2026-10-01 09:49:19.403421-04	2026-10-01 09:49:19.403421-04
110	Flower Room	main	1	\N	Jour	00:00:00	10:00:00	0	25	27	1.1	\N		2026-10-17 09:23:43.514782-04	2026-10-17 09:23:43.514782-04
111	Flower Room	main	1	\N	Nuit	10:00:00	16:00:00	60	24	27	1.1	\N		2026-10-17 09:23:43.514782-04	2026-10-17 09:23:43.514782-04
112	Flower Room	main	1	\N	Jour2	16:00:00	00:00:00	60	25	27	1.1	\N		2026-10-17 09:23:43.514782-04	2026-10-17 09:23:43.514782-04
25	Flower Room	main	2	2	Night	05:00:00	17:00:00	30	20	24	0.8	600		2026-04-11 13:25:02.342712-04	2026-04-11 13:25:02.342712-04
26	Flower Room	main	2	2	Day	17:00:00	05:00:00	30	24	28	1	800		2026-04-11 13:25:02.346413-04	2026-04-11 13:25:02.346413-04
27	Flower Room	main	2	3	Night	05:00:00	17:00:00	30	20	24	0.8	600		2026-04-11 13:25:05.580316-04	2026-04-11 13:25:05.580316-04
28	Flower Room	main	2	3	Day	17:00:00	05:00:00	30	24	28	1	800		2026-04-11 13:25:05.582598-04	2026-04-11 13:25:05.582598-04
31	Flower Room	main	3	\N	Drying	00:00:00	00:00:00	0	14	25	0.8	0		2026-04-27 08:52:08.17125-04	2026-04-27 08:52:08.17125-04
91	Veg Room	main	1	\N	Period 1	00:00:00	09:00:00	0	25	28	1	800		2026-09-20 14:15:06.823623-04	2026-09-20 14:15:06.823623-04
92	Veg Room	main	1	\N	Period 2	09:01:00	16:00:00	60	23	26	1	800		2026-09-20 14:15:06.823623-04	2026-09-20 14:15:06.823623-04
93	Veg Room	main	1	\N	Period 3	16:01:00	23:59:00	60	25	28	1	800		2026-09-20 14:15:06.823623-04	2026-09-20 14:15:06.823623-04
\.


--
-- Data for Name: config_versions; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.config_versions (version_id, "timestamp", author, comment, config_type, location, cluster, changes) FROM stdin;
\.


--
-- Data for Name: control_history; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.control_history (id, "time", location, cluster, device_name, channel, old_state, new_state, mode, reason, sensor_value, setpoint, load_percent, manual_expires_at) FROM stdin;
\.


--
-- Data for Name: crop_batch; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.crop_batch (batch_id, crop_name, start_date, end_date, room_id, created_at) FROM stdin;
\.


--
-- Data for Name: device; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.device (device_id, rack_id, name, type, ip_address, serial_number, created_at) FROM stdin;
1	1	Node 1	BME280	\N	\N	2025-12-12 16:38:35.179824-05
2	1	Node 2	SCD30	\N	\N	2025-12-12 16:38:35.179824-05
3	5	Soil Sensor - Front Bed	RS485 Soil Sensor	\N	MODBUS-1	2025-12-15 16:28:02.742251-05
4	\N	Weather Station YUL	Weather Station	\N	\N	2026-01-21 16:03:42.518734-05
5	\N	Weather Station QC	Weather Station	\N	\N	2026-01-21 16:09:46.761495-05
6	1	Soil Sensor - Back Bed	RS485 Soil Sensor	\N	MODBUS-2	2026-01-27 17:58:30.323967-05
7	\N	Weather Station Quebec City	Weather Station	\N	\N	2026-01-30 21:44:51.806855-05
8	\N	Weather Station Québec	Weather Station	\N	\N	2026-03-03 18:10:58.095936-05
\.


--
-- Data for Name: device_mappings; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.device_mappings (id, location, cluster, device_name, channel, active_high, safe_state, mcp_board_id, updated_at) FROM stdin;
\.


--
-- Data for Name: device_registry; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.device_registry (device_id, location, cluster, device_name, display_name, device_type, channel, dimming_enabled, dimming_type, dimming_board_id, dimming_channel, safety_level, pid_enabled, interlock_with, pid_setpoints, per_room_index, created_at, updated_at) FROM stdin;
4	Flower Room	main	light_f_3	Chilled Back	light	12	t	dfr0971	2	1	0	f	[]	{}	3	2026-07-12 17:35:05.293128-04	2026-07-12 17:35:05.293128-04
9	Veg Room	main	light_v_2	Ridgetop Bottom Right	light	4	t	dfr0971	0	1	40	f	[]	{}	2	2026-07-12 17:35:05.315413-04	2026-07-12 17:35:05.315413-04
8	Veg Room	main	light_v_1	Eyefinity Top	light	10	t	dfr0971	0	0	0	f	[]	{}	1	2026-07-12 17:35:05.315413-04	2026-07-30 15:45:09.187143-04
10	Veg Room	main	light_v_3	Ridgetop Bottom Left	light	11	t	dfr0971	1	0	40	f	[]	{}	3	2026-07-12 17:35:05.315413-04	2026-07-30 15:45:21.325944-04
3	Flower Room	main	light_f_2	Apache	light	2	t	dfr0971	1	1	0	f	[]	{}	2	2026-07-12 17:35:05.293128-04	2026-07-30 15:45:36.310927-04
2	Flower Room	main	light_f_1	Chilled Front	light	3	t	dfr0971	2	0	0	f	[]	{}	1	2026-07-12 17:35:05.293128-04	2026-07-30 15:45:41.462787-04
\.


--
-- Data for Name: device_states; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.device_states (id, location, cluster, device_name, channel, state, mode, updated_at) FROM stdin;
\.


--
-- Data for Name: effective_setpoints; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.effective_setpoints ("time", location, cluster, mode, effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint, effective_co2_setpoint, effective_vpd_setpoint, nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint, nominal_co2_setpoint, nominal_vpd_setpoint, ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity, ramp_progress_co2, ramp_progress_vpd, device_name, effective_light_intensity, nominal_light_intensity, ramp_progress_light, monitoring_ingest_id, ingested_at) FROM stdin;
\.


--
-- Data for Name: flower_submodes; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.flower_submodes (id, name, description, week_start, week_end, created_at) FROM stdin;
1	stretch	Stretch phase - weeks 1-3	1	3	2026-01-15 09:26:37.413731-05
2	bulk	Bulk phase - weeks 4-6	4	6	2026-01-15 09:26:37.413731-05
3	ripen	Ripen phase - weeks 7-9	7	9	2026-01-15 09:26:37.413731-05
\.


--
-- Data for Name: grow_lifecycle; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.grow_lifecycle (grow_plan_id, crop_batch_id, state, calendar_revision, original_drying_end, first_closed_at, closed_at, updated_at) FROM stdin;
\.


--
-- Data for Name: grow_plan_idempotency; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.grow_plan_idempotency (idempotency_key, grow_plan_id, created_at, expires_at) FROM stdin;
\.


--
-- Data for Name: light_presets; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.light_presets (id, mode_id, submode_id, lights_on_hour, lights_off_hour, intensity_day, intensity_night, created_at) FROM stdin;
\.


--
-- Data for Name: light_programs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.light_programs (id, device_id, location, cluster, mode_id, name, program_type, start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds, target_intensity, ramp_up_minutes, ramp_down_minutes, day_of_week, enabled, priority, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: light_state_history; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.light_state_history (id, location, cluster, device_name, old_state, new_state, intensity, ramp_progress, triggered_by, triggered_at) FROM stdin;
\.


--
-- Data for Name: light_target_intensity; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.light_target_intensity (device_id, mode_id, target_intensity, created_at, updated_at) FROM stdin;
9	1	20	2026-07-12 18:01:16.645197-04	2026-09-28 19:20:02.025492-04
10	1	20	2026-07-12 18:01:16.677659-04	2026-09-28 19:20:02.187741-04
8	1	60	2026-07-12 18:01:16.589153-04	2026-09-28 19:20:02.328073-04
2	4	100	2026-08-24 06:52:30.488165-04	2026-10-02 07:31:25.074766-04
3	4	100	2026-08-24 06:52:30.826494-04	2026-10-02 07:31:25.182255-04
4	4	100	2026-08-24 06:52:31.059972-04	2026-10-02 07:31:25.312791-04
3	1	30	2026-10-03 18:33:33.995692-04	2026-10-20 18:34:54.855534-04
2	1	75	2026-10-03 18:31:56.345995-04	2026-10-20 18:34:54.931213-04
4	1	70	2026-10-03 18:31:56.181696-04	2026-10-20 18:34:55.048052-04
\.


--
-- Data for Name: measurement; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.measurement ("time", sensor_id, value, status) FROM stdin;
\.


--
-- Data for Name: migration_0012_state; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.migration_0012_state (created_table, had_view) FROM stdin;
f	t
\.


--
-- Data for Name: mode_notes; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.mode_notes (id, location, mode, content, updated_at) FROM stdin;
1	Flower Room	flower	Test note from API	2026-01-20 11:56:23.163935-05
\.


--
-- Data for Name: mode_parameters; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.mode_parameters (id, location, cluster, mode_id, submode_id, day_start_time, night_start_time, ramp_up_minutes, ramp_down_minutes, pre_day_minutes, pre_night_minutes, day_heat_temp, day_cool_temp, day_vpd, day_co2, day_leaf_delta, night_heat_temp, night_cool_temp, night_vpd, night_co2, night_leaf_delta, main_light_intensity, supplemental_light_intensity, created_at, updated_at, pre_day_heat_temp, pre_day_cool_temp, pre_day_vpd, pre_day_co2, pre_night_heat_temp, pre_night_cool_temp, pre_night_vpd, pre_night_co2, pre_day_ramp_minutes, pre_night_ramp_minutes, light_ramp_up_minutes, light_ramp_down_minutes) FROM stdin;
5	Veg Room	main	1	\N	16:00:00	10:00:00	30	30	60	60	24	28	1	800	-2	20	24	0.8	600	-1	100	0	2026-01-18 09:35:40.978417-05	2026-09-28 19:20:01.648812-04	22	26	0.9	700	22	26	0.9	700	30	30	60	60
10	Flower Room	main	4	\N	17:00:00	11:00:00	30	30	30	30	24	28	1	800	-2	20	24	0.8	600	-1	100	0	2026-05-10 11:49:43.35655-04	2026-10-03 18:31:22.752299-04	22	26	0.9	700	22	26	0.9	700	30	30	15	15
6	Flower Room	main	2	1	17:00:00	05:00:00	15	15	120	60	26	28	1.2	800	-2	24	26	1.2	600	-1	100	0	2026-02-08 10:48:23.656661-05	2026-04-27 08:45:26.252899-04	25	28	1.2	700	24	28	1.2	700	15	15	30	30
7	Flower Room	main	2	2	17:00:00	05:00:00	30	30	30	30	24	28	1	800	-2	20	24	0.8	600	-1	100	0	2026-03-18 18:43:22.280385-04	2026-04-27 08:45:27.223237-04	22	26	0.9	700	22	26	0.9	700	30	30	15	15
8	Flower Room	main	2	3	17:00:00	05:00:00	30	30	30	30	24	28	1	800	-2	20	24	0.8	600	-1	100	0	2026-04-09 16:27:32.264831-04	2026-04-27 08:45:29.670083-04	22	26	0.9	700	22	26	0.9	700	30	30	60	60
9	Flower Room	main	3	\N	17:00:00	11:00:00	30	30	30	30	24	28	1	800	-2	20	24	0.8	600	-1	100	0	2026-04-27 08:45:31.414795-04	2026-05-10 11:40:31.639773-04	22	26	0.9	700	22	26	0.9	700	30	30	15	15
4	Flower Room	main	1	\N	17:00:00	11:00:00	15	15	120	60	26	28	1.2	800	-2	24	26	1.2	600	-1	100	0	2026-01-17 19:03:57.283377-05	2026-10-17 09:23:43.514782-04	25	28	1.2	700	24	28	1.2	700	15	15	60	60
\.


--
-- Data for Name: mode_transition_history; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.mode_transition_history (id, location, cluster, old_mode_id, old_submode_id, new_mode_id, new_submode_id, triggered_by, "time", parameters_synced, success, error_message, created_at) FROM stdin;
\.


--
-- Data for Name: monitoring_cagg_backfill_marker; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_cagg_backfill_marker (cagg_name, covered_through, completed_at, completed_by) FROM stdin;
monitoring_measurement_1min	2026-08-11 18:56:26.080972-04	2026-08-11 18:56:26.080972-04	manual_todo17_catchup
monitoring_measurement_5min	2026-08-11 18:56:26.080972-04	2026-08-11 18:56:26.080972-04	manual_todo17_catchup
monitoring_effective_setpoints_1min	2026-08-11 18:56:26.156399-04	2026-08-11 18:56:26.156399-04	manual_todo17_catchup
monitoring_effective_setpoints_5min	2026-08-11 18:56:26.156399-04	2026-08-11 18:56:26.156399-04	manual_todo17_catchup
monitoring_automation_state_1min	2026-08-11 18:56:26.534439-04	2026-08-11 18:56:26.534439-04	manual_todo17_catchup
monitoring_automation_state_5min	2026-08-11 18:56:26.534439-04	2026-08-11 18:56:26.534439-04	manual_todo17_catchup
\.


--
-- Data for Name: monitoring_dependent_view_snapshot_0007; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_dependent_view_snapshot_0007 (view_schema, view_name, view_definition, dependency_depth, view_owner, view_owner_oid, view_acl) FROM stdin;
public	monitoring_cagg_watermark	 SELECT continuous_agg.user_view_name::text AS cagg_name,\n    GREATEST(_timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(continuous_agg.mat_hypertable_id)), coverage.materialization_coverage) AS materialization_watermark,\n    invalidation.invalidation_source,\n    _timescaledb_functions.to_timestamp(invalidation.lowest_modified_value) AS invalidation_start,\n    _timescaledb_functions.to_timestamp(invalidation.greatest_modified_value) AS invalidation_end\n   FROM _timescaledb_catalog.continuous_agg continuous_agg\n     LEFT JOIN monitoring_cagg_materialization_coverage coverage ON coverage.cagg_name = continuous_agg.user_view_name\n     LEFT JOIN LATERAL ( SELECT 'materialization'::text AS invalidation_source,\n            materialization_log.lowest_modified_value,\n            materialization_log.greatest_modified_value\n           FROM _timescaledb_catalog.continuous_aggs_materialization_invalidation_log materialization_log\n          WHERE materialization_log.materialization_id = continuous_agg.mat_hypertable_id\n        UNION ALL\n         SELECT 'hypertable'::text AS invalidation_source,\n            hypertable_log.lowest_modified_value,\n            hypertable_log.greatest_modified_value\n           FROM _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log hypertable_log\n          WHERE hypertable_log.hypertable_id = continuous_agg.raw_hypertable_id) invalidation ON true\n  WHERE continuous_agg.user_view_schema = 'public'::name AND continuous_agg.user_view_name ~~ like_escape('monitoring\\_%'::text, '\\'::text);	2	cea_user	17164	\N
public	monitoring_cagg_materialization_coverage	 SELECT 'monitoring_measurement_1min'::text AS cagg_name,\n    max(monitoring_measurement_1min.last_observed_at) AS materialization_coverage\n   FROM monitoring_measurement_1min\nUNION ALL\n SELECT 'monitoring_measurement_5min'::text AS cagg_name,\n    max(monitoring_measurement_5min.last_observed_at) AS materialization_coverage\n   FROM monitoring_measurement_5min\nUNION ALL\n SELECT 'monitoring_effective_setpoints_1min'::text AS cagg_name,\n    max(monitoring_effective_setpoints_1min.last_observed_at) AS materialization_coverage\n   FROM monitoring_effective_setpoints_1min\nUNION ALL\n SELECT 'monitoring_effective_setpoints_5min'::text AS cagg_name,\n    max(monitoring_effective_setpoints_5min.last_observed_at) AS materialization_coverage\n   FROM monitoring_effective_setpoints_5min\nUNION ALL\n SELECT 'monitoring_automation_state_1min'::text AS cagg_name,\n    max(monitoring_automation_state_1min.last_observed_at) AS materialization_coverage\n   FROM monitoring_automation_state_1min\nUNION ALL\n SELECT 'monitoring_automation_state_5min'::text AS cagg_name,\n    max(monitoring_automation_state_5min.last_observed_at) AS materialization_coverage\n   FROM monitoring_automation_state_5min;	1	cea_user	17164	\N
\.


--
-- Data for Name: monitoring_index_snapshot_0007; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_index_snapshot_0007 (table_name, old_index_name, new_index_name, old_constraint_name) FROM stdin;
control_history	control_history_timestamp_idx	control_history_time_idx	\N
effective_setpoints	effective_setpoints_timestamp_idx	effective_setpoints_time_idx	\N
monitoring_photoperiod_coverage	monitoring_photoperiod_covera_location_cluster_observed_at__key	monitoring_photoperiod_covera_location_cluster_time__key	monitoring_photoperiod_covera_location_cluster_observed_at__key
relay_observation	relay_observation_observed_at_idx	relay_observation_time_idx	\N
\.


--
-- Data for Name: monitoring_job_snapshot_0007; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_job_snapshot_0007 (job_id, proc_schema, proc_name, scheduled, schedule_interval, max_runtime, max_retries, retry_period, config, next_start, target_schema, target_name, target_is_cagg, restore_after_down, check_schema, check_name, fixed_schedule, initial_start, timezone) FROM stdin;
1008	_timescaledb_functions	policy_refresh_continuous_aggregate	t	00:01:00	00:00:00	-1	00:01:00	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 590}	2026-10-20 12:20:03.928937-04	public	monitoring_measurement_1min	t	t	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
1020	public	monitoring_refresh_overview_incremental	t	00:05:00	00:01:00	0	00:05:00	{"family": "measurement", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	2026-10-20 12:19:47.666536-04	\N	\N	f	t	\N	\N	t	2026-10-12 14:44:47.666536-04	\N
1021	public	monitoring_refresh_overview_incremental	t	00:05:00	00:01:00	0	00:05:00	{"family": "light_intensity", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	2026-10-20 12:19:47.667988-04	\N	\N	f	t	\N	\N	t	2026-10-12 14:44:47.667988-04	\N
1023	_timescaledb_functions	policy_refresh_continuous_aggregate	f	01:00:00	00:01:00	0	01:00:00	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 608, "refresh_newest_first": false, "max_batches_per_execution": 1}	\N	public	monitoring_relay_duration_1day	t	t	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
1022	public	monitoring_refresh_overview_incremental	t	00:05:00	00:01:00	0	00:05:00	{"family": "requested_pid", "enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	2026-10-20 12:19:47.669816-04	\N	\N	f	t	\N	\N	t	2026-10-12 14:44:47.669816-04	\N
1024	public	monitoring_rebuild_relay_overview	t	00:05:00	00:01:00	0	00:05:00	{"enabled": true, "acknowledged_failures": {"monitoring_requested_pid_1hour": 1, "monitoring_rebuild_relay_overview": 1, "monitoring_refresh_overview_incremental": 3}}	2026-10-20 12:19:47.765936-04	\N	\N	f	t	\N	\N	t	2026-10-12 14:44:47.765936-04	\N
1026	_timescaledb_functions	policy_compression	t	12:00:00	00:00:00	-1	01:00:00	{"hypertable_id": 565, "compress_after": "3 mons"}	2026-10-20 22:52:11.272458-04	public	automation_state	f	t	_timescaledb_functions	policy_compression_check	f	\N	\N
1028	_timescaledb_functions	policy_compression	t	12:00:00	00:00:00	-1	01:00:00	{"hypertable_id": 600, "compress_after": "3 mons"}	2026-10-20 22:52:10.905162-04	public	relay_observation	f	t	_timescaledb_functions	policy_compression_check	f	\N	\N
1029	_timescaledb_functions	policy_compression	t	12:00:00	00:00:00	-1	01:00:00	{"hypertable_id": 611, "compress_after": "3 mons"}	2026-10-20 18:39:50.94292-04	public	control_history	f	t	_timescaledb_functions	policy_compression_check	f	\N	\N
1031	_timescaledb_functions	policy_compression	t	12:00:00	00:00:00	-1	01:00:00	{"hypertable_id": 423, "compress_after": "3 mons"}	2026-10-20 18:40:12.259835-04	public	effective_setpoints	f	t	_timescaledb_functions	policy_compression_check	f	\N	\N
\.


--
-- Data for Name: monitoring_job_snapshot_0008; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_job_snapshot_0008 (job_id, proc_schema, proc_name, scheduled, target_schema, target_name, target_is_cagg, restore_after_backfill, drop_after_backfill) FROM stdin;
1053	_timescaledb_functions	policy_compression	t	public	monitoring_requested_pid_1hour	t	t	f
1036	_timescaledb_functions	policy_refresh_continuous_aggregate	t	public	monitoring_requested_pid_1hour	t	t	f
1054	_timescaledb_functions	policy_compression	t	public	monitoring_requested_pid_1day	t	t	f
1037	_timescaledb_functions	policy_refresh_continuous_aggregate	t	public	monitoring_requested_pid_1day	t	t	f
1056	public	monitoring_backfill_aggregates	f	\N	\N	f	t	f
1024	public	monitoring_rebuild_relay_overview	t	\N	\N	f	t	f
1022	public	monitoring_refresh_overview_incremental	t	\N	\N	f	t	f
1021	public	monitoring_refresh_overview_incremental	t	\N	\N	f	t	f
1020	public	monitoring_refresh_overview_incremental	t	\N	\N	f	t	f
\.


--
-- Data for Name: monitoring_overview_coverage; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_overview_coverage (model_name, covered_from, covered_through) FROM stdin;
monitoring_relay_duration_1hour	2026-09-25 20:00:00-04	2026-10-20 20:00:00-04
monitoring_requested_pid_1day	2026-01-01 19:00:00-05	2026-10-19 20:00:00-04
monitoring_measurement_1day	2026-03-04 19:00:00-05	2026-10-19 20:00:00-04
monitoring_light_intensity_1day	2026-07-07 20:00:00-04	2026-10-19 20:00:00-04
monitoring_requested_pid_1hour	2026-01-01 19:00:00-05	2026-10-21 12:00:00-04
monitoring_measurement_1hour	2026-03-04 19:00:00-05	2026-10-21 12:00:00-04
monitoring_light_intensity_1hour	2026-07-07 20:00:00-04	2026-10-21 12:00:00-04
\.


--
-- Data for Name: monitoring_photoperiod_coverage; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_photoperiod_coverage (id, "time", location, cluster, state, reason, runtime_snapshot_version) FROM stdin;
\.


--
-- Data for Name: monitoring_refresh_policy_snapshot_0007; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_refresh_policy_snapshot_0007 (view_name, job_id, scheduled, schedule_interval, max_runtime, max_retries, retry_period, config, next_start, check_schema, check_name, fixed_schedule, initial_start, timezone) FROM stdin;
monitoring_measurement_5min	1009	t	00:05:00	00:00:00	-1	00:05:00	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 591}	2026-10-20 12:24:04.648892-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_effective_setpoints_1min	1010	t	00:01:00	00:00:00	-1	00:01:00	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 596}	2026-10-20 12:19:32.397988-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_effective_setpoints_5min	1011	t	00:05:00	00:00:00	-1	00:05:00	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 597}	2026-10-20 12:20:55.481733-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_automation_state_1min	1012	t	00:01:00	00:00:00	-1	00:01:00	{"end_offset": "00:01:00", "start_offset": "7 days", "mat_hypertable_id": 598}	2026-10-20 12:19:17.643893-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_automation_state_5min	1013	t	00:05:00	00:00:00	-1	00:05:00	{"end_offset": "00:05:00", "start_offset": "7 days", "mat_hypertable_id": 599}	2026-10-20 12:21:13.50573-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_measurement_1hour	1014	t	00:05:00	00:01:00	0	00:05:00	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 601, "refresh_newest_first": false, "max_batches_per_execution": 0}	2026-10-20 12:21:20.02163-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_measurement_1day	1015	f	01:00:00	00:01:00	0	01:00:00	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 602, "refresh_newest_first": false, "max_batches_per_execution": 1}	\N	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_light_intensity_1hour	1016	t	00:05:00	00:01:00	0	00:05:00	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 603, "refresh_newest_first": false, "max_batches_per_execution": 0}	2026-10-20 12:21:13.55094-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_light_intensity_1day	1017	f	01:00:00	00:01:00	0	01:00:00	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 604, "refresh_newest_first": false, "max_batches_per_execution": 1}	\N	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_requested_pid_1hour	1018	t	00:05:00	00:01:00	0	00:05:00	{"end_offset": "01:00:00", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 605, "refresh_newest_first": false, "max_batches_per_execution": 0}	2026-10-20 12:21:51.087691-04	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
monitoring_requested_pid_1day	1019	f	01:00:00	00:01:00	0	01:00:00	{"end_offset": "1 day", "start_offset": null, "buckets_per_batch": 1, "mat_hypertable_id": 606, "refresh_newest_first": false, "max_batches_per_execution": 1}	\N	_timescaledb_functions	policy_refresh_continuous_aggregate_check	f	\N	\N
\.


--
-- Data for Name: monitoring_relay_duration_1hour; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_relay_duration_1hour (bucket, location, cluster, channel, device_id, device_name, device_type, on_microseconds, off_microseconds, last_observed_at, source_watermark, active) FROM stdin;
\.


--
-- Data for Name: monitoring_relay_overview_progress; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_relay_overview_progress (id, covered_from, covered_through, dirty_from, dirty_through, claimed_from, claimed_through, dirty_during_claim_from, dirty_generation, last_error) FROM stdin;
t	\N	\N	\N	\N	\N	\N	\N	0	\N
\.


--
-- Data for Name: monitoring_room_photoperiod; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_room_photoperiod (id, "time", location, cluster, phase, mode_id, submode_id, runtime_snapshot_version, source) FROM stdin;
\.


--
-- Data for Name: monitoring_toolkit_snapshot_0008; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.monitoring_toolkit_snapshot_0008 (singleton, extension_preexisting) FROM stdin;
t	f
\.


--
-- Data for Name: notes; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.notes (note_id, location, cluster, mode, content, created_at, updated_at) FROM stdin;
1	Flower Room	main	default	Test note for persistence verification - saved to database	2026-02-06 12:54:27.396722-05	2026-02-06 12:54:27.396722-05
2	Flower Room	main	veg	Start at 250 umols (lower than you think) and let them rise into max 350. maintain moist for first week then big dryback 120+	2026-02-06 12:56:18.682837-05	2026-02-06 15:52:03.569778-05
\.


--
-- Data for Name: pid_adjustments; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.pid_adjustments (id, location, cluster, device_type, parameter, old_value, new_value, adjustment_type, created_at) FROM stdin;
\.


--
-- Data for Name: pid_autotune_state; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.pid_autotune_state (device_type, is_active, started_at, cycles_completed, current_amplitude, current_period, current_ku, current_tu, suggested_kp, suggested_ki, suggested_kd, last_change_reason, last_update, status) FROM stdin;
\.


--
-- Data for Name: pid_parameter_history; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.pid_parameter_history (id, "time", device_type, kp, ki, kd, updated_by, source, change_reason, changed_at, location, cluster, binary_hysteresis) FROM stdin;
\.


--
-- Data for Name: pid_parameters; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.pid_parameters (device_type, kp, ki, kd, updated_at, updated_by, source, control_mode, hysteresis_high, hysteresis_low, location, cluster, binary_hysteresis) FROM stdin;
co2	10	0.01	0	2026-02-12 20:53:36.582171-05	system	config	on_off	1	0.5	Flower Room	main	0.1
heater	2	0.1	0	2026-07-01 17:54:34.204734-04	system	api	on_off	1	0.5	Flower Room	main	0.1
\.


--
-- Data for Name: rack; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.rack (rack_id, room_id, name, created_at) FROM stdin;
5	1	Front Bed	2025-12-15 16:28:02.725697-05
1	1	Back Bed	2025-12-12 17:46:13.156839-05
10	2	Bottom Right	2025-12-19 07:39:54.787624-05
11	2	Bottom Left	2025-12-19 07:39:54.787624-05
12	2	Top	2025-12-19 07:39:54.787624-05
13	2	Clones	2025-12-19 07:39:54.787624-05
\.


--
-- Data for Name: relay_observation; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.relay_observation (observation_id, "time", session_id, channel, observed_state, device_id, device_name, device_type, location, cluster, registry_version, reason) FROM stdin;
\.


--
-- Data for Name: rollout_events; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.rollout_events ("timestamp", system, phase, scope, event_type, event_reason, release_id, gate_snapshot_json, action_json, operator) FROM stdin;
\.


--
-- Data for Name: room; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.room (room_id, name, target_vpd, target_temp, created_at) FROM stdin;
1	Flower Room	\N	\N	2025-12-12 16:38:35.179824-05
2	Veg Room	\N	\N	2025-12-12 16:38:35.179824-05
3	Lab	\N	\N	2025-12-12 16:38:35.179824-05
4	Outside	\N	\N	2025-12-12 16:38:35.179824-05
\.


--
-- Data for Name: room_active_mode; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.room_active_mode (id, location, cluster, mode_id, submode_id, activated_at) FROM stdin;
2	Veg Room	main	1	\N	2026-07-30 15:46:59.697551-04
1	Flower Room	main	1	\N	2026-10-03 18:31:22.752299-04
\.


--
-- Data for Name: room_light_schedule; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.room_light_schedule (id, room_name, lights_on, lights_off, effective_from, is_active, created_at) FROM stdin;
2	Veg Room	16:00:00	10:00:00	2026-01-12	t	2026-01-12 13:15:31.685765-05
1	Flower Room	17:00:00	11:00:00	2026-01-27	t	2026-01-12 13:15:31.685765-05
\.


--
-- Data for Name: room_modes; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.room_modes (id, name, description, photoperiod_hours, is_constant, created_at) FROM stdin;
1	veg	Vegetative growth - 18/6 photoperiod	18	f	2026-01-15 09:26:37.406992-05
2	flower	Flowering - 12/12 photoperiod	12	f	2026-01-15 09:26:37.406992-05
3	drying	Drying - 24h constant conditions	0	t	2026-01-15 09:26:37.406992-05
4	sleep	Sleep mode - minimal energy	0	t	2026-01-15 09:26:37.406992-05
\.


--
-- Data for Name: rules; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.rules (id, name, enabled, location, cluster, condition_sensor, condition_operator, condition_value, action_device, action_state, priority, schedule_id, created_at) FROM stdin;
\.


--
-- Data for Name: schedules; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.schedules (id, name, location, cluster, device_name, day_of_week, start_time, end_time, enabled, created_at, mode, target_intensity, ramp_up_duration, ramp_down_duration, pre_day_duration, pre_night_duration, updated_at) FROM stdin;
\.


--
-- Data for Name: schema_migration; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.schema_migration (version, sha256, downtime, applied_at) FROM stdin;
0001_alembic_stamp_side_branch	625a94e9ef7d84d9da5aac0b633755cd81d325fd6d43daadd4e245be951daa46	f	2026-10-19 16:47:30.188453-04
0002_control_history_hypertable	c7524398948f9ce8b2a745f524af21da996d0e78f15e9344ca85a876268ed5d9	t	2026-10-19 18:39:50.558497-04
0003_drop_actuator_events	6fed4e1455580b97e353749582ccbde756bac1f17502691b60b863b950d1db00	f	2026-10-19 18:39:50.814823-04
0004_index_cleanup	948eb4e57b235723a5611703bef2c3c87a528e3e4a74e77ba807549e263fa604	f	2026-10-19 18:40:11.26174-04
0005_drop_legacy_measurement_caggs	4f00705fa8820cf764d610e98c904066d633084eb61ab26b7558c3ff2ce6871a	f	2026-10-19 18:40:11.478161-04
0006_monitoring_gate_auto_clear	16dc4fad4ce6cb905e06914d9c1508594558525884f4b5e0d76e71dcc402651e	f	2026-10-19 18:40:11.694227-04
0007_time_column_rename	2a2f07eef574d7886163e56ef1e732b08f36873f45db3d4ac1301c770d4e5890	t	2026-10-20 12:19:12.500569-04
0008_requested_pid_time_weighted	5eaefb40332ac370a68940674e1c5a02e105e19c42930b0b2f59b2c334462cf6	f	2026-10-20 16:41:59.164344-04
0009_service_setting	eec72d756a935a2046de2d7c3d9b500956b488ae9cc83a636c687236280d098b	f	2026-10-20 16:53:17.230183-04
0010_sensor_identity	76122a1e9fc23c22874ff7bf191ace3a366ff8e37263e59132a9cb07ec6ab169	f	2026-10-20 16:59:04.084904-04
0011_db_hardening	b7c06b398947fe3d6da1866db86bf092f47f905d9d7717530c2555d806184f10	f	2026-10-20 21:10:17.767797-04
0012_mode_transition_history	1eeeaf28a7aac4cf3379923dac686f8383bd7edabefcde5113d060701abdb41a	f	2026-10-21 14:11:56.254617-04
\.


--
-- Data for Name: sensor; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.sensor (sensor_id, device_id, name, unit, data_type, channel, calibration_offset, created_at) FROM stdin;
1	1	secondary_temp_b	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
2	1	vpd_b	kPa	pressure_deficit	\N	0	2025-12-12 16:38:35.179824-05
3	2	wet_bulb_f	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
4	2	dry_bulb_f	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
5	1	pressure_b	hPa	pressure	\N	0	2025-12-12 16:38:35.179824-05
6	1	wet_bulb_b	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
7	1	secondary_rh_b	%	humidity	\N	0	2025-12-12 16:38:35.179824-05
8	1	rh_b	%	humidity	\N	0	2025-12-12 16:38:35.179824-05
9	2	secondary_temp_f	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
10	1	dry_bulb_b	°C	temperature	\N	0	2025-12-12 16:38:35.179824-05
11	2	co2_f	ppm	gas	\N	0	2025-12-12 16:38:35.179824-05
12	2	secondary_rh_f	%	humidity	\N	0	2025-12-12 16:38:35.179824-05
13	1	co2_b	ppm	gas	\N	0	2025-12-12 16:38:35.179824-05
14	3	soil_sensor_front_bed_temperature	°C	temperature	\N	0	2025-12-15 16:28:02.742251-05
15	3	soil_sensor_front_bed_humidity	%	humidity	\N	0	2025-12-15 16:28:02.742251-05
16	3	soil_sensor_front_bed_ec	µS/cm	electrical_conductivity	\N	0	2025-12-15 16:28:02.742251-05
17	3	soil_sensor_front_bed_ph	pH	ph	\N	0	2025-12-15 16:28:02.742251-05
18	4	outside_temp	°C	temperature	\N	0	2026-01-21 16:03:42.54045-05
19	4	outside_rh	%	humidity	\N	0	2026-01-21 16:03:42.54045-05
20	4	outside_pressure	hPa	pressure	\N	0	2026-01-21 16:03:42.54045-05
21	4	outside_wind_speed	m/s	wind_speed	\N	0	2026-01-21 16:03:42.54045-05
22	4	outside_wind_direction	degrees	wind_direction	\N	0	2026-01-21 16:03:42.54045-05
23	4	outside_precipitation	mm	precipitation	\N	0	2026-01-21 16:03:42.54045-05
24	5	outside_temp	°C	temperature	\N	0	2026-01-21 16:09:46.793158-05
25	5	outside_rh	%	humidity	\N	0	2026-01-21 16:09:46.793158-05
26	5	outside_pressure	hPa	pressure	\N	0	2026-01-21 16:09:46.793158-05
27	5	outside_wind_speed	m/s	wind_speed	\N	0	2026-01-21 16:09:46.793158-05
28	5	outside_wind_direction	degrees	wind_direction	\N	0	2026-01-21 16:09:46.793158-05
29	5	outside_precipitation	mm	precipitation	\N	0	2026-01-21 16:09:46.793158-05
30	6	soil_sensor_back_bed_temperature	°C	temperature	\N	0	2026-01-27 17:58:30.323967-05
31	6	soil_sensor_back_bed_humidity	%	humidity	\N	0	2026-01-27 17:58:30.323967-05
32	6	soil_sensor_back_bed_ec	µS/cm	electrical_conductivity	\N	0	2026-01-27 17:58:30.323967-05
33	6	soil_sensor_back_bed_ph	pH	ph	\N	0	2026-01-27 17:58:30.323967-05
34	3	soil_sensor_1_temperature	°C	temperature	\N	0	2026-01-27 17:58:30.344865-05
35	3	soil_sensor_1_humidity	%	humidity	\N	0	2026-01-27 17:58:30.344865-05
36	3	soil_sensor_1_ec	µS/cm	electrical_conductivity	\N	0	2026-01-27 17:58:30.344865-05
37	3	soil_sensor_1_ph	pH	ph	\N	0	2026-01-27 17:58:30.344865-05
38	7	outside_temp	°C	temperature	\N	0	2026-01-30 21:44:51.821531-05
39	7	outside_rh	%	humidity	\N	0	2026-01-30 21:44:51.821531-05
40	7	outside_pressure	hPa	pressure	\N	0	2026-01-30 21:44:51.821531-05
41	7	outside_wind_speed	m/s	wind_speed	\N	0	2026-01-30 21:44:51.821531-05
42	7	outside_wind_direction	degrees	wind_direction	\N	0	2026-01-30 21:44:51.821531-05
43	7	outside_precipitation	mm	precipitation	\N	0	2026-01-30 21:44:51.821531-05
44	8	outside_temp	°C	temperature	\N	0	2026-03-03 18:10:58.137291-05
45	8	outside_rh	%	humidity	\N	0	2026-03-03 18:10:58.137291-05
46	8	outside_pressure	hPa	pressure	\N	0	2026-03-03 18:10:58.137291-05
47	8	outside_wind_speed	m/s	wind_speed	\N	0	2026-03-03 18:10:58.137291-05
48	8	outside_wind_direction	degrees	wind_direction	\N	0	2026-03-03 18:10:58.137291-05
49	8	outside_precipitation	mm	precipitation	\N	0	2026-03-03 18:10:58.137291-05
50	2	rh_f	%	humidity	\N	0	2026-05-09 14:15:06.166143-04
51	2	vpd_f	kPa	pressure_deficit	\N	0	2026-05-09 14:15:06.166143-04
52	1	rh	%	humidity	\N	0	2026-09-21 14:53:58.251337-04
53	1	dry_bulb	°C	temperature	\N	0	2026-09-21 14:53:58.260168-04
54	1	vpd	kPa	vpd	\N	0	2026-09-21 14:53:58.263983-04
55	1	wet_bulb	°C	temperature	\N	0	2026-09-21 14:53:58.265435-04
\.


--
-- Data for Name: sensor_address_history; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.sensor_address_history (registry_id, bus, hardware_address, valid_from, valid_to) FROM stdin;
2	can	2	2025-12-12 16:38:35.179824-05	\N
3	can	3	2026-09-21 14:45:51.077708-04	\N
4	rs485	2	2026-01-27 17:58:30.323967-05	\N
5	rs485	1	2025-12-15 16:28:02.742251-05	\N
1	can	1	2025-12-12 16:38:35.179824-05	\N
\.


--
-- Data for Name: sensor_registry; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.sensor_registry (registry_id, bus, hardware_address, device_id, display_name, room_id, rack_id, location_in_room, first_seen, last_seen, updated_at, installation_depth_cm, installation_orientation, blusoak_section, installed_at, calibration_profile, calibrated_at, last_maintained_at, notes, sensor_uid, identity_status, pending_candidate_registry_id, pending_since) FROM stdin;
2	can	2	2	Node 2	1	\N	front	2025-12-12 16:38:35.179824-05	2026-09-21 14:45:51.077708-04	2026-09-21 14:45:51.077708-04	\N	\N	\N	\N	\N	\N	\N	\N	6550057d-1b45-4546-8a08-84e9dbb82ddc	confirmed	\N	\N
3	can	3	\N	Node 3	2	\N	main	2026-09-21 14:45:51.077708-04	2026-09-21 14:45:51.077708-04	2026-09-21 14:45:51.077708-04	\N	\N	\N	\N	\N	\N	\N	\N	1e2d5936-c6d6-47be-adff-8f3a6ed3befe	confirmed	\N	\N
4	rs485	2	6	Soil Sensor - Back Bed	\N	1	\N	2026-01-27 17:58:30.323967-05	2026-10-19 10:42:30.670109-04	2026-09-21 14:45:51.077708-04	\N	\N	\N	\N	\N	\N	\N	\N	8b722b0a-a254-4490-b511-a61a06664a64	confirmed	\N	\N
5	rs485	1	3	Soil Sensor - Front Bed	\N	5	\N	2025-12-15 16:28:02.742251-05	2026-10-19 10:42:30.670109-04	2026-09-21 14:45:51.077708-04	\N	\N	\N	\N	\N	\N	\N	\N	e11fafbd-6904-4eb7-b505-6621d55aaca5	confirmed	\N	\N
1	can	1	1	Node 1	1	\N	back	2025-12-12 16:38:35.179824-05	2026-10-21 13:42:22.950049-04	2026-10-21 13:42:22.950049-04	\N	\N	\N	\N	\N	\N	\N	\N	e7c52fad-aa8e-403d-8ad7-4522b46772bd	confirmed	\N	\N
\.


--
-- Data for Name: service_setting; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.service_setting (service, key, value, updated_at, updated_by) FROM stdin;
automation	update_interval	1	2026-10-20 16:53:17.126027-04	migration-0009
automation	last_good_hold_period	30	2026-10-20 16:53:17.12625-04	migration-0009
automation	binary_hysteresis	0.1	2026-10-20 16:53:17.126271-04	migration-0009
automation	pid_defaults	{"co2": {"kd": 0, "ki": 0.01, "kp": 10}, "fan": {"kd": 0, "ki": 0.02, "kp": 25}, "heater": {"kd": 0, "ki": 0.02, "kp": 25}, "default": {"kd": 0, "ki": 0.01, "kp": 10}}	2026-10-20 16:53:17.126283-04	migration-0009
automation	pid_limits	{"co2": {"kd_max": 5, "kd_min": 0, "ki_max": 0.5, "ki_min": 0, "kp_max": 50, "kp_min": 0}, "fan": {"kd_max": 10, "kd_min": 0, "ki_max": 1, "ki_min": 0, "kp_max": 100, "kp_min": 0}, "heater": {"kd_max": 10, "kd_min": 0, "ki_max": 1, "ki_min": 0, "kp_max": 100, "kp_min": 0}}	2026-10-20 16:53:17.12629-04	migration-0009
automation	safety_limits	{"max_co2": 2000, "min_co2": 400, "max_humidity": 90, "min_humidity": 30, "max_temperature": 35, "min_temperature": 10}	2026-10-20 16:53:17.126298-04	migration-0009
soil	poll_interval_seconds	5	2026-10-20 16:53:17.126305-04	migration-0009
soil	discovery_interval_seconds	30	2026-10-20 16:53:17.126312-04	migration-0009
onewire	poll_interval_seconds	1	2026-10-20 16:53:17.126318-04	migration-0009
weather	station_icao	"CYQB"	2026-10-20 16:53:17.126324-04	migration-0009
weather	poll_interval_seconds	900	2026-10-20 16:53:17.12633-04	migration-0009
weather	api_url	"https://aviationweather.gov/api/data/metar"	2026-10-20 16:53:17.126336-04	migration-0009
weather	room_name	"Outside"	2026-10-20 16:53:17.126342-04	migration-0009
weather	device_name	"Weather Station Québec"	2026-10-20 16:53:17.126349-04	migration-0009
\.


--
-- Data for Name: setpoint_history; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.setpoint_history (id, "timestamp", location, cluster, mode, heating_setpoint, humidity, co2, vpd, cooling_setpoint) FROM stdin;
\.


--
-- Data for Name: setpoints; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.setpoints (id, location, cluster, heating_setpoint, humidity, co2, updated_at, mode, vpd, cooling_setpoint, ramp_in_duration, leaf_delta_day, leaf_delta_night) FROM stdin;
29	Veg Room	main	24	\N	\N	2026-01-02 13:50:26.510296-05	DAY	1.1	28	15	-2	-1
28	Veg Room	main	22	\N	\N	2026-01-02 13:50:26.510296-05	NIGHT	1.1	24	15	-2	-1
83	Veg Room	main	\N	\N	\N	2026-01-02 13:50:26.510296-05	PRE_DAY	\N	\N	0	-2	-1
84	Veg Room	main	\N	\N	\N	2026-01-02 13:50:26.510296-05	PRE_NIGHT	\N	\N	0	-2	-1
35	Flower Room	main	25	\N	\N	2026-01-15 19:35:36.190452-05	PRE_DAY	1.2	28	15	-2	-1
40	Flower Room	main	21	\N	\N	2026-01-15 19:35:36.190452-05	PRE_NIGHT	1.2	24	15	-2	-1
246	Veg Room	main	25	68	1000	2026-01-06 11:35:24.945403-05	\N	\N	\N	\N	-2	-1
30	Flower Room	main	26	\N	800	2026-02-13 19:02:11.686356-05	DAY	1.2	28	15	-2	-1
22	Flower Room	main	24	\N	600	2026-02-13 19:02:11.686356-05	NIGHT	1.2	26	15	-2	-1
245	Flower Room	main	25	65	1200	2026-01-06 11:35:24.638788-05	\N	\N	\N	\N	-2	-1
\.


--
-- Data for Name: soil_bed_profile; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.soil_bed_profile (rack_id, blusoak_total_length_ft, section_count, section_length_ft, standard_carrots_per_section, maxi_carrots_per_section, substrate, updated_at) FROM stdin;
5	24.00	2	12.00	1	1	living_soil	2026-09-23 07:05:38.852391-04
1	24.00	2	12.00	1	1	living_soil	2026-09-23 07:05:38.852391-04
\.


--
-- Data for Name: soil_phase_target; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.soil_phase_target (event_type, lower_mbar, upper_mbar, updated_at) FROM stdin;
bed_veg	100.00	120.00	2026-09-23 07:05:38.852391-04
flower_bulk	100.00	120.00	2026-09-23 07:05:38.852391-04
flower_stretch	80.00	100.00	2026-09-23 07:05:38.852391-04
flower_ripen	80.00	100.00	2026-09-23 07:05:38.852391-04
\.


--
-- Data for Name: water_tank_monitor_config; Type: TABLE DATA; Schema: public; Owner: cea_user
--

COPY public.water_tank_monitor_config (config_id, sensor_id, full_distance_mm, empty_distance_mm, capacity_liters, updated_at) FROM stdin;
1	\N	\N	\N	\N	2026-09-23 07:05:38.852391-04
\.


--
-- Name: bgw_job_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.bgw_job_id_seq', 1060, true);


--
-- Name: chunk_column_stats_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.chunk_column_stats_id_seq', 1, false);


--
-- Name: chunk_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.chunk_id_seq', 1309, true);


--
-- Name: dimension_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.dimension_id_seq', 620, true);


--
-- Name: dimension_slice_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.dimension_slice_id_seq', 1109, true);


--
-- Name: hypertable_id_seq; Type: SEQUENCE SET; Schema: _timescaledb_catalog; Owner: postgres
--

SELECT pg_catalog.setval('_timescaledb_catalog.hypertable_id_seq', 640, true);


--
-- Name: archive_availability_availability_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.archive_availability_availability_id_seq', 1, false);


--
-- Name: archive_scope_override_override_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.archive_scope_override_override_id_seq', 1, false);


--
-- Name: automation_state_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.automation_state_id_seq', 166799555, true);


--
-- Name: calendar_event_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.calendar_event_id_seq', 1, true);


--
-- Name: calendar_mode_application_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.calendar_mode_application_id_seq', 1, false);


--
-- Name: calendar_sync_connection_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.calendar_sync_connection_id_seq', 1, true);


--
-- Name: climate_periods_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.climate_periods_id_seq', 112, true);


--
-- Name: config_versions_version_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.config_versions_version_id_seq', 213, true);


--
-- Name: control_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.control_history_id_seq', 30585112, true);


--
-- Name: crop_batch_batch_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.crop_batch_batch_id_seq', 1, false);


--
-- Name: device_device_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.device_device_id_seq', 8, true);


--
-- Name: device_mappings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.device_mappings_id_seq', 1, false);


--
-- Name: device_registry_device_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.device_registry_device_id_seq', 13, true);


--
-- Name: device_states_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.device_states_id_seq', 340, true);


--
-- Name: flower_submodes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.flower_submodes_id_seq', 3933, true);


--
-- Name: light_presets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.light_presets_id_seq', 1, false);


--
-- Name: light_programs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.light_programs_id_seq', 1, false);


--
-- Name: light_state_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.light_state_history_id_seq', 198, true);


--
-- Name: mode_notes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.mode_notes_id_seq', 1, true);


--
-- Name: mode_parameters_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.mode_parameters_id_seq', 10, true);


--
-- Name: mode_transition_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.mode_transition_history_id_seq', 40, true);


--
-- Name: monitoring_effective_setpoints_ingest_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.monitoring_effective_setpoints_ingest_id_seq', 14469107, true);


--
-- Name: monitoring_photoperiod_coverage_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.monitoring_photoperiod_coverage_id_seq', 126, true);


--
-- Name: monitoring_room_photoperiod_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.monitoring_room_photoperiod_id_seq', 93520, true);


--
-- Name: notes_note_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.notes_note_id_seq', 10, true);


--
-- Name: pid_adjustments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.pid_adjustments_id_seq', 1, false);


--
-- Name: pid_parameter_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.pid_parameter_history_id_seq', 582, true);


--
-- Name: rack_rack_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.rack_rack_id_seq', 14, true);


--
-- Name: relay_observation_observation_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.relay_observation_observation_id_seq', 72255, true);


--
-- Name: room_active_mode_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.room_active_mode_id_seq', 63, true);


--
-- Name: room_light_schedule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.room_light_schedule_id_seq', 2, true);


--
-- Name: room_modes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.room_modes_id_seq', 5233, true);


--
-- Name: room_room_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.room_room_id_seq', 4, true);


--
-- Name: rules_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.rules_id_seq', 1, false);


--
-- Name: schedules_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.schedules_id_seq', 69, true);


--
-- Name: sensor_registry_registry_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.sensor_registry_registry_id_seq', 2757695, true);


--
-- Name: sensor_sensor_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.sensor_sensor_id_seq', 799, true);


--
-- Name: setpoint_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.setpoint_history_id_seq', 667698, true);


--
-- Name: setpoints_id_seq; Type: SEQUENCE SET; Schema: public; Owner: cea_user
--

SELECT pg_catalog.setval('public.setpoints_id_seq', 260, true);


--
-- Name: alembic_version alembic_version_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkey PRIMARY KEY (version_num);


--
-- Name: archive_artifact archive_artifact_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_artifact
    ADD CONSTRAINT archive_artifact_pkey PRIMARY KEY (artifact_id);


--
-- Name: archive_availability archive_availability_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_availability
    ADD CONSTRAINT archive_availability_pkey PRIMARY KEY (availability_id);


--
-- Name: archive_component archive_component_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_component
    ADD CONSTRAINT archive_component_pkey PRIMARY KEY (parent_artifact_id, child_dataset_id);


--
-- Name: archive_dataset archive_dataset_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_dataset
    ADD CONSTRAINT archive_dataset_pkey PRIMARY KEY (dataset_id);


--
-- Name: archive_expiry_cursor archive_expiry_cursor_pk; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_expiry_cursor
    ADD CONSTRAINT archive_expiry_cursor_pk PRIMARY KEY (source_table, room);


--
-- Name: archive_job archive_job_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_job
    ADD CONSTRAINT archive_job_pkey PRIMARY KEY (job_id);


--
-- Name: archive_load archive_load_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_load
    ADD CONSTRAINT archive_load_pkey PRIMARY KEY (load_id);


--
-- Name: archive_scope_override archive_scope_override_no_overlap; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_scope_override
    ADD CONSTRAINT archive_scope_override_no_overlap EXCLUDE USING gist (source_table WITH =, (((source_identity ->> 'sensor_id'::text))::bigint) WITH =, tstzrange(start_at, end_at, '[)'::text) WITH &&);


--
-- Name: archive_scope_override archive_scope_override_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_scope_override
    ADD CONSTRAINT archive_scope_override_pkey PRIMARY KEY (override_id);


--
-- Name: archive_window archive_window_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_window
    ADD CONSTRAINT archive_window_pkey PRIMARY KEY (dataset_id, calendar_revision, event_id);


--
-- Name: automation_state automation_state_time_bounds; Type: CHECK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE public.automation_state
    ADD CONSTRAINT automation_state_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))) NOT VALID;


--
-- Name: calendar_event calendar_event_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_event
    ADD CONSTRAINT calendar_event_pkey PRIMARY KEY (id);


--
-- Name: calendar_mode_application calendar_mode_application_event_id_applied_date_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_mode_application
    ADD CONSTRAINT calendar_mode_application_event_id_applied_date_key UNIQUE (event_id, applied_date);


--
-- Name: calendar_mode_application calendar_mode_application_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_mode_application
    ADD CONSTRAINT calendar_mode_application_pkey PRIMARY KEY (id);


--
-- Name: calendar_room_profile calendar_room_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_room_profile
    ADD CONSTRAINT calendar_room_profile_pkey PRIMARY KEY (location);


--
-- Name: calendar_sync_connection calendar_sync_connection_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_sync_connection
    ADD CONSTRAINT calendar_sync_connection_pkey PRIMARY KEY (id);


--
-- Name: climate_periods climate_periods_location_cluster_mode_id_submode_id_period__key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.climate_periods
    ADD CONSTRAINT climate_periods_location_cluster_mode_id_submode_id_period__key UNIQUE (location, cluster, mode_id, submode_id, period_name);


--
-- Name: climate_periods climate_periods_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.climate_periods
    ADD CONSTRAINT climate_periods_pkey PRIMARY KEY (id);


--
-- Name: config_versions config_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_pkey PRIMARY KEY (version_id);


--
-- Name: control_history control_history_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.control_history
    ADD CONSTRAINT control_history_pkey PRIMARY KEY (id, "time");


--
-- Name: control_history control_history_time_bounds; Type: CHECK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE public.control_history
    ADD CONSTRAINT control_history_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))) NOT VALID;


--
-- Name: crop_batch crop_batch_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.crop_batch
    ADD CONSTRAINT crop_batch_pkey PRIMARY KEY (batch_id);


--
-- Name: device_mappings device_mappings_location_cluster_device_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_mappings
    ADD CONSTRAINT device_mappings_location_cluster_device_name_key UNIQUE (location, cluster, device_name);


--
-- Name: device_mappings device_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_mappings
    ADD CONSTRAINT device_mappings_pkey PRIMARY KEY (id);


--
-- Name: device device_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device
    ADD CONSTRAINT device_pkey PRIMARY KEY (device_id);


--
-- Name: device_registry device_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_registry
    ADD CONSTRAINT device_registry_pkey PRIMARY KEY (device_id);


--
-- Name: device_states device_states_location_cluster_device_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_states
    ADD CONSTRAINT device_states_location_cluster_device_name_key UNIQUE (location, cluster, device_name);


--
-- Name: device_states device_states_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_states
    ADD CONSTRAINT device_states_pkey PRIMARY KEY (id);


--
-- Name: effective_setpoints effective_setpoints_time_bounds; Type: CHECK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE public.effective_setpoints
    ADD CONSTRAINT effective_setpoints_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))) NOT VALID;


--
-- Name: flower_submodes flower_submodes_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.flower_submodes
    ADD CONSTRAINT flower_submodes_name_key UNIQUE (name);


--
-- Name: flower_submodes flower_submodes_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.flower_submodes
    ADD CONSTRAINT flower_submodes_pkey PRIMARY KEY (id);


--
-- Name: grow_lifecycle grow_lifecycle_crop_batch_id_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.grow_lifecycle
    ADD CONSTRAINT grow_lifecycle_crop_batch_id_key UNIQUE (crop_batch_id);


--
-- Name: grow_lifecycle grow_lifecycle_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.grow_lifecycle
    ADD CONSTRAINT grow_lifecycle_pkey PRIMARY KEY (grow_plan_id);


--
-- Name: grow_plan_idempotency grow_plan_idempotency_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.grow_plan_idempotency
    ADD CONSTRAINT grow_plan_idempotency_pkey PRIMARY KEY (idempotency_key);


--
-- Name: light_presets light_presets_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.light_presets
    ADD CONSTRAINT light_presets_pkey PRIMARY KEY (id);


--
-- Name: light_programs light_programs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_programs
    ADD CONSTRAINT light_programs_pkey PRIMARY KEY (id);


--
-- Name: light_state_history light_state_history_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_state_history
    ADD CONSTRAINT light_state_history_pkey PRIMARY KEY (id);


--
-- Name: light_target_intensity light_target_intensity_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_target_intensity
    ADD CONSTRAINT light_target_intensity_pkey PRIMARY KEY (device_id, mode_id);


--
-- Name: measurement measurement_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.measurement
    ADD CONSTRAINT measurement_pkey PRIMARY KEY ("time", sensor_id);


--
-- Name: measurement measurement_time_bounds; Type: CHECK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE public.measurement
    ADD CONSTRAINT measurement_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))) NOT VALID;


--
-- Name: mode_notes mode_notes_location_mode_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_notes
    ADD CONSTRAINT mode_notes_location_mode_key UNIQUE (location, mode);


--
-- Name: mode_notes mode_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_notes
    ADD CONSTRAINT mode_notes_pkey PRIMARY KEY (id);


--
-- Name: mode_parameters mode_parameters_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_parameters
    ADD CONSTRAINT mode_parameters_pkey PRIMARY KEY (id);


--
-- Name: mode_transition_history mode_transition_history_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_transition_history
    ADD CONSTRAINT mode_transition_history_pkey PRIMARY KEY (id);


--
-- Name: monitoring_cagg_backfill_marker monitoring_cagg_backfill_marker_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_cagg_backfill_marker
    ADD CONSTRAINT monitoring_cagg_backfill_marker_pkey PRIMARY KEY (cagg_name);


--
-- Name: monitoring_dependent_view_snapshot_0007 monitoring_dependent_view_snapshot_0007_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_dependent_view_snapshot_0007
    ADD CONSTRAINT monitoring_dependent_view_snapshot_0007_pkey PRIMARY KEY (view_schema, view_name);


--
-- Name: monitoring_index_snapshot_0007 monitoring_index_snapshot_0007_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_index_snapshot_0007
    ADD CONSTRAINT monitoring_index_snapshot_0007_pkey PRIMARY KEY (old_index_name);


--
-- Name: monitoring_job_snapshot_0007 monitoring_job_snapshot_0007_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_job_snapshot_0007
    ADD CONSTRAINT monitoring_job_snapshot_0007_pkey PRIMARY KEY (job_id);


--
-- Name: monitoring_job_snapshot_0008 monitoring_job_snapshot_0008_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_job_snapshot_0008
    ADD CONSTRAINT monitoring_job_snapshot_0008_pkey PRIMARY KEY (job_id);


--
-- Name: monitoring_overview_coverage monitoring_overview_coverage_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_overview_coverage
    ADD CONSTRAINT monitoring_overview_coverage_pkey PRIMARY KEY (model_name);


--
-- Name: monitoring_photoperiod_coverage monitoring_photoperiod_covera_location_cluster_time__key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_photoperiod_coverage
    ADD CONSTRAINT monitoring_photoperiod_covera_location_cluster_time__key UNIQUE (location, cluster, "time", state);


--
-- Name: monitoring_photoperiod_coverage monitoring_photoperiod_coverage_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_photoperiod_coverage
    ADD CONSTRAINT monitoring_photoperiod_coverage_pkey PRIMARY KEY (id);


--
-- Name: monitoring_refresh_policy_snapshot_0007 monitoring_refresh_policy_snapshot_0007_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_refresh_policy_snapshot_0007
    ADD CONSTRAINT monitoring_refresh_policy_snapshot_0007_pkey PRIMARY KEY (view_name);


--
-- Name: monitoring_relay_overview_progress monitoring_relay_overview_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_relay_overview_progress
    ADD CONSTRAINT monitoring_relay_overview_progress_pkey PRIMARY KEY (id);


--
-- Name: monitoring_room_photoperiod monitoring_room_photoperiod_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_room_photoperiod
    ADD CONSTRAINT monitoring_room_photoperiod_pkey PRIMARY KEY (id);


--
-- Name: monitoring_toolkit_snapshot_0008 monitoring_toolkit_snapshot_0008_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_toolkit_snapshot_0008
    ADD CONSTRAINT monitoring_toolkit_snapshot_0008_pkey PRIMARY KEY (singleton);


--
-- Name: notes notes_location_cluster_mode_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_location_cluster_mode_key UNIQUE (location, cluster, mode);


--
-- Name: notes notes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_pkey PRIMARY KEY (note_id);


--
-- Name: pid_adjustments pid_adjustments_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_adjustments
    ADD CONSTRAINT pid_adjustments_pkey PRIMARY KEY (id);


--
-- Name: pid_autotune_state pid_autotune_state_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_autotune_state
    ADD CONSTRAINT pid_autotune_state_pkey PRIMARY KEY (device_type);


--
-- Name: pid_parameter_history pid_parameter_history_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_parameter_history
    ADD CONSTRAINT pid_parameter_history_pkey PRIMARY KEY (id);


--
-- Name: pid_parameters pid_parameters_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.pid_parameters
    ADD CONSTRAINT pid_parameters_pkey PRIMARY KEY (location, cluster, device_type);


--
-- Name: monitoring_relay_duration_1hour pk_monitoring_relay_duration_1hour; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.monitoring_relay_duration_1hour
    ADD CONSTRAINT pk_monitoring_relay_duration_1hour PRIMARY KEY (bucket, location, cluster, channel, device_id, device_name, device_type);


--
-- Name: relay_observation pk_relay_observation; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.relay_observation
    ADD CONSTRAINT pk_relay_observation PRIMARY KEY ("time", observation_id);


--
-- Name: rack rack_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rack
    ADD CONSTRAINT rack_pkey PRIMARY KEY (rack_id);


--
-- Name: rack rack_room_id_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rack
    ADD CONSTRAINT rack_room_id_name_key UNIQUE (room_id, name);


--
-- Name: relay_observation relay_observation_time_bounds; Type: CHECK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE public.relay_observation
    ADD CONSTRAINT relay_observation_time_bounds CHECK ((("time" >= '2020-01-01 00:00:00-05'::timestamp with time zone) AND ("time" <= (now() + '1 day'::interval)))) NOT VALID;


--
-- Name: room_active_mode room_active_mode_location_cluster_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_active_mode
    ADD CONSTRAINT room_active_mode_location_cluster_key UNIQUE (location, cluster);


--
-- Name: room_active_mode room_active_mode_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_active_mode
    ADD CONSTRAINT room_active_mode_pkey PRIMARY KEY (id);


--
-- Name: room_light_schedule room_light_schedule_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_light_schedule
    ADD CONSTRAINT room_light_schedule_pkey PRIMARY KEY (id);


--
-- Name: room_modes room_modes_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_modes
    ADD CONSTRAINT room_modes_name_key UNIQUE (name);


--
-- Name: room_modes room_modes_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_modes
    ADD CONSTRAINT room_modes_pkey PRIMARY KEY (id);


--
-- Name: room room_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room
    ADD CONSTRAINT room_name_key UNIQUE (name);


--
-- Name: room room_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room
    ADD CONSTRAINT room_pkey PRIMARY KEY (room_id);


--
-- Name: rules rules_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rules
    ADD CONSTRAINT rules_pkey PRIMARY KEY (id);


--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (id);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (version);


--
-- Name: sensor_address_history sensor_address_history_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_address_history
    ADD CONSTRAINT sensor_address_history_pkey PRIMARY KEY (registry_id, valid_from);


--
-- Name: sensor sensor_device_id_name_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor
    ADD CONSTRAINT sensor_device_id_name_key UNIQUE (device_id, name);


--
-- Name: sensor sensor_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor
    ADD CONSTRAINT sensor_pkey PRIMARY KEY (sensor_id);


--
-- Name: sensor_registry sensor_registry_device_unique; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_device_unique UNIQUE (device_id);


--
-- Name: sensor_registry sensor_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_pkey PRIMARY KEY (registry_id);


--
-- Name: sensor_registry sensor_registry_sensor_uid_key; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_sensor_uid_key UNIQUE (sensor_uid);


--
-- Name: service_setting service_setting_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.service_setting
    ADD CONSTRAINT service_setting_pkey PRIMARY KEY (service, key);


--
-- Name: setpoint_history setpoint_history_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.setpoint_history
    ADD CONSTRAINT setpoint_history_pkey PRIMARY KEY (id);


--
-- Name: setpoints setpoints_location_cluster_mode_unique; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.setpoints
    ADD CONSTRAINT setpoints_location_cluster_mode_unique UNIQUE NULLS NOT DISTINCT (location, cluster, mode);


--
-- Name: setpoints setpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.setpoints
    ADD CONSTRAINT setpoints_pkey PRIMARY KEY (id);


--
-- Name: soil_bed_profile soil_bed_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.soil_bed_profile
    ADD CONSTRAINT soil_bed_profile_pkey PRIMARY KEY (rack_id);


--
-- Name: soil_phase_target soil_phase_target_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.soil_phase_target
    ADD CONSTRAINT soil_phase_target_pkey PRIMARY KEY (event_type);


--
-- Name: device_registry uq_device_registry_loc_cluster_name; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device_registry
    ADD CONSTRAINT uq_device_registry_loc_cluster_name UNIQUE (location, cluster, device_name);


--
-- Name: water_tank_monitor_config water_tank_monitor_config_pkey; Type: CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.water_tank_monitor_config
    ADD CONSTRAINT water_tank_monitor_config_pkey PRIMARY KEY (config_id);


--
-- Name: _materialized_hypertable_590_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_590_bucket_idx ON _timescaledb_internal._materialized_hypertable_590 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_590_sensor_id_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_590_sensor_id_bucket_idx ON _timescaledb_internal._materialized_hypertable_590 USING btree (sensor_id, bucket DESC);


--
-- Name: _materialized_hypertable_608_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_608_channel_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_channel_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (channel, bucket DESC);


--
-- Name: _materialized_hypertable_608_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_608_device_id_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_device_id_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (device_id, bucket DESC);


--
-- Name: _materialized_hypertable_608_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_608_device_type_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_device_type_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (device_type, bucket DESC);


--
-- Name: _materialized_hypertable_608_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_608_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_608 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_613_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_613_bucket_idx ON _timescaledb_internal._materialized_hypertable_613 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_613_sensor_id_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_613_sensor_id_bucket_idx ON _timescaledb_internal._materialized_hypertable_613 USING btree (sensor_id, bucket DESC);


--
-- Name: _materialized_hypertable_614_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_614_bucket_idx ON _timescaledb_internal._materialized_hypertable_614 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_614_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_614_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_614 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_614_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_614_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_614 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_614_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_614_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_614 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_614_mode_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_614_mode_bucket_idx ON _timescaledb_internal._materialized_hypertable_614 USING btree (mode, bucket DESC);


--
-- Name: _materialized_hypertable_615_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_615_bucket_idx ON _timescaledb_internal._materialized_hypertable_615 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_615_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_615_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_615 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_615_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_615_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_615 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_615_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_615_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_615 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_615_mode_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_615_mode_bucket_idx ON _timescaledb_internal._materialized_hypertable_615 USING btree (mode, bucket DESC);


--
-- Name: _materialized_hypertable_616_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_616_bucket_idx ON _timescaledb_internal._materialized_hypertable_616 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_616_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_616_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_616 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_616_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_616_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_616 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_616_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_616_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_616 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_617_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_617_bucket_idx ON _timescaledb_internal._materialized_hypertable_617 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_617_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_617_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_617 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_617_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_617_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_617 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_617_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_617_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_617 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_618_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_618_bucket_idx ON _timescaledb_internal._materialized_hypertable_618 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_618_sensor_id_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_618_sensor_id_bucket_idx ON _timescaledb_internal._materialized_hypertable_618 USING btree (sensor_id, bucket DESC);


--
-- Name: _materialized_hypertable_619_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_619_bucket_idx ON _timescaledb_internal._materialized_hypertable_619 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_619_sensor_id_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_619_sensor_id_bucket_idx ON _timescaledb_internal._materialized_hypertable_619 USING btree (sensor_id, bucket DESC);


--
-- Name: _materialized_hypertable_620_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_620_bucket_idx ON _timescaledb_internal._materialized_hypertable_620 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_620_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_620_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_620 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_620_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_620_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_620 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_620_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_620_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_620 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_621_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_621_bucket_idx ON _timescaledb_internal._materialized_hypertable_621 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_621_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_621_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_621 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_621_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_621_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_621 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_621_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_621_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_621 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_637_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_637_bucket_idx ON _timescaledb_internal._materialized_hypertable_637 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_637_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_637_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_637 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_637_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_637_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_637 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_637_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_637_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_637 USING btree (location, bucket DESC);


--
-- Name: _materialized_hypertable_638_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_638_bucket_idx ON _timescaledb_internal._materialized_hypertable_638 USING btree (bucket DESC);


--
-- Name: _materialized_hypertable_638_cluster_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_638_cluster_bucket_idx ON _timescaledb_internal._materialized_hypertable_638 USING btree (cluster, bucket DESC);


--
-- Name: _materialized_hypertable_638_device_name_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_638_device_name_bucket_idx ON _timescaledb_internal._materialized_hypertable_638 USING btree (device_name, bucket DESC);


--
-- Name: _materialized_hypertable_638_location_bucket_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX _materialized_hypertable_638_location_bucket_idx ON _timescaledb_internal._materialized_hypertable_638 USING btree (location, bucket DESC);


--
-- Name: compress_hyper_634_1276_chunk__ts_meta_v2_first_bucket__ts__idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX compress_hyper_634_1276_chunk__ts_meta_v2_first_bucket__ts__idx ON _timescaledb_internal.compress_hyper_634_1276_chunk USING btree (_ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, _ts_meta_v2_first_location, _ts_meta_v2_last_location, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name);


--
-- Name: compress_hyper_634_1284_chunk__ts_meta_v2_first_bucket__ts__idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX compress_hyper_634_1284_chunk__ts_meta_v2_first_bucket__ts__idx ON _timescaledb_internal.compress_hyper_634_1284_chunk USING btree (_ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, _ts_meta_v2_first_location, _ts_meta_v2_last_location, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name);


--
-- Name: compress_hyper_635_1280_chunk__ts_meta_v2_first_bucket__ts__idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX compress_hyper_635_1280_chunk__ts_meta_v2_first_bucket__ts__idx ON _timescaledb_internal.compress_hyper_635_1280_chunk USING btree (_ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, _ts_meta_v2_first_location, _ts_meta_v2_last_location, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name);


--
-- Name: compress_hyper_635_1285_chunk__ts_meta_v2_first_bucket__ts__idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX compress_hyper_635_1285_chunk__ts_meta_v2_first_bucket__ts__idx ON _timescaledb_internal.compress_hyper_635_1285_chunk USING btree (_ts_meta_v2_first_bucket, _ts_meta_v2_last_bucket, _ts_meta_v2_first_location, _ts_meta_v2_last_location, _ts_meta_v2_first_cluster, _ts_meta_v2_last_cluster, _ts_meta_v2_first_device_name, _ts_meta_v2_last_device_name);


--
-- Name: monitoring_light_intensity_1day_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_light_intensity_1day_group_idx ON _timescaledb_internal._materialized_hypertable_621 USING btree (location, cluster, device_name, bucket);


--
-- Name: monitoring_light_intensity_1hour_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_light_intensity_1hour_group_idx ON _timescaledb_internal._materialized_hypertable_620 USING btree (location, cluster, device_name, bucket);


--
-- Name: monitoring_measurement_1day_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_measurement_1day_group_idx ON _timescaledb_internal._materialized_hypertable_619 USING btree (sensor_id, bucket);


--
-- Name: monitoring_measurement_1hour_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_measurement_1hour_group_idx ON _timescaledb_internal._materialized_hypertable_618 USING btree (sensor_id, bucket);


--
-- Name: monitoring_requested_pid_1day_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_requested_pid_1day_group_idx ON _timescaledb_internal._materialized_hypertable_638 USING btree (location, cluster, device_name, bucket);


--
-- Name: monitoring_requested_pid_1hour_group_idx; Type: INDEX; Schema: _timescaledb_internal; Owner: cea_user
--

CREATE INDEX monitoring_requested_pid_1hour_group_idx ON _timescaledb_internal._materialized_hypertable_637 USING btree (location, cluster, device_name, bucket);


--
-- Name: archive_artifact_dataset_revision_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_artifact_dataset_revision_idx ON public.archive_artifact USING btree (dataset_id, revision, state);


--
-- Name: archive_artifact_superseded_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_artifact_superseded_idx ON public.archive_artifact USING btree (dataset_id, revision) WHERE (state = 'superseded'::text);


--
-- Name: archive_artifact_verified_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_artifact_verified_unique ON public.archive_artifact USING btree (dataset_id, revision) WHERE (state = 'verified'::text);


--
-- Name: archive_availability_lookup_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_availability_lookup_idx ON public.archive_availability USING btree (source_table, state, start_at, end_at);


--
-- Name: archive_availability_scope_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_availability_scope_unique ON public.archive_availability USING btree (dataset_id, revision, source_table, room, start_at, end_at) NULLS NOT DISTINCT;


--
-- Name: archive_dataset_grow_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_dataset_grow_unique ON public.archive_dataset USING btree (grow_plan_id) WHERE (kind = 'grow'::text);


--
-- Name: archive_dataset_room_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_dataset_room_idx ON public.archive_dataset USING btree (room, kind);


--
-- Name: archive_dataset_state_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_dataset_state_idx ON public.archive_dataset USING btree (state);


--
-- Name: archive_dataset_veg_quarter_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_dataset_veg_quarter_unique ON public.archive_dataset USING btree (calendar_year, quarter) WHERE (kind = 'veg_quarter'::text);


--
-- Name: archive_dataset_veg_year_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_dataset_veg_year_unique ON public.archive_dataset USING btree (calendar_year) WHERE (kind = 'veg_year'::text);


--
-- Name: archive_expiry_cursor_frontier_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_expiry_cursor_frontier_idx ON public.archive_expiry_cursor USING btree (processed_before, source_table, room);


--
-- Name: archive_job_active_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX archive_job_active_unique ON public.archive_job USING btree (dataset_id, kind, requested_revision) WHERE (state = ANY (ARRAY['pending'::text, 'running'::text, 'blocked'::text]));


--
-- Name: archive_job_due_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_job_due_idx ON public.archive_job USING btree (next_attempt_at) WHERE (state = ANY (ARRAY['pending'::text, 'running'::text]));


--
-- Name: archive_load_artifact_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_load_artifact_idx ON public.archive_load USING btree (artifact_id) WHERE (artifact_id IS NOT NULL);


--
-- Name: archive_load_expiry_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_load_expiry_idx ON public.archive_load USING btree (expires_at);


--
-- Name: archive_load_staging_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_load_staging_idx ON public.archive_load USING btree (staging_path) WHERE (staging_path IS NOT NULL);


--
-- Name: archive_scope_override_identity_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_scope_override_identity_idx ON public.archive_scope_override USING btree (source_table, (((source_identity ->> 'sensor_id'::text))::bigint), start_at, end_at);


--
-- Name: archive_window_dataset_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_window_dataset_idx ON public.archive_window USING btree (dataset_id, calendar_revision);


--
-- Name: archive_window_scope_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX archive_window_scope_idx ON public.archive_window USING btree (room, start_at, end_at);


--
-- Name: automation_state_timestamp_id_uidx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX automation_state_timestamp_id_uidx ON public.automation_state USING btree ("time", id);


--
-- Name: calendar_event_sync_due_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX calendar_event_sync_due_idx ON public.calendar_event USING btree (sync_status, sync_next_attempt_at, sync_lease_until) WHERE (sync_status = ANY (ARRAY['pending_push'::text, 'pending_delete'::text]));


--
-- Name: control_history_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX control_history_time_idx ON public.control_history USING btree ("time" DESC);


--
-- Name: effective_setpoints_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX effective_setpoints_time_idx ON public.effective_setpoints USING btree ("time" DESC);


--
-- Name: idx_automation_state_device; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_automation_state_device ON public.automation_state USING btree (location, cluster, device_name);


--
-- Name: idx_automation_state_location; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_automation_state_location ON public.automation_state USING btree (location, cluster);


--
-- Name: idx_automation_state_lookup; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_automation_state_lookup ON public.automation_state USING btree (location, cluster, device_name, "time" DESC);


--
-- Name: idx_automation_state_timestamp; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_automation_state_timestamp ON public.automation_state USING btree ("time" DESC);


--
-- Name: idx_calendar_event_active; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_calendar_event_active ON public.calendar_event USING btree (location, start_date, end_date) WHERE (deleted_at IS NULL);


--
-- Name: idx_calendar_event_external; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX idx_calendar_event_external ON public.calendar_event USING btree (external_provider, external_calendar_id, external_event_id) WHERE ((external_event_id IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: idx_calendar_event_grow_plan; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_calendar_event_grow_plan ON public.calendar_event USING btree (grow_plan_id) WHERE ((deleted_at IS NULL) AND (grow_plan_id IS NOT NULL));


--
-- Name: idx_calendar_event_ical_uid; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX idx_calendar_event_ical_uid ON public.calendar_event USING btree (ical_uid);


--
-- Name: idx_climate_periods_location_cluster; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_climate_periods_location_cluster ON public.climate_periods USING btree (location, cluster);


--
-- Name: idx_climate_periods_mode; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_climate_periods_mode ON public.climate_periods USING btree (mode_id, submode_id);


--
-- Name: idx_climate_periods_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_climate_periods_time ON public.climate_periods USING btree (start_time, end_time);


--
-- Name: idx_config_versions_timestamp; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_config_versions_timestamp ON public.config_versions USING btree ("timestamp" DESC);


--
-- Name: idx_config_versions_type; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_config_versions_type ON public.config_versions USING btree (config_type);


--
-- Name: idx_control_history_device_channel_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_control_history_device_channel_time ON public.control_history USING btree (location, cluster, device_name, channel, "time" DESC);


--
-- Name: idx_control_history_location; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_control_history_location ON public.control_history USING btree (location, cluster);


--
-- Name: idx_control_history_manual_expires; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_control_history_manual_expires ON public.control_history USING btree (manual_expires_at) WHERE ((manual_expires_at IS NOT NULL) AND (mode = 'manual'::text));


--
-- Name: idx_device_mappings_location_cluster; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_device_mappings_location_cluster ON public.device_mappings USING btree (location, cluster);


--
-- Name: idx_device_registry_light; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_device_registry_light ON public.device_registry USING btree (location, device_type) WHERE (device_type = 'light'::text);


--
-- Name: idx_device_registry_location; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_device_registry_location ON public.device_registry USING btree (location);


--
-- Name: idx_device_registry_relay; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_device_registry_relay ON public.device_registry USING btree (channel) WHERE ((channel IS NOT NULL) AND (device_type = 'light'::text));


--
-- Name: idx_device_states_location_cluster; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_device_states_location_cluster ON public.device_states USING btree (location, cluster);


--
-- Name: idx_effective_setpoints_device; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_effective_setpoints_device ON public.effective_setpoints USING btree (location, cluster, device_name, "time" DESC);


--
-- Name: idx_effective_setpoints_device_mode_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_effective_setpoints_device_mode_time ON public.effective_setpoints USING btree (location, cluster, device_name, mode, "time" DESC);


--
-- Name: idx_light_programs_device; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_programs_device ON public.light_programs USING btree (device_id, enabled);


--
-- Name: idx_light_programs_lookup; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_programs_lookup ON public.light_programs USING btree (location, cluster, enabled);


--
-- Name: idx_light_state_history_device; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_state_history_device ON public.light_state_history USING btree (location, cluster, device_name, triggered_at DESC);


--
-- Name: idx_light_state_history_loc_clus_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_state_history_loc_clus_time ON public.light_state_history USING btree (location, cluster, triggered_at DESC);


--
-- Name: idx_light_target_intensity_device; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_target_intensity_device ON public.light_target_intensity USING btree (device_id);


--
-- Name: idx_light_target_intensity_mode; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_light_target_intensity_mode ON public.light_target_intensity USING btree (mode_id);


--
-- Name: idx_measurement_sensor_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_measurement_sensor_time ON public.measurement USING btree (sensor_id, "time" DESC);


--
-- Name: idx_mode_parameters_lookup; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_mode_parameters_lookup ON public.mode_parameters USING btree (location, cluster, mode_id, submode_id);


--
-- Name: idx_mode_transition_history_loc_clus_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_mode_transition_history_loc_clus_time ON public.mode_transition_history USING btree (location, cluster, "time" DESC);


--
-- Name: idx_pid_adj_location; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_pid_adj_location ON public.pid_adjustments USING btree (location, cluster);


--
-- Name: idx_pid_adj_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_pid_adj_time ON public.pid_adjustments USING btree (created_at DESC);


--
-- Name: idx_pid_parameter_history_device_type; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_pid_parameter_history_device_type ON public.pid_parameter_history USING btree (device_type, "time" DESC);


--
-- Name: idx_pid_parameter_history_room_type_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_pid_parameter_history_room_type_time ON public.pid_parameter_history USING btree (location, cluster, device_type, "time" DESC);


--
-- Name: idx_relay_observation_channel_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_relay_observation_channel_time ON public.relay_observation USING btree (channel, "time", observation_id);


--
-- Name: idx_relay_observation_location_cluster_device_time; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_relay_observation_location_cluster_device_time ON public.relay_observation USING btree (location, cluster, device_id, "time", observation_id);


--
-- Name: idx_rollout_events_system_ts; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_rollout_events_system_ts ON public.rollout_events USING btree (system, "timestamp" DESC);


--
-- Name: idx_rollout_events_type_ts; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_rollout_events_type_ts ON public.rollout_events USING btree (event_type, "timestamp" DESC);


--
-- Name: idx_schedules_one_climate_per_loc_cluster; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX idx_schedules_one_climate_per_loc_cluster ON public.schedules USING btree (location, cluster) WHERE (device_name = 'climate'::text);


--
-- Name: idx_sensor_name; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_sensor_name ON public.sensor USING btree (name);


--
-- Name: idx_setpoint_history_location; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_setpoint_history_location ON public.setpoint_history USING btree (location, cluster, mode);


--
-- Name: idx_setpoint_history_timestamp; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_setpoint_history_timestamp ON public.setpoint_history USING btree ("timestamp" DESC);


--
-- Name: idx_setpoints_loc_cluster_mode_updated_at; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX idx_setpoints_loc_cluster_mode_updated_at ON public.setpoints USING btree (location, cluster, mode, updated_at DESC);


--
-- Name: measurement_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX measurement_time_idx ON public.measurement USING btree ("time" DESC);


--
-- Name: mode_params_unique_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX mode_params_unique_idx ON public.mode_parameters USING btree (location, cluster, mode_id, COALESCE(submode_id, '-1'::integer));


--
-- Name: monitoring_automation_state_id_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_automation_state_id_idx ON public.automation_state USING btree (id);


--
-- Name: monitoring_effective_setpoints_ingest_id_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_effective_setpoints_ingest_id_idx ON public.effective_setpoints USING btree (monitoring_ingest_id) WHERE (monitoring_ingest_id IS NOT NULL);


--
-- Name: monitoring_effective_setpoints_ingested_at_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_effective_setpoints_ingested_at_idx ON public.effective_setpoints USING btree (ingested_at) WHERE (ingested_at IS NOT NULL);


--
-- Name: monitoring_photoperiod_coverage_room_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_photoperiod_coverage_room_time_idx ON public.monitoring_photoperiod_coverage USING btree (location, cluster, "time" DESC, id DESC);


--
-- Name: monitoring_relay_duration_1hour_bucket_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_relay_duration_1hour_bucket_idx ON public.monitoring_relay_duration_1hour USING btree (bucket DESC);


--
-- Name: monitoring_room_photoperiod_room_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_room_photoperiod_room_time_idx ON public.monitoring_room_photoperiod USING btree (location, cluster, "time" DESC);


--
-- Name: monitoring_room_photoperiod_snapshot_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX monitoring_room_photoperiod_snapshot_idx ON public.monitoring_room_photoperiod USING btree (runtime_snapshot_version);


--
-- Name: relay_observation_time_idx; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE INDEX relay_observation_time_idx ON public.relay_observation USING btree ("time" DESC);


--
-- Name: sensor_registry_bus_address_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX sensor_registry_bus_address_unique ON public.sensor_registry USING btree (bus, hardware_address) WHERE (hardware_address IS NOT NULL);


--
-- Name: sensor_registry_can_position_unique; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX sensor_registry_can_position_unique ON public.sensor_registry USING btree (room_id, location_in_room) WHERE ((bus = 'can'::text) AND (room_id IS NOT NULL) AND (location_in_room IS NOT NULL));


--
-- Name: uq_device_registry_dfr_channel; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX uq_device_registry_dfr_channel ON public.device_registry USING btree (dimming_board_id, dimming_channel) WHERE (dimming_board_id IS NOT NULL);


--
-- Name: uq_device_registry_loc_room_index; Type: INDEX; Schema: public; Owner: cea_user
--

CREATE UNIQUE INDEX uq_device_registry_loc_room_index ON public.device_registry USING btree (location, per_room_index) WHERE (device_type = 'light'::text);


--
-- Name: sensor clear_deleted_water_tank_monitor_sensor_trg; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER clear_deleted_water_tank_monitor_sensor_trg BEFORE DELETE ON public.sensor FOR EACH ROW EXECUTE FUNCTION public.clear_deleted_water_tank_monitor_sensor();


--
-- Name: grow_lifecycle grow_lifecycle_write_once; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER grow_lifecycle_write_once BEFORE UPDATE ON public.grow_lifecycle FOR EACH ROW EXECUTE FUNCTION public.grow_lifecycle_enforce_write_once();


--
-- Name: relay_observation monitoring_relay_overview_progress_insert_dirty; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER monitoring_relay_overview_progress_insert_dirty AFTER INSERT ON public.relay_observation REFERENCING NEW TABLE AS relay_observation_inserted FOR EACH STATEMENT EXECUTE FUNCTION public.monitoring_relay_overview_progress_dirty();


--
-- Name: relay_observation monitoring_relay_overview_progress_update_dirty; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER monitoring_relay_overview_progress_update_dirty AFTER UPDATE ON public.relay_observation REFERENCING OLD TABLE AS relay_observation_update_old NEW TABLE AS relay_observation_update_new FOR EACH STATEMENT EXECUTE FUNCTION public.monitoring_relay_overview_progress_dirty();


--
-- Name: calendar_event sj_calendar_bind_pending; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER sj_calendar_bind_pending BEFORE INSERT OR UPDATE ON public.calendar_event FOR EACH ROW EXECUTE FUNCTION public.sj_calendar_bind_pending();


--
-- Name: water_tank_monitor_config water_tank_monitor_config_sensor_unit_trg; Type: TRIGGER; Schema: public; Owner: cea_user
--

CREATE TRIGGER water_tank_monitor_config_sensor_unit_trg BEFORE INSERT OR UPDATE OF sensor_id ON public.water_tank_monitor_config FOR EACH ROW EXECUTE FUNCTION public.validate_water_tank_monitor_config_sensor();


--
-- Name: archive_artifact archive_artifact_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_artifact
    ADD CONSTRAINT archive_artifact_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE;


--
-- Name: archive_availability archive_availability_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_availability
    ADD CONSTRAINT archive_availability_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE;


--
-- Name: archive_component archive_component_child_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_component
    ADD CONSTRAINT archive_component_child_dataset_id_fkey FOREIGN KEY (child_dataset_id) REFERENCES public.archive_dataset(dataset_id);


--
-- Name: archive_component archive_component_parent_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_component
    ADD CONSTRAINT archive_component_parent_artifact_id_fkey FOREIGN KEY (parent_artifact_id) REFERENCES public.archive_artifact(artifact_id) ON DELETE CASCADE;


--
-- Name: archive_dataset archive_dataset_grow_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_dataset
    ADD CONSTRAINT archive_dataset_grow_plan_id_fkey FOREIGN KEY (grow_plan_id) REFERENCES public.grow_lifecycle(grow_plan_id);


--
-- Name: archive_job archive_job_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_job
    ADD CONSTRAINT archive_job_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE;


--
-- Name: archive_load archive_load_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_load
    ADD CONSTRAINT archive_load_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.archive_artifact(artifact_id);


--
-- Name: archive_load archive_load_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_load
    ADD CONSTRAINT archive_load_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE;


--
-- Name: archive_window archive_window_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.archive_window
    ADD CONSTRAINT archive_window_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE;


--
-- Name: calendar_event calendar_event_crop_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_event
    ADD CONSTRAINT calendar_event_crop_batch_id_fkey FOREIGN KEY (crop_batch_id) REFERENCES public.crop_batch(batch_id) ON DELETE SET NULL;


--
-- Name: calendar_event calendar_event_recurrence_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_event
    ADD CONSTRAINT calendar_event_recurrence_parent_id_fkey FOREIGN KEY (recurrence_parent_id) REFERENCES public.calendar_event(id) ON DELETE SET NULL;


--
-- Name: calendar_event calendar_event_sync_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_event
    ADD CONSTRAINT calendar_event_sync_connection_fk FOREIGN KEY (sync_connection_id) REFERENCES public.calendar_sync_connection(id);


--
-- Name: calendar_mode_application calendar_mode_application_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.calendar_mode_application
    ADD CONSTRAINT calendar_mode_application_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.calendar_event(id) ON DELETE CASCADE;


--
-- Name: climate_periods climate_periods_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.climate_periods
    ADD CONSTRAINT climate_periods_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id);


--
-- Name: climate_periods climate_periods_submode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.climate_periods
    ADD CONSTRAINT climate_periods_submode_id_fkey FOREIGN KEY (submode_id) REFERENCES public.flower_submodes(id);


--
-- Name: crop_batch crop_batch_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.crop_batch
    ADD CONSTRAINT crop_batch_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.room(room_id) ON DELETE CASCADE;


--
-- Name: device device_rack_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.device
    ADD CONSTRAINT device_rack_id_fkey FOREIGN KEY (rack_id) REFERENCES public.rack(rack_id) ON DELETE SET NULL;


--
-- Name: grow_lifecycle grow_lifecycle_crop_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.grow_lifecycle
    ADD CONSTRAINT grow_lifecycle_crop_batch_id_fkey FOREIGN KEY (crop_batch_id) REFERENCES public.crop_batch(batch_id);


--
-- Name: light_presets light_presets_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.light_presets
    ADD CONSTRAINT light_presets_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id);


--
-- Name: light_presets light_presets_submode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.light_presets
    ADD CONSTRAINT light_presets_submode_id_fkey FOREIGN KEY (submode_id) REFERENCES public.flower_submodes(id);


--
-- Name: light_programs light_programs_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_programs
    ADD CONSTRAINT light_programs_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.device_registry(device_id) ON DELETE CASCADE;


--
-- Name: light_programs light_programs_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_programs
    ADD CONSTRAINT light_programs_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id) ON DELETE SET NULL;


--
-- Name: light_target_intensity light_target_intensity_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_target_intensity
    ADD CONSTRAINT light_target_intensity_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.device_registry(device_id) ON DELETE CASCADE;


--
-- Name: light_target_intensity light_target_intensity_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.light_target_intensity
    ADD CONSTRAINT light_target_intensity_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id) ON DELETE CASCADE;


--
-- Name: measurement measurement_sensor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.measurement
    ADD CONSTRAINT measurement_sensor_id_fkey FOREIGN KEY (sensor_id) REFERENCES public.sensor(sensor_id) ON DELETE CASCADE;


--
-- Name: mode_parameters mode_parameters_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_parameters
    ADD CONSTRAINT mode_parameters_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id);


--
-- Name: mode_parameters mode_parameters_submode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.mode_parameters
    ADD CONSTRAINT mode_parameters_submode_id_fkey FOREIGN KEY (submode_id) REFERENCES public.flower_submodes(id);


--
-- Name: rack rack_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rack
    ADD CONSTRAINT rack_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.room(room_id) ON DELETE CASCADE;


--
-- Name: room_active_mode room_active_mode_mode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_active_mode
    ADD CONSTRAINT room_active_mode_mode_id_fkey FOREIGN KEY (mode_id) REFERENCES public.room_modes(id);


--
-- Name: room_active_mode room_active_mode_submode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.room_active_mode
    ADD CONSTRAINT room_active_mode_submode_id_fkey FOREIGN KEY (submode_id) REFERENCES public.flower_submodes(id);


--
-- Name: rules rules_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.rules
    ADD CONSTRAINT rules_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.schedules(id) ON DELETE SET NULL;


--
-- Name: sensor_address_history sensor_address_history_registry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_address_history
    ADD CONSTRAINT sensor_address_history_registry_id_fkey FOREIGN KEY (registry_id) REFERENCES public.sensor_registry(registry_id) ON DELETE CASCADE;


--
-- Name: sensor sensor_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor
    ADD CONSTRAINT sensor_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.device(device_id) ON DELETE CASCADE;


--
-- Name: sensor_registry sensor_registry_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.device(device_id) ON DELETE SET NULL;


--
-- Name: sensor_registry sensor_registry_pending_candidate_registry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_pending_candidate_registry_id_fkey FOREIGN KEY (pending_candidate_registry_id) REFERENCES public.sensor_registry(registry_id) ON DELETE SET NULL;


--
-- Name: sensor_registry sensor_registry_rack_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_rack_id_fkey FOREIGN KEY (rack_id) REFERENCES public.rack(rack_id) ON DELETE RESTRICT;


--
-- Name: sensor_registry sensor_registry_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.sensor_registry
    ADD CONSTRAINT sensor_registry_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.room(room_id) ON DELETE RESTRICT;


--
-- Name: soil_bed_profile soil_bed_profile_rack_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.soil_bed_profile
    ADD CONSTRAINT soil_bed_profile_rack_id_fkey FOREIGN KEY (rack_id) REFERENCES public.rack(rack_id) ON DELETE CASCADE;


--
-- Name: water_tank_monitor_config water_tank_monitor_config_sensor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cea_user
--

ALTER TABLE ONLY public.water_tank_monitor_config
    ADD CONSTRAINT water_tank_monitor_config_sensor_id_fkey FOREIGN KEY (sensor_id) REFERENCES public.sensor(sensor_id) ON DELETE SET NULL;


--
-- Name: FUNCTION get_light_periods(p_location text, p_from timestamp with time zone, p_to timestamp with time zone); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.get_light_periods(p_location text, p_from timestamp with time zone, p_to timestamp with time zone) TO cea_user;


--
-- Name: PROCEDURE monitoring_backfill_aggregates(IN job_id integer, IN config jsonb); Type: ACL; Schema: public; Owner: cea_user
--

REVOKE ALL ON PROCEDURE public.monitoring_backfill_aggregates(IN job_id integer, IN config jsonb) FROM PUBLIC;


--
-- Name: PROCEDURE monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb); Type: ACL; Schema: public; Owner: cea_user
--

REVOKE ALL ON PROCEDURE public.monitoring_rebuild_relay_overview(IN job_id integer, IN config jsonb) FROM PUBLIC;


--
-- Name: PROCEDURE monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb); Type: ACL; Schema: public; Owner: cea_user
--

REVOKE ALL ON PROCEDURE public.monitoring_refresh_overview_incremental(IN job_id integer, IN config jsonb) FROM PUBLIC;


--
-- Name: PROCEDURE monitoring_restore_quiesced_jobs_0007(); Type: ACL; Schema: public; Owner: cea_user
--

REVOKE ALL ON PROCEDURE public.monitoring_restore_quiesced_jobs_0007() FROM PUBLIC;


--
-- Name: PROCEDURE monitoring_restore_requested_pid_job_schedule_0008(); Type: ACL; Schema: public; Owner: cea_user
--

REVOKE ALL ON PROCEDURE public.monitoring_restore_requested_pid_job_schedule_0008() FROM PUBLIC;


--
-- Name: TABLE alembic_version; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.alembic_version TO cea_user;


--
-- Name: TABLE climate_periods; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.climate_periods TO cea_user;


--
-- Name: SEQUENCE climate_periods_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.climate_periods_id_seq TO cea_user;


--
-- Name: TABLE light_programs; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.light_programs TO cea_user;


--
-- Name: SEQUENCE light_programs_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.light_programs_id_seq TO cea_user;


--
-- Name: TABLE light_state_history; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.light_state_history TO cea_user;


--
-- Name: SEQUENCE light_state_history_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.light_state_history_id_seq TO cea_user;


--
-- Name: TABLE light_target_intensity; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.light_target_intensity TO cea_user;


--
-- Name: TABLE notes; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.notes TO cea_user;


--
-- Name: SEQUENCE notes_note_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.notes_note_id_seq TO cea_user;


--
-- Name: TABLE room_light_schedule; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.room_light_schedule TO cea_user;


--
-- Name: SEQUENCE room_light_schedule_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.room_light_schedule_id_seq TO cea_user;


--
-- PostgreSQL database dump complete
--

\unrestrict HbAVjU46hjlQhzvg1lanmOqfHpnyanOqZgtRQJi7ODkfRoVzScNj2boYD3g9Thl

