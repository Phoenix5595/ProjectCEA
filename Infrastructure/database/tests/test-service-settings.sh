#!/usr/bin/env bash
set -euo pipefail

readonly TEST_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$TEST_DIRECTORY/../../.." && pwd)"
readonly MIGRATION="$REPO_ROOT/Infrastructure/database/migrations/0009_service_setting.sql"
readonly MIGRATOR="$REPO_ROOT/Infrastructure/scripts/db_migrate.py"
readonly PYTHON="$REPO_ROOT/Infrastructure/automation-service/.venv/bin/python"
readonly HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'
readonly DB_NAME="monitoring_test_service_settings_$$"
TMP_ROOT=''
DB_CREATED=0

fail() {
  printf 'service settings database proof: %s\n' "$1" >&2
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
  postgres_env \
    POSTGRES_HOST=/var/run/postgresql \
    POSTGRES_PORT=5432 \
    POSTGRES_DB="$DB_NAME" \
    POSTGRES_USER=postgres \
    POSTGRES_PASSWORD= \
    python3 "$MIGRATOR" --release "$TMP_ROOT/release" "$@"
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if (( DB_CREATED )); then
    if [[ "$DB_NAME" =~ ^monitoring_test_service_settings_[0-9]+$ ]]; then
      if ! psql_as_postgres --dbname=postgres \
        --command="DROP DATABASE IF EXISTS \"$DB_NAME\"" >/dev/null; then
        fail "could not drop disposable database: $DB_NAME"
        status=1
      else
        DB_CREATED=0
      fi
    else
      fail "refusing unsafe database cleanup: $DB_NAME"
      status=1
    fi
  fi
  if [[ -n "$TMP_ROOT" ]]; then
    sudo chown -R "$(id -un):$(id -gn)" "$TMP_ROOT" 2>/dev/null || true
    rm -rf -- "$TMP_ROOT" || status=1
  fi
  exit "$status"
}

[[ "$DB_NAME" =~ ^monitoring_test_service_settings_[0-9]+$ ]] || {
  fail "refusing unsafe disposable database name: $DB_NAME"
  exit 1
}
reject_inherited_database_environment
for required_command in flock sudo psql python3 mktemp cp; do
  command -v "$required_command" >/dev/null 2>&1 || {
    fail "required command not found: $required_command"
    exit 1
  }
done
[[ -f "$MIGRATION" && -f "$MIGRATOR" ]] || {
  fail 'migration source or runner not found'
  exit 1
}
[[ -x "$PYTHON" ]] || {
  fail "automation virtualenv Python not found: $PYTHON"
  exit 1
}

exec {lock_fd}> "$HARNESS_LOCK" || {
  fail "could not open shared database harness lock: $HARNESS_LOCK"
  exit 1
}
flock -x "$lock_fd" || {
  fail "could not acquire shared database harness lock: $HARNESS_LOCK"
  exit 1
}

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/projectcea-service-settings.XXXXXX")"
chmod 755 "$TMP_ROOT"
mkdir -p \
  "$TMP_ROOT/release/Infrastructure/database/migrations" \
  "$TMP_ROOT/release/Infrastructure/automation-service" \
  "$TMP_ROOT/cache"
chmod 755 "$TMP_ROOT/release" \
  "$TMP_ROOT/release/Infrastructure" \
  "$TMP_ROOT/release/Infrastructure/database" \
  "$TMP_ROOT/release/Infrastructure/database/migrations" \
  "$TMP_ROOT/release/Infrastructure/automation-service"
sudo chown postgres:postgres "$TMP_ROOT/cache"
cp "$MIGRATION" "$TMP_ROOT/release/Infrastructure/database/migrations/"
trap cleanup EXIT

