#!/usr/bin/env bash
set -euo pipefail

SCHEMA_HYGIENE_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCHEMA_HYGIENE_DIRECTORY
readonly SCHEMA_HYGIENE_REPO_ROOT="$(cd -- "$SCHEMA_HYGIENE_DIRECTORY/../../.." && pwd)"
readonly SCHEMA_HYGIENE_PRODUCTION_FIXTURE="$SCHEMA_HYGIENE_DIRECTORY/fixtures/schema_hygiene_production.sql"
readonly SCHEMA_HYGIENE_EMPTY_FIXTURE="$SCHEMA_HYGIENE_DIRECTORY/fixtures/schema_hygiene_empty.sql"
readonly SCHEMA_HYGIENE_DB_MIGRATE="$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure/scripts/db_migrate.py"
readonly SCHEMA_HYGIENE_CEA_SCHEMA="$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure/database/cea_schema.sql"
readonly SCHEMA_HYGIENE_ALEMBIC_DIR="$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure/automation-service"
readonly SCHEMA_HYGIENE_LOCK='/tmp/grow-lifecycle-harness.lock'
SCHEMA_HYGIENE_ALEMBIC_BIN="$SCHEMA_HYGIENE_ALEMBIC_DIR/.venv/bin/alembic"
if [[ ! -x "$SCHEMA_HYGIENE_ALEMBIC_BIN" ]]; then
  SCHEMA_HYGIENE_ALEMBIC_BIN="$(command -v alembic || true)"
fi
readonly SCHEMA_HYGIENE_ALEMBIC_BIN

fail() {
  printf 'schema hygiene migration proof: %s\n' "$1" >&2
  return 1
}

assert_database_url() {
  local database_url="${MONITORING_TEST_DATABASE_URL:-}"
  local database_host raw_database_host database_port database_name
  local database_user database_password

  [[ "$database_url" =~ ^postgresql://([^:/@]+):([^/@]+)@([^:/?#]+):([0-9]+)/([^/?#]+)$ ]] || {
    fail 'missing or malformed guarded disposable database URL'
    return 1
  }
  database_user="${BASH_REMATCH[1]}"
  database_password="${BASH_REMATCH[2]}"
  raw_database_host="${BASH_REMATCH[3]}"
  database_port="${BASH_REMATCH[4]}"
  database_name="${BASH_REMATCH[5]}"
  database_host="${raw_database_host,,}"
  database_name="${database_name,,}"

  [[ "$database_host" == '127.0.0.1' || "$database_host" == 'localhost' || "$database_host" == '::1' ]] || {
    fail "refusing non-loopback database host: $database_host"
    return 1
  }
  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    fail "refusing database outside monitoring_test_ namespace: $database_name"
    return 1
  }
  [[ "$database_name" != *cea_sensors* && "$database_name" != *sj_cea* ]] || {
    fail "refusing protected database name: $database_name"
    return 1
  }
  [[ "$database_port" =~ ^[0-9]+$ ]] || {
    fail 'refusing invalid database port'
    return 1
  }

  export POSTGRES_HOST="$raw_database_host"
  export POSTGRES_PORT="$database_port"
  export POSTGRES_DB="$database_name"
  export POSTGRES_USER="$database_user"
  export POSTGRES_PASSWORD="$database_password"
}

run_alembic_fresh_install() {
  PYTHONPATH="$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure:$SCHEMA_HYGIENE_ALEMBIC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$SCHEMA_HYGIENE_ALEMBIC_BIN" \
    -c "$SCHEMA_HYGIENE_ALEMBIC_DIR/alembic.ini" upgrade 003_climate_single_row

  psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" \
    --file="$SCHEMA_HYGIENE_CEA_SCHEMA"

  # Alembic 005 installs policies on the compression settings configured for
  # fresh hypertables, so seed those settings before upgrading past revision 003.
  psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" <<'SQL'
ALTER TABLE public.measurement SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby = 'time DESC'
);
ALTER TABLE public.effective_setpoints SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name,mode',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SQL

  PYTHONPATH="$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure:$SCHEMA_HYGIENE_ALEMBIC_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$SCHEMA_HYGIENE_ALEMBIC_BIN" \
    -c "$SCHEMA_HYGIENE_ALEMBIC_DIR/alembic.ini" upgrade heads

  psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" <<'SQL'
