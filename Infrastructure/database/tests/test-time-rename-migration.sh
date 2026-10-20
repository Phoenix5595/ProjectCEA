#!/usr/bin/env bash
set -euo pipefail

HARNESS_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd -- "$HARNESS_DIRECTORY/../../.." && pwd)"
MIGRATOR="$WORKTREE_ROOT/Infrastructure/scripts/db_migrate.py"
PRE_MIGRATION_FIXTURE="$HARNESS_DIRECTORY/fixtures/time_column_rename_pre_migration.sql"
PRE_MIGRATION_CANONICAL="$WORKTREE_ROOT/Infrastructure/database/migrations/0007_time_column_rename.down.d"
HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'
DATABASE_NAME="monitoring_test_time_rename_$$"
DATABASES_CREATED=()
TMP_ROOT=''
MIGRATION_RELEASE_ROOT=''

fail() {
  printf 'time column rename proof: %s\n' "$1" >&2
  return 1
}

reject_inherited_database_environment() {
  local entry variable_name upper_name
  while IFS= read -r entry; do
    variable_name="${entry%%=*}"
    upper_name="${variable_name^^}"
    case "$upper_name" in
      PG* | POSTGRES_* | DATABASE_URL | MONITORING_TEST_DATABASE_URL)
        fail "refusing inherited database environment variable: $variable_name"
        return 1
        ;;
    esac
  done < <(env)
}

postgres_env() {
  sudo -u postgres env -i \
    "PATH=$PATH" \
    HOME=/var/lib/postgresql \
    PGHOST=/var/run/postgresql \
    PGPORT=5432 \
    PGUSER=postgres \
    "$@"
}

psql_as_postgres() {
  postgres_env psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$@"
}

query_db() {
  local database_name="$1" sql="$2"
  psql_as_postgres --dbname="$database_name" --tuples-only --no-align --command="$sql"
}

run_migrator() {
  local database_name="$1"
  shift
  postgres_env \
    POSTGRES_HOST=/var/run/postgresql \
    POSTGRES_PORT=5432 \
    POSTGRES_DB="$database_name" \
    POSTGRES_USER=postgres \
    POSTGRES_PASSWORD= \
    python3 "$MIGRATOR" --release "$MIGRATION_RELEASE_ROOT" "$@"
}

assert_equal() {
  local actual="$1" expected="$2" description="$3"
  if [[ "$actual" != "$expected" ]]; then
    fail "$description (expected '$expected', got '$actual')"
    return 1
  fi
}

assert_positive() {
  local actual="$1" description="$2"
  if [[ ! "$actual" =~ ^[0-9]+$ ]] || ((actual < 1)); then
    fail "$description (expected positive count, got '$actual')"
    return 1
  fi
}

create_disposable_database() {
  local database_name="$1"
  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    fail "refusing unsafe disposable database name: $database_name"
    return 1
  }
  [[ "${database_name,,}" != *cea_sensors* && "${database_name,,}" != *sj_cea* ]] || {
    fail "refusing protected database name: $database_name"
    return 1
  }
  if [[ "$(psql_as_postgres --dbname=postgres --tuples-only --no-align \
      --command="SELECT count(*) FROM pg_database WHERE datname = '$database_name'")" != '0' ]]; then
    fail "disposable database already exists: $database_name"
    return 1
  fi
  psql_as_postgres --dbname=postgres \
    --command="CREATE DATABASE \"$database_name\" OWNER postgres TEMPLATE template0" >/dev/null
  DATABASES_CREATED+=("$database_name")
  printf 'created disposable database: %s\n' "$database_name"
  psql_as_postgres --dbname="$database_name" --command='CREATE EXTENSION timescaledb' >/dev/null
}

prepare_isolated_release() {
  local migration_file
  local release_database_directory="$MIGRATION_RELEASE_ROOT/Infrastructure/database"
  mkdir -p "$release_database_directory/migrations/0007_time_column_rename.down.d"
  mkdir -p "$MIGRATION_RELEASE_ROOT/Infrastructure/scripts"

  for migration_file in "$WORKTREE_ROOT"/Infrastructure/database/migrations/000[1-7]_*.sql; do
    cp -- "$migration_file" "$release_database_directory/migrations/"
  done
  for canonical_file in monitoring_read_models.sql monitoring_history_overview.sql monitoring_relay_history_overview.sql; do
    cp -- "$WORKTREE_ROOT/Infrastructure/database/$canonical_file" "$release_database_directory/"
  done
  for canonical_file in monitoring_read_models.sql monitoring_history_overview.sql monitoring_relay_history_overview.sql; do
    cp -- "$PRE_MIGRATION_CANONICAL/$canonical_file" \
      "$release_database_directory/migrations/0007_time_column_rename.down.d/"
  done
}

seed_previous_migration_ledger() {
  local migration_file version digest header downtime
  run_migrator "$DATABASE_NAME" status >/dev/null
  for migration_file in "$MIGRATION_RELEASE_ROOT"/Infrastructure/database/migrations/000[1-6]_*.sql; do
    [[ "$migration_file" == *.down.sql ]] && continue
    version="$(basename -- "${migration_file%.sql}")"
    digest="$(sha256sum "$migration_file")"
    digest="${digest%% *}"
    IFS= read -r header < "$migration_file"
    if [[ "$header" == '-- migration: downtime=true' ]]; then
      downtime=true
    elif [[ "$header" == '-- migration: downtime=false' ]]; then
      downtime=false
    else
      fail "migration has no downtime header: $version"
      return 1
    fi
    psql_as_postgres --dbname="$DATABASE_NAME" \
      --command="INSERT INTO public.schema_migration (version, sha256, downtime) VALUES ('$version', '$digest', $downtime)" \
      >/dev/null
  done
}

