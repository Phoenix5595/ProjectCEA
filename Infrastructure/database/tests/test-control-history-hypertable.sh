#!/usr/bin/env bash
set -euo pipefail

HARNESS_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd -- "$HARNESS_DIRECTORY/../../.." && pwd)"
MIGRATOR="$WORKTREE_ROOT/Infrastructure/scripts/db_migrate.py"
PRODUCTION_FIXTURE="$HARNESS_DIRECTORY/fixtures/grow_data_lifecycle_fixture.sql"
HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'
DATABASE_NAME="monitoring_test_control_history_hypertable_$$"
FRESH_DATABASE_NAME="monitoring_test_control_history_fresh_$$"
DATABASES_CREATED=()
TMP_ROOT=''
MIGRATION_RELEASE_ROOT=''

fail() {
  printf 'control history hypertable proof: %s\n' "$1" >&2
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

query_db() {
  local database_name="$1" sql="$2"
  psql_as_postgres --quiet --dbname="$database_name" --tuples-only --no-align --command="$sql"
}
row_data_digest() {
  local database_name="$1"
  query_db "$database_name" \
    "SELECT md5(COALESCE(jsonb_agg(to_jsonb(h) ORDER BY h.id, h.timestamp)::text, '[]')) FROM public.control_history AS h"
}

assert_equal() {
  local actual="$1" expected="$2" description="$3"
  if [[ "$actual" != "$expected" ]]; then
    fail "$description (expected '$expected', got '$actual')"
    return 1
  fi
}

assert_row_data() {
  local database_name="$1"
  local actual_digest
  actual_digest="$(row_data_digest "$database_name")"
  assert_equal "$actual_digest" "$EXPECTED_ROW_DIGEST" 'control_history row data'
}

create_disposable_database() {
  local database_name="$1"
  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    fail "refusing unsafe database name: $database_name"
    return 1
  }
  [[ "${database_name,,}" != *cea_sensors* && "${database_name,,}" != *sj_cea* ]] || {
    fail "refusing protected database name: $database_name"
    return 1
  }
  if [[ -n "$(psql_as_postgres --dbname=postgres --tuples-only --no-align \
      --command="SELECT 1 FROM pg_database WHERE datname = '$database_name'")" ]]; then
    fail "disposable database already exists: $database_name"
    return 1
  fi
  psql_as_postgres --dbname=postgres \
    --command="CREATE DATABASE \"$database_name\" OWNER postgres TEMPLATE template0" >/dev/null
  DATABASES_CREATED+=("$database_name")
  printf 'created disposable database: %s\n' "$database_name"
  psql_as_postgres --dbname="$database_name" \
    --command='CREATE EXTENSION timescaledb' >/dev/null
  printf 'activated TimescaleDB in: %s\n' "$database_name"
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

assert_hypertable() {
  local database_name="$1"
  assert_equal "$(query_db "$database_name" \
    "SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_schema='public' AND hypertable_name='control_history')")" \
    't' 'control_history is a hypertable'
  assert_equal "$(query_db "$database_name" \
    "SELECT string_agg(a.attname, ',' ORDER BY k.ordinality) FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum WHERE c.conrelid='public.control_history'::regclass AND c.contype='p'")" \
    'id,timestamp' 'hypertable primary key columns'
  assert_equal "$(query_db "$database_name" \
    "SELECT count(*) FROM public.control_history")" \
    "$EXPECTED_ROW_COUNT" 'hypertable row count'
  assert_row_data "$database_name"
  assert_equal "$(query_db "$database_name" \
    "SELECT string_agg(attname, ',' ORDER BY attnum) FROM pg_attribute WHERE attrelid='public.control_history'::regclass AND attnum>0 AND NOT attisdropped")" \
    'id,timestamp,location,cluster,device_name,channel,old_state,new_state,mode,reason,sensor_value,setpoint,load_percent,manual_expires_at' \
    'hypertable column order'
  assert_equal "$(query_db "$database_name" \
    "SELECT segmentby || '|' || orderby FROM timescaledb_information.hypertable_compression_settings WHERE hypertable='public.control_history'::regclass")" \
    'location,cluster,device_name,channel|"timestamp" DESC' \
    'compression segment and order settings'
  assert_equal "$(query_db "$database_name" \
    "SELECT EXISTS (SELECT 1 FROM timescaledb_information.dimensions WHERE hypertable_schema='public' AND hypertable_name='control_history' AND column_name='timestamp' AND time_interval=INTERVAL '7 days')")" \
    't' 'seven-day chunk interval'
  assert_equal "$(query_db "$database_name" \
    "SELECT h.compression_enabled AND EXISTS (SELECT 1 FROM timescaledb_information.jobs j WHERE j.hypertable_schema='public' AND j.hypertable_name='control_history' AND j.proc_name='policy_compression' AND (j.config->>'compress_after')::interval=INTERVAL '3 months') FROM timescaledb_information.hypertables h WHERE h.hypertable_schema='public' AND h.hypertable_name='control_history'")" \
    't' 'three-month compression policy'
  assert_equal "$(query_db "$database_name" \
    "SELECT to_regclass('public.control_history_timestamp_idx') IS NOT NULL AND to_regclass('public.idx_control_history_timestamp') IS NULL AND to_regclass('public.idx_control_history_location') IS NOT NULL AND to_regclass('public.idx_control_history_manual_expires') IS NOT NULL AND to_regclass('public.idx_control_history_device_channel_time') IS NOT NULL")" \
    't' 'hypertable indexes'
}