CREATE VIEW public.measurement_legacy_grafana AS
    SELECT time, sensor_id FROM public.measurement;

CREATE FUNCTION public.get_sensor_data_optimized(text[], timestamptz, timestamptz)
RETURNS SETOF public.measurement
LANGUAGE sql
AS $$ SELECT * FROM public.measurement WHERE FALSE $$;

CREATE FUNCTION public.get_sensor_stats(text[], timestamptz, timestamptz)
RETURNS TABLE(sensor_id integer, reading_count bigint)
LANGUAGE sql
AS $$ SELECT sensor_id, count(*) FROM public.measurement WHERE FALSE GROUP BY sensor_id $$;

DO $fresh_seed_assertions$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT count(*)
    INTO v_count
    FROM timescaledb_information.continuous_aggregates
    WHERE view_schema = 'public'
      AND view_name = ANY (ARRAY[
          'measurement_1min', 'measurement_5min',
          'measurement_hourly', 'measurement_daily'
      ]);
    IF v_count <> 4 THEN
        RAISE EXCEPTION 'fresh install did not create all four legacy measurement CAGGs (found %)', v_count;
    END IF;

    SELECT count(*)
    INTO v_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_retention'
      AND hypertable_schema = 'public'
      AND hypertable_name = ANY (ARRAY['measurement', 'effective_setpoints']);
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'fresh install did not create both legacy retention policies (found %)', v_count;
    END IF;
END
$fresh_seed_assertions$;
SQL
}

assert_removed_and_added_objects() {
  psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" <<'SQL'
DO $schema_hygiene_assertions$
DECLARE
    v_name TEXT;
    v_definition TEXT;
    v_count INTEGER;
BEGIN
    FOREACH v_name IN ARRAY ARRAY[
        'idx_measurement_time',
        'idx_measurement_sensor_id',
        'idx_effective_setpoints_timestamp',
        'idx_effective_setpoints_location'
    ] LOOP
        IF to_regclass(format('public.%I', v_name)) IS NOT NULL THEN
            RAISE EXCEPTION 'duplicate or prefix index remains: %', v_name;
        END IF;
    END LOOP;

    FOREACH v_name IN ARRAY ARRAY[
        'measurement_time_idx',
        'idx_measurement_sensor_time',
        'effective_setpoints_timestamp_idx',
        'idx_effective_setpoints_device',
        'idx_effective_setpoints_device_mode_time',
        'idx_pid_parameter_history_room_type_time'
    ] LOOP
        IF to_regclass(format('public.%I', v_name)) IS NULL THEN
            RAISE EXCEPTION 'expected surviving or new index is missing: %', v_name;
        END IF;
    END LOOP;

    v_definition := pg_get_indexdef('public.idx_effective_setpoints_device_mode_time'::regclass);
    IF position('(location, cluster, device_name, mode, "timestamp" DESC)' IN v_definition) = 0 THEN
        RAISE EXCEPTION 'new effective-setpoints index has wrong definition: %', v_definition;
    END IF;
    v_definition := pg_get_indexdef('public.idx_pid_parameter_history_room_type_time'::regclass);
    IF position('(location, cluster, device_type, "timestamp" DESC)' IN v_definition) = 0 THEN
        RAISE EXCEPTION 'new PID-history index has wrong definition: %', v_definition;
    END IF;

    FOREACH v_name IN ARRAY ARRAY[
        'measurement_1min', 'measurement_5min', 'measurement_hourly', 'measurement_daily'
    ] LOOP
        IF to_regclass(format('public.%I', v_name)) IS NOT NULL THEN
            RAISE EXCEPTION 'legacy measurement CAGG remains: %', v_name;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1
        FROM pg_views
        WHERE schemaname = 'public'
          AND viewname ~ '^measurement_.*_grafana$'
    ) THEN
        RAISE EXCEPTION 'legacy measurement Grafana view remains';
    END IF;
    IF to_regprocedure('public.get_sensor_data_optimized(text[],timestamptz,timestamptz)') IS NOT NULL
       OR to_regprocedure('public.get_sensor_stats(text[],timestamptz,timestamptz)') IS NOT NULL THEN
        RAISE EXCEPTION 'legacy measurement helper function remains';
    END IF;

    SELECT count(*)
    INTO v_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_compression'
      AND hypertable_schema = 'public'
      AND hypertable_name = ANY (ARRAY['measurement', 'effective_setpoints'])
      AND (config ->> 'compress_after')::interval = INTERVAL '3 months';
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'expected two three-month compression policies, found %', v_count;
    END IF;

    SELECT count(*)
    INTO v_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_retention'
      AND hypertable_schema = 'public'
      AND hypertable_name = ANY (ARRAY['measurement', 'effective_setpoints']);
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'legacy measurement retention policies remain: %', v_count;
    END IF;