assert_event_time_cutover() {
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name = ANY (ARRAY['automation_state','effective_setpoints','control_history','pid_parameter_history','relay_observation','monitoring_room_photoperiod','monitoring_photoperiod_coverage','mode_transition_history']) AND column_name IN ('timestamp','observed_at','triggered_at')")" \
    '0' 'all eight source columns renamed'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM (VALUES ('automation_state'),('effective_setpoints'),('control_history'),('pid_parameter_history'),('relay_observation'),('monitoring_room_photoperiod'),('monitoring_photoperiod_coverage'),('mode_transition_history'),('measurement')) AS expected(table_name) WHERE EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name=expected.table_name AND c.column_name='time' AND c.data_type='timestamp with time zone')")" \
    '9' 'all renamed and unchanged time columns exist'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='automation_state' AND indexname IN ('idx_automation_state_timestamp','automation_state_timestamp_id_uidx')")" \
    '2' 'automation_state timestamp index names remain unchanged'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "WITH expected AS (SELECT index_name FROM public.time_rename_expected_indexes WHERE table_name='automation_state'), actual AS (SELECT indexname AS index_name FROM pg_indexes WHERE schemaname='public' AND tablename='automation_state'), differences AS ((SELECT * FROM expected EXCEPT ALL SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)) SELECT count(*) FROM differences")" \
    '0' 'all automation_state index names remain unchanged'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename IN ('effective_setpoints','control_history','pid_parameter_history','relay_observation','monitoring_room_photoperiod','monitoring_photoperiod_coverage','mode_transition_history') AND (indexname LIKE '%timestamp%' OR indexname LIKE '%observed_at%')")" \
    '0' 'all non-automation event-time index names are updated'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname = ANY (ARRAY['effective_setpoints_time_idx','control_history_time_idx','relay_observation_time_idx'])")" \
    '3' 'Timescale/default event-time indexes are renamed'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM public.monitoring_index_snapshot_0007")" \
    '4' 'all non-automation old index names are snapshotted'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM (VALUES ('automation_state_time_bounds'),('effective_setpoints_time_bounds'),('control_history_time_bounds'),('pid_parameter_history_time_bounds'),('relay_observation_time_bounds'),('monitoring_room_photoperiod_time_bounds'),('monitoring_photoperiod_coverage_time_bounds'),('mode_transition_history_time_bounds'),('measurement_time_bounds')) AS expected(constraint_name) JOIN pg_constraint c ON c.conrelid = to_regclass('public.' || split_part(expected.constraint_name, '_time_bounds', 1)) AND c.conname = expected.constraint_name")" \
    '9' 'all nine event-time bounds constraints exist'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM timescaledb_information.continuous_aggregates WHERE view_schema='public' AND view_name = ANY (ARRAY['monitoring_measurement_1min','monitoring_measurement_5min','monitoring_measurement_1hour','monitoring_measurement_1day','monitoring_effective_setpoints_1min','monitoring_effective_setpoints_5min','monitoring_automation_state_1min','monitoring_automation_state_5min','monitoring_light_intensity_1hour','monitoring_light_intensity_1day','monitoring_requested_pid_1hour','monitoring_requested_pid_1day','monitoring_relay_duration_1day']) AND compression_enabled")" \
    '13' 'all thirteen aggregates are compression-enabled'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_schema='_timescaledb_functions' AND proc_name='policy_compression' AND hypertable_schema='public' AND hypertable_name LIKE 'monitoring_%' AND config->>'compress_after'='3 mons'")" \
    '13' 'all thirteen aggregate compression policies use three months'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name='policy_compression' AND hypertable_schema='public' AND hypertable_name = ANY (ARRAY['measurement','automation_state','effective_setpoints','control_history','relay_observation']) AND config->>'compress_after'='3 mons'")" \
    '5' 'source compression policies survive the event-time rename'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM timescaledb_information.hypertable_compression_settings WHERE hypertable = ANY (ARRAY['public.measurement'::REGCLASS,'public.automation_state'::REGCLASS,'public.effective_setpoints'::REGCLASS,'public.control_history'::REGCLASS,'public.relay_observation'::REGCLASS]) AND (orderby LIKE '%timestamp%' OR orderby LIKE '%observed_at%')")" \
    '0' 'raw hypertable compression order columns follow the renamed source fields'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM pg_constraint WHERE conname LIKE '%_time_bounds' AND convalidated AND connamespace = 'public'::regnamespace")" \
    '4' 'four plain-table time bounds are validated'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM pg_constraint WHERE conname LIKE '%_time_bounds' AND NOT convalidated AND connamespace = 'public'::regnamespace")" \
    '5' 'five compression-enabled hypertable time bounds remain NOT VALID'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM public.monitoring_job_snapshot_0007")" \
    '4' 'all overview jobs are snapshotted'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_schema='public' AND proc_name IN ('monitoring_refresh_overview_incremental','monitoring_rebuild_relay_overview') AND NOT scheduled")" \
    '4' 'overview jobs are disabled'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM public.monitoring_refresh_policy_snapshot_0007")" \
    '11' 'all rebuilt continuous-aggregate policies are snapshotted'
  assert_equal "$(query_db "$DATABASE_NAME" \
    "SELECT count(*) FROM _timescaledb_catalog.continuous_agg cagg JOIN timescaledb_information.jobs job ON (job.config->>'mat_hypertable_id')::integer=cagg.mat_hypertable_id WHERE cagg.user_view_schema='public' AND cagg.user_view_name = ANY (ARRAY['monitoring_measurement_5min','monitoring_measurement_1hour','monitoring_measurement_1day','monitoring_effective_setpoints_1min','monitoring_effective_setpoints_5min','monitoring_automation_state_1min','monitoring_automation_state_5min','monitoring_light_intensity_1hour','monitoring_light_intensity_1day','monitoring_requested_pid_1hour','monitoring_requested_pid_1day']) AND job.proc_name='policy_refresh_continuous_aggregate' AND NOT job.scheduled")" \
    '11' 'all rebuilt aggregate refresh policies start paused'
}

assert_time_bounds_writes() {
  psql_as_postgres --dbname="$DATABASE_NAME" --command="DO \$\$ BEGIN BEGIN INSERT INTO public.automation_state (\"time\", location, cluster, device_name, device_state, device_mode) VALUES (now() + INTERVAL '3 days', 'Proof Room', 'main', 'future_probe', 1, 'auto'); EXCEPTION WHEN check_violation THEN RAISE NOTICE 'new_chunk_time_check_rejected'; RETURN; END; RAISE EXCEPTION 'NOT_VALID_check_accepted_new_chunk_row'; END \$\$" \
    >/dev/null

  psql_as_postgres --dbname="$DATABASE_NAME" --command="CREATE TABLE public.time_bounds_compressed_probe (\"timestamp\" TIMESTAMPTZ NOT NULL, device TEXT NOT NULL); SELECT create_hypertable('public.time_bounds_compressed_probe', 'timestamp', chunk_time_interval => INTERVAL '100 years'); INSERT INTO public.time_bounds_compressed_probe VALUES (now() + INTERVAL '1 hour', 'valid'); ALTER TABLE public.time_bounds_compressed_probe SET (timescaledb.compress, timescaledb.compress_segmentby='device', timescaledb.compress_orderby='timestamp DESC'); SELECT compress_chunk(chunk) FROM show_chunks('public.time_bounds_compressed_probe') AS chunk; ALTER TABLE public.time_bounds_compressed_probe RENAME COLUMN \"timestamp\" TO \"time\"; ALTER TABLE public.time_bounds_compressed_probe ADD CONSTRAINT time_bounds_compressed_probe_check CHECK (\"time\" >= '2020-01-01'::timestamptz AND \"time\" <= now() + INTERVAL '1 day') NOT VALID" \
    >/dev/null
  psql_as_postgres --dbname="$DATABASE_NAME" --command="DO \$\$ BEGIN BEGIN INSERT INTO public.time_bounds_compressed_probe VALUES (now() + INTERVAL '25 hours', 'compressed'); EXCEPTION WHEN check_violation THEN RAISE NOTICE 'compressed_range_time_check_rejected'; RETURN; END; RAISE EXCEPTION 'NOT_VALID_check_accepted_compressed_range_row'; END \$\$" \
    >/dev/null
}
assert_dst_utc_windows() {
  psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
SET TIME ZONE 'America/Toronto';
DO $utc_day_windows$
DECLARE
    spring_cursor TIMESTAMPTZ := '2026-03-08 00:00:00+00';
    fall_cursor TIMESTAMPTZ := '2026-11-01 00:00:00+00';
    transition RECORD;
    window_start TIMESTAMPTZ;
    expected_start TIMESTAMPTZ;
    window_count INTEGER;
BEGIN
    IF current_setting('TimeZone') <> 'America/Toronto' THEN
        RAISE EXCEPTION 'DST proof did not use America/Toronto';
    END IF;
    IF spring_cursor + INTERVAL '24 hours'
       IS DISTINCT FROM '2026-03-09 00:00:00+00'::TIMESTAMPTZ
       OR fall_cursor + INTERVAL '24 hours'
       IS DISTINCT FROM '2026-11-02 00:00:00+00'::TIMESTAMPTZ THEN
        RAISE EXCEPTION 'elapsed UTC backfill cursor did not advance 24 hours';
    END IF;
    IF spring_cursor + INTERVAL '1 day' = spring_cursor + INTERVAL '24 hours'
       OR fall_cursor + INTERVAL '1 day' = fall_cursor + INTERVAL '24 hours' THEN
        RAISE EXCEPTION 'DST proof did not distinguish calendar and elapsed days';
    END IF;

    FOR transition IN
        SELECT * FROM (VALUES
            ('spring-forward',
             '2026-03-07 00:00:00+00'::TIMESTAMPTZ,
             '2026-03-10 00:00:00+00'::TIMESTAMPTZ),
            ('fall-back',
             '2026-10-31 00:00:00+00'::TIMESTAMPTZ,
             '2026-11-03 00:00:00+00'::TIMESTAMPTZ)
        ) AS cases(transition_name, range_start, range_end)
    LOOP
        expected_start := transition.range_start;
        window_count := 0;
        FOR window_start IN
            SELECT generate_series(
                date_trunc('day', transition.range_start AT TIME ZONE 'UTC')
                    AT TIME ZONE 'UTC',
                date_trunc('day', transition.range_end AT TIME ZONE 'UTC')
                    AT TIME ZONE 'UTC',
                INTERVAL '24 hours')
        LOOP
            IF window_start IS DISTINCT FROM expected_start THEN
                RAISE EXCEPTION '% generated a non-UTC 24-hour window: expected %, got %',
                    transition.transition_name, expected_start, window_start;
            END IF;
            expected_start := expected_start + INTERVAL '24 hours';
            window_count := window_count + 1;
        END LOOP;
        IF window_count <> 4
           OR expected_start IS DISTINCT FROM
              transition.range_end + INTERVAL '24 hours' THEN
            RAISE EXCEPTION '% did not cover each UTC day through %',
                transition.transition_name, transition.range_end;
        END IF;
    END LOOP;
END
$utc_day_windows$;
SQL
  printf 'verified 24-hour UTC backfill and rollback windows across Toronto DST transitions\n'
}


