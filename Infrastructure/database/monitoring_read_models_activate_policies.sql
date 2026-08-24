\set ON_ERROR_STOP on

BEGIN;

SELECT monitoring_assert_timescaledb_catalog();

DO $activation_guard$
DECLARE
    target_cagg_name TEXT;
    source_latest TIMESTAMPTZ;
    marker_coverage TIMESTAMPTZ;
    catalog_watermark TIMESTAMPTZ;
BEGIN
    FOREACH target_cagg_name IN ARRAY ARRAY[
        'monitoring_measurement_1min',
        'monitoring_measurement_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min'
    ] LOOP
        SELECT covered_through
        INTO marker_coverage
        FROM monitoring_cagg_backfill_marker
        WHERE monitoring_cagg_backfill_marker.cagg_name = target_cagg_name;

        IF marker_coverage IS NULL THEN
            RAISE EXCEPTION 'supervised backfill marker missing for %', target_cagg_name;
        END IF;

        SELECT max(materialization_watermark)
        INTO catalog_watermark
        FROM monitoring_cagg_watermark
        WHERE monitoring_cagg_watermark.cagg_name = target_cagg_name;

        IF target_cagg_name LIKE 'monitoring_measurement_%' THEN
            SELECT max(time) INTO source_latest FROM measurement;
        ELSIF target_cagg_name LIKE 'monitoring_effective_setpoints_%' THEN
            SELECT max(timestamp) INTO source_latest FROM effective_setpoints;
        ELSE
            SELECT max(timestamp) INTO source_latest FROM automation_state;
        END IF;

        IF catalog_watermark IS NULL
           OR catalog_watermark < marker_coverage
           OR (source_latest IS NOT NULL AND marker_coverage < source_latest) THEN
            RAISE EXCEPTION 'backfill coverage is incomplete for %: marker %, watermark %, source latest %',
                target_cagg_name, marker_coverage, catalog_watermark, source_latest;
        END IF;
    END LOOP;
END
$activation_guard$;

SELECT add_continuous_aggregate_policy(
    'monitoring_measurement_1min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_measurement_5min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_effective_setpoints_1min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_effective_setpoints_5min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_automation_state_1min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_automation_state_5min',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

COMMIT;