END
$schema_hygiene_assertions$;
SQL
}

run_internal() {
  [[ "${MONITORING_HARNESS_INTERNAL:-}" == '1' ]] || {
    fail 'internal case runner must be called by the guarded monitoring harness'
    return 1
  }
  assert_database_url

  local case_name="${SCHEMA_HYGIENE_CASE:-}"
  local migration_root="${SCHEMA_HYGIENE_MIGRATION_ROOT:-}"
  [[ -d "$migration_root/Infrastructure/database/migrations" ]] || {
    fail 'temporary migration release is missing'
    return 1
  }
  [[ "$case_name" != 'fresh-install' || -x "$SCHEMA_HYGIENE_ALEMBIC_BIN" ]] || {
    fail 'automation Alembic executable is missing'
    return 1
  }

  if [[ "$case_name" == 'fresh-install' ]]; then
    run_alembic_fresh_install
  elif [[ "$case_name" == 'production-shaped' ]]; then
    psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" <<'SQL'
DO $production_index_assertions$
BEGIN
    IF to_regclass('public.idx_measurement_time') IS NULL
       OR pg_get_indexdef('public.idx_measurement_time'::regclass)
          <> 'CREATE INDEX idx_measurement_time ON public.measurement USING btree ("time" DESC)'
       OR pg_get_indexdef('public.measurement_time_idx'::regclass)
          <> 'CREATE INDEX measurement_time_idx ON public.measurement USING btree ("time" DESC)' THEN
        RAISE EXCEPTION 'measurement time indexes are not exact duplicates';
    END IF;
    IF pg_get_indexdef('public.idx_measurement_sensor_id'::regclass)
          <> 'CREATE INDEX idx_measurement_sensor_id ON public.measurement USING btree (sensor_id)'
       OR pg_get_indexdef('public.idx_measurement_sensor_time'::regclass)
          <> 'CREATE INDEX idx_measurement_sensor_time ON public.measurement USING btree (sensor_id, "time" DESC)' THEN
        RAISE EXCEPTION 'measurement sensor index is not a pure prefix';
    END IF;
    IF pg_get_indexdef('public.idx_effective_setpoints_timestamp'::regclass)
          <> 'CREATE INDEX idx_effective_setpoints_timestamp ON public.effective_setpoints USING btree ("timestamp" DESC)'
       OR pg_get_indexdef('public.effective_setpoints_timestamp_idx'::regclass)
          <> 'CREATE INDEX effective_setpoints_timestamp_idx ON public.effective_setpoints USING btree ("timestamp" DESC)' THEN
        RAISE EXCEPTION 'effective-setpoints timestamp indexes are not exact duplicates';
    END IF;
    IF pg_get_indexdef('public.idx_effective_setpoints_location'::regclass)
          <> 'CREATE INDEX idx_effective_setpoints_location ON public.effective_setpoints USING btree (location, cluster)'
       OR pg_get_indexdef('public.idx_effective_setpoints_device'::regclass)
          <> 'CREATE INDEX idx_effective_setpoints_device ON public.effective_setpoints USING btree (location, cluster, device_name, "timestamp" DESC)' THEN
        RAISE EXCEPTION 'effective-setpoints location index is not a pure prefix';
    END IF;
END
$production_index_assertions$;
SQL
  else
    fail "unknown internal case: $case_name"
    return 2
  fi

  local automation_indexes_before automation_indexes_after first_apply second_apply
  automation_indexes_before="$(psql -X --no-psqlrc --tuples-only --no-align \
    "$MONITORING_TEST_DATABASE_URL" \
    --command="SELECT COALESCE(string_agg(indexname || E'\\t' || indexdef, E'\\n' ORDER BY indexname), '') FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'automation_state'")"

  first_apply="$(python3 "$SCHEMA_HYGIENE_DB_MIGRATE" --release "$migration_root" apply --skip-alembic --json 2>&1)" || {
    printf '%s\n' "$first_apply" >&2
    fail 'first db_migrate.py apply failed'
    return 1
  }
  printf '%s\n' "$first_apply"
  [[ "$first_apply" == *'"applied":["0004_index_cleanup","0005_drop_legacy_measurement_caggs"]'* ]] || {
    fail 'first db_migrate.py apply did not apply exactly migrations 0004 and 0005'
    return 1
  }
  assert_removed_and_added_objects

  second_apply="$(python3 "$SCHEMA_HYGIENE_DB_MIGRATE" --release "$migration_root" apply --skip-alembic --json 2>&1)" || {
    printf '%s\n' "$second_apply" >&2
    fail 'second db_migrate.py apply failed'
    return 1
  }
  printf '%s\n' "$second_apply"
  [[ "$second_apply" == *'"applied":[]'* ]] || {
    fail 'second db_migrate.py apply was not idempotent'
    return 1
  }
  assert_removed_and_added_objects

  # Simulate an interrupted transaction_per_chunk build: root index invalid and one
  # chunk left unindexed, with 0004 unrecorded. A re-run must rebuild every chunk.
  local database_name retry_apply
  database_name="$(psql -X --no-psqlrc --tuples-only --no-align "$MONITORING_TEST_DATABASE_URL" \
    --command='SELECT current_database()')"
  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    fail "refusing superuser simulation on non-disposable database: $database_name"
    return 1
  }
  sudo -u postgres psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$database_name" <<'SQL' || {