assert_backfill_parity() {
  psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL'
DO $measurement_hierarchy_parity$
DECLARE
    mismatch_count BIGINT;
BEGIN
    WITH expected AS (
        SELECT time_bucket(INTERVAL '5 minutes', "time") AS bucket,
               sensor_id,
               count(value)::BIGINT AS sample_count,
               sum(value::NUMERIC) AS value_sum,
               sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
               min(value::NUMERIC) AS min_value,
               max(value::NUMERIC) AS max_value,
               max("time") AS last_observed_at
        FROM public.measurement
        GROUP BY 1, sensor_id
    ),
    actual AS (
        SELECT bucket, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, last_observed_at
        FROM public.monitoring_measurement_5min
    )
    SELECT count(*) INTO mismatch_count
    FROM (
        (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
    ) AS differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'monitoring_measurement_5min raw parity mismatches: %', mismatch_count;
    END IF;

    WITH expected AS (
        SELECT time_bucket(INTERVAL '1 hour', "time") AS bucket,
               sensor_id,
               count(value)::BIGINT AS sample_count,
               sum(value::NUMERIC) AS value_sum,
               sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
               min(value::NUMERIC) AS min_value,
               max(value::NUMERIC) AS max_value,
               max("time") AS last_observed_at
        FROM public.measurement
        GROUP BY 1, sensor_id
    ),
    actual AS (
        SELECT bucket, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, last_observed_at
        FROM public.monitoring_measurement_1hour
    )
    SELECT count(*) INTO mismatch_count
    FROM (
        (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
    ) AS differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'monitoring_measurement_1hour raw parity mismatches: %', mismatch_count;
    END IF;

    WITH expected AS (
        SELECT time_bucket(INTERVAL '1 day', "time") AS bucket,
               sensor_id,
               count(value)::BIGINT AS sample_count,
               sum(value::NUMERIC) AS value_sum,
               sum(value::NUMERIC * value::NUMERIC) AS value_sum_squares,
               min(value::NUMERIC) AS min_value,
               max(value::NUMERIC) AS max_value,
               max("time") AS last_observed_at
        FROM public.measurement
        GROUP BY 1, sensor_id
    ),
    actual AS (
        SELECT bucket, sensor_id, sample_count, value_sum, value_sum_squares,
               min_value, max_value, last_observed_at
        FROM public.monitoring_measurement_1day
    )
    SELECT count(*) INTO mismatch_count
    FROM (
        (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
    ) AS differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'monitoring_measurement_1day raw parity mismatches: %', mismatch_count;
    END IF;
END
$measurement_hierarchy_parity$;
SQL
}

assert_measurement_ancestor_freshness() {
  psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
CREATE TEMP TABLE measurement_ancestor_test_window AS
WITH base AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
               - INTERVAL '30 days' + INTERVAL '15 minutes' AS test_time
)
SELECT test_time,
       time_bucket(INTERVAL '1 day', test_time) AS day_start,
       time_bucket(INTERVAL '1 day', test_time) + INTERVAL '24 hours' AS day_end,
       time_bucket(INTERVAL '1 hour', test_time) AS hour_start,
       time_bucket(INTERVAL '1 hour', test_time) + INTERVAL '1 hour' AS hour_end
FROM base;

CREATE TEMP TABLE measurement_ancestor_prior_coverage AS
SELECT model_name, covered_from, covered_through
FROM public.monitoring_overview_coverage
WHERE model_name IN ('monitoring_measurement_1hour', 'monitoring_measurement_1day');
CREATE TEMP TABLE measurement_ancestor_prior_minute_policy AS
SELECT job.job_id, job.scheduled
FROM timescaledb_information.jobs AS job
JOIN _timescaledb_catalog.continuous_agg AS cagg
  ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
WHERE cagg.user_view_schema = 'public'
  AND cagg.user_view_name = 'monitoring_measurement_1min'
  AND job.proc_schema = '_timescaledb_functions'
  AND job.proc_name = 'policy_refresh_continuous_aggregate';
CREATE PROCEDURE pg_temp.refresh_measurement_ancestor_cagg(view_to_refresh REGCLASS)
LANGUAGE plpgsql
AS $refresh$
DECLARE
    range_start TIMESTAMPTZ;
    range_end TIMESTAMPTZ;
BEGIN
    SELECT day_start, day_end
      INTO range_start, range_end
      FROM pg_temp.measurement_ancestor_test_window;
    CALL public.refresh_continuous_aggregate(
        view_to_refresh, range_start, range_end, TRUE,
        '{"buckets_per_batch":0}'::JSONB);
END
$refresh$;

CREATE FUNCTION pg_temp.measurement_overview_guard_ok()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $guard$
WITH bounds AS (
    SELECT hour.covered_from AS h_from,
           hour.covered_through AS h_through,
           day.covered_from AS d_from,
           day.covered_through AS d_through,
           test_window.day_start AS range_start,
           test_window.day_end AS range_end,
           (SELECT MIN(materialization_watermark)
              FROM public.monitoring_history_cagg_watermark
             WHERE cagg_name IN ('monitoring_measurement_1hour',
                                 'monitoring_measurement_1min')) AS h_wm,
           (SELECT MIN(materialization_watermark)
              FROM public.monitoring_history_cagg_watermark
             WHERE cagg_name IN ('monitoring_measurement_1day',
                                 'monitoring_measurement_1hour',
                                 'monitoring_measurement_1min')) AS d_wm
      FROM pg_temp.measurement_ancestor_test_window AS test_window
      JOIN public.monitoring_overview_coverage AS hour
        ON hour.model_name = 'monitoring_measurement_1hour'
      JOIN public.monitoring_overview_coverage AS day
        ON day.model_name = 'monitoring_measurement_1day'
),
frontiers AS (
    SELECT *,
           CASE WHEN h_wm IS NOT NULL THEN LEAST(h_through, h_wm) END AS h_read_through,
           CASE WHEN d_wm IS NOT NULL THEN LEAST(d_through, d_wm) END AS d_read_through
    FROM bounds
)
SELECT d_read_through >= range_end
   AND NOT EXISTS (
       SELECT 1 FROM public.monitoring_history_cagg_watermark
        WHERE cagg_name IN ('monitoring_measurement_1hour',
                            'monitoring_measurement_1min')
          AND invalidation_source IS NOT NULL
          AND invalidation_start < LEAST(frontiers.h_read_through, frontiers.range_end)
          AND invalidation_end >= GREATEST(frontiers.h_from, frontiers.range_start)
   )
   AND NOT EXISTS (
       SELECT 1 FROM public.monitoring_history_cagg_watermark
        WHERE cagg_name IN ('monitoring_measurement_1day',
                            'monitoring_measurement_1hour',
                            'monitoring_measurement_1min')
          AND invalidation_source IS NOT NULL
          AND invalidation_start < LEAST(frontiers.d_read_through, frontiers.range_end)
          AND invalidation_end >= GREATEST(frontiers.d_from, frontiers.range_start)
   )
FROM frontiers;
$guard$;

SELECT alter_job(job.job_id, scheduled => FALSE)
FROM timescaledb_information.jobs AS job
JOIN _timescaledb_catalog.continuous_agg AS cagg
  ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
WHERE cagg.user_view_schema = 'public'
  AND cagg.user_view_name IN (
      'monitoring_measurement_1min',
      'monitoring_measurement_5min',
      'monitoring_measurement_1hour',
      'monitoring_measurement_1day')
  AND job.proc_schema = '_timescaledb_functions'
  AND job.proc_name = 'policy_refresh_continuous_aggregate';
SELECT alter_job(job_id, scheduled => FALSE)
FROM timescaledb_information.jobs
WHERE proc_schema = 'public'
  AND proc_name IN ('monitoring_refresh_overview_incremental',
                    'monitoring_rebuild_relay_overview');

INSERT INTO public.measurement (time, sensor_id, value)
SELECT test_time, 1, 100.0
FROM pg_temp.measurement_ancestor_test_window;
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1min'::REGCLASS);
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1hour'::REGCLASS);
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1day'::REGCLASS);
INSERT INTO public.monitoring_overview_coverage (model_name, covered_from, covered_through)
SELECT 'monitoring_measurement_1hour', hour_start, hour_end
FROM pg_temp.measurement_ancestor_test_window
ON CONFLICT (model_name) DO UPDATE
SET covered_from = EXCLUDED.covered_from,
    covered_through = EXCLUDED.covered_through;
INSERT INTO public.monitoring_overview_coverage (model_name, covered_from, covered_through)
SELECT 'monitoring_measurement_1day', day_start, day_end
FROM pg_temp.measurement_ancestor_test_window
ON CONFLICT (model_name) DO UPDATE
SET covered_from = EXCLUDED.covered_from,
    covered_through = EXCLUDED.covered_through;
DO $baseline_guard$
BEGIN
    IF pg_temp.measurement_overview_guard_ok() IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'measurement overview guard was not fresh after ancestor refresh';
    END IF;
END
$baseline_guard$;

UPDATE public.measurement AS measurement
   SET value = measurement.value + 1
  FROM pg_temp.measurement_ancestor_test_window AS test_window
 WHERE measurement.time = test_window.test_time
   AND measurement.sensor_id = 1;
DO $invalidated_guard$
BEGIN
    IF pg_temp.measurement_overview_guard_ok() IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'measurement overview guard missed its 1min ancestor invalidation';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.monitoring_history_cagg_watermark AS watermark
        CROSS JOIN pg_temp.measurement_ancestor_test_window AS test_window
        WHERE watermark.cagg_name = 'monitoring_measurement_1min'
          AND watermark.invalidation_source IS NOT NULL
          AND watermark.invalidation_start < test_window.day_end
          AND watermark.invalidation_end >= test_window.day_start
    ) THEN
        RAISE EXCEPTION 'raw correction did not invalidate the old 1min measurement bucket';
    END IF;
END
$invalidated_guard$;

CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1min'::REGCLASS);
DO $ancestor_refreshed$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.monitoring_history_cagg_watermark AS watermark
        CROSS JOIN pg_temp.measurement_ancestor_test_window AS test_window
        WHERE watermark.cagg_name = 'monitoring_measurement_1min'
          AND watermark.invalidation_source IS NOT NULL
          AND watermark.invalidation_start < test_window.day_end
          AND watermark.invalidation_end >= test_window.day_start
    ) THEN
        RAISE EXCEPTION 'refresh did not clear the 1min ancestor invalidation';
    END IF;
END
$ancestor_refreshed$;
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1hour'::REGCLASS);
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1day'::REGCLASS);
DO $fully_refreshed_guard$
BEGIN
    IF pg_temp.measurement_overview_guard_ok() IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'measurement overview guard stayed stale after ancestor and parents refreshed';
    END IF;
END
$fully_refreshed_guard$;

DELETE FROM public.measurement AS measurement
USING pg_temp.measurement_ancestor_test_window AS test_window
WHERE measurement.time = test_window.test_time
  AND measurement.sensor_id = 1;
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1min'::REGCLASS);
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1hour'::REGCLASS);
CALL pg_temp.refresh_measurement_ancestor_cagg(
    'public.monitoring_measurement_1day'::REGCLASS);
DELETE FROM public.monitoring_overview_coverage
WHERE model_name IN ('monitoring_measurement_1hour', 'monitoring_measurement_1day');
INSERT INTO public.monitoring_overview_coverage
SELECT * FROM pg_temp.measurement_ancestor_prior_coverage;
SELECT alter_job(job.job_id, scheduled => job.scheduled)
FROM pg_temp.measurement_ancestor_prior_minute_policy AS job;
SQL
  printf 'verified overview guard blocks old measurement invalidation until the 1min ancestor is refreshed\n'
}

