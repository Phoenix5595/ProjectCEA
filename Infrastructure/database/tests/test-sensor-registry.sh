#!/usr/bin/env bash
set -euo pipefail

# Sensor registry migration harness.
#
# Proves on a disposable PostgreSQL database (never `cea_sensors`):
#   * migration idempotence (applied twice with ON_ERROR_STOP)
#   * unique (bus, hardware_address) and unique device_id
#   * CAN room-position uniqueness (partial unique index)
#   * the exactly-legal bus-specific assignment shapes
#   * the canonical Flower Room / Front Bed / Back Bed rows
#   * legacy CAN + RS-485 seed preservation
#   * deterministic overflow-to-unassigned for legacy bed capacity
#
# Reuses the guarded disposable-database harness from
# test-monitoring-read-models.sh (monitoring_test_ namespace).

readonly SENSOR_REGISTRY_SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SENSOR_REGISTRY_MIGRATION_SQL="$SENSOR_REGISTRY_SCRIPT_DIRECTORY/../migrate_sensor_registry.sql"
readonly SENSOR_REGISTRY_FIXTURE_SQL="$SENSOR_REGISTRY_SCRIPT_DIRECTORY/fixtures/sensor_registry_fixture.sql"
readonly SENSOR_REGISTRY_CASE_SQL="$SENSOR_REGISTRY_SCRIPT_DIRECTORY/cases/sensor-registry.sql"
readonly SENSOR_REGISTRY_MONITORING_HARNESS="$SENSOR_REGISTRY_SCRIPT_DIRECTORY/test-monitoring-read-models.sh"

fail() {
  printf 'sensor registry harness: %s\n' "$1" >&2
  return 1
}

reject_production_database() {
  if [[ "${PGDATABASE:-}" == 'cea_sensors' ]]; then
    fail 'refusing production database name: cea_sensors'
    return 1
  fi
}

run_internal_case() {
  [[ "${SENSOR_REGISTRY_HARNESS_INTERNAL:-}" == '1' ]] || {
    fail 'internal case runner requires the disposable harness'
    return 1
  }
  [[ -n "${MONITORING_TEST_DATABASE_URL:-}" ]] || {
    fail 'internal case runner requires a disposable database URL'
    return 1
  }
  local database_url="$MONITORING_TEST_DATABASE_URL"

  printf 'applying sensor_registry migration (pass 1)\n'
  psql -X --set=ON_ERROR_STOP=1 --file="$SENSOR_REGISTRY_MIGRATION_SQL" "$database_url" >/dev/null
  printf 'applying sensor_registry migration (pass 2, idempotence)\n'
  psql -X --set=ON_ERROR_STOP=1 --file="$SENSOR_REGISTRY_MIGRATION_SQL" "$database_url" >/dev/null

  printf 'running sensor registry assertions\n'
  psql -X --set=ON_ERROR_STOP=1 --file="$SENSOR_REGISTRY_CASE_SQL" "$database_url"
}

main() {
  reject_production_database
  [[ -f "$SENSOR_REGISTRY_MIGRATION_SQL" ]] || fail "migration SQL is missing: $SENSOR_REGISTRY_MIGRATION_SQL"
  [[ -f "$SENSOR_REGISTRY_FIXTURE_SQL" ]] || fail "fixture SQL is missing: $SENSOR_REGISTRY_FIXTURE_SQL"
  [[ -f "$SENSOR_REGISTRY_CASE_SQL" ]] || fail "case SQL is missing: $SENSOR_REGISTRY_CASE_SQL"
  if [[ "${1:-}" == '--internal' ]]; then
    run_internal_case
    return
  fi
  [[ "$#" -eq 0 ]] || {
    fail 'usage: test-sensor-registry.sh'
    return 2
  }
  source "$SENSOR_REGISTRY_MONITORING_HARNESS"
  MONITORING_TEST_FIXTURE_SQL="$SENSOR_REGISTRY_FIXTURE_SQL" \
    with_monitoring_test_db env SENSOR_REGISTRY_HARNESS_INTERNAL=1 \
    bash "$0" --internal
}

main "$@"
