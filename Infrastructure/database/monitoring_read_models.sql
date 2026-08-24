\set ON_ERROR_STOP on

BEGIN;

CREATE OR REPLACE FUNCTION monitoring_assert_timescaledb_catalog()
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
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
$function$;

SELECT monitoring_assert_timescaledb_catalog();

DO $preflight$
DECLARE
    object_name TEXT;
    relation_kind "char";
BEGIN
    FOREACH object_name IN ARRAY ARRAY[
        'monitoring_measurement_1min',
        'monitoring_measurement_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min'
    ] LOOP
        SELECT relkind
        INTO relation_kind
        FROM pg_class
        WHERE oid = to_regclass(format('public.%I', object_name));

        IF relation_kind IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM _timescaledb_catalog.continuous_agg
               WHERE user_view_schema = 'public'
                 AND user_view_name = object_name
                 AND materialized_only
           ) THEN
            RAISE EXCEPTION 'incompatible existing monitoring object: %', object_name;
        END IF;
    END LOOP;
END
$preflight$;

ALTER TABLE effective_setpoints
    ADD COLUMN IF NOT EXISTS monitoring_ingest_id BIGINT,
    ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;

CREATE SEQUENCE IF NOT EXISTS monitoring_effective_setpoints_ingest_id_seq AS BIGINT;
ALTER SEQUENCE monitoring_effective_setpoints_ingest_id_seq
    OWNED BY effective_setpoints.monitoring_ingest_id;
ALTER TABLE effective_setpoints
    ALTER COLUMN monitoring_ingest_id
        SET DEFAULT nextval('monitoring_effective_setpoints_ingest_id_seq'::regclass),
    ALTER COLUMN ingested_at SET DEFAULT now();

CREATE INDEX IF NOT EXISTS monitoring_effective_setpoints_ingest_id_idx
    ON effective_setpoints (monitoring_ingest_id)
    WHERE monitoring_ingest_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS monitoring_effective_setpoints_ingested_at_idx
    ON effective_setpoints (ingested_at)
    WHERE ingested_at IS NOT NULL;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_measurement_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 minute', time) AS bucket,
    sensor_id,
    count(value) AS sample_count,
    sum(value::NUMERIC) AS value_sum,
    sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
    min(value::NUMERIC) AS min_value,
    max(value::NUMERIC) AS max_value,
    max(time) AS last_observed_at
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_measurement_5min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '5 minutes', time) AS bucket,
    sensor_id,
    count(value) AS sample_count,
    sum(value::NUMERIC) AS value_sum,
    sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
    min(value::NUMERIC) AS min_value,
    max(value::NUMERIC) AS max_value,
    max(time) AS last_observed_at
