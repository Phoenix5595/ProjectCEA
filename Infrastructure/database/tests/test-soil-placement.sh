#!/usr/bin/env bash
# Guarded R9 soil placement proof on disposable databases (15/test only).
#
# Database A (baseline + 0013): backfill, down/up of 0013, then the soil
# service and backend placement SQL (test_soil_placement_db.py).
# Database B (baseline + 0013): the guarded clear script aborts on a
# measurement, clears exactly the target rows, and aborts on a second run.
set -euo pipefail

soil_fail() {
  printf 'soil placement test: %s\n' "$1" >&2
}

WORKTREE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
INFRA="$WORKTREE/Infrastructure"
SCRIPTS="$INFRA/scripts"
CLEAR_SQL="$SCRIPTS/clear_soil_sensors_2026-10.sql"
BPY="${BPY:-/home/antoine/ProjectCEA/Infrastructure/backend/.venv/bin/python}"
CEA_TEST_PGPORT="${CEA_TEST_PGPORT:-5433}"
export CEA_TEST_PGPORT
[[ "$CEA_TEST_PGPORT" =~ ^[0-9]+$ && "$CEA_TEST_PGPORT" != '5432' ]] || {
  soil_fail "CEA_TEST_PGPORT must be a non-production port: $CEA_TEST_PGPORT"
  exit 2
}
lowered_name="${PGDATABASE:-}"
lowered_name="${lowered_name,,}"
if [[ "$lowered_name" == *cea_sensors* || "$lowered_name" == *sj_cea* ]]; then
  soil_fail 'refusing protected database name'
  exit 2
fi

exec 9>/tmp/grow-lifecycle-harness.lock
flock -x 9

postgres_local() {
  sudo -u postgres env -i "PATH=$PATH" HOME=/var/lib/postgresql \
    PGHOST=/var/run/postgresql "PGPORT=$CEA_TEST_PGPORT" PGUSER=postgres "$@"
}

DATA_DIRECTORY="$(postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --tuples-only --no-align \
  --dbname=postgres --command='SHOW data_directory')"
[[ "$DATA_DIRECTORY" == */15/test ]] || {
  soil_fail "refusing cluster outside 15/test: $DATA_DIRECTORY"
  exit 2
}

if [[ -z "${PGPASSWORD:-}" ]]; then
  PGPASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' /opt/projectcea/shared/env/postgres.env | head -n1)"
  PGPASSWORD="${PGPASSWORD%\"}"; PGPASSWORD="${PGPASSWORD#\"}"
  PGPASSWORD="${PGPASSWORD%\'}"; PGPASSWORD="${PGPASSWORD#\'}"
fi
export PGPASSWORD

STAMP="$(date +%s%N)"
DB_A="monitoring_test_soil_${STAMP}"
DB_B="monitoring_test_soilclear_${STAMP}"

