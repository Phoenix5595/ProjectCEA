#!/usr/bin/env bash
# Load the committed schema baseline into a new, empty database.
# Usage: bootstrap_fresh_db.sh <db_name> [--port N]   (default port 5432)
# Refuses (exit 2) when the database already has tables in public.
set -euo pipefail

bootstrap_fail() {
  printf 'fresh bootstrap: %s\n' "$1" >&2
}

usage() {
  bootstrap_fail 'usage: bootstrap_fresh_db.sh <db_name> [--port N]'
  exit 2
}

[[ "$#" -eq 1 || "$#" -eq 3 ]] || usage
DB_NAME="$1"
PORT=5432
if [[ "$#" -eq 3 ]]; then
  [[ "$2" == '--port' ]] || usage
  PORT="$3"
fi
[[ "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]] || {
  bootstrap_fail "invalid database name: $DB_NAME"
  exit 2
}
[[ "$PORT" =~ ^[0-9]+$ ]] || {
  bootstrap_fail "invalid port: $PORT"
  exit 2
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_DIR="$SCRIPT_DIR/../database/baseline"
BASELINE_SQL="$BASELINE_DIR/baseline.sql"
MANIFEST="$BASELINE_DIR/baseline_manifest.json"
[[ -f "$BASELINE_SQL" && -f "$MANIFEST" ]] || {
  bootstrap_fail "baseline files missing under $BASELINE_DIR"
  exit 1
}

EXPECTED_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$MANIFEST")"
MIGRATION_HEAD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["migration_head"])' "$MANIFEST")"
ACTUAL_SHA256="$(sha256sum "$BASELINE_SQL" | cut -d' ' -f1)"
[[ "$ACTUAL_SHA256" == "$EXPECTED_SHA256" ]] || {
  bootstrap_fail "baseline.sql sha256 $ACTUAL_SHA256 does not match manifest $EXPECTED_SHA256"
  exit 1
}

postgres_local() {
  sudo -u postgres env -i \
    "PATH=$PATH" \
    HOME=/var/lib/postgresql \
    PGHOST=/var/run/postgresql \
    "PGPORT=$PORT" \
    PGUSER=postgres \
    "$@"
}

psql_admin() {
  local database="$1"
  shift
  postgres_local psql -p "$PORT" -X --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
    --tuples-only --no-align --dbname="$database" "$@"
}

DB_EXISTS="$(psql_admin postgres --command="SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")"
if [[ -n "$DB_EXISTS" ]]; then
  TABLE_COUNT="$(psql_admin "$DB_NAME" --command="SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
  if [[ "$TABLE_COUNT" != '0' ]]; then
    bootstrap_fail "database $DB_NAME already has $TABLE_COUNT tables in public; refusing"
    exit 2
  fi
else
  postgres_local createdb -p "$PORT" --owner=cea_user "$DB_NAME"
fi

psql_admin "$DB_NAME" \
  --command='CREATE EXTENSION IF NOT EXISTS timescaledb' \
  --command='SELECT timescaledb_pre_restore()' >/dev/null
psql_admin "$DB_NAME" < "$BASELINE_SQL" >/dev/null
psql_admin "$DB_NAME" --command='SELECT timescaledb_post_restore()' >/dev/null

printf 'baseline loaded at %s; now run: python3 Infrastructure/scripts/db_migrate.py apply\n' "$MIGRATION_HEAD"
