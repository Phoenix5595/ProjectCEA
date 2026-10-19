\set ON_ERROR_STOP on
SET search_path = public, pg_catalog;

BEGIN;

DO $database_guard$
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION
            'grow_data_source_compression may only run on sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;
END
$database_guard$;

DO $compression$
DECLARE
    v_stream RECORD;
    v_relation REGCLASS;
    v_compression_enabled BOOLEAN;
BEGIN
    FOR v_stream IN
        SELECT source_table, time_column, segment_columns
        FROM (VALUES
            ('measurement', 'time', 'sensor_id'),
            ('actuator_events', 'time', 'device_id'),
            ('automation_state', 'timestamp', 'location,cluster,device_name'),
            ('effective_setpoints', 'timestamp', 'location,cluster,device_name,mode'),
            ('control_history', 'timestamp', 'location,cluster,device_name,channel'),
            ('relay_observation', 'observed_at', 'channel')
        ) AS streams(source_table, time_column, segment_columns)
    LOOP
        v_relation := to_regclass(format('public.%I', v_stream.source_table));
        IF v_relation IS NULL THEN
            CONTINUE;
        END IF;

        SELECT h.compression_enabled
        INTO v_compression_enabled
        FROM timescaledb_information.hypertables h
        WHERE h.hypertable_schema = 'public'
          AND h.hypertable_name = v_stream.source_table;
        IF NOT FOUND THEN
            RAISE NOTICE 'Skipping public.%: relation exists but is not a TimescaleDB hypertable',
                v_stream.source_table;
            CONTINUE;
        END IF;

        IF NOT v_compression_enabled THEN
            EXECUTE format(
                'ALTER TABLE public.%I SET (timescaledb.compress, timescaledb.compress_segmentby = %L, timescaledb.compress_orderby = %L)',
                v_stream.source_table,
                v_stream.segment_columns,
                v_stream.time_column || ' DESC');
        END IF;

        PERFORM remove_retention_policy(v_relation, if_exists => TRUE);
        PERFORM remove_compression_policy(v_relation, if_exists => TRUE);
        PERFORM add_compression_policy(
            v_relation,
            compress_after => INTERVAL '3 months');
    END LOOP;
END
$compression$;

COMMIT;