DO $simulate_interrupted_build$
DECLARE
    chunk_index regclass;
BEGIN
    -- Guarantee at least one chunk exists, so the proof covers a chunk index.
    INSERT INTO public.effective_setpoints ("timestamp", location, cluster, device_name, mode)
    VALUES (now(), 'Schema Hygiene Proof', 'main', 'proof', 'veg');
    SELECT format('%I.%I', ci.chunk_index_schema, ci.chunk_index_name)::regclass
      INTO chunk_index
      FROM (
          SELECT c.chunk_schema AS chunk_index_schema, ix.relname AS chunk_index_name
            FROM timescaledb_information.chunks c
            JOIN pg_index i ON i.indrelid = format('%I.%I', c.chunk_schema, c.chunk_name)::regclass
            JOIN pg_class ix ON ix.oid = i.indexrelid
           WHERE c.hypertable_name = 'effective_setpoints'
             AND ix.relname LIKE '%idx_effective_setpoints_device_mode_time'
           LIMIT 1
      ) ci;
    IF chunk_index IS NULL THEN
        RAISE EXCEPTION 'no effective_setpoints chunk index to simulate a partial build';
    END IF;
    EXECUTE format('DROP INDEX %s', chunk_index);
    RAISE NOTICE 'simulated partial build: dropped %', chunk_index;
    UPDATE pg_index SET indisvalid = false
     WHERE indexrelid = 'public.idx_effective_setpoints_device_mode_time'::regclass;
    DELETE FROM public.schema_migration WHERE version = '0004_index_cleanup';