assert_original_state_restored() {
  psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL'
DO $restore_assertions$
DECLARE
    mismatch_count BIGINT;
    snapshot_row RECORD;
    actual_count BIGINT;
BEGIN
    WITH actual AS (
        SELECT table_name, column_name, ordinal_position, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN (
              'automation_state', 'effective_setpoints', 'control_history',
              'pid_parameter_history', 'relay_observation',
              'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
              'mode_transition_history')
    ),
    differences AS (
        (SELECT * FROM public.time_rename_expected_columns
         EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual
         EXCEPT ALL SELECT * FROM public.time_rename_expected_columns)
    )
    SELECT count(*) INTO mismatch_count FROM differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'rollback source columns differ from the original shape (% differences)',
            mismatch_count;
    END IF;

    WITH actual AS (
        SELECT tablename AS table_name, indexname AS index_name, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN (
              'automation_state', 'effective_setpoints', 'control_history',
              'pid_parameter_history', 'relay_observation',
              'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
              'mode_transition_history')
    ),
    differences AS (
        (SELECT * FROM public.time_rename_expected_indexes
         EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual
         EXCEPT ALL SELECT * FROM public.time_rename_expected_indexes)
    )
    SELECT count(*) INTO mismatch_count FROM differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'rollback source indexes differ from the original shape (% differences)',
            mismatch_count;
    END IF;

    WITH actual AS (
        SELECT conrelid::REGCLASS AS relation_name, conname, contype,
               pg_get_constraintdef(oid) AS definition, convalidated
        FROM pg_constraint
        WHERE conrelid = ANY (ARRAY[
            'public.automation_state'::REGCLASS,
            'public.effective_setpoints'::REGCLASS,
            'public.control_history'::REGCLASS,
            'public.pid_parameter_history'::REGCLASS,
            'public.relay_observation'::REGCLASS,
            'public.monitoring_room_photoperiod'::REGCLASS,
            'public.monitoring_photoperiod_coverage'::REGCLASS,
            'public.mode_transition_history'::REGCLASS,
            'public.measurement'::REGCLASS
        ])
    ),
    differences AS (
        (SELECT * FROM public.time_rename_expected_constraints
         EXCEPT ALL SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual
         EXCEPT ALL SELECT * FROM public.time_rename_expected_constraints)
    )
    SELECT count(*) INTO mismatch_count FROM differences;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'rollback source constraints differ from the original shape (% differences)',
            mismatch_count;
    END IF;
    IF (SELECT count(*)
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'public'
          AND view_name = ANY (ARRAY[
              'monitoring_measurement_1min', 'monitoring_measurement_5min',
              'monitoring_measurement_1hour', 'monitoring_measurement_1day',
              'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
              'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
              'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
              'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
              'monitoring_relay_duration_1day'
          ])) <> 13 THEN
        RAISE EXCEPTION 'rollback did not restore all thirteen monitoring aggregates';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'public'
          AND view_name = ANY (ARRAY[
              'monitoring_measurement_1min', 'monitoring_measurement_5min',
              'monitoring_measurement_1hour', 'monitoring_measurement_1day',
              'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
              'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
              'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
              'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
              'monitoring_relay_duration_1day'
          ])
          AND compression_enabled
    ) THEN
        RAISE EXCEPTION 'rollback left aggregate compression enabled';
    END IF;
    IF to_regclass('public.monitoring_job_snapshot_0007') IS NOT NULL
       OR to_regclass('public.monitoring_refresh_policy_snapshot_0007') IS NOT NULL
       OR to_regclass('public.monitoring_index_snapshot_0007') IS NOT NULL
       OR to_regprocedure('public.monitoring_backfill_aggregates(integer,jsonb)') IS NOT NULL THEN
        RAISE EXCEPTION 'rollback left Phase 4 snapshot/backfill objects';
    END IF;
    IF position('monitoring_measurement_1min' IN
                pg_get_viewdef('public.monitoring_measurement_5min'::REGCLASS, TRUE)) > 0
       OR position('monitoring_measurement_1min' IN
                   pg_get_viewdef('public.monitoring_measurement_1hour'::REGCLASS, TRUE)) > 0 THEN
        RAISE EXCEPTION 'rollback did not restore direct raw measurement aggregates';
    END IF;

    SELECT count(*) INTO mismatch_count
    FROM public.time_rename_expected_refresh_policies AS expected
    LEFT JOIN _timescaledb_catalog.continuous_agg AS cagg
      ON cagg.user_view_schema = 'public'
     AND cagg.user_view_name = expected.view_name
    LEFT JOIN timescaledb_information.jobs AS job
      ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
     AND job.proc_schema = '_timescaledb_functions'
     AND job.proc_name = 'policy_refresh_continuous_aggregate'
    WHERE job.job_id IS NULL
       OR job.scheduled IS DISTINCT FROM expected.scheduled
       OR job.schedule_interval IS DISTINCT FROM expected.schedule_interval
       OR job.max_runtime IS DISTINCT FROM expected.max_runtime
       OR job.max_retries IS DISTINCT FROM expected.max_retries
       OR (job.config - 'mat_hypertable_id')
          IS DISTINCT FROM (expected.config - 'mat_hypertable_id');
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'rollback refresh policy state differs from the original (% mismatches)',
            mismatch_count;
    END IF;

    SELECT count(*) INTO mismatch_count
    FROM public.time_rename_expected_overview_jobs AS expected
    FULL JOIN (
        SELECT job_id, scheduled, config
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name IN ('monitoring_refresh_overview_incremental',
                            'monitoring_rebuild_relay_overview')
    ) AS job USING (job_id)
    WHERE expected.job_id IS NULL
       OR job.job_id IS NULL
       OR job.scheduled IS DISTINCT FROM expected.scheduled
       OR job.config IS DISTINCT FROM expected.config;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'rollback overview job state differs from the original (% mismatches)',
            mismatch_count;
    END IF;

    FOR snapshot_row IN
        SELECT view_name, row_count
        FROM public.time_rename_expected_cagg_rows
    LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', snapshot_row.view_name)
        INTO actual_count;
        IF actual_count IS DISTINCT FROM snapshot_row.row_count THEN
            RAISE EXCEPTION 'rollback aggregate % has % rows; expected %',
                snapshot_row.view_name, actual_count, snapshot_row.row_count;
        END IF;
    END LOOP;
