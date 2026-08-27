\set ON_ERROR_STOP on

\ir ../../monitoring_read_models.sql
\ir ../../monitoring_read_models.sql

DO $case$
DECLARE
    cagg_name TEXT;
    materialized_row_count BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM monitoring_fixture_metadata
        WHERE fixture_name = 'grafana-replacement-veg-flower'
          AND fixture_version = 1
    ) THEN
        RAISE EXCEPTION 'monitoring fixture metadata is missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_refresh_continuous_aggregate'
    ) THEN
        RAISE EXCEPTION 'fixture unexpectedly installed a continuous aggregate policy';
    END IF;

    IF (
        SELECT count(*)
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name LIKE 'monitoring\_%' ESCAPE '\'
          AND materialized_only
    ) <> 6 THEN
        RAISE EXCEPTION 'expected six materialized-only monitoring continuous aggregates';
    END IF;

    FOREACH cagg_name IN ARRAY ARRAY[
        'monitoring_measurement_1min',
        'monitoring_measurement_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM %I', cagg_name)
        INTO materialized_row_count;
        IF materialized_row_count <> 0 THEN
            RAISE EXCEPTION '% was populated by definition SQL', cagg_name;
        END IF;
    END LOOP;

    IF (SELECT count(*) FROM monitoring_cagg_watermark) <> 6
       OR EXISTS (
           SELECT 1
           FROM monitoring_cagg_watermark
           WHERE materialization_watermark IS NULL
       ) THEN
        RAISE EXCEPTION 'catalog adapter did not return all monitoring watermarks';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM effective_setpoints
        WHERE monitoring_ingest_id IS NOT NULL
           OR ingested_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'historical effective setpoints metadata was rewritten';
    END IF;

    IF EXISTS (SELECT 1 FROM monitoring_room_photoperiod) THEN
        RAISE EXCEPTION 'photoperiod history must start empty';
    END IF;
END
$case$;

INSERT INTO effective_setpoints (
    timestamp,
    location,
    cluster,
    mode,
    device_name,
    effective_heating_setpoint
)
VALUES (
    '2026-01-08T00:00:00Z',
    'Veg Room',
    'main',
    'DAY',
    NULL,
    22.5
);

DO $future_defaults$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM effective_setpoints
        WHERE timestamp = '2026-01-08T00:00:00Z'
          AND monitoring_ingest_id IS NOT NULL
          AND ingested_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'future effective setpoints did not receive ingest metadata';
    END IF;
END
$future_defaults$;

SELECT json_build_object(
    'case', 'schema-idempotent-and-policy-disabled',
    'fixture_ready', TRUE,
    'materialized_only_cagg_count', (
        SELECT count(*)
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name LIKE 'monitoring\_%' ESCAPE '\'
          AND materialized_only
    ),
    'catalog_adapter_rows', (SELECT count(*) FROM monitoring_cagg_watermark),
    'pending_invalidation_rows', (
        SELECT count(*)
        FROM monitoring_cagg_watermark
        WHERE invalidation_source IS NOT NULL
    ),
    'refresh_policy_count', (
        SELECT count(*)
        FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_refresh_continuous_aggregate'
    )
) AS result;