assert_plain_table() {
  local database_name="$1"
  assert_equal "$(query_db "$database_name" \
    "SELECT NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_schema='public' AND hypertable_name='control_history')")" \
    't' 'control_history is a plain table after rollback'
  assert_equal "$(query_db "$database_name" \
    "SELECT string_agg(a.attname, ',' ORDER BY k.ordinality) FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum WHERE c.conrelid='public.control_history'::regclass AND c.contype='p'")" \
    'id' 'plain-table primary key columns'
  assert_equal "$(query_db "$database_name" \
    "SELECT count(*) FROM public.control_history")" \
    "$EXPECTED_ROW_COUNT" 'plain-table row count'
  assert_row_data "$database_name"
  assert_equal "$(query_db "$database_name" \
    "SELECT to_regclass('public.idx_control_history_location') IS NOT NULL AND to_regclass('public.idx_control_history_timestamp') IS NOT NULL AND to_regclass('public.idx_control_history_manual_expires') IS NOT NULL AND to_regclass('public.idx_control_history_device_channel_time') IS NULL")" \
    't' 'restored plain-table indexes'
  assert_equal "$(query_db "$database_name" \
    "SELECT NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs WHERE hypertable_schema='public' AND hypertable_name='control_history' AND proc_name='policy_compression')")" \
    't' 'compression policy removed on rollback'
}

reject_inherited_database_environment
for required_command in cp flock mktemp sudo psql python3; do
  command -v "$required_command" >/dev/null 2>&1 || {
    fail "required command not found: $required_command"
    exit 1
  }
