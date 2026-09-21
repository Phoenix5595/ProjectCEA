#!/usr/bin/env bash
set -euo pipefail

# Disposable-database harness for backend sensor-registry integration tests.
# Applies the sensor_registry migration to a fresh monitoring_test_ database,
# then runs pytest (integration markers included). Refuses production.

readonly SENSOR_REGISTRY_BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SENSOR_REGISTRY_INFRA_DIR="$(cd -- "$SENSOR_REGISTRY_BACKEND_DIR/.." && pwd)"
readonly SENSOR_REGISTRY_DB_TESTS="$SENSOR_REGISTRY_INFRA_DIR/database/tests"
readonly SENSOR_REGISTRY_MIGRATION_SQL="$SENSOR_REGISTRY_INFRA_DIR/database/migrate_sensor_registry.sql"

if [[ "${PGDATABASE:-}" == 'cea_sensors' ]]; then
  echo 'sensor registry backend harness: refusing production database name: cea_sensors' >&2
  exit 1
fi

cd -- "$SENSOR_REGISTRY_BACKEND_DIR"
source "$SENSOR_REGISTRY_DB_TESTS/test-monitoring-read-models.sh"

MONITORING_TEST_FIXTURE_SQL="$SENSOR_REGISTRY_DB_TESTS/fixtures/sensor_registry_fixture.sql" \
  with_monitoring_test_db bash -c '
    set -euo pipefail
    psql -X --set=ON_ERROR_STOP=1 --file="'"$SENSOR_REGISTRY_MIGRATION_SQL"'" "$MONITORING_TEST_DATABASE_URL" >/dev/null
    python3 -m pytest -q app/tests/sensor_registry
  '