END
$restore_assertions$;
SQL
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  local database_name
  for database_name in "${DATABASES_CREATED[@]}"; do
    if [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] && \
       [[ "${database_name,,}" != *cea_sensors* && "${database_name,,}" != *sj_cea* ]]; then
      if psql_as_postgres --dbname=postgres \
          --command="DROP DATABASE IF EXISTS \"$database_name\" WITH (FORCE)" >/dev/null; then
        printf 'dropped disposable database: %s\n' "$database_name"
      else
        fail "could not drop disposable database: $database_name"
        status=1
      fi
    else
      fail "refusing unsafe database cleanup: $database_name"
      status=1
    fi
  done
  if [[ -n "$TMP_ROOT" ]]; then
    rm -rf -- "$TMP_ROOT" || status=1
  fi
  exit "$status"
}

reject_inherited_database_environment
for required_command in cp flock mktemp psql sha256sum sudo python3; do
  command -v "$required_command" >/dev/null 2>&1 || {
    fail "required command not found: $required_command"
    exit 1
  }
done
[[ -f "$MIGRATOR" && -f "$PRE_MIGRATION_FIXTURE" ]] || {
  fail 'migration runner or pre-migration fixture is missing'
  exit 1
}
for canonical_file in monitoring_read_models.sql monitoring_history_overview.sql monitoring_relay_history_overview.sql; do
  [[ -f "$PRE_MIGRATION_CANONICAL/$canonical_file" ]] || {
    fail "pre-migration canonical file is missing: $canonical_file"
    exit 1
  }