if [[ "$(psql_as_postgres --dbname=postgres --tuples-only --no-align \
  --command="SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" != '' ]]; then
  fail "disposable database already exists: $DB_NAME"
  exit 1
fi
DB_CREATED=1
psql_as_postgres --dbname=postgres \
  --command="CREATE DATABASE \"$DB_NAME\" OWNER postgres" >/dev/null

printf 'applying migration 0009 to %s\n' "$DB_NAME"
run_migrator apply --skip-alembic
printf 'applying migration 0009 again to prove ledger idempotence\n'
run_migrator apply --skip-alembic

seeded_count="$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command='SELECT count(*) FROM public.service_setting')"
[[ "$seeded_count" == '14' ]] || fail "expected 14 seeded settings, got $seeded_count"
ledger_count="$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command="SELECT count(*) FROM public.schema_migration WHERE version = '0009_service_setting'")"
[[ "$ledger_count" == '1' ]] || fail "expected one 0009 ledger row, got $ledger_count"
seed_mismatch_count="$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align <<'SQL'
WITH expected(service, key, value) AS (
    VALUES
        ('automation', 'update_interval', '1'::jsonb),
        ('automation', 'last_good_hold_period', '30'::jsonb),
        ('automation', 'binary_hysteresis', '0.1'::jsonb),
        ('automation', 'pid_defaults', '{"heater":{"kp":25,"ki":0.02,"kd":0},"fan":{"kp":25,"ki":0.02,"kd":0},"co2":{"kp":10,"ki":0.01,"kd":0},"default":{"kp":10,"ki":0.01,"kd":0}}'::jsonb),
        ('automation', 'pid_limits', '{"heater":{"kp_min":0,"kp_max":100,"ki_min":0,"ki_max":1,"kd_min":0,"kd_max":10},"fan":{"kp_min":0,"kp_max":100,"ki_min":0,"ki_max":1,"kd_min":0,"kd_max":10},"co2":{"kp_min":0,"kp_max":50,"ki_min":0,"ki_max":0.5,"kd_min":0,"kd_max":5}}'::jsonb),
        ('automation', 'safety_limits', '{"max_temperature":35,"min_temperature":10,"max_humidity":90,"min_humidity":30,"max_co2":2000,"min_co2":400}'::jsonb),
        ('soil', 'poll_interval_seconds', '5'::jsonb),
        ('soil', 'discovery_interval_seconds', '30'::jsonb),
        ('onewire', 'poll_interval_seconds', '1'::jsonb),
        ('weather', 'station_icao', '"CYQB"'::jsonb),
        ('weather', 'poll_interval_seconds', '900'::jsonb),
        ('weather', 'api_url', '"https://aviationweather.gov/api/data/metar"'::jsonb),
        ('weather', 'room_name', '"Outside"'::jsonb),
        ('weather', 'device_name', '"Weather Station Québec"'::jsonb)
)
SELECT count(*)
FROM expected
LEFT JOIN public.service_setting USING (service, key)
WHERE service_setting.value IS DISTINCT FROM expected.value
   OR service_setting.updated_by IS DISTINCT FROM 'migration-0009';
SQL
)"
[[ "$seed_mismatch_count" == '0' ]] || fail "seeded settings mismatch count: $seed_mismatch_count"

printf 'loading and round-tripping seeded settings through ServiceSettings\n'
postgres_env \
  POSTGRES_HOST=/var/run/postgresql \
  POSTGRES_PORT=5432 \
  POSTGRES_DB="$DB_NAME" \
  POSTGRES_USER=postgres \
  SETTINGS_CACHE_PATH="$TMP_ROOT/cache/automation.json" \
  PYTHONPATH="$REPO_ROOT/Infrastructure" \
  "$PYTHON" - <<'PY'
import asyncio
import json
import os
from pathlib import Path

import asyncpg
from shared.service_settings import ServiceSettings


async def main() -> None:
    pool = await asyncpg.create_pool(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
    )
    cache_path = Path(os.environ["SETTINGS_CACHE_PATH"])
    try:
        settings = await ServiceSettings.load(
            pool,
            "automation",
            cache_path=cache_path,
            fallback={"update_interval": 5},
        )
        assert settings["update_interval"] == 1, settings
        assert settings["last_good_hold_period"] == 30, settings
        assert settings["binary_hysteresis"] == 0.1, settings
        assert settings["pid_defaults"]["heater"] == {"kp": 25, "ki": 0.02, "kd": 0}, settings

        await pool.execute(
            "UPDATE public.service_setting SET value = $1::jsonb WHERE service = 'automation' AND key = 'update_interval'",
            json.dumps(3),
        )
        updated = await ServiceSettings.load(pool, "automation", cache_path=cache_path)
        assert updated["update_interval"] == 3, updated
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached["update_interval"] == 3, cached
        print("service settings round-trip passed: DB values override fallback and refresh cache")
    finally:
        await pool.close()


asyncio.run(main())
PY
