\set ON_ERROR_STOP on
\set apply 0
\set confirm_database ''

CREATE TEMP TABLE purge_proof_rows (
    stage TEXT NOT NULL,
    table_name TEXT NOT NULL,
    payload BYTEA NOT NULL
) ON COMMIT PRESERVE ROWS;
CREATE TEMP TABLE purge_proof_expected_delete (
    table_name TEXT NOT NULL,
    payload BYTEA NOT NULL
) ON COMMIT PRESERVE ROWS;
CREATE TEMP TABLE purge_proof_chunk_events (
    stage TEXT NOT NULL,
    table_name TEXT NOT NULL,
    range_start TIMESTAMPTZ NOT NULL,
    range_end TIMESTAMPTZ NOT NULL,
    chunks_dropped BIGINT NOT NULL,
    deleted_rows BIGINT NOT NULL,
    restored_rows BIGINT NOT NULL
) ON COMMIT PRESERVE ROWS;

CREATE OR REPLACE FUNCTION pg_temp.purge_proof_snapshot(p_stage TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $snapshot$
DECLARE
    v_table TEXT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
        'control_history', 'relay_observation', 'monitoring_room_photoperiod',
        'monitoring_photoperiod_coverage', 'mode_transition_history',
        'pid_parameter_history'
    ] LOOP
        IF to_regclass(format('public.%I', v_table)) IS NOT NULL THEN
            EXECUTE format(
                'INSERT INTO pg_temp.purge_proof_rows(stage, table_name, payload) '
                'SELECT $1, $2, record_send(source_row) FROM public.%I AS source_row',
                v_table
            ) USING p_stage, v_table;
        END IF;
    END LOOP;

    FOR v_table IN
        SELECT c.relname
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND (
                c.relname LIKE 'archive\_%' ESCAPE '\'
                OR c.relname LIKE 'calendar\_%' ESCAPE '\'
                OR c.relname = ANY (ARRAY[
                    'device', 'device_mappings', 'device_states', 'device_registry',
                    'sensor', 'sensor_registry', 'room', 'rack', 'setpoints',
                    'mode_parameters', 'flower_submodes', 'room_modes', 'room_active_mode',
                    'active_room_modes', 'pid_parameters', 'climate_periods', 'schedules',
                    'rules', 'crop_batch', 'grow_lifecycle', 'grow_plan_idempotency',
                    'config_versions', 'calendar_mode_application', 'users',
                    'light_state_history', 'pid_adjustments', 'setpoint_history', 'rollout_events'
                ])
           )
         ORDER BY c.relname
    LOOP
        EXECUTE format(
            'INSERT INTO pg_temp.purge_proof_rows(stage, table_name, payload) '
            'SELECT $1, $2, record_send(source_row) FROM public.%I AS source_row',
            v_table
        ) USING p_stage, v_table;
    END LOOP;
END
$snapshot$;

SELECT pg_temp.purge_proof_snapshot('before');

-- Default mode is dry run. This invocation must leave all source and protected
-- full-row binary snapshots unchanged.
\ir ../../operator_purge_history_before_2026_08.sql
SELECT pg_temp.purge_proof_snapshot('after_dry_run');
DO $dry_run_assertions$
BEGIN
    IF EXISTS (
        SELECT 1 FROM (
            (SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'before'
             EXCEPT ALL
             SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'after_dry_run')
            UNION ALL
            (SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'after_dry_run'
             EXCEPT ALL
             SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'before')
        ) AS changed_rows
    ) THEN
        RAISE EXCEPTION 'dry-run changed source or protected table rows';
    END IF;

    IF (SELECT count(*) FROM pg_temp.operator_purge_targets WHERE present) <> 10 THEN
        RAISE EXCEPTION 'proof fixture did not exercise the exact ten configured source streams';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.chunks
         WHERE hypertable_schema = 'public' AND hypertable_name = 'measurement' AND is_compressed
           AND range_end <= TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto'
    ) OR NOT EXISTS (
        SELECT 1 FROM timescaledb_information.chunks
         WHERE hypertable_schema = 'public' AND hypertable_name = 'effective_setpoints' AND is_compressed
    ) THEN
        RAISE EXCEPTION 'purge proof requires a compressed pre-cutoff measurement chunk and compressed effective_setpoints source chunk';
    END IF;
