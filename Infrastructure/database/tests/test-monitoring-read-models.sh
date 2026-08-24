#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIRECTORY
readonly FIXTURE_SQL="$SCRIPT_DIRECTORY/fixtures/monitoring_test_fixture.sql"
readonly READ_MODELS_SQL="$SCRIPT_DIRECTORY/../monitoring_read_models.sql"
readonly ACTIVATE_POLICIES_SQL="$SCRIPT_DIRECTORY/../monitoring_read_models_activate_policies.sql"
readonly POSTGRES_ADMIN_HOST='/var/run/postgresql'
readonly POSTGRES_ADMIN_DATABASE='postgres'

fail() {
  printf 'monitoring test harness: %s\n' "$1" >&2
  return 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

reject_inherited_connection_environment() {
  local environment_line
  local variable_name

  while IFS= read -r environment_line; do
    variable_name="${environment_line%%=*}"
    case "$variable_name" in
      PG* | DATABASE_URL | MONITORING_TEST_DATABASE_URL)
        fail "refusing inherited database environment variable: $variable_name"
        return 1
        ;;
    esac
  done < <(env)
}

assert_safe_database_url() {
  local database_url="$1"
  local host
  local port
  local database_name
  local lower_host
  local lower_database_name

  if [[ ! "$database_url" =~ ^postgresql://[^:/@]+:[^/@]+@([^:/?#]+):([0-9]+)/([^/?#]+)$ ]]; then
    fail 'generated database URL does not match the guarded PostgreSQL URL shape'
    return 1
  fi

  host="${BASH_REMATCH[1]}"
  port="${BASH_REMATCH[2]}"
  database_name="${BASH_REMATCH[3]}"
  lower_host="${host,,}"
  lower_database_name="${database_name,,}"

  case "$lower_host" in
    127.0.0.1 | localhost | ::1) ;;
    *)
      fail "refusing non-loopback database host: $host"
      return 1
      ;;
  esac

  case "$lower_host" in
    *mothernode* | *iskra* | *projectcea*)
      fail "refusing production-looking database host: $host"
      return 1
      ;;
  esac

  if [[ "$lower_database_name" == 'cea_sensors' || "$lower_database_name" == 'cea_sensors_test' || "$lower_database_name" == *cea_sensors* ]]; then
    fail "refusing production database name: $database_name"
    return 1
  fi

  if [[ ! "$lower_database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]]; then
    fail "test database name must use the monitoring_test_ namespace: $database_name"
    return 1
  fi

  if ((10#$port < 1 || 10#$port > 65535)); then
    fail "invalid PostgreSQL port: $port"
    return 1
  fi
}

case_sql_path() {
  case "$1" in
    harness-lifecycle | schema-idempotent-and-policy-disabled | reject-incompatible-and-destructive | statistics-equivalence | reject-average-of-averages | sensor-tier-edges-tail-invalidation | control-history-tiers-and-precedence)
      printf '%s/cases/%s.sql\n' "$SCRIPT_DIRECTORY" "$1"
      ;;
    *)
      fail "unknown case: $1"
      return 1
      ;;
  esac
}

run_internal_case() {
  local case_name="$1"
  local case_sql
  local database_url="${MONITORING_TEST_DATABASE_URL:-}"

  [[ "${MONITORING_HARNESS_INTERNAL:-}" == '1' ]] || {
    fail 'internal case runner may only be called by with_monitoring_test_db'
    return 1
  }
  [[ -n "$database_url" ]] || {
    fail 'internal case runner did not receive a disposable database URL'
    return 1
  }

  assert_safe_database_url "$database_url"
  require_command psql
  case_sql="$(case_sql_path "$case_name")"
  [[ -f "$case_sql" ]] || {
    fail "case SQL is missing: $case_sql"
    return 1
  }

  printf 'running monitoring case: %s\n' "$case_name"

  if [[ "$case_name" == 'reject-incompatible-and-destructive' ]]; then
    local definition_sql
    local activation_sql
    local forbidden_pattern

    [[ -f "$READ_MODELS_SQL" && -f "$ACTIVATE_POLICIES_SQL" ]] || {
      fail 'monitoring SQL artifacts are missing'
      return 1
    }
    definition_sql="$(<"$READ_MODELS_SQL")"
    activation_sql="$(<"$ACTIVATE_POLICIES_SQL")"
    forbidden_pattern='(^|[^[:alpha:]_])(DROP|TRUNCATE|DELETE)([^[:alpha:]_]|$)|ALTER[[:space:]]+TABLE[^;]+DROP[[:space:]]+COLUMN'
    shopt -s nocasematch
    if [[ "$definition_sql" =~ $forbidden_pattern || "$activation_sql" =~ $forbidden_pattern ]]; then
      fail 'monitoring SQL contains a forbidden destructive statement'
      return 1
    fi
    shopt -u nocasematch

    psql -X --set=ON_ERROR_STOP=1 \
      --command='CREATE TABLE monitoring_measurement_1min (incompatible INTEGER)' \
      "$database_url" >/dev/null
    if psql -X --set=ON_ERROR_STOP=1 --file="$READ_MODELS_SQL" "$database_url" >/dev/null 2>&1; then
      fail 'definition SQL accepted an incompatible existing object'
      return 1
    fi
    psql -X --set=ON_ERROR_STOP=1 \
      --command='ALTER TABLE monitoring_measurement_1min RENAME TO monitoring_incompatible_probe' \
      "$database_url" >/dev/null
    psql -X --set=ON_ERROR_STOP=1 --file="$READ_MODELS_SQL" "$database_url" >/dev/null
    if psql -X --set=ON_ERROR_STOP=1 --file="$ACTIVATE_POLICIES_SQL" "$database_url" >/dev/null 2>&1; then
      fail 'policy activation succeeded without supervised backfill markers'
      return 1
    fi
  fi

  psql -X --set=ON_ERROR_STOP=1 --file="$case_sql" "$database_url"
}

cleanup_monitoring_test_db() {
  local original_status=$?
  local cleanup_status=0

  trap - EXIT INT TERM HUP
  set +e

  if ((MONITORING_DATABASE_CREATED)); then
    if sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
      --host="$POSTGRES_ADMIN_HOST" \
      --dbname="$POSTGRES_ADMIN_DATABASE" \
      --command="DROP DATABASE \"$MONITORING_DATABASE_NAME\" WITH (FORCE)" >/dev/null 2>&1; then
      printf 'dropped monitoring database: %s\n' "$MONITORING_DATABASE_NAME"
    else
      printf 'failed to drop monitoring database: %s\n' "$MONITORING_DATABASE_NAME" >&2
      cleanup_status=1
    fi
  fi

  if ((MONITORING_ROLE_CREATED)); then
    if sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
      --host="$POSTGRES_ADMIN_HOST" \
      --dbname="$POSTGRES_ADMIN_DATABASE" \
      --command="DROP ROLE \"$MONITORING_ROLE_NAME\"" >/dev/null 2>&1; then
      printf 'dropped monitoring role: %s\n' "$MONITORING_ROLE_NAME"
    else
      printf 'failed to drop monitoring role: %s\n' "$MONITORING_ROLE_NAME" >&2
      cleanup_status=1
    fi
  fi

  if ((original_status == 0 && cleanup_status != 0)); then
    exit "$cleanup_status"
  fi
  exit "$original_status"
}

with_monitoring_test_db() (
  set -euo pipefail

  [[ "$#" -gt 0 ]] || {
    fail 'with_monitoring_test_db requires a child command'
    exit 2
  }

  reject_inherited_connection_environment
  require_command psql
  require_command openssl
  require_command sudo
  [[ -f "$FIXTURE_SQL" ]] || {
    fail "fixture SQL is missing: $FIXTURE_SQL"
    exit 1
  }

  local token
  token="$(date -u +%Y%m%d%H%M%S)_$$_${RANDOM}"
  MONITORING_DATABASE_NAME="monitoring_test_$token"
  MONITORING_ROLE_NAME="monitoring_role_$token"
  MONITORING_DATABASE_CREATED=0
  MONITORING_ROLE_CREATED=0
  local database_password
  database_password="$(openssl rand -hex 24)"
  local database_port
  local database_url
  local timescaledb_version

  trap cleanup_monitoring_test_db EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP

  database_port="$(sudo -u postgres psql -X --tuples-only --no-align \
    --host="$POSTGRES_ADMIN_HOST" \
    --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command='SHOW port')"
  [[ "$database_port" =~ ^[0-9]+$ ]] || {
    fail "local PostgreSQL reported an invalid port: $database_port"
    exit 1
  }

  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" \
    --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command="CREATE ROLE \"$MONITORING_ROLE_NAME\" LOGIN PASSWORD '$database_password'" >/dev/null
  MONITORING_ROLE_CREATED=1
  printf 'created monitoring role: %s\n' "$MONITORING_ROLE_NAME"

  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" \
    --dbname="$POSTGRES_ADMIN_DATABASE" \
    --command="CREATE DATABASE \"$MONITORING_DATABASE_NAME\" OWNER \"$MONITORING_ROLE_NAME\" TEMPLATE template0" >/dev/null
  MONITORING_DATABASE_CREATED=1
  printf 'created monitoring database: %s\n' "$MONITORING_DATABASE_NAME"

  database_url="postgresql://$MONITORING_ROLE_NAME:$database_password@127.0.0.1:$database_port/$MONITORING_DATABASE_NAME"
  assert_safe_database_url "$database_url"

  sudo -u postgres psql -X --set=ON_ERROR_STOP=1 \
    --host="$POSTGRES_ADMIN_HOST" \
    --dbname="$MONITORING_DATABASE_NAME" \
    --command='CREATE EXTENSION IF NOT EXISTS timescaledb' >/dev/null
  timescaledb_version="$(sudo -u postgres psql -X --tuples-only --no-align \
    --host="$POSTGRES_ADMIN_HOST" \
    --dbname="$MONITORING_DATABASE_NAME" \
    --command="SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")"
  [[ -n "$timescaledb_version" ]] || {
    fail 'TimescaleDB extension was not installed in the disposable database'
    exit 1
  }
  printf 'activated TimescaleDB %s in: %s\n' "$timescaledb_version" "$MONITORING_DATABASE_NAME"

  psql -X --set=ON_ERROR_STOP=1 --file="$FIXTURE_SQL" "$database_url"
  printf 'loaded monitoring fixtures into: %s\n' "$MONITORING_DATABASE_NAME"

  env \
    MONITORING_HARNESS_INTERNAL=1 \
    MONITORING_TEST_DATABASE_URL="$database_url" \
    "$@"
)

usage() {
  cat <<'USAGE'
Usage: test-monitoring-read-models.sh --case CASE

Cases:
  harness-lifecycle
  schema-idempotent-and-policy-disabled
  reject-incompatible-and-destructive
  statistics-equivalence
  reject-average-of-averages
  sensor-tier-edges-tail-invalidation
  control-history-tiers-and-precedence

Source this file to call with_monitoring_test_db <child-command> directly.
USAGE
}

main() {
  local case_name

  if [[ "${1:-}" == '--internal-case' ]]; then
    [[ "$#" -eq 2 ]] || {
      fail '--internal-case requires exactly one case name'
      return 2
    }
    run_internal_case "$2"
    return
  fi

  reject_inherited_connection_environment

  case "${1:-}" in
    --case)
      [[ "$#" -eq 2 ]] || {
        fail '--case requires exactly one case name'
        return 2
      }
      case_name="$2"
      case_sql_path "$case_name" >/dev/null
      with_monitoring_test_db bash "$SCRIPT_DIRECTORY/test-monitoring-read-models.sh" --internal-case "$case_name"
      ;;
    --help | -h)
      usage
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
