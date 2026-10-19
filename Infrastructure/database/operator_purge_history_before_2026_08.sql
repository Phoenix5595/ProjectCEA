\set ON_ERROR_STOP on

-- Operator-only, dry-run-first removal of removable time-series observations
-- before 2026-08-01 00:00 America/Toronto. Run this file directly with psql
-- autocommit enabled; do not wrap it in BEGIN or --single-transaction because
-- apply commits complete old chunk ranges individually, then the remaining
-- row-wise work in Toronto-local day batches.
--
-- Default: report only. The only deletion targets are the ten fixed
-- source_retention.ANCHOR_COLUMNS time-series observations below. Apply
-- requires -v apply=1 and -v confirm_database=<current_database()>; only the
-- owner may apply. IDs/registries/current state, static setpoint and period
-- configuration, schedules/rules, Calendar, config, archive, and user tables
-- are never targets.

\if :{?apply}
\else
\set apply 0
\endif
\if :{?confirm_database}
\else
\set confirm_database ''
\endif

SELECT set_config('operator_purge_history.apply', :'apply', false);
SELECT set_config('operator_purge_history.confirm_database', :'confirm_database', false);

DO $mode_guard$
DECLARE
    v_apply TEXT := current_setting('operator_purge_history.apply', true);
    v_confirm_database TEXT := current_setting('operator_purge_history.confirm_database', true);
BEGIN
    IF v_apply NOT IN ('0', '1') THEN
        RAISE EXCEPTION 'apply must be 0 (dry run) or 1';
    END IF;
    IF v_apply = '1' AND v_confirm_database IS DISTINCT FROM current_database() THEN
        RAISE EXCEPTION
            'apply refused: -v confirm_database must equal current_database() (current_database=%)',
            current_database();
    END IF;
END
$mode_guard$;

