#!/usr/bin/env bash
# Regenerate Infrastructure/database/baseline/baseline.sql from production.
# Production (sj_cea on 5432) is only read by pg_dump; every write happens in a
# disposable monitoring_test_baseline_* database on the 15/test cluster.
# Usage: generate_schema_baseline.sh <release_path> [--keep-reference]
set -euo pipefail

baseline_fail() {
  printf 'schema baseline: %s\n' "$1" >&2
  return 1
}

KEEP_REFERENCE=0
if [[ "$#" -eq 2 && "$2" == '--keep-reference' ]]; then
  KEEP_REFERENCE=1
elif [[ "$#" -ne 1 ]]; then
  baseline_fail 'usage: generate_schema_baseline.sh <release_path> [--keep-reference]'
  exit 2
fi

RELEASE_PATH="$(cd -- "$1" && pwd)"
RUNNER="$RELEASE_PATH/Infrastructure/scripts/db_migrate.py"
BASELINE_DIR="$RELEASE_PATH/Infrastructure/database/baseline"
CLEAR_SQL="$BASELINE_DIR/clear_history.sql"
DEPLOY_MANIFEST="${BASELINE_DEPLOY_MANIFEST:-/opt/projectcea/current/deploy_manifest.json}"
for required_file in "$RUNNER" "$CLEAR_SQL" "$DEPLOY_MANIFEST"; do
  [[ -f "$required_file" ]] || {
    baseline_fail "required file not found: $required_file"
    exit 1
  }
done

SOURCE_DB='sj_cea'
DUMP_DIR="${REHEARSAL_DUMP_DIR:-/var/lib/projectcea/rehearsal}"
CEA_TEST_PGPORT="${CEA_TEST_PGPORT:-5433}"
CEA_TEST_PGHOST="${CEA_TEST_PGHOST:-127.0.0.1}"
POSTGRES_HOST="$CEA_TEST_PGHOST"
POSTGRES_PORT="$CEA_TEST_PGPORT"
POSTGRES_USER='cea_user'
export CEA_TEST_PGPORT CEA_TEST_PGHOST POSTGRES_HOST POSTGRES_PORT POSTGRES_USER

if [[ "${POSTGRES_PASSWORD+x}" != x && -r /opt/projectcea/shared/env/postgres.env ]]; then
  while IFS= read -r line; do
    if [[ "$line" == POSTGRES_PASSWORD=* ]]; then
      POSTGRES_PASSWORD="${line#POSTGRES_PASSWORD=}"
      if [[ "${#POSTGRES_PASSWORD}" -ge 2 ]]; then
        first_char="${POSTGRES_PASSWORD:0:1}"
        last_char="${POSTGRES_PASSWORD: -1}"
        if [[ "$first_char" == "$last_char" && ( "$first_char" == '"' || "$first_char" == "'" ) ]]; then
          POSTGRES_PASSWORD="${POSTGRES_PASSWORD:1:${#POSTGRES_PASSWORD}-2}"
        fi
      fi
      export POSTGRES_PASSWORD
      break
    fi
  done < /opt/projectcea/shared/env/postgres.env
fi
if [[ "${POSTGRES_PASSWORD+x}" == x ]]; then
  export PGPASSWORD="$POSTGRES_PASSWORD"
else
  unset PGPASSWORD || true
fi
export PGHOST="$CEA_TEST_PGHOST" PGPORT="$CEA_TEST_PGPORT" PGUSER="$POSTGRES_USER"

postgres_local() {
  sudo -u postgres env -i \
    "PATH=$PATH" \
    HOME=/var/lib/postgresql \
    PGHOST=/var/run/postgresql \
    "PGPORT=$CEA_TEST_PGPORT" \
    PGUSER=postgres \
    "$@"
}

assert_disposable_target() {
  local database_name="$1"
  local target_identity
  local -a target_settings

  [[ "$database_name" =~ ^monitoring_test_[a-z0-9_]+$ ]] || {
    baseline_fail "refusing unsafe disposable database name: $database_name"
    return 1
  }
  [[ "$CEA_TEST_PGPORT" =~ ^[0-9]+$ && "$CEA_TEST_PGPORT" != '5432' ]] || {
    baseline_fail "CEA_TEST_PGPORT must be a non-production port: $CEA_TEST_PGPORT"
    return 1
  }
  case "${CEA_TEST_PGHOST,,}" in
    127.0.0.1 | localhost) ;;
    *)
      baseline_fail "CEA_TEST_PGHOST must be a loopback TCP host: $CEA_TEST_PGHOST"
      return 1
      ;;
  esac
  target_identity="$(postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align \
    --dbname=postgres --command='SHOW data_directory; SHOW port')" || {
    baseline_fail "could not inspect PostgreSQL target on port $CEA_TEST_PGPORT"
    return 1
  }
  mapfile -t target_settings <<< "$target_identity"
  [[ "${#target_settings[@]}" -eq 2 && "${target_settings[0]}" == */15/test ]] || {
    baseline_fail "refusing PostgreSQL data directory outside cluster 15/test: ${target_settings[0]:-}"
    return 1
  }
  [[ "${target_settings[1]}" != '5432' && "${target_settings[1]}" == "$CEA_TEST_PGPORT" ]] || {
    baseline_fail "refusing PostgreSQL port ${target_settings[1]} (requested $CEA_TEST_PGPORT)"
    return 1
  }
}