END
$dry_run_assertions$;
DO $protected_fixture_guard$
DECLARE
    v_table TEXT;
    v_rows BIGINT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'device', 'device_mappings', 'device_states', 'device_registry',
        'sensor', 'sensor_registry', 'room', 'rack', 'setpoints', 'mode_parameters',
        'flower_submodes', 'room_modes', 'room_active_mode', 'active_room_modes',
        'pid_parameters', 'climate_periods', 'schedules', 'rules', 'crop_batch',
        'grow_plan_idempotency', 'config_versions', 'calendar_mode_application',
        'users', 'light_state_history', 'pid_adjustments', 'setpoint_history',
        'rollout_events', 'archive_purge_protected_probe'
    ] LOOP
        IF to_regclass(format('public.%I', v_table)) IS NULL THEN
            RAISE EXCEPTION 'protected proof fixture is missing public.%', v_table;
        END IF;
        EXECUTE format('SELECT count(*) FROM public.%I', v_table) INTO v_rows;
        IF v_rows = 0 THEN
            RAISE EXCEPTION 'protected proof fixture has no seeded row in public.%', v_table;
        END IF;
    END LOOP;

    FOR v_table IN
        SELECT c.relname
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p')
           AND c.relname LIKE 'calendar\_%' ESCAPE '\'
    LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', v_table) INTO v_rows;
        IF v_rows = 0 THEN
            RAISE EXCEPTION 'Calendar proof fixture has no seeded row in public.%', v_table;
        END IF;
    END LOOP;
END
$protected_fixture_guard$;


-- Build the expected full-row deletion multiset from the same checked
-- allowlist and cutoff, then independently assert representative anchor rules.
DO $expected_delete$
DECLARE
    v_target RECORD;
    v_predicate TEXT;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    FOR v_target IN SELECT * FROM pg_temp.operator_purge_targets WHERE present LOOP
        v_predicate := pg_temp.operator_purge_predicate(
            v_target.table_name, v_target.time_column, v_target.anchor_columns,
            v_target.has_location);
        EXECUTE format(
            'INSERT INTO pg_temp.purge_proof_expected_delete(table_name, payload) '
            'SELECT $2, record_send(source_row) FROM public.%1$I AS source_row WHERE %2$s',
            v_target.table_name, replace(v_predicate, 'm.', 'source_row.')
        ) USING v_cutoff, v_target.table_name;
    END LOOP;
END
$expected_delete$;

DO $expected_rows_guard$
BEGIN
    IF EXISTS (
        SELECT target.table_name
          FROM pg_temp.operator_purge_targets AS target
          LEFT JOIN pg_temp.purge_proof_expected_delete AS expected
            ON expected.table_name = target.table_name
         WHERE target.present
         GROUP BY target.table_name
        HAVING count(expected.payload) = 0
    ) THEN
        RAISE EXCEPTION 'purge proof expected deletion set missed a present source stream';
    END IF;
END
$expected_rows_guard$;

SELECT user_view_name, mat_hypertable_id
  FROM _timescaledb_catalog.continuous_agg
 ORDER BY 2;
SELECT current_database() AS purge_confirm_database \gset
\set confirm_database :purge_confirm_database
\set apply 1
\ir ../../operator_purge_history_before_2026_08.sql

SELECT pg_temp.purge_proof_snapshot('after_first_apply');
INSERT INTO pg_temp.purge_proof_chunk_events(
    stage, table_name, range_start, range_end, chunks_dropped, deleted_rows, restored_rows
)
SELECT 'after_first_apply', table_name, range_start, range_end,
       chunks_dropped, deleted_rows, restored_rows
  FROM pg_temp.operator_purge_chunk_events;