drop_disposable() {
  local name="$1"
  [[ -n "$name" && "$name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || return 0
  postgres_local dropdb -p "$CEA_TEST_PGPORT" --if-exists --force "$name"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  drop_disposable "$DB_A"
  drop_disposable "$DB_B"
  exit "$status"
}
trap on_exit EXIT

query() {
  local database="$1"
  shift
  psql -h 127.0.0.1 -p "$CEA_TEST_PGPORT" -U cea_user -d "$database" -X --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align "$@"
}

migrate() {
  local database="$1"
  shift
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="$CEA_TEST_PGPORT" POSTGRES_DB="$database" \
    POSTGRES_PASSWORD="$PGPASSWORD" python3 "$SCRIPTS/db_migrate.py" --release "$WORKTREE" "$@"
}

expect_eq() {
  local label="$1" actual="$2" expected="$3"
  [[ "$actual" == "$expected" ]] || {
    soil_fail "$label: expected '$expected', got '$actual'"
    exit 1
  }
  printf '%s: ok\n' "$label"
}

LEGAL_STATE_SQL="SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'sensor_registry_legal_state'"

# --- Database A -------------------------------------------------------------
bash "$SCRIPTS/bootstrap_fresh_db.sh" "$DB_A" --port "$CEA_TEST_PGPORT"
OLD_CHECK="$(query "$DB_A" --command="$LEGAL_STATE_SQL")"
migrate "$DB_A" apply --skip-alembic

expect_eq 'backfill: open placements for registry 4/5 at 0.5/0.5' \
  "$(query "$DB_A" --command="SELECT string_agg(registry_id || ':' || x_frac || ':' || y_frac, ',' ORDER BY registry_id) FROM soil_probe_placement WHERE removed_at IS NULL")" \
  '4:0.500:0.500,5:0.500:0.500'
expect_eq 'backfill: placement racks mirror the registry' \
  "$(query "$DB_A" --command="SELECT count(*) FROM soil_probe_placement p JOIN sensor_registry sr USING (registry_id) WHERE p.rack_id = sr.rack_id")" \
  '2'

migrate "$DB_A" down --to 0012_mode_transition_history
expect_eq 'down: placement table dropped' \
  "$(query "$DB_A" --command="SELECT to_regclass('public.soil_probe_placement') IS NULL")" 't'
expect_eq 'down: bed dimension columns dropped' \
  "$(query "$DB_A" --command="SELECT count(*) FROM information_schema.columns WHERE table_name = 'soil_bed_profile' AND column_name IN ('width_ft', 'length_ft')")" '0'
expect_eq 'down: original legal-state check restored' \
  "$(query "$DB_A" --command="$LEGAL_STATE_SQL")" "$OLD_CHECK"

migrate "$DB_A" apply --skip-alembic

ENCODED_PASSWORD="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$PGPASSWORD")"
(
  cd "$INFRA/backend"
  MONITORING_TEST_DATABASE_URL="postgresql://cea_user:${ENCODED_PASSWORD}@127.0.0.1:${CEA_TEST_PGPORT}/${DB_A}" \
  PYTHONPATH="$INFRA/backend:$INFRA/soil-sensor-service:$INFRA" REDIS_URL=redis://127.0.0.1:1 \
  PYTHONDONTWRITEBYTECODE=1 \
    "$BPY" -m pytest -q -p no:cacheprovider "$INFRA/database/tests/test_soil_placement_db.py"
)

# --- Database B: guarded clear script ---------------------------------------
bash "$SCRIPTS/bootstrap_fresh_db.sh" "$DB_B" --port "$CEA_TEST_PGPORT"
migrate "$DB_B" apply --skip-alembic

TARGET_COUNTS_SQL="SELECT (SELECT count(*) FROM sensor_registry WHERE registry_id IN (4, 5)) || ',' ||
  (SELECT count(*) FROM sensor_address_history WHERE registry_id IN (4, 5)) || ',' ||
  (SELECT count(*) FROM soil_probe_placement WHERE registry_id IN (4, 5)) || ',' ||
  (SELECT count(*) FROM device WHERE device_id IN (3, 6)) || ',' ||
  (SELECT count(*) FROM sensor WHERE device_id IN (3, 6))"
expect_eq 'clear: target rows before' "$(query "$DB_B" --command="$TARGET_COUNTS_SQL")" '2,2,2,2,12'
OTHER_COUNTS_SQL="SELECT (SELECT count(*) FROM sensor_registry WHERE registry_id NOT IN (4, 5)) || ',' ||
  (SELECT count(*) FROM device WHERE device_id NOT IN (3, 6)) || ',' ||
  (SELECT count(*) FROM sensor WHERE device_id NOT IN (3, 6))"
OTHER_BEFORE="$(query "$DB_B" --command="$OTHER_COUNTS_SQL")"

query "$DB_B" --command="INSERT INTO measurement (time, sensor_id, value) VALUES (now(), 14, 1.0)" >/dev/null
if query "$DB_B" --file="$CLEAR_SQL" >/dev/null 2>&1; then
  soil_fail 'clear script ran despite a measurement on sensor 14'
  exit 1
fi
expect_eq 'clear: measurement guard aborts with all rows intact' \
  "$(query "$DB_B" --command="$TARGET_COUNTS_SQL")" '2,2,2,2,12'

query "$DB_B" --command="DELETE FROM measurement WHERE sensor_id = 14" >/dev/null
query "$DB_B" --file="$CLEAR_SQL"
expect_eq 'clear: target rows removed' "$(query "$DB_B" --command="$TARGET_COUNTS_SQL")" '0,0,0,0,0'
expect_eq 'clear: other rows untouched' "$(query "$DB_B" --command="$OTHER_COUNTS_SQL")" "$OTHER_BEFORE"

if query "$DB_B" --file="$CLEAR_SQL" >/dev/null 2>&1; then
  soil_fail 'clear script succeeded on a second run'
  exit 1
fi
printf 'clear: second run aborts: ok\n'
printf 'soil placement test: all checks passed\n'