done
[[ "$DATABASE_NAME" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
  fail 'generated disposable database name is unsafe'
  exit 1
}

exec {lock_fd}> "$HARNESS_LOCK" || { fail "could not open shared database harness lock: $HARNESS_LOCK"; exit 1; }
flock -x "$lock_fd" || { fail "could not acquire shared database harness lock: $HARNESS_LOCK"; exit 1; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/projectcea-time-rename.XXXXXX")"
chmod 755 "$TMP_ROOT"
MIGRATION_RELEASE_ROOT="$TMP_ROOT/release"
prepare_isolated_release
create_disposable_database "$DATABASE_NAME"

printf 'loading pre-0007 source schema and recent multi-day rows\n'
psql_as_postgres --dbname="$DATABASE_NAME" --file="$PRE_MIGRATION_FIXTURE" >/dev/null
psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
ALTER TABLE public.automation_state
    RENAME CONSTRAINT automation_state_pkey TO automation_state_timestamp_id_uidx;
DROP INDEX public.automation_state_timestamp_idx;
CREATE INDEX IF NOT EXISTS idx_automation_state_lookup
    ON public.automation_state (location, cluster, device_name, timestamp DESC);

DROP INDEX public.idx_effective_setpoints_location;
DROP INDEX public.idx_effective_setpoints_timestamp;
CREATE INDEX IF NOT EXISTS effective_setpoints_timestamp_idx
    ON public.effective_setpoints (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_effective_setpoints_device_mode_time
    ON public.effective_setpoints (location, cluster, device_name, mode, timestamp DESC)
    WITH (timescaledb.transaction_per_chunk);

DROP INDEX public.idx_control_history_timestamp;
CREATE INDEX IF NOT EXISTS control_history_timestamp_idx
    ON public.control_history (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_control_history_device_channel_time
    ON public.control_history (location, cluster, device_name, channel, timestamp DESC)
    WITH (timescaledb.transaction_per_chunk);

CREATE INDEX IF NOT EXISTS relay_observation_observed_at_idx
    ON public.relay_observation (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pid_parameter_history_room_type_time
    ON public.pid_parameter_history (location, cluster, device_type, timestamp DESC);
SQL
psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
TRUNCATE TABLE public.measurement, public.automation_state, public.effective_setpoints,
    public.control_history, public.pid_parameter_history, public.relay_observation,
    public.monitoring_room_photoperiod, public.monitoring_photoperiod_coverage,
    public.mode_transition_history RESTART IDENTITY;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index), ticks AS (SELECT generate_series(0, 1) AS tick),
     sensors AS (SELECT unnest(ARRAY[1, 4]) AS sensor_id)
INSERT INTO public.measurement (time, sensor_id, value)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day'
       + INTERVAL '10 minutes' + ticks.tick * INTERVAL '30 seconds',
       sensors.sensor_id,
       20.0 + days.day_index + ticks.tick + sensors.sensor_id / 10.0
FROM bounds CROSS JOIN days CROSS JOIN ticks CROSS JOIN sensors;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index), ticks AS (SELECT generate_series(0, 1) AS tick)
INSERT INTO public.effective_setpoints (
    timestamp, location, cluster, mode, device_name,
    effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
    effective_co2_setpoint, effective_vpd_setpoint, effective_light_intensity,
    nominal_light_intensity, ramp_progress_light)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day'
       + INTERVAL '1 minute' + ticks.tick * INTERVAL '30 seconds',
       'Flower Room', 'main', 'DAY', 'light_f_1',
       23.0, 27.0, 58.0, 1000.0, 1.2, 70.0 + ticks.tick,
       75.0 + ticks.tick, 0.2 + ticks.tick / 10.0
FROM bounds CROSS JOIN days CROSS JOIN ticks;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index), ticks AS (SELECT generate_series(0, 1) AS tick)
INSERT INTO public.automation_state (
    timestamp, location, cluster, device_name, device_state, device_mode,
    pid_output, duty_cycle_percent, control_reason)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day'
       + INTERVAL '2 minutes' + ticks.tick * INTERVAL '30 seconds',
       'Flower Room', 'main', 'heater_f_1', 1, 'auto',
       0.2 + ticks.tick * 0.6, 20.0 + ticks.tick * 60.0, 'proof'
FROM bounds CROSS JOIN days CROSS JOIN ticks;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.control_history (
    timestamp, location, cluster, device_name, channel, old_state, new_state,
    mode, reason)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '3 minutes',
       'Flower Room', 'main', 'heater_f_1', 0, 0, 1, 'auto', 'proof'
FROM bounds CROSS JOIN days;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.relay_observation (
    observed_at, session_id, channel, observed_state, device_id, device_name,
    device_type, location, cluster, registry_version, reason)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '4 minutes',
       '00000000-0000-0000-0000-000000000001', 0, TRUE, 1, 'heater_f_1',
       'heating', 'Flower Room', 'main', 1, 'initial'
FROM bounds CROSS JOIN days;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.monitoring_room_photoperiod (
    observed_at, location, cluster, phase, mode_id, submode_id,
    runtime_snapshot_version, source)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '5 minutes',
       'Flower Room', 'main', 'SUN', 2, 1, 1, 'phase4-proof'
FROM bounds CROSS JOIN days;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.monitoring_photoperiod_coverage (
    observed_at, location, cluster, state, reason, runtime_snapshot_version)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '6 minutes',
       'Flower Room', 'main', 'available', 'initial', 1
FROM bounds CROSS JOIN days;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.mode_transition_history (
    location, cluster, new_mode_id, triggered_by, triggered_at)
SELECT 'Flower Room', 'main', 2, 'api',
       bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '7 minutes'
FROM bounds CROSS JOIN days;
WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
           - INTERVAL '3 days' AS day0
), days AS (SELECT generate_series(0, 2) AS day_index)
INSERT INTO public.pid_parameter_history (timestamp, device_type, kp, ki, kd, location, cluster)
SELECT bounds.day0 + days.day_index * INTERVAL '1 day' + INTERVAL '8 minutes',
       'heater', 25.0, 0.02, 0.0, 'Flower Room', 'main'
FROM bounds CROSS JOIN days;
SQL

printf 'enabling source compression and compressing existing chunks\n'
psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
ALTER TABLE public.measurement SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby = 'time DESC');
ALTER TABLE public.automation_state SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name',
    timescaledb.compress_orderby = 'timestamp DESC');
ALTER TABLE public.effective_setpoints SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name,mode',
    timescaledb.compress_orderby = 'timestamp DESC');
ALTER TABLE public.control_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name,channel',
    timescaledb.compress_orderby = 'timestamp DESC');
ALTER TABLE public.relay_observation SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'channel',
    timescaledb.compress_orderby = 'observed_at DESC');
SELECT compress_chunk(chunk) FROM show_chunks('public.measurement') AS chunk;
SELECT compress_chunk(chunk) FROM show_chunks('public.automation_state') AS chunk;
SELECT compress_chunk(chunk) FROM show_chunks('public.effective_setpoints') AS chunk;
SELECT compress_chunk(chunk) FROM show_chunks('public.control_history') AS chunk;
SELECT compress_chunk(chunk) FROM show_chunks('public.relay_observation') AS chunk;
SELECT add_compression_policy('public.measurement'::REGCLASS, INTERVAL '3 months');
SELECT add_compression_policy('public.automation_state'::REGCLASS, INTERVAL '3 months');
SELECT add_compression_policy('public.effective_setpoints'::REGCLASS, INTERVAL '3 months');
SELECT add_compression_policy('public.control_history'::REGCLASS, INTERVAL '3 months');
SELECT add_compression_policy('public.relay_observation'::REGCLASS, INTERVAL '3 months');
SQL
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(DISTINCT hypertable_name) FROM timescaledb_information.chunks WHERE hypertable_schema='public' AND hypertable_name = ANY (ARRAY['measurement','automation_state','effective_setpoints','control_history','relay_observation']) AND is_compressed")" \
  '5' 'all five production-shaped source hypertables have compressed chunks'

printf 'creating pre-0007 aggregates with materialized rows\n'
psql_as_postgres --dbname="$DATABASE_NAME" \
  --command="SET app.grow_cycle_history_disposable='1'" \
  --file="$PRE_MIGRATION_CANONICAL/monitoring_read_models.sql" >/dev/null
