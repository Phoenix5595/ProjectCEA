-- migration: downtime=false
\set ON_ERROR_STOP on
SET search_path = public, pg_catalog;

BEGIN;

DROP MATERIALIZED VIEW IF EXISTS
    public.measurement_daily,
    public.measurement_hourly,
    public.measurement_5min,
    public.measurement_1min
CASCADE;

DO $drop_measurement_grafana_views$
DECLARE
    v_view_name TEXT;
BEGIN
    FOR v_view_name IN
        SELECT viewname
        FROM pg_views
        WHERE schemaname = 'public'
          AND viewname ~ '^measurement_.*_grafana$'
    LOOP
        EXECUTE format('DROP VIEW IF EXISTS public.%I CASCADE', v_view_name);
    END LOOP;
END
$drop_measurement_grafana_views$;

DROP FUNCTION IF EXISTS public.get_sensor_data_optimized(text[], timestamptz, timestamptz);
DROP FUNCTION IF EXISTS public.get_sensor_stats(text[], timestamptz, timestamptz);

DO $measurement_compression$
DECLARE
    v_source RECORD;
    v_relation REGCLASS;
    v_compression_enabled BOOLEAN;
BEGIN
    FOR v_source IN
        SELECT source_table, time_column, segment_columns
        FROM (VALUES
            ('measurement', 'time', 'sensor_id'),
            ('effective_setpoints', 'timestamp', 'location,cluster,device_name,mode')
        ) AS sources(source_table, time_column, segment_columns)
    LOOP
        v_relation := to_regclass(format('public.%I', v_source.source_table));
        IF v_relation IS NULL THEN
            CONTINUE;
        END IF;

        SELECT h.compression_enabled
        INTO v_compression_enabled
        FROM timescaledb_information.hypertables AS h
        WHERE h.hypertable_schema = 'public'
          AND h.hypertable_name = v_source.source_table;
        IF NOT FOUND THEN
            RAISE NOTICE 'Skipping public.%: relation exists but is not a TimescaleDB hypertable',
                v_source.source_table;
            CONTINUE;
        END IF;

        IF NOT v_compression_enabled THEN
            EXECUTE format(
                'ALTER TABLE public.%I SET (timescaledb.compress, timescaledb.compress_segmentby = %L, timescaledb.compress_orderby = %L)',
                v_source.source_table,
                v_source.segment_columns,
                v_source.time_column || ' DESC');
        END IF;

        PERFORM remove_retention_policy(v_relation, if_exists => TRUE);
        PERFORM remove_compression_policy(v_relation, if_exists => TRUE);
        PERFORM add_compression_policy(
            v_relation,
            compress_after => INTERVAL '3 months');
    END LOOP;
END
$measurement_compression$;

COMMIT;
