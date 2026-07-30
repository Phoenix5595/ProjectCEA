#!/usr/bin/env bash
set -euo pipefail

database_name="${REGISTRY_TEST_DB_NAME:-registry_task6_test}"
if [[ "${database_name,,}" == "cea_sensors" ]]; then
  printf '%s\n' "Refusing to run registry integration tests against cea_sensors." >&2
  exit 64
fi
if [[ -n "${PGHOST:-}" && "$PGHOST" != /tmp/* ]]; then
  printf '%s\n' "Refusing a non-temporary PostgreSQL host: $PGHOST" >&2
  exit 64
fi
if [[ -n "${REDIS_URL:-}" ]]; then
  printf '%s\n' "Refusing a preconfigured Redis target: $REDIS_URL" >&2
  exit 64
fi

for command in initdb pg_ctl createdb redis-server; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required local test command is unavailable: %s\n' "$command" >&2
    exit 69
  fi
done

temp_root="$(mktemp -d /tmp/registry-control.XXXXXX)"
postgres_data="${temp_root}/postgres"
postgres_socket="${temp_root}/postgres-socket"
redis_dir="${temp_root}/redis"
postgres_port="$(python3 -c 'import socket; socket_instance = socket.socket(); socket_instance.bind(("127.0.0.1", 0)); print(socket_instance.getsockname()[1]); socket_instance.close()')"
redis_port="$(python3 -c 'import socket; socket_instance = socket.socket(); socket_instance.bind(("127.0.0.1", 0)); print(socket_instance.getsockname()[1]); socket_instance.close()')"

cleanup() {
  pg_ctl -D "$postgres_data" stop -m immediate >/dev/null 2>&1 || true
  [[ -n "${redis_pid:-}" ]] && kill "$redis_pid" >/dev/null 2>&1 || true
  rm -rf "$temp_root"
}
trap cleanup EXIT

case "$temp_root" in
  /tmp/registry-control.*) ;;
  *) printf '%s\n' "Refusing non-temporary integration directory: $temp_root" >&2; exit 64 ;;
esac

mkdir -p "$postgres_socket" "$redis_dir"
initdb --username=registry_test --auth=trust --no-locale --encoding=UTF8 -D "$postgres_data" >/dev/null
pg_ctl -D "$postgres_data" -o "-k $postgres_socket -p $postgres_port" -w start >/dev/null
createdb -h "$postgres_socket" -p "$postgres_port" -U registry_test "$database_name"
redis-server --bind 127.0.0.1 --port "$redis_port" --dir "$redis_dir" --save '' --appendonly no >/dev/null 2>&1 &
redis_pid="$!"

export REGISTRY_TEST_DB_NAME="$database_name"
export PGHOST="$postgres_socket"
export PGPORT="$postgres_port"
export PGUSER=registry_test
export REDIS_URL="redis://127.0.0.1:${redis_port}/15"
pytest -q app/tests/integration/registry_control