psql_as_postgres --dbname="$DATABASE_NAME" \
  --command="SET app.grow_cycle_history_disposable='1'" \
  --file="$PRE_MIGRATION_CANONICAL/monitoring_history_overview.sql" >/dev/null
psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL' >/dev/null
INSERT INTO public.monitoring_relay_duration_1hour (
    bucket, location, cluster, channel, device_id, device_name, device_type,
    on_microseconds, off_microseconds, last_observed_at, source_watermark, active)
SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
       - INTERVAL '1 day' + INTERVAL '1 hour',
       'Flower Room', 'main', 0, 1, 'heater_f_1', 'heating',
       3600000000, 0, now() - INTERVAL '1 day', 4, TRUE;
DO $refresh_pre_migration_aggregates$
DECLARE
    view_name TEXT;
    day_number INTEGER;
    source_day TIMESTAMPTZ;
    day_start TIMESTAMPTZ :=
        (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') - INTERVAL '3 days';
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_measurement_1min', 'monitoring_measurement_5min',
        'monitoring_measurement_1hour', 'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
    ] LOOP
        FOR day_number IN 0..2 LOOP
            source_day := day_start + day_number * INTERVAL '1 day';
            CALL public.refresh_continuous_aggregate(
                format('public.%I', view_name)::REGCLASS,
                source_day,
                source_day + INTERVAL '1 day');
        END LOOP;
    END LOOP;
    CALL public.refresh_continuous_aggregate(
        'public.monitoring_relay_duration_1day'::REGCLASS,
        day_start + INTERVAL '2 days',
        day_start + INTERVAL '3 days');
END
$refresh_pre_migration_aggregates$;
DO $install_base_refresh_policies$
DECLARE
    policy_spec RECORD;
    local_job_id INTEGER;
BEGIN
    FOR policy_spec IN
        SELECT * FROM (VALUES
            ('monitoring_measurement_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_measurement_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_effective_setpoints_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_effective_setpoints_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_automation_state_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_automation_state_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes')
        ) AS policies(view_name, start_offset, end_offset, schedule_interval)
    LOOP
        local_job_id := add_continuous_aggregate_policy(
            format('public.%I', policy_spec.view_name)::REGCLASS,
            start_offset => policy_spec.start_offset,
            end_offset => policy_spec.end_offset,
            schedule_interval => policy_spec.schedule_interval,
            if_not_exists => FALSE);
    END LOOP;

    FOR local_job_id IN
        SELECT job.job_id
        FROM _timescaledb_catalog.continuous_agg AS cagg
        JOIN timescaledb_information.jobs AS job
          ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
        WHERE cagg.user_view_schema = 'public'
          AND cagg.user_view_name IN ('monitoring_measurement_1hour',
                                 'monitoring_light_intensity_1hour',
                                 'monitoring_requested_pid_1hour')
          AND job.proc_name = 'policy_refresh_continuous_aggregate'
    LOOP
        PERFORM alter_job(local_job_id, scheduled => TRUE);
    END LOOP;

    FOR local_job_id IN
        SELECT job_id
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name IN ('monitoring_refresh_overview_incremental',
                            'monitoring_rebuild_relay_overview')
    LOOP
        PERFORM alter_job(local_job_id, scheduled => TRUE);
    END LOOP;
END
$install_base_refresh_policies$;
CREATE TABLE public.time_rename_expected_indexes AS
SELECT tablename AS table_name, indexname AS index_name, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('automation_state','effective_setpoints','control_history',
                    'pid_parameter_history','relay_observation',
                    'monitoring_room_photoperiod','monitoring_photoperiod_coverage',
                    'mode_transition_history');
CREATE TABLE public.time_rename_expected_columns AS
SELECT table_name, column_name, ordinal_position, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('automation_state','effective_setpoints','control_history',
                     'pid_parameter_history','relay_observation',
                     'monitoring_room_photoperiod','monitoring_photoperiod_coverage',
                     'mode_transition_history');
CREATE TABLE public.time_rename_expected_constraints AS
SELECT conrelid::REGCLASS AS relation_name, conname, contype,
       pg_get_constraintdef(oid) AS definition, convalidated
FROM pg_constraint
WHERE conrelid = ANY (ARRAY[
    'public.automation_state'::REGCLASS,
    'public.effective_setpoints'::REGCLASS,
    'public.control_history'::REGCLASS,
    'public.pid_parameter_history'::REGCLASS,
    'public.relay_observation'::REGCLASS,
    'public.monitoring_room_photoperiod'::REGCLASS,
    'public.monitoring_photoperiod_coverage'::REGCLASS,
    'public.mode_transition_history'::REGCLASS,
    'public.measurement'::REGCLASS
]);
CREATE TABLE public.time_rename_expected_overview_jobs AS
SELECT job_id, scheduled, config
FROM timescaledb_information.jobs
WHERE proc_schema = 'public'
  AND proc_name IN ('monitoring_refresh_overview_incremental',
                    'monitoring_rebuild_relay_overview');
CREATE TABLE public.time_rename_expected_refresh_policies AS
SELECT cagg.user_view_name AS view_name, job.scheduled, job.schedule_interval,
       job.max_runtime, job.max_retries, job.config
FROM _timescaledb_catalog.continuous_agg AS cagg
JOIN timescaledb_information.jobs AS job
  ON (job.config ->> 'mat_hypertable_id')::INTEGER = cagg.mat_hypertable_id
WHERE cagg.user_view_schema = 'public'
  AND cagg.user_view_name = ANY (ARRAY[
      'monitoring_measurement_5min',
      'monitoring_measurement_1hour', 'monitoring_measurement_1day',
      'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
      'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
      'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
      'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
  ])
  AND job.proc_schema = '_timescaledb_functions'
  AND job.proc_name = 'policy_refresh_continuous_aggregate';
CREATE TABLE public.time_rename_expected_cagg_rows (
    view_name TEXT PRIMARY KEY,
    row_count BIGINT NOT NULL);
DO $capture_cagg_row_counts$
DECLARE
    view_name TEXT;
    row_count BIGINT;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_measurement_1min', 'monitoring_measurement_5min',
        'monitoring_measurement_1hour', 'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
        'monitoring_relay_duration_1day'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', view_name) INTO row_count;
        INSERT INTO public.time_rename_expected_cagg_rows VALUES (view_name, row_count);
        IF row_count < 1 THEN
            RAISE EXCEPTION 'pre-migration aggregate % has no materialized data', view_name;
        END IF;
    END LOOP;
END
$capture_cagg_row_counts$;
SQL

for view_name in \
  monitoring_measurement_1min monitoring_measurement_5min \
  monitoring_measurement_1hour monitoring_measurement_1day \
  monitoring_effective_setpoints_1min monitoring_effective_setpoints_5min \
  monitoring_automation_state_1min monitoring_automation_state_5min \
  monitoring_light_intensity_1hour monitoring_light_intensity_1day \
  monitoring_requested_pid_1hour monitoring_requested_pid_1day \
  monitoring_relay_duration_1day; do
  assert_positive "$(query_db "$DATABASE_NAME" "SELECT count(*) FROM public.$view_name")" \
    "pre-migration aggregate $view_name contains data"
done

printf 'building isolated migration release and prior ledger head\n'
seed_previous_migration_ledger
printf 'applying 0007 with --skip-alembic --allow-downtime\n'
run_migrator "$DATABASE_NAME" apply --skip-alembic --allow-downtime
BACKFILL_JOB_ID="$(query_db "$DATABASE_NAME" \
  "SELECT job_id FROM timescaledb_information.jobs WHERE proc_schema='public' AND proc_name='monitoring_backfill_aggregates'")"
