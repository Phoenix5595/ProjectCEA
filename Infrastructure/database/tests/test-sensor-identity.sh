#!/usr/bin/env bash
set -euo pipefail

CEA_TEST_PGPORT=5433
export CEA_TEST_PGPORT
readonly IDENTITY_TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly IDENTITY_INFRA_DIR="$(cd -- "$IDENTITY_TEST_DIR/../.." && pwd)"
readonly IDENTITY_FIXTURE="$IDENTITY_TEST_DIR/fixtures/sensor_registry_fixture.sql"

original_name="${PGDATABASE:-}"
lowered_name="${original_name,,}"
if [[ "$lowered_name" == *cea_sensors* || "$lowered_name" == *sj_cea* ]]; then
  echo 'sensor identity harness: refusing protected database name' >&2
  exit 1
fi

# DB harnesses share this lock so a disposable proof cannot overlap another
# migration or lifecycle proof on the machine.
exec 9>/tmp/grow-lifecycle-harness.lock
flock -x 9

source "$IDENTITY_TEST_DIR/test-monitoring-read-models.sh"

MONITORING_TEST_FIXTURE_SQL="$IDENTITY_FIXTURE" \
  with_monitoring_test_db env \
  IDENTITY_INFRA_DIR="$IDENTITY_INFRA_DIR" \
  IDENTITY_TEST_DIR="$IDENTITY_TEST_DIR" \
  bash -c '
    set -euo pipefail
    psql -X --set=ON_ERROR_STOP=1 --file="$IDENTITY_INFRA_DIR/database/migrate_sensor_registry.sql" "$MONITORING_TEST_DATABASE_URL" >/dev/null
    psql -X --set=ON_ERROR_STOP=1 --file="$IDENTITY_INFRA_DIR/database/migrations/0010_sensor_identity.sql" "$MONITORING_TEST_DATABASE_URL" >/dev/null
    psql -X --set=ON_ERROR_STOP=1 --command="CREATE TABLE public.archive_availability (source_table TEXT NOT NULL, start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL, source_rows_removed BOOLEAN NOT NULL)" "$MONITORING_TEST_DATABASE_URL" >/dev/null
    cd "$IDENTITY_INFRA_DIR/backend"
    PYTHONPATH="$IDENTITY_INFRA_DIR/backend:$IDENTITY_INFRA_DIR/soil-sensor-service:$IDENTITY_INFRA_DIR/can-processor-service:$IDENTITY_INFRA_DIR" \
      python3 -m pytest -q \
      "$IDENTITY_TEST_DIR/test_sensor_identity_db.py" \
      "$IDENTITY_TEST_DIR/test_sensor_identity_concurrency.py"
    psql -X --set=ON_ERROR_STOP=1 \
      --file="$IDENTITY_INFRA_DIR/database/migrations/0010_sensor_identity.down.sql" \
      "$MONITORING_TEST_DATABASE_URL" >/dev/null
    psql -X --set=ON_ERROR_STOP=1 \
      --file="$IDENTITY_TEST_DIR/sensor_identity_down_proof.sql" \
      "$MONITORING_TEST_DATABASE_URL" >/dev/null
'
