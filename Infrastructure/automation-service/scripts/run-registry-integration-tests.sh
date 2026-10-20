#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIRECTORY/../../database/tests/test-monitoring-read-models.sh"

fail() {
  printf '%s\n' "$1" >&2
  return 1
}

reject_inherited_connection_environment
if [[ -n "${REDIS_URL:-}" ]]; then
  fail "Refusing a preconfigured Redis target: $REDIS_URL"
  exit 64
fi


database_name="${REGISTRY_TEST_DB_NAME:-monitoring_test_registry_control_$$}"
role_name="monitoring_role_registry_control_$$"
if [[ ! "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]]; then
  fail "Refusing database outside monitoring_test_ namespace: $database_name"
  exit 64
fi

for command in mktemp openssl psql python3 redis-server sudo pytest; do
  if ! command -v "$command" >/dev/null 2>&1; then
    fail "Required local test command is unavailable: $command"
    exit 69
  fi
done

temp_root="$(mktemp -d /tmp/registry-control.XXXXXX)"
redis_dir="${temp_root}/redis"
redis_port="$(python3 -c 'import socket; instance = socket.socket(); instance.bind(("127.0.0.1", 0)); print(instance.getsockname()[1]); instance.close()')"
database_password="$(openssl rand -hex 24)"
database_created=0
role_created=0
redis_pid=''

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if ((database_created)); then
    if assert_monitoring_test_target "$database_name"; then
      if monitoring_postgres_psql --dbname=postgres \
        --command="DROP DATABASE IF EXISTS \"$database_name\" WITH (FORCE)" >/dev/null; then
        database_created=0
      else
        fail "Could not drop disposable database: $database_name"
        status=1
      fi
    else
      status=1
    fi
  fi
  if ((role_created)); then
    if assert_monitoring_test_target "$database_name" && \
       monitoring_postgres_psql --dbname=postgres \
        --command="DROP ROLE \"$role_name\"" >/dev/null; then
      role_created=0
    else
      fail "Could not drop disposable role: $role_name"
      status=1
    fi
  fi
  if [[ -n "$redis_pid" ]]; then
    kill "$redis_pid" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$temp_root" || status=1
  exit "$status"
}
trap cleanup EXIT

case "$temp_root" in
  /tmp/registry-control.*) ;;
  *) fail "Refusing non-temporary integration directory: $temp_root"; exit 64 ;;
esac
mkdir -p "$redis_dir"

assert_monitoring_test_target "$database_name"
if [[ "$(monitoring_postgres_psql --tuples-only --no-align --dbname=postgres \
  --command="SELECT count(*) FROM pg_database WHERE datname = '$database_name'")" != '0' ]]; then
  fail "Disposable database already exists: $database_name"
  exit 1
fi
if [[ "$(monitoring_postgres_psql --tuples-only --no-align --dbname=postgres \
  --command="SELECT count(*) FROM pg_roles WHERE rolname = '$role_name'")" != '0' ]]; then
  fail "Disposable role already exists: $role_name"
  exit 1
fi

monitoring_postgres_psql --dbname=postgres \
  --command="CREATE ROLE \"$role_name\" LOGIN PASSWORD '$database_password'" >/dev/null
role_created=1
monitoring_postgres_psql --dbname=postgres \
  --command="CREATE DATABASE \"$database_name\" OWNER \"$role_name\" TEMPLATE template0" >/dev/null
database_created=1
monitoring_postgres_psql --dbname="$database_name" \
  --command='CREATE EXTENSION IF NOT EXISTS timescaledb' >/dev/null

export CEA_TEST_PGPORT CEA_TEST_PGHOST
export PGHOST="$CEA_TEST_PGHOST" PGPORT="$CEA_TEST_PGPORT"
export PGUSER="$role_name" PGPASSWORD="$database_password" PGDATABASE="$database_name"
export REGISTRY_TEST_DB_NAME="$database_name"
export REDIS_URL="redis://127.0.0.1:${redis_port}/15"
redis-server --bind 127.0.0.1 --port "$redis_port" --dir "$redis_dir" --save '' --appendonly no >/dev/null 2>&1 &
redis_pid="$!"
pytest -q app/tests/integration/registry_control