[[ "$BACKFILL_JOB_ID" =~ ^[0-9]+$ ]] || { fail 'backfill job was not created'; exit 1; }
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT scheduled FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'backfill job is initially scheduled'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT max_retries = -1 FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'backfill job retries transient failures indefinitely'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT schedule_interval=INTERVAL '1 minute' AND max_runtime=INTERVAL '10 minutes' AND config->>'done'='false' AND config->>'start'=config->>'cursor' AND jsonb_array_length(config->'views')=11 FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'backfill job has the configured one-minute oldest-first window'
psql_as_postgres --dbname="$DATABASE_NAME" \
  --command="SELECT alter_job($BACKFILL_JOB_ID, scheduled => FALSE)" >/dev/null
assert_event_time_cutover
assert_time_bounds_writes
assert_dst_utc_windows

printf 'proving the Timescale scheduler retries a transient backfill failure\n'
backfill_config="$(query_db "$DATABASE_NAME" \
  "SELECT config::text FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")"
backfill_cursor="$(query_db "$DATABASE_NAME" \
  "SELECT config->>'cursor' FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")"
psql_as_postgres --dbname="$DATABASE_NAME" \
  --command="SELECT alter_job($BACKFILL_JOB_ID, schedule_interval => INTERVAL '1 second', retry_period => INTERVAL '1 second', max_retries => -1, config => jsonb_set(config, '{views,0}', to_jsonb('monitoring_test_missing_cagg'::TEXT), TRUE), next_start => clock_timestamp(), scheduled => TRUE) FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID" \
  >/dev/null
failure_seen=false
for ((attempt = 1; attempt <= 30; attempt++)); do
  if [[ "$(query_db "$DATABASE_NAME" \
      "SELECT COALESCE((SELECT last_run_status = 'Failed' FROM timescaledb_information.job_stats WHERE job_id=$BACKFILL_JOB_ID), FALSE)")" == 't' ]]; then
    failure_seen=true
    break
  fi
  sleep 1
done
assert_equal "$failure_seen" 'true' 'scheduler recorded the forced transient backfill failure'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT scheduled FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'backfill remains scheduled after a failed scheduler run'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT max_retries = -1 FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'Timescale retains unlimited retries after failure'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT config->>'cursor' = '$backfill_cursor' FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")" \
  't' 'failed backfill leaves its persisted cursor unchanged'
psql_as_postgres --dbname="$DATABASE_NAME" \
  --command="SELECT alter_job($BACKFILL_JOB_ID, config => '$backfill_config'::JSONB, next_start => clock_timestamp(), scheduled => TRUE)" \
  >/dev/null
backfill_done=false
backfill_scheduled=true
for ((attempt = 1; attempt <= 120; attempt++)); do
  backfill_done="$(query_db "$DATABASE_NAME" \
    "SELECT config->>'done' FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")"
  backfill_scheduled="$(query_db "$DATABASE_NAME" \
    "SELECT scheduled FROM timescaledb_information.jobs WHERE job_id=$BACKFILL_JOB_ID")"
  [[ "$backfill_done" == 'true' && "$backfill_scheduled" == 'f' ]] && break
  sleep 1
done
assert_equal "$backfill_done" 'true' 'scheduler resumes and completes the persisted backfill cursor'
assert_equal "$backfill_scheduled" 'f' 'completed scheduled backfill job is unscheduled'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM _timescaledb_catalog.continuous_agg cagg JOIN timescaledb_information.jobs job ON (job.config->>'mat_hypertable_id')::integer=cagg.mat_hypertable_id WHERE cagg.user_view_schema='public' AND cagg.user_view_name = ANY (ARRAY['monitoring_measurement_5min','monitoring_measurement_1hour','monitoring_measurement_1day','monitoring_effective_setpoints_1min','monitoring_effective_setpoints_5min','monitoring_automation_state_1min','monitoring_automation_state_5min','monitoring_light_intensity_1hour','monitoring_light_intensity_1day','monitoring_requested_pid_1hour','monitoring_requested_pid_1day']) AND job.proc_name='policy_refresh_continuous_aggregate' AND job.scheduled")" \
  '11' 'backfill completion schedules all eleven view refresh policies'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_schema='public' AND proc_name IN ('monitoring_refresh_overview_incremental','monitoring_rebuild_relay_overview') AND scheduled")" \
  '4' 'overview jobs restore their snapshotted schedules'
for view_name in \
  monitoring_measurement_5min monitoring_measurement_1hour monitoring_measurement_1day \
  monitoring_effective_setpoints_1min monitoring_effective_setpoints_5min \
  monitoring_automation_state_1min monitoring_automation_state_5min \
  monitoring_light_intensity_1hour monitoring_light_intensity_1day \
  monitoring_requested_pid_1hour monitoring_requested_pid_1day; do
  assert_positive "$(query_db "$DATABASE_NAME" "SELECT count(*) FROM public.$view_name")" \
    "backfill materialized $view_name"
done
assert_backfill_parity
assert_measurement_ancestor_freshness

printf 'forcing down migration failure after its first committed phase\n'
down_migration_file="$MIGRATION_RELEASE_ROOT/Infrastructure/database/migrations/0007_time_column_rename.down.sql"
if psql_as_postgres --dbname="$DATABASE_NAME" \
    --command="SELECT set_config('app.time_rename_test_fail_after_first_commit', '1', FALSE)" \
    --file="$down_migration_file" >/dev/null; then
  fail 'test-only down migration failure switch did not stop after the first commit'
  exit 1
fi
printf 'first down phase committed; checking snapshots and renamed schema remain resumable\n'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('automation_state','effective_setpoints','control_history','pid_parameter_history','relay_observation','monitoring_room_photoperiod','monitoring_photoperiod_coverage','mode_transition_history') AND column_name IN ('timestamp','observed_at','triggered_at')")" \
  '8' 'first down phase restored all source event-column names'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM public.monitoring_job_snapshot_0007")" \
  '4' 'down snapshots survive the injected post-commit failure'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT to_regclass('public.monitoring_job_snapshot_0007') IS NOT NULL AND to_regclass('public.monitoring_refresh_policy_snapshot_0007') IS NOT NULL AND to_regclass('public.monitoring_index_snapshot_0007') IS NOT NULL")" \
  't' 'all down snapshots survive until the final phase succeeds'
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM timescaledb_information.continuous_aggregates WHERE view_schema='public' AND view_name = ANY (ARRAY['monitoring_measurement_5min','monitoring_measurement_1hour','monitoring_measurement_1day','monitoring_effective_setpoints_1min','monitoring_effective_setpoints_5min','monitoring_automation_state_1min','monitoring_automation_state_5min','monitoring_light_intensity_1hour','monitoring_light_intensity_1day','monitoring_requested_pid_1hour','monitoring_requested_pid_1day'])")" \
  '0' 'first down phase leaves rebuilt aggregates absent for canonical restoration'
printf 're-running down migration from the committed phase\n'
run_migrator "$DATABASE_NAME" down --to 0006_monitoring_gate_auto_clear
assert_original_state_restored

printf 'reapplying 0007 after restartable down\n'
run_migrator "$DATABASE_NAME" apply --skip-alembic --allow-downtime
assert_event_time_cutover
assert_equal "$(query_db "$DATABASE_NAME" \
  "SELECT count(*) FROM public.monitoring_job_snapshot_0007")" \
  '4' 'reapplied migration snapshots overview jobs again'
printf 'Phase 4 time-column migration proof passed\n'