done
[[ -f "$MIGRATOR" ]] || { fail "migration runner not found: $MIGRATOR"; exit 1; }
[[ -f "$WORKTREE_ROOT/Infrastructure/database/migrations/0002_control_history_hypertable.sql" ]] || {
  fail '0002_control_history_hypertable.sql is missing'
  exit 1
}
[[ -f "$PRODUCTION_FIXTURE" ]] || { fail "fresh-schema fixture not found: $PRODUCTION_FIXTURE"; exit 1; }
[[ "$DATABASE_NAME" =~ ^monitoring_test_[a-z0-9_]+$ && "$FRESH_DATABASE_NAME" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
  fail 'generated disposable database name is unsafe'
  exit 1
}

exec {lock_fd}> "$HARNESS_LOCK" || { fail "could not open shared database harness lock: $HARNESS_LOCK"; exit 1; }
flock -x "$lock_fd" || { fail "could not acquire shared database harness lock: $HARNESS_LOCK"; exit 1; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/projectcea-control-history-hypertable.XXXXXX")"
MIGRATION_RELEASE_ROOT="$TMP_ROOT/release"
mkdir -p "$MIGRATION_RELEASE_ROOT/Infrastructure/database/migrations"
chmod 755 "$TMP_ROOT"
for migration in \
  0001_alembic_stamp_side_branch.sql \
  0001_alembic_stamp_side_branch.down.sql \
  0002_control_history_hypertable.sql \
  0002_control_history_hypertable.down.sql; do
  cp -- "$WORKTREE_ROOT/Infrastructure/database/migrations/$migration" \
    "$MIGRATION_RELEASE_ROOT/Infrastructure/database/migrations/$migration"
done

printf 'using isolated runner release with 0001 and 0002 only\n'

create_disposable_database "$DATABASE_NAME"
psql_as_postgres --dbname="$DATABASE_NAME" <<'SQL'
CREATE TABLE public.control_history (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    channel INTEGER NOT NULL,
    old_state INTEGER,
    new_state INTEGER,
    mode TEXT,
    reason TEXT,
    sensor_value REAL,
    setpoint REAL,
    load_percent REAL,
    manual_expires_at TIMESTAMPTZ
);
CREATE INDEX idx_control_history_location
    ON public.control_history (location, cluster);
CREATE INDEX idx_control_history_manual_expires
    ON public.control_history (manual_expires_at)
    WHERE manual_expires_at IS NOT NULL AND mode = 'manual';
CREATE INDEX idx_control_history_timestamp
    ON public.control_history (timestamp DESC);
WITH sample AS (
    SELECT
        g,
        CASE
            WHEN g = 1 THEN '2026-01-06 10:01:10.124886-05'::timestamptz
            WHEN g = 60 THEN '2026-10-03 11:31:17.917421-04'::timestamptz
            ELSE '2026-01-06 10:01:10.124886-05'::timestamptz
                 + (g - 1) * INTERVAL '4 days'
        END AS control_time
    FROM generate_series(1, 60) AS g
)
INSERT INTO public.control_history (
    id, timestamp, location, cluster, device_name, channel,
    old_state, new_state, mode, reason, sensor_value, setpoint, load_percent, manual_expires_at
)
SELECT
    g,
    control_time,
    'Flower Room',
    'main',
    format('light_%s', 1 + (g % 3)),
    g % 4,
    CASE WHEN g % 2 = 0 THEN 0 ELSE 1 END,
    CASE WHEN g % 2 = 0 THEN 1 ELSE 0 END,
    CASE WHEN g % 7 = 0 THEN 'manual' ELSE 'auto' END,
    format('proof row %s', g),
    20.0 + (g::real / 10),
    22.0,
    (g % 100)::real,
    CASE WHEN g % 7 = 0 THEN control_time + INTERVAL '1 hour' END
FROM sample;
SELECT setval(
    'public.control_history_id_seq',
    (SELECT max(id) FROM public.control_history),
    TRUE);
SQL
EXPECTED_ROW_COUNT=60
EXPECTED_ROW_DIGEST="$(row_data_digest "$DATABASE_NAME")"
printf 'created production-shape plain control_history table with %s rows\n' "$EXPECTED_ROW_COUNT"

run_migrator "$DATABASE_NAME" apply --allow-downtime --skip-alembic
assert_hypertable "$DATABASE_NAME"
printf 'production-shape apply: hypertable, rows, primary key, indexes and compression policy passed\n'

run_migrator "$DATABASE_NAME" down --to 0001_alembic_stamp_side_branch
assert_plain_table "$DATABASE_NAME"
printf 'production-shape rollback: plain table, rows, primary key and indexes passed\n'

run_migrator "$DATABASE_NAME" apply --allow-downtime --skip-alembic
assert_hypertable "$DATABASE_NAME"
NEW_ID="$(query_db "$DATABASE_NAME" \
  "INSERT INTO public.control_history (location, cluster, device_name, channel) VALUES ('Flower Room', 'main', 'light_1', 0) RETURNING id")"
assert_equal "$NEW_ID" '61' 'sequence-generated id after reapply'
EXPECTED_ROW_COUNT=61
EXPECTED_ROW_DIGEST="$(row_data_digest "$DATABASE_NAME")"
assert_hypertable "$DATABASE_NAME"
printf 'production-shape reapply: default sequence inserted id=%s\n' "$NEW_ID"

# The lifecycle fixture represents the Alembic-created schema shape used by
# fresh installs; exercise the migration against its already-hypertable source.
create_disposable_database "$FRESH_DATABASE_NAME"
psql_as_postgres --dbname="$FRESH_DATABASE_NAME" --file="$PRODUCTION_FIXTURE" >/dev/null
EXPECTED_ROW_COUNT="$(query_db "$FRESH_DATABASE_NAME" 'SELECT count(*) FROM public.control_history')"
EXPECTED_ROW_DIGEST="$(row_data_digest "$FRESH_DATABASE_NAME")"
run_migrator "$FRESH_DATABASE_NAME" apply --allow-downtime --skip-alembic
assert_hypertable "$FRESH_DATABASE_NAME"
printf 'fresh-schema fixture apply: hypertable, %s rows, primary key, indexes and compression policy passed\n' "$EXPECTED_ROW_COUNT"
run_migrator "$FRESH_DATABASE_NAME" down --to 0001_alembic_stamp_side_branch
assert_plain_table "$FRESH_DATABASE_NAME"
run_migrator "$FRESH_DATABASE_NAME" apply --allow-downtime --skip-alembic
assert_hypertable "$FRESH_DATABASE_NAME"
printf 'fresh-schema fixture rollback/reapply passed\n'
