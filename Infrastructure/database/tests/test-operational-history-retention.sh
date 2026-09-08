#!/usr/bin/env bash
set -euo pipefail

readonly OPERATIONAL_RETENTION_SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly OPERATIONAL_RETENTION_MONITORING_HARNESS="$OPERATIONAL_RETENTION_SCRIPT_DIRECTORY/test-monitoring-read-models.sh"
readonly OPERATIONAL_RETENTION_CASE_SQL="$OPERATIONAL_RETENTION_SCRIPT_DIRECTORY/cases/operational-history-retention.sql"
readonly OPERATIONAL_RETENTION_POLICY_SQL="$OPERATIONAL_RETENTION_SCRIPT_DIRECTORY/../operational_history_retention.sql"

fail() {
  printf 'operational retention harness: %s\n' "$1" >&2
  return 1
}

reject_production_database() {
  if [[ "${PGDATABASE:-}" == 'cea_sensors' ]]; then
    fail 'refusing production database name: cea_sensors'
    return 1
  fi
}

reject_sensor_retention_targets() {
  local policy_sql

  policy_sql="$(<"$OPERATIONAL_RETENTION_POLICY_SQL")"
  [[ "$policy_sql" != *"add_retention_policy('measurement'"* ]] || {
    fail 'retention policy must not target measurement'
    return 1
  }
  [[ "$policy_sql" != *"add_retention_policy('sensor'"* ]] || {
    fail 'retention policy must not target sensor'
    return 1
  }
  [[ "$policy_sql" != *"add_continuous_aggregate_policy('monitoring_measurement"* ]] || {
    fail 'retention policy must not refresh monitoring measurement aggregates'
    return 1
  }
  [[ "$policy_sql" != *"add_continuous_aggregate_policy('monitoring_sensor"* ]] || {
    fail 'retention policy must not refresh monitoring sensor aggregates'
    return 1
  }
}

run_case() {
  [[ "${OPERATIONAL_RETENTION_HARNESS_INTERNAL:-}" == '1' ]] || {
    fail 'internal case runner requires the disposable harness'
    return 1
  }
  [[ -n "${MONITORING_TEST_DATABASE_URL:-}" ]] || {
    fail 'internal case runner requires a disposable database URL'
    return 1
  }
  psql -X --set=ON_ERROR_STOP=1 --file="$OPERATIONAL_RETENTION_CASE_SQL" "$MONITORING_TEST_DATABASE_URL"
}

main() {
  reject_production_database
  [[ -f "$OPERATIONAL_RETENTION_CASE_SQL" ]] || fail "case SQL is missing: $OPERATIONAL_RETENTION_CASE_SQL"
  [[ -f "$OPERATIONAL_RETENTION_POLICY_SQL" ]] || fail "policy SQL is missing: $OPERATIONAL_RETENTION_POLICY_SQL"
  reject_sensor_retention_targets
  if [[ "${1:-}" == '--internal' ]]; then
    run_case
    return
  fi
  [[ "$#" -eq 0 ]] || {
    fail 'usage: test-operational-history-retention.sh'
    return 2
  }
  source "$OPERATIONAL_RETENTION_MONITORING_HARNESS"
  with_monitoring_test_db env OPERATIONAL_RETENTION_HARNESS_INTERNAL=1 \
    bash "$0" --internal
}

main "$@"