END
$simulate_interrupted_build$;
SQL
    fail 'could not simulate an interrupted transaction_per_chunk build'
    return 1
  }
  retry_apply="$(python3 "$SCHEMA_HYGIENE_DB_MIGRATE" --release "$migration_root" apply --skip-alembic --json 2>&1)" || {
    printf '%s\n' "$retry_apply" >&2
    fail 'retry after interrupted index build failed'
    return 1
  }
  [[ "$retry_apply" == *'"applied":["0004_index_cleanup"]'* ]] || {
    fail 'retry did not re-apply exactly 0004'
    return 1
  }
  psql -X --no-psqlrc --set=ON_ERROR_STOP=1 "$MONITORING_TEST_DATABASE_URL" <<'SQL' || {
DO $rebuilt_index_assertions$
DECLARE
    unindexed integer;
BEGIN
    IF NOT (SELECT indisvalid FROM pg_index
             WHERE indexrelid = 'public.idx_effective_setpoints_device_mode_time'::regclass) THEN
        RAISE EXCEPTION 'rebuilt index is still invalid';
    END IF;
    SELECT count(*) INTO unindexed
      FROM timescaledb_information.chunks c
     WHERE c.hypertable_name = 'effective_setpoints'
       AND NOT EXISTS (
           SELECT 1 FROM pg_index i JOIN pg_class ix ON ix.oid = i.indexrelid
            WHERE i.indrelid = format('%I.%I', c.chunk_schema, c.chunk_name)::regclass
              AND ix.relname LIKE '%idx_effective_setpoints_device_mode_time'
       );
    IF unindexed > 0 THEN
        RAISE EXCEPTION 'rebuilt index is missing on % chunk(s)', unindexed;
    END IF;
END
$rebuilt_index_assertions$;
SQL
    fail 'interrupted index build was not fully rebuilt'
    return 1
  }
  assert_removed_and_added_objects

  automation_indexes_after="$(psql -X --no-psqlrc --tuples-only --no-align \
    "$MONITORING_TEST_DATABASE_URL" \
    --command="SELECT COALESCE(string_agg(indexname || E'\\t' || indexdef, E'\\n' ORDER BY indexname), '') FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'automation_state'")"
  [[ "$automation_indexes_before" == "$automation_indexes_after" ]] || {
    fail 'automation_state index definitions changed'
    return 1
  }

  printf 'schema hygiene %s: pass\n' "$case_name"
}

source "$SCHEMA_HYGIENE_DIRECTORY/test-monitoring-read-models.sh"

if [[ "${1:-}" == '--internal' ]]; then
  [[ "$#" -eq 1 ]] || {
    fail '--internal takes no arguments'
    exit 2
  }
  run_internal
  exit
fi

reject_inherited_connection_environment
require_command flock
require_command python3
[[ -f "$SCHEMA_HYGIENE_PRODUCTION_FIXTURE" ]] || fail 'production-shaped fixture is missing'
[[ -f "$SCHEMA_HYGIENE_EMPTY_FIXTURE" ]] || fail 'empty install fixture is missing'

# This proof shares the local PostgreSQL instance with the lifecycle harness.
# It acquires the same lock itself rather than wrapping another harness.
exec {schema_hygiene_lock_fd}> "$SCHEMA_HYGIENE_LOCK"
flock -x "$schema_hygiene_lock_fd"

schema_hygiene_release="$(mktemp -d)"
trap 'rm -rf -- "$schema_hygiene_release"' EXIT
mkdir -p "$schema_hygiene_release/Infrastructure/database/migrations"
for migration_name in \
  0004_index_cleanup.sql \
  0004_index_cleanup.down.sql \
  0005_drop_legacy_measurement_caggs.sql \
  0005_drop_legacy_measurement_caggs.down.sql; do
  ln -s "$SCHEMA_HYGIENE_REPO_ROOT/Infrastructure/database/migrations/$migration_name" \
    "$schema_hygiene_release/Infrastructure/database/migrations/$migration_name"
done

MONITORING_TEST_FIXTURE_SQL="$SCHEMA_HYGIENE_PRODUCTION_FIXTURE" \
  with_monitoring_test_db env \
    SCHEMA_HYGIENE_CASE=production-shaped \
    SCHEMA_HYGIENE_MIGRATION_ROOT="$schema_hygiene_release" \
    bash "$0" --internal

MONITORING_TEST_FIXTURE_SQL="$SCHEMA_HYGIENE_EMPTY_FIXTURE" \
  with_monitoring_test_db env \
    SCHEMA_HYGIENE_CASE=fresh-install \
    SCHEMA_HYGIENE_MIGRATION_ROOT="$schema_hygiene_release" \
    bash "$0" --internal

printf 'schema hygiene migration proof: all cases passed\n'