FROM measurement
GROUP BY bucket, sensor_id
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_effective_setpoints_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 minute', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    mode,
    count(effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_sum,
    sum(effective_heating_setpoint::NUMERIC * effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_sum_squares,
    min(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_min,
    max(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_max,
    last(effective_heating_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_heating_setpoint_last,
    count(effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_sum,
    sum(effective_cooling_setpoint::NUMERIC * effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_sum_squares,
    min(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_min,
    max(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_max,
    last(effective_cooling_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_cooling_setpoint_last,
    count(effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_sum,
    sum(effective_humidity_setpoint::NUMERIC * effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_sum_squares,
    min(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_min,
    max(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_max,
    last(effective_humidity_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_humidity_setpoint_last,
    count(effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_sum,
    sum(effective_co2_setpoint::NUMERIC * effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_sum_squares,
    min(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_min,
    max(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_max,
    last(effective_co2_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_co2_setpoint_last,
    count(effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_sum,
    sum(effective_vpd_setpoint::NUMERIC * effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_sum_squares,
    min(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_min,
    max(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_max,
    last(effective_vpd_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_vpd_setpoint_last,
    count(effective_light_intensity) AS effective_light_intensity_count,
    sum(effective_light_intensity::NUMERIC) AS effective_light_intensity_sum,
    sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS effective_light_intensity_sum_squares,
    min(effective_light_intensity::NUMERIC) AS effective_light_intensity_min,
    max(effective_light_intensity::NUMERIC) AS effective_light_intensity_max,
    last(effective_light_intensity, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_light_intensity_last,
    max(timestamp) AS last_observed_at
FROM effective_setpoints
GROUP BY bucket, location, cluster, device_name, mode
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_effective_setpoints_5min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '5 minutes', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    mode,
    count(effective_heating_setpoint) AS effective_heating_setpoint_count,
    sum(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_sum,
    sum(effective_heating_setpoint::NUMERIC * effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_sum_squares,
    min(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_min,
    max(effective_heating_setpoint::NUMERIC) AS effective_heating_setpoint_max,
    last(effective_heating_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_heating_setpoint_last,
    count(effective_cooling_setpoint) AS effective_cooling_setpoint_count,
    sum(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_sum,
    sum(effective_cooling_setpoint::NUMERIC * effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_sum_squares,
    min(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_min,
    max(effective_cooling_setpoint::NUMERIC) AS effective_cooling_setpoint_max,
    last(effective_cooling_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_cooling_setpoint_last,
    count(effective_humidity_setpoint) AS effective_humidity_setpoint_count,
    sum(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_sum,
    sum(effective_humidity_setpoint::NUMERIC * effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_sum_squares,
    min(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_min,
    max(effective_humidity_setpoint::NUMERIC) AS effective_humidity_setpoint_max,
    last(effective_humidity_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_humidity_setpoint_last,
    count(effective_co2_setpoint) AS effective_co2_setpoint_count,
    sum(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_sum,
    sum(effective_co2_setpoint::NUMERIC * effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_sum_squares,
    min(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_min,
    max(effective_co2_setpoint::NUMERIC) AS effective_co2_setpoint_max,
    last(effective_co2_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_co2_setpoint_last,
    count(effective_vpd_setpoint) AS effective_vpd_setpoint_count,
    sum(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_sum,
    sum(effective_vpd_setpoint::NUMERIC * effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_sum_squares,
    min(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_min,
    max(effective_vpd_setpoint::NUMERIC) AS effective_vpd_setpoint_max,
    last(effective_vpd_setpoint, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_vpd_setpoint_last,
    count(effective_light_intensity) AS effective_light_intensity_count,
    sum(effective_light_intensity::NUMERIC) AS effective_light_intensity_sum,
    sum(effective_light_intensity::NUMERIC * effective_light_intensity::NUMERIC) AS effective_light_intensity_sum_squares,
    min(effective_light_intensity::NUMERIC) AS effective_light_intensity_min,
    max(effective_light_intensity::NUMERIC) AS effective_light_intensity_max,
    last(effective_light_intensity, ROW(timestamp, COALESCE(monitoring_ingest_id, 0))) AS effective_light_intensity_last,
    max(timestamp) AS last_observed_at
FROM effective_setpoints
GROUP BY bucket, location, cluster, device_name, mode
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_automation_state_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 minute', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    count(*) AS sample_count,
    min(device_state) AS device_state_min,
    max(device_state) AS device_state_max,
    last(device_state, ROW(timestamp, id)) AS device_state_last,
    last(device_mode, ROW(timestamp, id)) AS device_mode_last,
    count(pid_output) AS pid_output_count,
    min(pid_output) AS pid_output_min,
    max(pid_output) AS pid_output_max,
    last(pid_output, ROW(timestamp, id)) AS pid_output_last,
    count(duty_cycle_percent) AS duty_cycle_percent_count,
    min(duty_cycle_percent) AS duty_cycle_percent_min,
    max(duty_cycle_percent) AS duty_cycle_percent_max,
    last(duty_cycle_percent, ROW(timestamp, id)) AS duty_cycle_percent_last,
    last(control_reason, ROW(timestamp, id)) AS control_reason_last,
    max(timestamp) AS last_observed_at
FROM automation_state
GROUP BY bucket, location, cluster, device_name
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS monitoring_automation_state_5min
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '5 minutes', timestamp) AS bucket,
    location,
    cluster,
    device_name,
    count(*) AS sample_count,
    min(device_state) AS device_state_min,
    max(device_state) AS device_state_max,
    last(device_state, ROW(timestamp, id)) AS device_state_last,
    last(device_mode, ROW(timestamp, id)) AS device_mode_last,
    count(pid_output) AS pid_output_count,
    min(pid_output) AS pid_output_min,
    max(pid_output) AS pid_output_max,
    last(pid_output, ROW(timestamp, id)) AS pid_output_last,
    count(duty_cycle_percent) AS duty_cycle_percent_count,
    min(duty_cycle_percent) AS duty_cycle_percent_min,
    max(duty_cycle_percent) AS duty_cycle_percent_max,
    last(duty_cycle_percent, ROW(timestamp, id)) AS duty_cycle_percent_last,
    last(control_reason, ROW(timestamp, id)) AS control_reason_last,
    max(timestamp) AS last_observed_at
FROM automation_state
GROUP BY bucket, location, cluster, device_name
WITH NO DATA;

CREATE TABLE IF NOT EXISTS monitoring_room_photoperiod (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('SUN', 'MOON', 'UNKNOWN')),
    mode_id INTEGER,
    submode_id INTEGER,
    runtime_snapshot_version BIGINT NOT NULL CHECK (runtime_snapshot_version >= 0),
    source TEXT NOT NULL CHECK (btrim(source) <> '')
);
CREATE INDEX IF NOT EXISTS monitoring_room_photoperiod_room_time_idx
    ON monitoring_room_photoperiod (location, cluster, observed_at DESC);
CREATE INDEX IF NOT EXISTS monitoring_room_photoperiod_snapshot_idx
    ON monitoring_room_photoperiod (runtime_snapshot_version);

CREATE TABLE IF NOT EXISTS monitoring_cagg_backfill_marker (
    cagg_name TEXT PRIMARY KEY,
    covered_through TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    completed_by TEXT NOT NULL CHECK (btrim(completed_by) <> '')
);

CREATE OR REPLACE VIEW monitoring_cagg_watermark AS
SELECT
    continuous_agg.user_view_name::TEXT AS cagg_name,
    _timescaledb_functions.to_timestamp(
        _timescaledb_functions.cagg_watermark(continuous_agg.mat_hypertable_id)
    ) AS materialization_watermark,
    invalidation.invalidation_source,
    _timescaledb_functions.to_timestamp(invalidation.lowest_modified_value) AS invalidation_start,
    _timescaledb_functions.to_timestamp(invalidation.greatest_modified_value) AS invalidation_end
FROM _timescaledb_catalog.continuous_agg AS continuous_agg
LEFT JOIN LATERAL (
    SELECT
        'materialization'::TEXT AS invalidation_source,
        materialization_log.lowest_modified_value,
        materialization_log.greatest_modified_value
    FROM _timescaledb_catalog.continuous_aggs_materialization_invalidation_log AS materialization_log
    WHERE materialization_log.materialization_id = continuous_agg.mat_hypertable_id
    UNION ALL
    SELECT
        'hypertable'::TEXT AS invalidation_source,
        hypertable_log.lowest_modified_value,
        hypertable_log.greatest_modified_value
    FROM _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log AS hypertable_log
    WHERE hypertable_log.hypertable_id = continuous_agg.raw_hypertable_id
) AS invalidation ON TRUE
WHERE continuous_agg.user_view_schema = 'public'
  AND continuous_agg.user_view_name LIKE 'monitoring\_%' ESCAPE '\';

CREATE OR REPLACE FUNCTION monitoring_assert_relation_columns(
    relation_name TEXT,
    expected_names TEXT[],
    expected_types TEXT[]
)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
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
$function$;

DO $column_compatibility$
DECLARE
    metric_name TEXT;
    suffix_name TEXT;
    effective_names TEXT[] := ARRAY['bucket', 'location', 'cluster', 'device_name', 'mode'];
    effective_types TEXT[] := ARRAY[
        'timestamp with time zone', 'text', 'text', 'text', 'text'
    ];
BEGIN
    PERFORM monitoring_assert_relation_columns(
        'monitoring_measurement_1min',
        ARRAY[
            'bucket', 'sensor_id', 'sample_count', 'value_sum',
            'value_sum_squares', 'min_value', 'max_value', 'last_observed_at'
        ],
        ARRAY[
            'timestamp with time zone', 'integer', 'bigint', 'numeric',
            'numeric', 'numeric', 'numeric', 'timestamp with time zone'
        ]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_measurement_5min',
        ARRAY[
            'bucket', 'sensor_id', 'sample_count', 'value_sum',
            'value_sum_squares', 'min_value', 'max_value', 'last_observed_at'
        ],
        ARRAY[
            'timestamp with time zone', 'integer', 'bigint', 'numeric',
            'numeric', 'numeric', 'numeric', 'timestamp with time zone'
        ]
    );

    FOREACH metric_name IN ARRAY ARRAY[
        'effective_heating_setpoint',
        'effective_cooling_setpoint',
        'effective_humidity_setpoint',
        'effective_co2_setpoint',
        'effective_vpd_setpoint',
        'effective_light_intensity'
    ] LOOP
        FOREACH suffix_name IN ARRAY ARRAY[
            'count', 'sum', 'sum_squares', 'min', 'max', 'last'
        ] LOOP
            effective_names := array_append(
                effective_names,
                format('%s_%s', metric_name, suffix_name)
            );
            effective_types := array_append(
                effective_types,
                CASE suffix_name
                    WHEN 'count' THEN 'bigint'
                    WHEN 'last' THEN 'double precision'
                    ELSE 'numeric'
                END
            );
        END LOOP;
    END LOOP;
    effective_names := array_append(effective_names, 'last_observed_at');
    effective_types := array_append(effective_types, 'timestamp with time zone');

    PERFORM monitoring_assert_relation_columns(
        'monitoring_effective_setpoints_1min', effective_names, effective_types
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_effective_setpoints_5min', effective_names, effective_types
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_automation_state_1min',
        ARRAY[
            'bucket', 'location', 'cluster', 'device_name', 'sample_count',
            'device_state_min', 'device_state_max', 'device_state_last',
            'device_mode_last', 'pid_output_count', 'pid_output_min',
            'pid_output_max', 'pid_output_last', 'duty_cycle_percent_count',
            'duty_cycle_percent_min', 'duty_cycle_percent_max',
            'duty_cycle_percent_last', 'control_reason_last', 'last_observed_at'
        ],
        ARRAY[
            'timestamp with time zone', 'text', 'text', 'text', 'bigint',
            'integer', 'integer', 'integer', 'text', 'bigint',
            'double precision', 'double precision', 'double precision', 'bigint',
            'double precision', 'double precision', 'double precision', 'text',
            'timestamp with time zone'
        ]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_automation_state_5min',
        ARRAY[
            'bucket', 'location', 'cluster', 'device_name', 'sample_count',
            'device_state_min', 'device_state_max', 'device_state_last',
            'device_mode_last', 'pid_output_count', 'pid_output_min',
            'pid_output_max', 'pid_output_last', 'duty_cycle_percent_count',
            'duty_cycle_percent_min', 'duty_cycle_percent_max',
            'duty_cycle_percent_last', 'control_reason_last', 'last_observed_at'
        ],
        ARRAY[
            'timestamp with time zone', 'text', 'text', 'text', 'bigint',
            'integer', 'integer', 'integer', 'text', 'bigint',
            'double precision', 'double precision', 'double precision', 'bigint',
            'double precision', 'double precision', 'double precision', 'text',
            'timestamp with time zone'
        ]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_room_photoperiod',
        ARRAY[
            'id', 'observed_at', 'location', 'cluster', 'phase', 'mode_id',
            'submode_id', 'runtime_snapshot_version', 'source'
        ],
        ARRAY[
            'bigint', 'timestamp with time zone', 'text', 'text', 'text',
            'integer', 'integer', 'bigint', 'text'
        ]
    );
    PERFORM monitoring_assert_relation_columns(
        'monitoring_cagg_backfill_marker',
        ARRAY['cagg_name', 'covered_through', 'completed_at', 'completed_by'],
        ARRAY['text', 'timestamp with time zone', 'timestamp with time zone', 'text']
    );
END
$column_compatibility$;

DO $compatibility$
DECLARE
    incompatible_count INTEGER;
BEGIN
    SELECT count(*)
    INTO incompatible_count
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public'
      AND user_view_name IN (
          'monitoring_measurement_1min',
          'monitoring_measurement_5min',
          'monitoring_effective_setpoints_1min',
          'monitoring_effective_setpoints_5min',
          'monitoring_automation_state_1min',
          'monitoring_automation_state_5min'
      )
      AND NOT materialized_only;
    IF incompatible_count <> 0 THEN
        RAISE EXCEPTION 'monitoring continuous aggregates must be materialized-only';
    END IF;

    IF (
        SELECT count(*)
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name IN (
              'monitoring_measurement_1min',
              'monitoring_measurement_5min',
              'monitoring_effective_setpoints_1min',
              'monitoring_effective_setpoints_5min',
              'monitoring_automation_state_1min',
              'monitoring_automation_state_5min'
          )
    ) <> 6 THEN
        RAISE EXCEPTION 'monitoring continuous aggregate catalog is incomplete';
    END IF;
END
$compatibility$;

COMMIT;
