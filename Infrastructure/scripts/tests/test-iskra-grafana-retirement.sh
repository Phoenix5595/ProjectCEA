#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIRECTORY/../../.." && pwd)"
readonly ISKRA_STACK="$REPOSITORY_ROOT/Infrastructure/iskra_stack"
readonly CADDYFILE="$REPOSITORY_ROOT/Infrastructure/caddy/Caddyfile"
readonly SERVICES="$REPOSITORY_ROOT/Infrastructure/services.yaml"
readonly REPLICA_ENTRYPOINT="$ISKRA_STACK/docker-entrypoint-replica.sh"
readonly LEGACY_ARCHIVE="$REPOSITORY_ROOT/archive/legacy/grafana"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local path="$1"
  local pattern="$2"
  grep -Fq -- "$pattern" "$path" || fail "$path is missing $pattern"
}

assert_no_grafana_deployment_reference() {
  local paths=(
    "$ISKRA_STACK/docker-compose.yml"
    "$ISKRA_STACK/.env.example"
    "$ISKRA_STACK/README.md"
    "$ISKRA_STACK/AGENTS.md"
    "$ISKRA_STACK/docker-entrypoint-replica.sh"
    "$ISKRA_STACK/scripts/redis_sync.py"
    "$REPOSITORY_ROOT/Infrastructure/scripts/verify_iskra.sh"
    "$CADDYFILE"
    "$REPOSITORY_ROOT/enable_autostart.sh"
    "$REPOSITORY_ROOT/restart_all_services.sh"
  )
  if grep -Iin --ignore-case -- 'grafana' "${paths[@]}"; then
    fail "Grafana deployment reference remains"
  fi
}

# Given: the repository deployment configuration.
# When: the Grafana retirement contract is checked.
# Then: Grafana-only configuration is absent while replica and monitoring contracts remain.
assert_no_grafana_deployment_reference

assert_contains "$ISKRA_STACK/docker-compose.yml" "projectcea_database:"
assert_contains "$ISKRA_STACK/docker-compose.yml" "projectcea_redis:"
assert_contains "$ISKRA_STACK/docker-compose.yml" "projectcea_redis_sync:"
assert_contains "$ISKRA_STACK/docker-compose.yml" "REPLICATION_SLOT: \${REPLICATION_SLOT}"
assert_contains "$ISKRA_STACK/docker-compose.yml" "PGDATA_HOST_PATH"
assert_contains "$ISKRA_STACK/docker-compose.yml" "POSTGRES_MAX_CONNECTIONS: \${POSTGRES_MAX_CONNECTIONS:-150}"
assert_contains "$ISKRA_STACK/docker-compose.yml" "REDIS_HOST: projectcea_redis"
assert_contains "$ISKRA_STACK/docker-compose.yml" "PGHOST: projectcea_database"
assert_contains "$ISKRA_STACK/docker-compose.yml" "SYNC_INTERVAL_SEC: 10"
assert_contains "$REPLICA_ENTRYPOINT" "primary_slot_name"
assert_contains "$REPLICA_ENTRYPOINT" "hot_standby_feedback=\${POSTGRES_HOT_STANDBY_FEEDBACK:-on}"
assert_contains "$ISKRA_STACK/scripts/redis_sync.py" "cea:sensor:global:main:"
assert_contains "$SERVICES" "name: monitoring-service"
assert_contains "$SERVICES" "http://127.0.0.1:8005/health"
assert_contains "$CADDYFILE" "reverse_proxy 127.0.0.1:8005"

for archive_name in humidity_alerts.json temperature_alerts.json water_level_alerts.json; do
  archive_path="$LEGACY_ARCHIVE/Infrastructure/frontend/grafana/alerting/alert-rules/$archive_name"
  [[ -f "$archive_path" ]] || fail "legacy JSON archive is missing $archive_name"
  python3 -m json.tool "$archive_path" >/dev/null
done
(cd "$LEGACY_ARCHIVE" && sha256sum --check SHA256SUMS >/dev/null)

if command -v docker >/dev/null 2>&1; then
  docker compose --env-file "$ISKRA_STACK/.env.example" -f "$ISKRA_STACK/docker-compose.yml" config >/dev/null
fi

printf 'iskra Grafana retirement configuration test passed\n'