TIMESTAMP="$(date +%s%N)"
DB_NAME="monitoring_test_baseline_${TIMESTAMP}"
DUMP_FILE="$DUMP_DIR/baseline-prod-${TIMESTAMP}.dump"
BASELINE_TMP="$BASELINE_DIR/baseline.sql.tmp.$$"
DB_CREATED=0
DUMP_CREATED=0

cleanup_resources() {
  local cleanup_status=0
  if (( DB_CREATED && ! KEEP_REFERENCE )); then
    if assert_disposable_target "$DB_NAME" && \
       postgres_local dropdb -p "$CEA_TEST_PGPORT" --if-exists --force "$DB_NAME"; then
      DB_CREATED=0
    else
      baseline_fail "could not drop disposable database: $DB_NAME"
      cleanup_status=1
    fi
  fi
  if (( DUMP_CREATED )); then
    rm -f -- "$DUMP_FILE" || cleanup_status=1
    DUMP_CREATED=0
  fi
  rm -f -- "$BASELINE_TMP" || cleanup_status=1
  return "$cleanup_status"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  if (( status != 0 )); then
    KEEP_REFERENCE=0
  fi
  cleanup_resources
  local cleanup_status=$?
  if (( status == 0 && cleanup_status != 0 )); then
    status=1
  fi
  exit "$status"
}
trap on_exit EXIT

for required_command in pg_dump pg_restore psql createdb dropdb sudo python3 sha256sum; do
  command -v "$required_command" >/dev/null 2>&1 || {
    baseline_fail "required command not found: $required_command"
    exit 1
  }
done

umask 077
mkdir -p -- "$DUMP_DIR"
assert_disposable_target "$DB_NAME"

DUMP_CREATED=1
pg_dump --format=custom --file="$DUMP_FILE" \
  --host=127.0.0.1 --port=5432 --username="$POSTGRES_USER" --dbname="$SOURCE_DB"

DB_CREATED=1
postgres_local createdb -p "$CEA_TEST_PGPORT" --owner="$POSTGRES_USER" "$DB_NAME"
postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$DB_NAME" \
  --command='CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();' >/dev/null
postgres_local pg_restore --host=/var/run/postgresql --port="$CEA_TEST_PGPORT" \
  --exit-on-error --dbname="$DB_NAME" < "$DUMP_FILE"
postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$DB_NAME" \
  --command='SELECT timescaledb_post_restore();' >/dev/null
rm -f -- "$DUMP_FILE"
DUMP_CREATED=0

POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" apply --allow-downtime

postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
  --dbname="$DB_NAME" < "$CLEAR_SQL" >/dev/null

umask 022
postgres_local pg_dump -p "$CEA_TEST_PGPORT" --format=plain --dbname="$DB_NAME" > "$BASELINE_TMP"
mv -- "$BASELINE_TMP" "$BASELINE_DIR/baseline.sql"

read_scalar() {
  postgres_local psql -p "$CEA_TEST_PGPORT" -X --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --dbname="$DB_NAME" --command="$1"
}
MIGRATION_HEAD="$(POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" head)"
ALEMBIC_HEADS="$(read_scalar 'SELECT version_num FROM public.alembic_version ORDER BY 1')"
BASELINE_SHA256="$(sha256sum "$BASELINE_DIR/baseline.sql" | cut -d' ' -f1)"
python3 - "$BASELINE_DIR/baseline_manifest.json" "$MIGRATION_HEAD" "$ALEMBIC_HEADS" \
  "$DEPLOY_MANIFEST" "$BASELINE_SHA256" <<'PY'
import datetime
import json
import sys

manifest_path, migration_head, alembic_heads, deploy_manifest, sha256 = sys.argv[1:]
with open(deploy_manifest, encoding="utf-8") as handle:
    source_release_id = json.load(handle)["release_id"]
manifest = {
    "migration_head": migration_head,
    "alembic_heads": [line for line in alembic_heads.splitlines() if line],
    "source_release_id": source_release_id,
    "generated_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sha256": sha256,
}
with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")
PY

printf 'schema baseline written at %s (%s)\n' "$MIGRATION_HEAD" "$BASELINE_SHA256"
if (( KEEP_REFERENCE )); then
  printf 'reference_database=%s\n' "$DB_NAME"
fi