CREATE TEMP TABLE IF NOT EXISTS operator_purge_targets (
    table_name TEXT PRIMARY KEY,
    time_column TEXT NOT NULL,
    anchor_columns TEXT[] NOT NULL,
    required_columns TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    present BOOLEAN NOT NULL DEFAULT FALSE,
    has_location BOOLEAN NOT NULL DEFAULT FALSE,
    is_hypertable BOOLEAN NOT NULL DEFAULT FALSE
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_targets;
INSERT INTO pg_temp.operator_purge_targets
    (table_name, time_column, anchor_columns, required_columns)
VALUES
    ('measurement', 'time', ARRAY['sensor_id'], ARRAY[]::TEXT[]),
    ('actuator_events', 'time', ARRAY['device_id'], ARRAY[]::TEXT[]),
    ('automation_state', 'timestamp', ARRAY['location', 'cluster', 'device_name'], ARRAY[]::TEXT[]),
    ('effective_setpoints', 'timestamp', ARRAY['location', 'cluster', 'device_name', 'mode'], ARRAY[]::TEXT[]),
    ('control_history', 'timestamp', ARRAY['location', 'cluster', 'device_name', 'channel'], ARRAY['manual_expires_at']),
    ('relay_observation', 'observed_at', ARRAY['channel'], ARRAY['location', 'reason']),
    ('monitoring_room_photoperiod', 'observed_at', ARRAY['location', 'cluster', 'source'], ARRAY[]::TEXT[]),
    ('monitoring_photoperiod_coverage', 'observed_at', ARRAY['location', 'cluster'], ARRAY[]::TEXT[]),
    ('mode_transition_history', 'triggered_at', ARRAY['location', 'cluster'], ARRAY[]::TEXT[]),
    ('pid_parameter_history', 'timestamp', ARRAY['location', 'cluster', 'device_type'], ARRAY[]::TEXT[]);

-- This allowlist must remain exactly the ten source_retention.ANCHOR_COLUMNS
-- observation streams. Unknown and non-time-series relations are never targets.
DO $fixed_source_allowlist$
DECLARE
    v_expected CONSTANT TEXT[] := ARRAY[
        'measurement', 'actuator_events', 'automation_state', 'effective_setpoints',
        'control_history', 'relay_observation', 'monitoring_room_photoperiod',
        'monitoring_photoperiod_coverage', 'mode_transition_history', 'pid_parameter_history'
    ];
BEGIN
    IF (SELECT count(*) FROM pg_temp.operator_purge_targets) <> cardinality(v_expected)
       OR EXISTS (
           SELECT 1 FROM pg_temp.operator_purge_targets
            WHERE NOT (table_name = ANY (v_expected))
               OR cardinality(anchor_columns) = 0
       ) THEN
        RAISE EXCEPTION 'purge source list must be exactly the ten approved ANCHOR_COLUMNS streams';
    END IF;
END
$fixed_source_allowlist$;

CREATE TEMP TABLE IF NOT EXISTS operator_purge_derived_targets (
    view_name TEXT PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_time_column TEXT NOT NULL,
    bucket_column TEXT,
    bucket_width INTERVAL NOT NULL,
    refresh_order INTEGER NOT NULL,
    parent_view_name TEXT,
    refresh_start TIMESTAMPTZ,
    present BOOLEAN NOT NULL DEFAULT FALSE
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_derived_targets;
INSERT INTO pg_temp.operator_purge_derived_targets
    (view_name, source_table, source_time_column, bucket_width, refresh_order, parent_view_name)
VALUES
    -- Legacy measurement CAGGs from the deleted migration remain in the
    -- deployed schema and must be recomputed along with canonical models.
    ('measurement_1min', 'measurement', 'time', INTERVAL '1 minute', 10, NULL),
    ('measurement_5min', 'measurement', 'time', INTERVAL '5 minutes', 11, NULL),
    ('measurement_hourly', 'measurement', 'time', INTERVAL '1 hour', 20, NULL),
    ('measurement_daily', 'measurement', 'time', INTERVAL '1 day', 30, NULL),
    ('monitoring_measurement_1min', 'measurement', 'time', INTERVAL '1 minute', 10, NULL),
    ('monitoring_measurement_5min', 'measurement', 'time', INTERVAL '5 minutes', 11, NULL),
    ('monitoring_measurement_1hour', 'measurement', 'time', INTERVAL '1 hour', 20, NULL),
    ('monitoring_measurement_1day', 'measurement', 'time', INTERVAL '1 day', 30, 'monitoring_measurement_1hour'),
    ('monitoring_effective_setpoints_1min', 'effective_setpoints', 'timestamp', INTERVAL '1 minute', 10, NULL),
    ('monitoring_effective_setpoints_5min', 'effective_setpoints', 'timestamp', INTERVAL '5 minutes', 11, NULL),
    ('monitoring_light_intensity_1hour', 'effective_setpoints', 'timestamp', INTERVAL '1 hour', 20, NULL),
    ('monitoring_light_intensity_1day', 'effective_setpoints', 'timestamp', INTERVAL '1 day', 30, 'monitoring_light_intensity_1hour'),
    ('monitoring_automation_state_1min', 'automation_state', 'timestamp', INTERVAL '1 minute', 10, NULL),
    ('monitoring_automation_state_5min', 'automation_state', 'timestamp', INTERVAL '5 minutes', 11, NULL),
    ('monitoring_requested_pid_1hour', 'automation_state', 'timestamp', INTERVAL '1 hour', 20, NULL),
    ('monitoring_requested_pid_1day', 'automation_state', 'timestamp', INTERVAL '1 day', 30, 'monitoring_requested_pid_1hour'),
    ('monitoring_relay_duration_1day', 'relay_observation', 'observed_at', INTERVAL '1 day', 40, 'monitoring_relay_duration_1hour');

CREATE TEMP TABLE IF NOT EXISTS operator_purge_deleted (
    table_name TEXT NOT NULL,
    toronto_day DATE NOT NULL,
    deleted_rows BIGINT NOT NULL
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_deleted;
CREATE TEMP TABLE IF NOT EXISTS operator_purge_chunk_events (
    table_name TEXT NOT NULL,
    range_start TIMESTAMPTZ NOT NULL,
    range_end TIMESTAMPTZ NOT NULL,
    chunks_dropped BIGINT NOT NULL,
    deleted_rows BIGINT NOT NULL,
    restored_rows BIGINT NOT NULL
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_chunk_events;
CREATE TEMP TABLE IF NOT EXISTS operator_purge_relay_calls (
    call_order INTEGER PRIMARY KEY,
    command_text TEXT NOT NULL
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_relay_calls;
CREATE TEMP TABLE IF NOT EXISTS operator_purge_cagg_calls (
    call_order INTEGER PRIMARY KEY,
    command_text TEXT NOT NULL
) ON COMMIT PRESERVE ROWS;
TRUNCATE pg_temp.operator_purge_cagg_calls;

CREATE OR REPLACE FUNCTION pg_temp.operator_purge_build_anchor(
    p_table TEXT,
    p_time_column TEXT,
    p_anchor_columns TEXT[],
    p_cutoff TIMESTAMPTZ
)
RETURNS VOID
LANGUAGE plpgsql
AS $build_anchor$
DECLARE
    v_anchor_name TEXT := 'operator_purge_anchor_' || p_table;
    v_key_columns TEXT;
BEGIN
    SELECT string_agg(format('%I', key_column), ', ' ORDER BY ordinal)
      INTO v_key_columns
      FROM unnest(p_anchor_columns) WITH ORDINALITY AS keys(key_column, ordinal);
    EXECUTE format('DROP TABLE IF EXISTS pg_temp.%I', v_anchor_name);
    EXECUTE format(
        'CREATE TEMP TABLE %1$I ON COMMIT PRESERVE ROWS AS '
        'SELECT %2$s, max(%3$I) AS latest, '
        'max(%3$I) FILTER (WHERE %3$I < $1) AS predecessor '
        'FROM public.%4$I GROUP BY %2$s',
        v_anchor_name, v_key_columns, p_time_column, p_table
    ) USING p_cutoff;
    EXECUTE format('ANALYZE pg_temp.%I', v_anchor_name);
END
$build_anchor$;

CREATE OR REPLACE FUNCTION pg_temp.operator_purge_predicate(
    p_table TEXT,
    p_time_column TEXT,
    p_anchor_columns TEXT[],
    p_has_location BOOLEAN
)
RETURNS TEXT
LANGUAGE plpgsql
AS $predicate$
DECLARE
    v_anchor_name TEXT := 'operator_purge_anchor_' || p_table;
    v_same_key TEXT;
    v_result TEXT;
BEGIN
    SELECT string_agg(
               format('anchors.%1$I IS NOT DISTINCT FROM m.%1$I', key_column),
               ' AND ' ORDER BY ordinal
           )
      INTO v_same_key
      FROM unnest(p_anchor_columns) WITH ORDINALITY AS keys(key_column, ordinal);
    v_result := format(
        'm.%1$I IS NOT NULL AND m.%1$I < $1 AND EXISTS ('
        'SELECT 1 FROM pg_temp.%2$I AS anchors WHERE %3$s '
        'AND m.%1$I < anchors.latest AND m.%1$I < anchors.predecessor)',
        p_time_column, v_anchor_name, v_same_key
    );
    IF p_has_location THEN
        -- Mirrors source_retention.removable_observation_sql: location-scoped
        -- streams never delete rows whose stored location is NULL.
        v_result := v_result || ' AND m.location IS NOT NULL';
    END IF;
    IF p_table IN ('effective_setpoints', 'monitoring_room_photoperiod') THEN
        v_result := v_result || format(' AND m.%I < $1 - INTERVAL ''60 seconds''', p_time_column);
    END IF;
    IF p_table = 'control_history' THEN
        v_result := v_result || ' AND m.manual_expires_at IS NULL';
    END IF;
    IF p_table = 'relay_observation' THEN
        v_result := v_result
            || ' AND m.channel IS NOT NULL'
            || ' AND m.reason NOT IN (''heartbeat'', ''session_started'', ''session_ended'')';
    END IF;
    RETURN '(' || v_result || ')';
END
$predicate$;

DO $schema_preflight$
DECLARE
    v_target RECORD;
    v_column TEXT;
    v_relation REGCLASS;
    v_relkind "char";
    v_type OID;
    v_has_location BOOLEAN;
    v_is_hypertable BOOLEAN;
    v_bucket_column TEXT;
    v_cagg_catalog REGCLASS := to_regclass('_timescaledb_catalog.continuous_agg');
    v_extension_version TEXT;
    v_source_min TIMESTAMPTZ;
    v_materialized_min TIMESTAMPTZ;
    v_refresh_start TIMESTAMPTZ;
    v_is_cagg BOOLEAN;
    v_relay_any BOOLEAN;
    v_relay_complete BOOLEAN;
BEGIN
    IF to_regclass('timescaledb_information.hypertables') IS NOT NULL THEN
        SELECT extversion INTO v_extension_version
          FROM pg_extension WHERE extname = 'timescaledb';
    END IF;
    FOR v_target IN SELECT * FROM pg_temp.operator_purge_targets ORDER BY table_name LOOP
        v_relation := to_regclass(format('public.%I', v_target.table_name));
        IF v_relation IS NULL THEN
            CONTINUE;
        END IF;

        SELECT relkind INTO v_relkind FROM pg_class WHERE oid = v_relation;
        IF v_relkind NOT IN ('r', 'p') THEN
            RAISE EXCEPTION 'public.% exists but is not a deletable table', v_target.table_name;
        END IF;
        v_is_hypertable := FALSE;
        IF to_regclass('timescaledb_information.hypertables') IS NOT NULL THEN
            EXECUTE
                'SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables '
                'WHERE hypertable_schema = ''public'' AND hypertable_name = $1)'
                INTO v_is_hypertable USING v_target.table_name;
        END IF;
        IF v_is_hypertable AND to_regclass('timescaledb_information.chunks') IS NULL THEN
            RAISE EXCEPTION 'Timescale chunk catalog is unavailable for hypertable public.%', v_target.table_name;
        END IF;

        FOR v_column IN
            SELECT unnest(
                ARRAY[v_target.time_column]
                || COALESCE(v_target.anchor_columns, ARRAY[]::TEXT[])
                || v_target.required_columns
            )
        LOOP
            SELECT a.atttypid INTO v_type
              FROM pg_attribute AS a
             WHERE a.attrelid = v_relation
               AND a.attname = v_column
               AND a.attnum > 0
               AND NOT a.attisdropped;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'public.% is missing required column %',
                    v_target.table_name, v_column;
            END IF;
            IF v_column = v_target.time_column AND v_type <> 'timestamp with time zone'::REGTYPE THEN
                RAISE EXCEPTION 'public.%.% must be timestamptz, found %',
                    v_target.table_name, v_column, format_type(v_type, NULL);
            END IF;
        END LOOP;

        SELECT EXISTS (
            SELECT 1 FROM pg_attribute AS a
             WHERE a.attrelid = v_relation
               AND a.attname = 'location'
               AND a.attnum > 0
               AND NOT a.attisdropped
        ) INTO v_has_location;
        UPDATE pg_temp.operator_purge_targets
           SET present = TRUE, has_location = v_has_location, is_hypertable = v_is_hypertable
         WHERE table_name = v_target.table_name;
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_temp.operator_purge_targets WHERE present AND is_hypertable)
       AND (v_extension_version IS NULL OR v_extension_version NOT LIKE '2.28.%') THEN
        RAISE EXCEPTION
            'chunk-drop purge requires the repository-supported TimescaleDB 2.28.x procedures; found %',
            COALESCE(v_extension_version, 'not installed');
    END IF;

    IF v_cagg_catalog IS NOT NULL THEN
        SELECT extversion INTO v_extension_version
          FROM pg_extension WHERE extname = 'timescaledb';
        FOR v_target IN SELECT * FROM pg_temp.operator_purge_derived_targets ORDER BY refresh_order, view_name LOOP
            IF to_regclass(format('public.%I', v_target.view_name)) IS NULL THEN
                CONTINUE;
            END IF;
            EXECUTE
                'SELECT EXISTS (SELECT 1 FROM _timescaledb_catalog.continuous_agg '
                'WHERE user_view_schema = ''public'' AND user_view_name = $1)'
                INTO v_is_cagg USING v_target.view_name;
            IF NOT v_is_cagg THEN
                RAISE EXCEPTION 'public.% exists but is not a Timescale continuous aggregate',
                    v_target.view_name;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_temp.operator_purge_targets
                 WHERE table_name = v_target.source_table AND present
            ) THEN
                RAISE EXCEPTION 'public.% depends on missing source table public.%',
                    v_target.view_name, v_target.source_table;
            END IF;

            v_bucket_column := NULL;
            IF EXISTS (
                SELECT 1 FROM pg_attribute
                 WHERE attrelid = to_regclass(format('public.%I', v_target.view_name))
                   AND attname = 'bucket' AND attnum > 0 AND NOT attisdropped
            ) THEN
                v_bucket_column := 'bucket';
            ELSIF EXISTS (
                SELECT 1 FROM pg_attribute
                 WHERE attrelid = to_regclass(format('public.%I', v_target.view_name))
                   AND attname = 'time' AND attnum > 0 AND NOT attisdropped
            ) THEN
                -- cea_schema.sql's legacy measurement_hourly/daily views use
                -- a "time" bucket; the deleted migration used "bucket".
                v_bucket_column := 'time';
            ELSE
                RAISE EXCEPTION 'public.% has no recognized bucket/time column',
                    v_target.view_name;
            END IF;
            EXECUTE format(
                'SELECT (SELECT min(source_row.%1$I) FROM public.%2$I AS source_row), '
                '(SELECT min(materialized.%4$I) FROM public.%3$I AS materialized)',
                v_target.source_time_column, v_target.source_table,
                v_target.view_name, v_bucket_column
            ) INTO v_source_min, v_materialized_min;
            IF v_source_min IS NOT NULL THEN
                v_source_min := date_bin(
                    v_target.bucket_width, v_source_min,
                    TIMESTAMPTZ '2000-01-01 00:00:00+00'
                );
            END IF;
            v_refresh_start := LEAST(
                COALESCE(v_source_min, 'infinity'::TIMESTAMPTZ),
                COALESCE(v_materialized_min, 'infinity'::TIMESTAMPTZ)
            );
            IF v_refresh_start = 'infinity'::TIMESTAMPTZ THEN
                v_refresh_start := NULL;
            END IF;
            UPDATE pg_temp.operator_purge_derived_targets
               SET present = TRUE, refresh_start = v_refresh_start,
                   bucket_column = v_bucket_column
             WHERE view_name = v_target.view_name;
        END LOOP;

        FOR v_target IN
            SELECT * FROM pg_temp.operator_purge_derived_targets WHERE present
        LOOP
            IF v_target.parent_view_name = 'monitoring_relay_duration_1hour' THEN
                IF to_regclass('public.monitoring_relay_duration_1hour') IS NULL THEN
                    RAISE EXCEPTION 'public.% requires missing parent public.monitoring_relay_duration_1hour',
                        v_target.view_name;
                END IF;
            ELSIF v_target.parent_view_name IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_temp.operator_purge_derived_targets AS parent
                       WHERE parent.view_name = v_target.parent_view_name AND parent.present
                  ) THEN
                RAISE EXCEPTION 'public.% requires missing parent aggregate public.%',
                    v_target.view_name, v_target.parent_view_name;
            END IF;
        END LOOP;

        IF EXISTS (SELECT 1 FROM pg_temp.operator_purge_derived_targets WHERE present)
           AND (v_extension_version IS NULL OR v_extension_version NOT LIKE '2.28.%') THEN
            RAISE EXCEPTION
                'native derived refresh requires the repository-supported TimescaleDB 2.28.x procedures; found %',
                COALESCE(v_extension_version, 'not installed');
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_derived_targets d
         WHERE to_regclass(format('public.%I', d.view_name)) IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Timescale continuous-aggregate catalog is unavailable for existing monitoring aggregates';
    END IF;

    v_relay_any := to_regclass('public.monitoring_relay_duration_1hour') IS NOT NULL
        OR to_regclass('public.monitoring_relay_overview_progress') IS NOT NULL
        OR to_regprocedure('public.monitoring_rebuild_relay_overview(integer,jsonb)') IS NOT NULL
        OR EXISTS (SELECT 1 FROM pg_temp.operator_purge_derived_targets
                    WHERE view_name = 'monitoring_relay_duration_1day' AND present);
    IF v_relay_any THEN
        v_relay_complete :=
            to_regclass('public.monitoring_relay_duration_1hour') IS NOT NULL
            AND to_regclass('public.monitoring_relay_overview_progress') IS NOT NULL
            AND to_regclass('public.monitoring_overview_coverage') IS NOT NULL
            AND to_regclass('public.monitoring_history_cagg_watermark') IS NOT NULL
            AND to_regprocedure('public.monitoring_rebuild_relay_overview(integer,jsonb)') IS NOT NULL
            AND to_regprocedure('public.monitoring_relay_duration_slices(timestamptz,timestamptz,bigint)') IS NOT NULL
            AND to_regprocedure('public.monitoring_relay_empty_span_end(timestamptz,timestamptz,bigint)') IS NOT NULL
            AND to_regprocedure('public.monitoring_overview_unacknowledged_failures(jsonb)') IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.monitoring_relay_overview_progress WHERE id)
            AND EXISTS (SELECT 1 FROM pg_temp.operator_purge_derived_targets
                         WHERE view_name = 'monitoring_relay_duration_1day' AND present);
        IF NOT v_relay_complete THEN
            RAISE EXCEPTION 'relay derived read model is partially installed; refusing source deletion';
        END IF;
    END IF;
END
$schema_preflight$;
-- The full report (anchor snapshot + per-table/chunk counts) is a dry-run
-- product: it re-reads every target table and took ~3 h on production. The
-- apply phase builds its own anchors and reports per batch, so apply skips it.
-- Review the dry-run output before applying.
\if :apply
\echo 'APPLY database cutoff=2026-08-01 00:00 America/Toronto: skipping the dry-run report; anchors are built in the apply phase'
\else
DO $dry_anchor_snapshot$
DECLARE
    v_target RECORD;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    FOR v_target IN
        SELECT * FROM pg_temp.operator_purge_targets WHERE present ORDER BY table_name
    LOOP
        PERFORM pg_temp.operator_purge_build_anchor(
            v_target.table_name, v_target.time_column, v_target.anchor_columns, v_cutoff);
    END LOOP;
END
$dry_anchor_snapshot$;


DO $dry_run_report$
DECLARE
    v_target RECORD;
    v_predicate TEXT;
    v_old_rows BIGINT;
    v_delete_rows BIGINT;
    v_all_anchor_rows BIGINT;
    v_chunk_candidate_rows BIGINT;
    v_chunk_preserve_rows BIGINT;
    v_chunk_anchor_rows BIGINT;
    v_chunk_count BIGINT;
    v_chunk_bytes BIGINT;
    v_anchor_predicate TEXT;
    v_key_matches TEXT;
    v_min_time TIMESTAMPTZ;
    v_max_time TIMESTAMPTZ;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    IF current_setting('operator_purge_history.apply', true) = '1' THEN
        RAISE NOTICE 'APPLY PLAN database=% cutoff=%', current_database(), v_cutoff;
    ELSE
        RAISE NOTICE 'DRY RUN ONLY database=% cutoff=%; no persistent data will change',
            current_database(), v_cutoff;
    END IF;

    FOR v_target IN SELECT * FROM pg_temp.operator_purge_targets ORDER BY table_name LOOP
        IF NOT v_target.present THEN
            RAISE NOTICE 'SKIP public.%: relation is absent', v_target.table_name;
            CONTINUE;
        END IF;
        v_predicate := pg_temp.operator_purge_predicate(
            v_target.table_name, v_target.time_column, v_target.anchor_columns,
            v_target.has_location);
        v_chunk_count := 0;
        v_chunk_bytes := 0;
        v_chunk_candidate_rows := 0;
        v_chunk_preserve_rows := 0;
        v_chunk_anchor_rows := 0;
        SELECT string_agg(
                   format('anchors.%1$I IS NOT DISTINCT FROM m.%1$I', key_column),
                   ' AND ' ORDER BY ordinal
               )
          INTO v_key_matches
          FROM unnest(v_target.anchor_columns) WITH ORDINALITY AS keys(key_column, ordinal);
        v_anchor_predicate := format(
            'EXISTS (SELECT 1 FROM pg_temp.%1$I AS anchors WHERE %2$s '
            'AND (m.%3$I = anchors.latest OR m.%3$I = anchors.predecessor))',
            'operator_purge_anchor_' || v_target.table_name,
            v_key_matches, v_target.time_column
        );
        IF v_target.is_hypertable THEN
            EXECUTE format(
                'WITH drop_ranges AS ('
                'SELECT DISTINCT c.range_start, c.range_end '
                'FROM timescaledb_information.chunks AS c '
                'WHERE c.hypertable_schema = ''public'' AND c.hypertable_name = %5$L '
                'AND c.range_end <= $1 '
                'AND EXISTS (SELECT 1 FROM public.%1$I AS m '
                'WHERE m.%2$I >= c.range_start AND m.%2$I < c.range_end AND %3$s)'
                '), chunk_sizes AS ('
                'SELECT chunk_schema, chunk_name, total_bytes '
                'FROM public.chunks_detailed_size(%6$L::regclass)'
                '), chunk_stats AS ('
                'SELECT count(*)::BIGINT AS chunk_count, '
                'COALESCE(sum(chunk_sizes.total_bytes), 0)::BIGINT AS total_bytes '
                'FROM timescaledb_information.chunks AS c '
                'JOIN drop_ranges AS r USING (range_start, range_end) '
                'LEFT JOIN chunk_sizes ON chunk_sizes.chunk_schema = c.chunk_schema::text '
                'AND chunk_sizes.chunk_name = c.chunk_name::text '
                'WHERE c.hypertable_schema = ''public'' AND c.hypertable_name = %5$L'
                '), source_stats AS ('
                'SELECT count(*) FILTER (WHERE %3$s IS TRUE)::BIGINT AS candidate_rows, '
                'count(*) FILTER (WHERE %3$s IS NOT TRUE)::BIGINT AS preserve_rows, '
                'count(*) FILTER (WHERE %4$s)::BIGINT AS anchor_rows '
                'FROM public.%1$I AS m '
                'WHERE EXISTS (SELECT 1 FROM drop_ranges AS r '
                'WHERE m.%2$I >= r.range_start AND m.%2$I < r.range_end)'
                ') SELECT chunk_stats.chunk_count, chunk_stats.total_bytes, '
                'source_stats.candidate_rows, source_stats.preserve_rows, source_stats.anchor_rows '
                'FROM chunk_stats CROSS JOIN source_stats',
                v_target.table_name, v_target.time_column, v_predicate,
                v_anchor_predicate, v_target.table_name,
                'public.' || v_target.table_name
            ) INTO v_chunk_count, v_chunk_bytes, v_chunk_candidate_rows,
                    v_chunk_preserve_rows, v_chunk_anchor_rows
              USING v_cutoff;
        END IF;
        RAISE NOTICE 'CHUNK PLAN public.%: chunks_to_drop=% total_bytes=% removable_rows_in_chunks=% anchors_to_preserve=% other_nonremovable_rows_to_preserve=%',
            v_target.table_name, v_chunk_count, v_chunk_bytes, v_chunk_candidate_rows,
            v_chunk_anchor_rows, v_chunk_preserve_rows - v_chunk_anchor_rows;
        EXECUTE format(
            'SELECT count(*) FILTER (WHERE m.%1$I < $1)::BIGINT, '
            'count(*) FILTER (WHERE m.%1$I < $1 AND %2$s)::BIGINT, '
            'min(m.%1$I), max(m.%1$I) FROM public.%3$I AS m',
            v_target.time_column, v_predicate, v_target.table_name
        ) INTO v_old_rows, v_delete_rows, v_min_time, v_max_time USING v_cutoff;
        EXECUTE format(
            'SELECT count(*) FROM public.%1$I AS m '
            'WHERE m.%2$I < $1 AND %3$s',
            v_target.table_name, v_target.time_column, v_anchor_predicate
        ) INTO v_all_anchor_rows USING v_cutoff;
        RAISE NOTICE 'TABLE public.%: rows_to_delete=% rows_kept_before_cutoff=% anchors_kept_before_cutoff=% other_protected_rows_kept_before_cutoff=% min_time=% max_time=%',
            v_target.table_name, v_delete_rows, v_old_rows - v_delete_rows,
            v_all_anchor_rows, v_old_rows - v_delete_rows - v_all_anchor_rows,
            v_min_time, v_max_time;
        RAISE NOTICE 'ROW-WISE PLAN public.%: rows_to_delete_rowwise=%',
            v_target.table_name, v_delete_rows - v_chunk_candidate_rows;
        IF v_target.is_hypertable THEN
            RAISE NOTICE 'CHUNK PLAN public.% uses Timescale drop_chunks only when range_end <= cutoff; cutoff-containing chunks use the row-wise path',
                v_target.table_name;
        ELSE
            RAISE NOTICE 'ROW-WISE PLAN public.% is a plain table; all eligible deletes use the row-wise path',
                v_target.table_name;
        END IF;
    END LOOP;

    FOR v_target IN
        SELECT * FROM pg_temp.operator_purge_derived_targets
         WHERE present
           AND refresh_start IS NOT NULL
           AND refresh_start < v_cutoff
         ORDER BY refresh_order, view_name
    LOOP
        RAISE NOTICE 'DERIVED CAGG public.% would be refreshed for [%, %)',
            v_target.view_name, v_target.refresh_start, v_cutoff;
    END LOOP;
    FOR v_target IN
        SELECT * FROM pg_temp.operator_purge_derived_targets
         WHERE present
           AND (refresh_start IS NULL OR refresh_start >= v_cutoff)
         ORDER BY refresh_order, view_name
    LOOP
        RAISE NOTICE 'DERIVED CAGG public.% has no source/materialized bucket before the cutoff; no refresh is needed',
            v_target.view_name;
    END LOOP;
    IF to_regprocedure('public.monitoring_rebuild_relay_overview(integer,jsonb)') IS NOT NULL THEN
        RAISE NOTICE 'DERIVED read model public.monitoring_relay_duration_1hour and relay coverage would be rebuilt with monitoring_rebuild_relay_overview';
    END IF;
END
$dry_run_report$;
\endif

\if :apply
DO $cagg_call_plan$
DECLARE
    v_target RECORD;
    v_day TIMESTAMPTZ;
    v_day_end TIMESTAMPTZ;
    v_call_end TIMESTAMPTZ;
    v_has_rows BOOLEAN;
    v_segment_has_rows BOOLEAN;
    v_uses_row_order BOOLEAN;
    v_segment RECORD;
    v_call_order INTEGER := 0;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
    v_last_day TIMESTAMPTZ;
BEGIN
    v_last_day := date_trunc(
        'day', (v_cutoff - INTERVAL '1 microsecond') AT TIME ZONE 'UTC'
    ) AT TIME ZONE 'UTC';

    FOR v_target IN
        SELECT * FROM pg_temp.operator_purge_derived_targets
         WHERE present
           AND refresh_start IS NOT NULL
           AND refresh_start < v_cutoff
         ORDER BY refresh_order, view_name
    LOOP
        -- Production's legacy measurement_1min/5min/hourly/daily are owned by
        -- postgres; refresh requires ownership. Skip (and report) any CAGG the
        -- running role does not own instead of failing the whole refresh run.
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.continuous_aggregates AS owned
             WHERE owned.view_schema = 'public'
               AND owned.view_name = v_target.view_name
               AND owned.view_owner = current_user
        ) THEN
            RAISE NOTICE 'CAGG refresh SKIPPED view=public.% (owned by %, not %): refresh it as its owner or drop it',
                v_target.view_name,
                (SELECT view_owner FROM timescaledb_information.continuous_aggregates
                  WHERE view_schema = 'public' AND view_name = v_target.view_name),
                current_user;
            CONTINUE;
        END IF;
        SELECT COALESCE((
            SELECT cagg_def.view_definition ~* 'ROW[[:space:]]*[(]'
              FROM timescaledb_information.continuous_aggregates AS cagg_def
             WHERE cagg_def.view_schema = 'public'
               AND cagg_def.view_name = v_target.view_name
        ), FALSE)
          INTO v_uses_row_order;
        v_day := date_trunc(
            'day', v_target.refresh_start AT TIME ZONE 'UTC'
        ) AT TIME ZONE 'UTC';
        WHILE v_day <= v_last_day LOOP
            v_day_end := ((v_day AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC';
            EXECUTE format(
                'SELECT EXISTS (SELECT 1 FROM public.%1$I AS source_row '
                'WHERE source_row.%2$I >= $1 AND source_row.%2$I < $2) '
                'OR EXISTS (SELECT 1 FROM public.%3$I AS materialized '
                'WHERE materialized.%4$I >= $1 AND materialized.%4$I < $2)',
                v_target.source_table, v_target.source_time_column,
                v_target.view_name, v_target.bucket_column
            ) INTO v_has_rows USING v_day, v_day_end;
            v_call_end := LEAST(v_day_end, v_cutoff);
            IF v_call_end - v_day < v_target.bucket_width THEN
                -- Timescale requires at least one whole aggregate bucket;
                -- refresh the containing bucket even when cutoff is partial.
                v_call_end := v_day + v_target.bucket_width;
            END IF;

            IF v_has_rows THEN
                -- ROW()-keyed partial aggregates cannot refresh across source
                -- chunk boundaries on the deployed Timescale version. Split
                -- every candidate window at its source hypertable's exact
                -- temporal chunk edges; include gaps so stale materialized
                -- buckets are still refreshed.
                FOR v_segment IN
                    WITH chunk_ranges AS (
                        SELECT DISTINCT
                               GREATEST(c.range_start, v_day) AS segment_start,
                               LEAST(c.range_end, v_call_end) AS segment_end
                          FROM timescaledb_information.chunks AS c
                         WHERE v_uses_row_order
                           AND c.hypertable_schema = 'public'
                           AND c.hypertable_name = v_target.source_table
                           AND c.range_start < v_call_end
                           AND c.range_end > v_day
                    ), boundaries AS (
                        SELECT v_day AS split_at
                        UNION
                        SELECT v_call_end
                        UNION
                        SELECT segment_start FROM chunk_ranges
                        UNION
                        SELECT segment_end FROM chunk_ranges
                    ), segments AS (
                        SELECT split_at AS segment_start,
                               LEAD(split_at) OVER (ORDER BY split_at) AS segment_end
                          FROM boundaries
                    )
                    SELECT segment_start, segment_end
                      FROM segments
                     WHERE segment_end IS NOT NULL
                       AND segment_start < segment_end
                     ORDER BY segment_start
                LOOP
                    EXECUTE format(
                        'SELECT EXISTS (SELECT 1 FROM public.%1$I AS source_row '
                        'WHERE source_row.%2$I >= $1 AND source_row.%2$I < $2) '
                        'OR EXISTS (SELECT 1 FROM public.%3$I AS materialized '
                        'WHERE materialized.%4$I >= $1 AND materialized.%4$I < $2)',
                        v_target.source_table, v_target.source_time_column,
                        v_target.view_name, v_target.bucket_column
                    ) INTO v_segment_has_rows
                      USING v_segment.segment_start, v_segment.segment_end;
                    IF v_segment_has_rows THEN
                        v_call_order := v_call_order + 1;
                        RAISE NOTICE 'CAGG refresh planned call=% view=public.% row_order=% window=[%,%)',
                            v_call_order, v_target.view_name, v_uses_row_order,
                            v_segment.segment_start, v_segment.segment_end;
                        INSERT INTO pg_temp.operator_purge_cagg_calls(call_order, command_text)
                        VALUES (
                            v_call_order,
                            format(
                                'CALL public.refresh_continuous_aggregate(%L::regclass, %L::timestamptz, '
                                '%L::timestamptz, true, %L::jsonb);',
                                'public.' || v_target.view_name,
                                v_segment.segment_start,
                                v_segment.segment_end,
                                '{"buckets_per_batch":0}'
                            )
                        );
                    END IF;
                END LOOP;
            END IF;
            v_day := v_day_end;
        END LOOP;
    END LOOP;
    RAISE NOTICE 'Planned % bounded continuous-aggregate refresh calls', v_call_order;
END
$cagg_call_plan$;
DO $apply_batches$
DECLARE
    v_target RECORD;
    v_predicate TEXT;
    v_next_time TIMESTAMPTZ;
    v_day_start TIMESTAMPTZ;
    v_day_end TIMESTAMPTZ;
    v_deleted BIGINT;
    v_chunk RECORD;
    v_chunk_deleted BIGINT;
    v_chunk_saved BIGINT;
    v_chunk_restored BIGINT;
    v_chunk_count BIGINT;
    v_dropped_chunks BIGINT;
    v_saved_table TEXT;
    v_writable_columns TEXT;
    v_identity_override TEXT;
    v_cutoff CONSTANT TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto';
BEGIN
    -- Owner allows extra CPU for this one-off purge. Each day batch commits;
    -- a crash loses at most the last unflushed batch, and a rerun redoes it
    -- (idempotent), so asynchronous commit is safe here. More sort/hash
    -- memory speeds the anchor joins.
    PERFORM set_config('synchronous_commit', 'off', FALSE);
    PERFORM set_config('work_mem', '256MB', FALSE);
    FOR v_target IN SELECT * FROM pg_temp.operator_purge_targets WHERE present ORDER BY table_name LOOP
        -- Deleted candidates are strictly older than predecessor; removing
        -- them cannot change latest/predecessor for this target.
        PERFORM pg_temp.operator_purge_build_anchor(
            v_target.table_name, v_target.time_column, v_target.anchor_columns, v_cutoff);
        v_predicate := pg_temp.operator_purge_predicate(
            v_target.table_name, v_target.time_column, v_target.anchor_columns,
            v_target.has_location);

        IF v_target.is_hypertable THEN
            v_saved_table := 'operator_purge_saved_' || v_target.table_name;
            EXECUTE format('DROP TABLE IF EXISTS pg_temp.%I', v_saved_table);
            EXECUTE format(
                'CREATE TEMP TABLE %1$I ON COMMIT PRESERVE ROWS AS '
                'SELECT * FROM public.%2$I AS source_row WHERE FALSE',
                v_saved_table, v_target.table_name
            );
            SELECT string_agg(format('%I', a.attname), ', ' ORDER BY a.attnum)
              INTO v_writable_columns
              FROM pg_attribute AS a
             WHERE a.attrelid = to_regclass(format('public.%I', v_target.table_name))
               AND a.attnum > 0
               AND NOT a.attisdropped
               AND a.attgenerated = '';
            IF v_writable_columns IS NULL THEN
                RAISE EXCEPTION 'public.% has no writable columns to preserve',
                    v_target.table_name;
            END IF;
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM pg_attribute
                 WHERE attrelid = to_regclass(format('public.%I', v_target.table_name))
                   AND attnum > 0 AND NOT attisdropped AND attidentity = 'a'
            ) THEN ' OVERRIDING SYSTEM VALUE' ELSE '' END
              INTO v_identity_override;

            FOR v_chunk IN
                SELECT range_start, range_end
                  FROM timescaledb_information.chunks
                 WHERE hypertable_schema = 'public'
                   AND hypertable_name = v_target.table_name
                   AND range_end <= v_cutoff
                 GROUP BY range_start, range_end
                 ORDER BY range_start, range_end
            LOOP
                PERFORM set_config('lock_timeout', '1s', TRUE);
                PERFORM set_config('statement_timeout', '60s', TRUE);
                EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', v_target.table_name);
                SELECT count(*) INTO v_chunk_count
                  FROM timescaledb_information.chunks
                 WHERE hypertable_schema = 'public'
                   AND hypertable_name = v_target.table_name
                   AND range_start = v_chunk.range_start
                   AND range_end = v_chunk.range_end;
                IF v_chunk_count = 0 THEN
                    COMMIT;
                    CONTINUE;
                END IF;

                EXECUTE format('TRUNCATE pg_temp.%I', v_saved_table);
                EXECUTE format(
                    'SELECT count(*) FROM public.%1$I AS m '
                    'WHERE m.%2$I >= $2 AND m.%2$I < $3 AND %3$s',
                    v_target.table_name, v_target.time_column, v_predicate
                ) INTO v_chunk_deleted USING v_cutoff, v_chunk.range_start, v_chunk.range_end;
                IF v_chunk_deleted = 0 THEN
                    COMMIT;
                    CONTINUE;
                END IF;
                EXECUTE format(
                    'INSERT INTO pg_temp.%1$I '
                    'SELECT m.* FROM public.%2$I AS m '
                    'WHERE m.%3$I >= $2 AND m.%3$I < $3 '
                    'AND NOT COALESCE(%4$s, FALSE)',
                    v_saved_table, v_target.table_name, v_target.time_column, v_predicate
                ) USING v_cutoff, v_chunk.range_start, v_chunk.range_end;
                GET DIAGNOSTICS v_chunk_saved = ROW_COUNT;

                RAISE NOTICE 'DROP CHUNK begin table=public.% window=[%,%) removable_rows=% rows_to_restore=%',
                    v_target.table_name, v_chunk.range_start, v_chunk.range_end,
                    v_chunk_deleted, v_chunk_saved;
                EXECUTE format(
                    'SELECT count(*) FROM public.drop_chunks('
                    'relation => %L::regclass, older_than => $1, newer_than => $2)',
                    'public.' || v_target.table_name
                ) INTO v_dropped_chunks USING v_chunk.range_end, v_chunk.range_start;
                IF v_dropped_chunks = 0 THEN
                    RAISE EXCEPTION 'drop_chunks did not remove public.% range [%,%)',
                        v_target.table_name, v_chunk.range_start, v_chunk.range_end;
                END IF;
                EXECUTE format(
                    'INSERT INTO public.%1$I (%2$s)%3$s '
                    'SELECT %2$s FROM pg_temp.%4$I',
                    v_target.table_name, v_writable_columns,
                    v_identity_override, v_saved_table
                );
                GET DIAGNOSTICS v_chunk_restored = ROW_COUNT;
                IF v_chunk_restored <> v_chunk_saved THEN
                    RAISE EXCEPTION 'public.% range [%,%) restored % of % protected rows',
                        v_target.table_name, v_chunk.range_start, v_chunk.range_end,
                        v_chunk_restored, v_chunk_saved;
                END IF;
                INSERT INTO pg_temp.operator_purge_chunk_events(
                    table_name, range_start, range_end, chunks_dropped,
                    deleted_rows, restored_rows
                )
                VALUES (
                    v_target.table_name, v_chunk.range_start, v_chunk.range_end,
                    v_dropped_chunks, v_chunk_deleted, v_chunk_restored
                );

                INSERT INTO pg_temp.operator_purge_deleted(table_name, toronto_day, deleted_rows)
                VALUES (
                    v_target.table_name,
                    (v_chunk.range_start AT TIME ZONE 'America/Toronto')::DATE,
                    v_chunk_deleted
                );
                RAISE NOTICE 'DROP CHUNK complete table=public.% window=[%,%) chunks_dropped=% rows_deleted=% rows_restored=%; normal compression policy may recompress recreated anchors later',
                    v_target.table_name, v_chunk.range_start, v_chunk.range_end,
                    v_dropped_chunks, v_chunk_deleted, v_chunk_restored;
                COMMIT;
            END LOOP;
            EXECUTE format('DROP TABLE pg_temp.%I', v_saved_table);
        END IF;

        -- Walk fixed Toronto days from the oldest row before the cutoff up to
        -- the day containing the cutoff. A per-day `min(time) WHERE <purge
        -- rule>` lookup rescanned the whole table each day (~60 s per day on
        -- production control_history); `min(time) WHERE time < cutoff` is a
        -- single index probe, and each day's DELETE is bounded by the time
        -- index (or chunk exclusion). Same rows are deleted: the purge rule
        -- already requires time < cutoff.
        PERFORM set_config('lock_timeout', '1s', TRUE);
        PERFORM set_config('statement_timeout', '60s', TRUE);
        EXECUTE format(
            'SELECT min(m.%1$I) FROM public.%2$I AS m WHERE m.%1$I < $1',
            v_target.time_column, v_target.table_name
        ) INTO v_next_time USING v_cutoff;
        COMMIT;
        IF v_next_time IS NOT NULL THEN
            v_day_start := date_trunc(
                'day', v_next_time AT TIME ZONE 'America/Toronto'
            ) AT TIME ZONE 'America/Toronto';
            WHILE v_day_start < v_cutoff LOOP
                v_day_end := (
                    (v_day_start AT TIME ZONE 'America/Toronto') + INTERVAL '1 day'
                ) AT TIME ZONE 'America/Toronto';
                PERFORM set_config('lock_timeout', '1s', TRUE);
                -- A busy control_history day can hold ~1M rows across four
                -- indexes; allow up to 15 min per day batch instead of 60 s.
                PERFORM set_config('statement_timeout', '15min', TRUE);
                -- Only cutoff-containing compressed chunks reach this path.
                -- Decompress them before row-wise deletion; policy owns later
                -- recompression.
                IF v_target.is_hypertable THEN
                    FOR v_chunk IN EXECUTE
                        'SELECT format(''%I.%I'', chunk_schema, chunk_name)::regclass AS chunk_relation '
                        'FROM timescaledb_information.chunks '
                        'WHERE hypertable_schema = ''public'' AND hypertable_name = $1 '
                        'AND is_compressed AND range_start < $3 AND range_end > $2 '
                        'ORDER BY range_start, chunk_name'
                        USING v_target.table_name, v_day_start, v_day_end
                    LOOP
                        RAISE NOTICE 'DECOMPRESS before row-wise purge table=public.% chunk=% toronto_day=%',
                            v_target.table_name, v_chunk.chunk_relation,
                            (v_day_start AT TIME ZONE 'America/Toronto')::DATE;
                        EXECUTE 'SELECT decompress_chunk($1::regclass, if_compressed => true)'
                            USING v_chunk.chunk_relation;
                    END LOOP;
                END IF;
                EXECUTE format(
                    'DELETE FROM public.%1$I AS m '
                    'WHERE m.%2$I >= $2 AND m.%2$I < $3 AND %3$s',
                    v_target.table_name, v_target.time_column, v_predicate
                ) USING v_cutoff, v_day_start, v_day_end;
                GET DIAGNOSTICS v_deleted = ROW_COUNT;
                IF v_deleted > 0 THEN
                    INSERT INTO pg_temp.operator_purge_deleted(table_name, toronto_day, deleted_rows)
                    VALUES (v_target.table_name, (v_day_start AT TIME ZONE 'America/Toronto')::DATE, v_deleted);
                    RAISE NOTICE 'ROW-WISE PURGE table=public.% toronto_day=% deleted_rows=%',
                        v_target.table_name, (v_day_start AT TIME ZONE 'America/Toronto')::DATE, v_deleted;
                END IF;
                COMMIT;
                v_day_start := v_day_end;
            END LOOP;
        END IF;
    END LOOP;
END
$apply_batches$;

DO $apply_summary$
DECLARE
    v_target RECORD;
    v_deleted BIGINT;
BEGIN
    FOR v_target IN SELECT table_name FROM pg_temp.operator_purge_targets WHERE present ORDER BY table_name LOOP
        SELECT COALESCE(sum(deleted_rows), 0) INTO v_deleted
          FROM pg_temp.operator_purge_deleted WHERE table_name = v_target.table_name;
        RAISE NOTICE 'PURGE SUMMARY table=public.% deleted_rows=%', v_target.table_name, v_deleted;
    END LOOP;
END
$apply_summary$;

-- The relay rebuild procedure commits internally. Plan top-level CALL
-- statements in a DO block, then execute them separately with psql \gexec.
DO $relay_call_plan$
DECLARE
    v_relay_enabled BOOLEAN;
    v_has_dirty BOOLEAN;
    v_call_count INTEGER;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_derived_targets
         WHERE view_name = 'monitoring_relay_duration_1day' AND present
    ) INTO v_relay_enabled;
    IF NOT v_relay_enabled THEN
        RETURN;
    END IF;
    -- The relay read models derive only from relay_observation. If this run
    -- deleted no relay_observation rows there is nothing to rebuild; the
    -- normal relay job owns its live dirty window (and its failure gate).
    IF NOT EXISTS (
        SELECT 1 FROM pg_temp.operator_purge_deleted
         WHERE table_name = 'relay_observation' AND deleted_rows > 0
    ) THEN
        RAISE NOTICE 'RELAY REBUILD skipped: no relay_observation rows were deleted';
        RETURN;
    END IF;

    EXECUTE
        'SELECT dirty_from IS NOT NULL AND dirty_through IS NOT NULL, '
        'GREATEST(1, CEIL(EXTRACT(EPOCH FROM ('
        'GREATEST(dirty_through, COALESCE(covered_through, dirty_through)) - '
        'LEAST(dirty_from, COALESCE(covered_through, dirty_from))'
        ')) / 86400)::INTEGER + 2) '
        'FROM public.monitoring_relay_overview_progress WHERE id'
        INTO v_has_dirty, v_call_count;

    IF v_has_dirty THEN
        INSERT INTO pg_temp.operator_purge_relay_calls(call_order, command_text)
        SELECT call_number,
               'CALL public.monitoring_rebuild_relay_overview(NULL, '
               '''{"enabled":true,"acknowledged_failures":{}}''::jsonb);'
          FROM generate_series(1, v_call_count) AS calls(call_number);
    END IF;
END
$relay_call_plan$;

SELECT command_text
  FROM pg_temp.operator_purge_relay_calls
 ORDER BY call_order
\gexec

-- Execute every planned CAGG refresh as a separate top-level CALL.
SELECT command_text
  FROM pg_temp.operator_purge_cagg_calls
 ORDER BY call_order
\gexec

-- A second clean relay checkpoint sees the refreshed daily CAGG and advances
-- its verified coverage frontier; it is a top-level CALL as required. Only
-- needed when relay_observation rows were deleted. It reuses the installed
-- relay job's config so owner failure acknowledgements apply.
SELECT format('CALL public.monitoring_rebuild_relay_overview(NULL, %L::jsonb);',
              COALESCE((SELECT config FROM timescaledb_information.jobs
                         WHERE proc_name = 'monitoring_rebuild_relay_overview'
                         ORDER BY job_id LIMIT 1),
                       '{"enabled":true,"acknowledged_failures":{}}'::jsonb)::text)
 WHERE EXISTS (SELECT 1 FROM pg_temp.operator_purge_derived_targets
               WHERE view_name = 'monitoring_relay_duration_1day' AND present)
   AND EXISTS (SELECT 1 FROM pg_temp.operator_purge_deleted
               WHERE table_name = 'relay_observation' AND deleted_rows > 0)
\gexec

\endif