DO $chunk_drop_assertions$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_temp.purge_proof_chunk_events
         WHERE stage = 'after_first_apply' AND table_name = 'measurement'
           AND chunks_dropped > 0 AND deleted_rows > 0
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_temp.purge_proof_chunk_events
         WHERE stage = 'after_first_apply' AND table_name = 'automation_state'
           AND chunks_dropped > 0 AND deleted_rows > 0
    ) THEN
        RAISE EXCEPTION 'compressed measurement and daily automation_state chunk-drop paths were not exercised';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_temp.purge_proof_chunk_events
         WHERE stage = 'after_first_apply'
           AND (range_end > TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto'
                OR chunks_dropped < 1)
    ) THEN
        RAISE EXCEPTION 'chunk drop crossed the cutoff or did not drop a full old range';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_chunk_events
         WHERE table_name = 'mode_transition_history'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_deleted
         WHERE table_name = 'mode_transition_history' AND deleted_rows > 0
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_deleted
         WHERE table_name = 'effective_setpoints'
           AND toronto_day = DATE '2026-07-31' AND deleted_rows > 0
    ) THEN
        RAISE EXCEPTION 'plain mode_transition_history or cutoff-containing effective_setpoints range missed the row-wise path';
    END IF;
END
$chunk_drop_assertions$;
DO $apply_assertions$
DECLARE
    v_sample_count BIGINT;
BEGIN
    IF EXISTS (
        WITH expected AS (
            SELECT table_name, payload
              FROM pg_temp.purge_proof_rows
             WHERE stage = 'before'
               AND table_name = ANY (ARRAY[
                    'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
                    'control_history', 'relay_observation', 'monitoring_room_photoperiod',
                    'monitoring_photoperiod_coverage', 'mode_transition_history',
                    'pid_parameter_history'
               ])
            EXCEPT ALL
            SELECT table_name, payload FROM pg_temp.purge_proof_expected_delete
        ), actual AS (
            SELECT table_name, payload
              FROM pg_temp.purge_proof_rows
             WHERE stage = 'after_first_apply'
               AND table_name = ANY (ARRAY[
                    'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
                    'control_history', 'relay_observation', 'monitoring_room_photoperiod',
                    'monitoring_photoperiod_coverage', 'mode_transition_history',
                    'pid_parameter_history'
               ])
        )
        SELECT 1 FROM (
            (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
        ) AS different_rows
    ) THEN
        RAISE EXCEPTION 'apply result differs from the exact expected removable-row multiset';
    END IF;

    IF EXISTS (
        WITH protected_before AS (
            SELECT table_name, payload FROM pg_temp.purge_proof_rows
             WHERE stage = 'before'
               AND NOT (table_name = ANY (ARRAY[
                    'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
                    'control_history', 'relay_observation', 'monitoring_room_photoperiod',
                    'monitoring_photoperiod_coverage', 'mode_transition_history',
                    'pid_parameter_history'
               ]))
        ), protected_after AS (
            SELECT table_name, payload FROM pg_temp.purge_proof_rows
             WHERE stage = 'after_first_apply'
               AND NOT (table_name = ANY (ARRAY[
                    'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
                    'control_history', 'relay_observation', 'monitoring_room_photoperiod',
                    'monitoring_photoperiod_coverage', 'mode_transition_history',
                    'pid_parameter_history'
               ]))
        )
        SELECT 1 FROM (
            (SELECT * FROM protected_before EXCEPT ALL SELECT * FROM protected_after)
            UNION ALL
            (SELECT * FROM protected_after EXCEPT ALL SELECT * FROM protected_before)
        ) AS changed_protected_rows
    ) THEN
        RAISE EXCEPTION 'apply changed a non-time-series registry/configuration/period/calendar/archive/current-state row';
    END IF;

    -- Latest plus predecessor, including all ties, are retained on the
    -- source_retention ANCHOR_COLUMNS keys.
    IF NOT EXISTS (SELECT 1 FROM measurement WHERE sensor_id = 7 AND time = TIMESTAMPTZ '2026-07-31 12:33:00+00')
       OR EXISTS (SELECT 1 FROM measurement WHERE sensor_id = 7 AND time = TIMESTAMPTZ '2026-07-30 12:33:00+00')
       OR NOT EXISTS (SELECT 1 FROM measurement WHERE sensor_id = 7 AND time = TIMESTAMPTZ '2026-08-02 12:33:00+00') THEN
        RAISE EXCEPTION 'measurement latest/predecessor/post-cutoff anchor rule failed';
    END IF;
    IF (SELECT count(*) FROM automation_state
         WHERE location = 'Purge Room' AND device_name = 'purge-heater'
           AND timestamp = TIMESTAMPTZ '2026-07-31 12:30:00+00') <> 2 THEN
        RAISE EXCEPTION 'automation_state timestamp ties were not all retained';
    END IF;
    IF EXISTS (SELECT 1 FROM actuator_events WHERE action = 'purge-old')
       OR NOT EXISTS (SELECT 1 FROM actuator_events WHERE action = 'purge-anchor')
       OR NOT EXISTS (SELECT 1 FROM actuator_events WHERE action = 'purge-post') THEN
        RAISE EXCEPTION 'actuator_events device anchor rule failed';
    END IF;
    IF EXISTS (SELECT 1 FROM effective_setpoints WHERE location = 'Purge Room' AND device_name = 'purge-light'
                AND timestamp = TIMESTAMPTZ '2026-08-01 03:58:59+00')
       OR NOT EXISTS (SELECT 1 FROM effective_setpoints WHERE location = 'Purge Room' AND device_name = 'purge-light'
                      AND timestamp = TIMESTAMPTZ '2026-08-01 03:59:00+00')
       OR NOT EXISTS (SELECT 1 FROM effective_setpoints WHERE location = 'Purge Room' AND device_name = 'purge-light'
                      AND timestamp = TIMESTAMPTZ '2026-08-02 12:30:00+00') THEN
        RAISE EXCEPTION 'effective_setpoints anchor or 60-second rule failed';
    END IF;
    IF (SELECT count(*) FROM control_history WHERE location = 'Purge Room' AND device_name = 'purge-relay'
         AND timestamp = TIMESTAMPTZ '2026-07-31 12:30:00+00' AND reason LIKE 'anchor%') <> 2
       OR NOT EXISTS (SELECT 1 FROM control_history WHERE location = 'Purge Room' AND device_name = 'purge-relay'
                      AND mode = 'manual' AND manual_expires_at IS NOT NULL) THEN
        RAISE EXCEPTION 'control_history ties or manual expiry guard failed';
    END IF;
    IF EXISTS (SELECT 1 FROM relay_observation WHERE location = 'Purge Room' AND channel = 0
                AND observed_at = TIMESTAMPTZ '2026-07-30 12:00:00+00')
       OR NOT EXISTS (SELECT 1 FROM relay_observation WHERE location = 'Purge Room' AND channel = 0
                       AND observed_at = TIMESTAMPTZ '2026-07-31 12:00:00+00')
       OR NOT EXISTS (SELECT 1 FROM relay_observation WHERE reason = 'heartbeat' AND channel IS NULL)
       OR (SELECT count(*) FROM relay_observation WHERE channel = 14 AND location IS NULL) <> 2 THEN
        RAISE EXCEPTION 'relay_observation anchor, heartbeat, or non-NULL-location guard failed';
    END IF;
    IF EXISTS (SELECT 1 FROM monitoring_room_photoperiod WHERE location = 'Purge Room' AND source = 'purge-proof'
                AND observed_at = TIMESTAMPTZ '2026-08-01 03:58:59+00')
       OR NOT EXISTS (SELECT 1 FROM monitoring_room_photoperiod WHERE location = 'Purge Room' AND source = 'purge-proof'
                      AND observed_at = TIMESTAMPTZ '2026-08-01 03:59:00+00')
       OR NOT EXISTS (SELECT 1 FROM monitoring_photoperiod_coverage WHERE location = 'Purge Room'
                      AND observed_at = TIMESTAMPTZ '2026-07-31 12:30:00+00') THEN
        RAISE EXCEPTION 'photoperiod source anchor or 60-second guard failed';
    END IF;
    IF EXISTS (SELECT 1 FROM mode_transition_history WHERE location = 'Purge Room'
                AND triggered_at = TIMESTAMPTZ '2026-07-30 12:30:00+00')
       OR NOT EXISTS (SELECT 1 FROM mode_transition_history WHERE location = 'Purge Room'
                      AND triggered_at = TIMESTAMPTZ '2026-07-31 12:30:00+00')
       OR NOT EXISTS (SELECT 1 FROM mode_transition_history WHERE location = 'Veg Room' AND triggered_at IS NULL) THEN
        RAISE EXCEPTION 'mode_transition_history latest/null-time rule failed';
    END IF;
    IF EXISTS (SELECT 1 FROM pid_parameter_history WHERE location = 'Purge Room'
                AND device_type = 'purge-proof' AND timestamp = TIMESTAMPTZ '2026-07-30 12:30:00+00')
       OR NOT EXISTS (SELECT 1 FROM pid_parameter_history WHERE location = 'Purge Room'
                      AND device_type = 'purge-proof' AND timestamp = TIMESTAMPTZ '2026-07-31 12:30:00+00')
       OR NOT EXISTS (SELECT 1 FROM pid_parameter_history WHERE location IS NULL AND id = 1) THEN
        RAISE EXCEPTION 'pid_parameter_history latest/location guard failed';
    END IF;

    -- These unknown/unavailable streams and similarly named configuration
    -- data are explicitly protected, including their pre-cutoff rows.
    IF (SELECT count(*) FROM light_state_history) <> 5
       OR (SELECT count(*) FROM pid_adjustments) <> 5
       OR (SELECT count(*) FROM setpoint_history) <> 5
       OR (SELECT count(*) FROM rollout_events) <> 3
       OR (SELECT count(*) FROM light_state_history WHERE triggered_at < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto') <> 4
       OR (SELECT count(*) FROM pid_adjustments WHERE created_at < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto') <> 4
       OR (SELECT count(*) FROM setpoint_history WHERE timestamp < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto') <> 4
       OR (SELECT count(*) FROM rollout_events WHERE timestamp < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto') <> 2 THEN
        RAISE EXCEPTION 'excluded optional history rows were not preserved byte-for-byte';
    END IF;

    SELECT sample_count INTO v_sample_count
      FROM public.monitoring_measurement_1min
     WHERE bucket = TIMESTAMPTZ '2026-07-31 12:33:00+00' AND sensor_id = 7;
    IF v_sample_count IS DISTINCT FROM 1
       OR EXISTS (SELECT 1 FROM public.monitoring_measurement_1min
                   WHERE bucket = TIMESTAMPTZ '2026-07-30 12:33:00+00' AND sensor_id = 7) THEN
        RAISE EXCEPTION 'measurement CAGG does not reflect retained source rows';
    END IF;
    SELECT effective_light_intensity_count INTO v_sample_count
      FROM public.monitoring_effective_setpoints_1min
     WHERE bucket = TIMESTAMPTZ '2026-08-01 03:59:00+00'
       AND location = 'Purge Room' AND cluster = 'main'
       AND device_name = 'purge-light' AND mode = 'DAY';
    IF v_sample_count IS DISTINCT FROM 1
       OR EXISTS (
           SELECT 1 FROM public.monitoring_effective_setpoints_1min
            WHERE bucket = TIMESTAMPTZ '2026-08-01 03:58:00+00'
              AND location = 'Purge Room' AND cluster = 'main'
              AND device_name = 'purge-light' AND mode = 'DAY'
       ) THEN
        RAISE EXCEPTION 'effective_setpoints CAGG does not reflect retained 60-second anchor rows';
    END IF;

    SELECT sample_count INTO v_sample_count
      FROM public.monitoring_automation_state_1min
     WHERE bucket = TIMESTAMPTZ '2026-07-31 12:30:00+00'
       AND location = 'Purge Room' AND cluster = 'main'
       AND device_name = 'purge-heater';
    IF v_sample_count IS DISTINCT FROM 2
       OR EXISTS (
           SELECT 1 FROM public.monitoring_automation_state_1min
            WHERE bucket = TIMESTAMPTZ '2026-07-30 12:30:00+00'
              AND location = 'Purge Room' AND cluster = 'main'
              AND device_name = 'purge-heater'
       ) THEN
        RAISE EXCEPTION 'automation_state CAGG does not reflect retained tie anchors';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.monitoring_relay_overview_progress
         WHERE claimed_from IS NOT NULL
            OR (dirty_from IS NOT NULL AND dirty_from <
                date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
    ) THEN
        RAISE EXCEPTION 'relay read-model rebuild left historical dirty or claimed work';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.monitoring_overview_coverage
                    WHERE model_name = 'monitoring_relay_duration_1hour') THEN
        RAISE EXCEPTION 'relay hourly coverage was not updated by the rebuild procedure';
    END IF;
    IF EXISTS (
        WITH hourly AS (
            SELECT time_bucket(INTERVAL '1 day', bucket) AS bucket,
                   location, cluster, channel, device_id, device_name, device_type,
                   sum(on_microseconds)::BIGINT AS on_microseconds,
                   sum(off_microseconds)::BIGINT AS off_microseconds,
                   max(last_observed_at) AS last_observed_at,
                   max(source_watermark) AS source_watermark
              FROM public.monitoring_relay_duration_1hour
             WHERE active
             GROUP BY 1, 2, 3, 4, 5, 6, 7
        ), expected AS (
            SELECT * FROM hourly
             WHERE bucket < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto'
        ), actual AS (
            SELECT bucket, location, cluster, channel, device_id, device_name, device_type,
                   on_microseconds, off_microseconds, last_observed_at, source_watermark
              FROM public.monitoring_relay_duration_1day
             WHERE bucket < TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto'
        )
        SELECT 1 FROM (
            (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
        ) AS relay_daily_mismatch
    ) THEN
        RAISE EXCEPTION 'relay daily CAGG does not match rebuilt active hourly source rows';
    END IF;
END
$apply_assertions$;


DO $first_apply_notice$
BEGIN
    RAISE NOTICE 'first apply, protected-table, anchor, compressed-chunk, and derived-data proofs passed';
END
$first_apply_notice$;

SELECT pg_temp.purge_proof_snapshot('before_second_apply');
\ir ../../operator_purge_history_before_2026_08.sql
SELECT pg_temp.purge_proof_snapshot('after_second_apply');
DO $idempotency_assertions$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_deleted WHERE deleted_rows <> 0
    ) OR (SELECT COALESCE(sum(deleted_rows), 0) FROM pg_temp.operator_purge_deleted) <> 0 THEN
        RAISE EXCEPTION 'second apply deleted rows instead of being idempotent';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_temp.operator_purge_chunk_events) THEN
        RAISE EXCEPTION 'second apply dropped a full chunk despite having no removable rows';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            (SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'before_second_apply'
             EXCEPT ALL
             SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'after_second_apply')
            UNION ALL
            (SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'after_second_apply'
             EXCEPT ALL
             SELECT table_name, payload FROM pg_temp.purge_proof_rows WHERE stage = 'before_second_apply')
        ) AS changed_rows
    ) THEN
        RAISE EXCEPTION 'second apply changed source or protected row bytes';
    END IF;
END
$idempotency_assertions$;

DO $second_apply_notice$
BEGIN
    RAISE NOTICE 'second apply deleted zero rows and preserved all source/protected row bytes';
END
$second_apply_notice$;

-- Synthetic scalability proof: one million, then two million old automation
-- ticks across 20 device keys and at least 30 daily chunks. Time anchor
-- aggregation plus candidate scan should remain near-linear when doubled.
-- This changes only the disposable hypertable's future chunk interval.
SELECT public.set_chunk_time_interval('public.automation_state'::REGCLASS, INTERVAL '1 day');
INSERT INTO public.automation_state(
    timestamp, location, cluster, device_name, device_state, device_mode
)
SELECT TIMESTAMPTZ '2026-06-01 00:00:00+00'
           + ((sample_no - 1) % 30) * INTERVAL '1 day'
           + ((sample_no - 1) / 30) * INTERVAL '1 second',
       'Purge Bench', 'main',
       'bench-' || lpad(((sample_no - 1) % 20)::TEXT, 2, '0'),
       (sample_no % 2)::INTEGER, 'auto'
  FROM generate_series(1, 1000000) AS samples(sample_no);

DO $anchor_scalability_1m$
DECLARE
    v_started TIMESTAMPTZ;
    v_elapsed DOUBLE PRECISION;
    v_predicate TEXT;
    v_candidate_rows BIGINT;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    v_started := clock_timestamp();
    PERFORM pg_temp.operator_purge_build_anchor(
        'automation_state', 'timestamp',
        ARRAY['location', 'cluster', 'device_name'], v_cutoff);
    v_predicate := pg_temp.operator_purge_predicate(
        'automation_state', 'timestamp',
        ARRAY['location', 'cluster', 'device_name'], TRUE);
    EXECUTE format(
        'SELECT count(*) FROM public.automation_state AS m '
        'WHERE m.location = $2 AND %s', v_predicate
    ) INTO v_candidate_rows USING v_cutoff, 'Purge Bench';
    v_elapsed := EXTRACT(EPOCH FROM clock_timestamp() - v_started);
    IF v_candidate_rows <> 999980 THEN
        RAISE EXCEPTION '1M-row anchor proof expected 999980 candidates, got %', v_candidate_rows;
    END IF;
    RAISE NOTICE 'ANCHOR PERF rows=1000000 keys=20 seconds=% candidates=%',
        round(v_elapsed::NUMERIC, 3), v_candidate_rows;
    PERFORM set_config('purge_proof.anchor_1m_seconds', v_elapsed::TEXT, FALSE);
END
$anchor_scalability_1m$;

INSERT INTO public.automation_state(
    timestamp, location, cluster, device_name, device_state, device_mode
)
SELECT TIMESTAMPTZ '2026-06-01 00:00:00+00'
           + ((sample_no - 1) % 30) * INTERVAL '1 day'
           + ((sample_no - 1) / 30) * INTERVAL '1 second',
       'Purge Bench', 'main',
       'bench-' || lpad(((sample_no - 1) % 20)::TEXT, 2, '0'),
       (sample_no % 2)::INTEGER, 'auto'
  FROM generate_series(1000001, 2000000) AS samples(sample_no);
DO $anchor_chunk_coverage$
DECLARE
    v_chunk_count INTEGER;
    v_row_count BIGINT;
BEGIN
    SELECT count(*) INTO v_chunk_count
      FROM timescaledb_information.chunks
     WHERE hypertable_schema = 'public'
       AND hypertable_name = 'automation_state'
       AND range_start >= TIMESTAMPTZ '2026-06-01 00:00:00+00'
       AND range_end <= TIMESTAMPTZ '2026-07-01 00:00:00+00';
    SELECT count(*) INTO v_row_count
      FROM public.automation_state
     WHERE location = 'Purge Bench';
    IF v_chunk_count < 30 OR v_row_count <> 2000000 THEN
        RAISE EXCEPTION 'timing fixture requires 2M automation_state rows across at least 30 daily chunks (chunks %, rows %)',
            v_chunk_count, v_row_count;
    END IF;
    RAISE NOTICE 'ANCHOR PERF fixture rows=% daily_chunks=%', v_row_count, v_chunk_count;
END
$anchor_chunk_coverage$;

DO $anchor_scalability_2m$
DECLARE
    v_started TIMESTAMPTZ;
    v_elapsed DOUBLE PRECISION;
    v_one_million_seconds DOUBLE PRECISION :=
        current_setting('purge_proof.anchor_1m_seconds')::DOUBLE PRECISION;
    v_predicate TEXT;
    v_candidate_rows BIGINT;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    v_started := clock_timestamp();
    PERFORM pg_temp.operator_purge_build_anchor(
        'automation_state', 'timestamp',
        ARRAY['location', 'cluster', 'device_name'], v_cutoff);
    v_predicate := pg_temp.operator_purge_predicate(
        'automation_state', 'timestamp',
        ARRAY['location', 'cluster', 'device_name'], TRUE);
    EXECUTE format(
        'SELECT count(*) FROM public.automation_state AS m '
        'WHERE m.location = $2 AND %s', v_predicate
    ) INTO v_candidate_rows USING v_cutoff, 'Purge Bench';
    v_elapsed := EXTRACT(EPOCH FROM clock_timestamp() - v_started);
    IF v_candidate_rows <> 1999980 THEN
        RAISE EXCEPTION '2M-row anchor proof expected 1999980 candidates, got %', v_candidate_rows;
    END IF;
    IF v_elapsed > v_one_million_seconds * 3.5 + 0.5 THEN
        RAISE EXCEPTION 'anchor refresh scaling exceeded 3.5x plus 0.5s (1M=%s, 2M=%s)',
            v_one_million_seconds, v_elapsed;
    END IF;
    RAISE NOTICE 'ANCHOR PERF rows=2000000 keys=20 seconds=% candidates=% ratio=%',
        round(v_elapsed::NUMERIC, 3), v_candidate_rows,
        round((v_elapsed / v_one_million_seconds)::NUMERIC, 2);
END
$anchor_scalability_2m$;
