#!/usr/bin/env bash
set -euo pipefail

rehearse_fail() {
  printf 'migration rehearsal: %s\n' "$1" >&2
  return 1
}

if [[ "$#" -ne 1 ]]; then
  rehearse_fail 'usage: rehearse_migrations.sh <release_path>'
  exit 2
fi

RELEASE_PATH="$(cd -- "$1" && pwd)"
RUNNER="$RELEASE_PATH/Infrastructure/scripts/db_migrate.py"
if [[ ! -f "$RUNNER" ]]; then
  rehearse_fail "migration runner not found: $RUNNER"
  exit 1
fi

SOURCE_DB="${REHEARSAL_SOURCE_DB:-sj_cea}"
DUMP_DIR="${REHEARSAL_DUMP_DIR:-/var/lib/projectcea/rehearsal}"
RESULT_DIR="${REHEARSAL_RESULT_DIR:-/var/lib/projectcea/migration-rehearsals}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-cea_user}"
POSTGRES_DB="$SOURCE_DB"
export POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_DB

if [[ "${POSTGRES_PASSWORD+x}" != x ]]; then
  if [[ -r /opt/projectcea/shared/env/postgres.env ]]; then
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
fi

if [[ "${POSTGRES_PASSWORD+x}" == x ]]; then
  export PGPASSWORD="$POSTGRES_PASSWORD"
else
  unset PGPASSWORD || true
fi

export PGHOST="$POSTGRES_HOST"
export PGPORT="$POSTGRES_PORT"
export PGUSER="$POSTGRES_USER"

postgres_local() {
  sudo -u postgres env -i \
    "PATH=$PATH" \
    HOME=/var/lib/postgresql \
    "PGPORT=$POSTGRES_PORT" \
    "$@"
}

TIMESTAMP="$(date +%s%N)"
DB_NAME="monitoring_test_rehearsal_${TIMESTAMP}"
if [[ ! "$DB_NAME" =~ ^monitoring_test_rehearsal_[0-9]+$ ]]; then
  rehearse_fail "refusing unsafe disposable database name: $DB_NAME"
  exit 1
fi

DUMP_FILE="$DUMP_DIR/prod-${TIMESTAMP}.dump"
RESULT_FILE=''
RESULT_TMP=''
DB_CREATED=0
DUMP_CREATED=0

elapsed_seconds() {
  local elapsed_ns=$(( $(date +%s%N) - $1 ))
  printf '%d.%03d' "$((elapsed_ns / 1000000000))" "$(((elapsed_ns % 1000000000) / 1000000))"
}

cleanup_resources() {
  local cleanup_status=0
  if (( DB_CREATED )); then
    if [[ "$DB_NAME" =~ ^monitoring_test_rehearsal_[0-9]+$ ]]; then
      if ! postgres_local dropdb --if-exists --force "$DB_NAME"; then
        rehearse_fail "could not drop disposable database: $DB_NAME"
        cleanup_status=1
      else
        DB_CREATED=0
      fi
    else
      rehearse_fail "refusing unsafe disposable database cleanup: $DB_NAME"
      cleanup_status=1
    fi
  fi
  if (( DUMP_CREATED )); then
    if ! rm -f -- "$DUMP_FILE"; then
      rehearse_fail "could not remove dump: $DUMP_FILE"
      cleanup_status=1
    else
      DUMP_CREATED=0
    fi
  fi
  if [[ -n "$RESULT_TMP" ]]; then
    rm -f -- "$RESULT_TMP" || cleanup_status=1
    RESULT_TMP=''
  fi
  return "$cleanup_status"
}

on_exit() {
  local status=$?
  trap - EXIT
  set +e
  cleanup_resources
  local cleanup_status=$?
  if (( status == 0 && cleanup_status != 0 )); then
    status=1
  fi
  exit "$status"
}
trap on_exit EXIT

for required_command in pg_dump pg_restore psql createdb dropdb sudo python3; do
  command -v "$required_command" >/dev/null 2>&1 || {
    rehearse_fail "required command not found: $required_command"
    exit 1
  }
done

umask 077
mkdir -p -- "$DUMP_DIR" "$RESULT_DIR"
if [[ -e "$DUMP_FILE" ]]; then
  rehearse_fail "dump path already exists: $DUMP_FILE"
  exit 1
fi

DB_EXISTS="$(postgres_local psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
  --dbname=postgres --command="SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")"
if [[ -n "$DB_EXISTS" ]]; then
  rehearse_fail "disposable database already exists: $DB_NAME"
  exit 1
fi

DURATIONS=()
STEP_START="$(date +%s%N)"
DUMP_CREATED=1
pg_dump --format=custom --file="$DUMP_FILE" --dbname="$SOURCE_DB"
DURATIONS+=("dump=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
DB_CREATED=1
postgres_local createdb --owner="$POSTGRES_USER" "$DB_NAME"
DURATIONS+=("create_database=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
postgres_local psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$DB_NAME" \
  --command='CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();'
DURATIONS+=("pre_restore=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
postgres_local pg_restore --exit-on-error --dbname="$DB_NAME" "$DUMP_FILE"
DURATIONS+=("restore=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
postgres_local psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$DB_NAME" \
  --command='SELECT timescaledb_post_restore();'
DURATIONS+=("post_restore=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
PRE_HEAD="$(POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" head)"
DURATIONS+=("read_pre_head=$(elapsed_seconds "$STEP_START")")
if [[ "$PRE_HEAD" == 'null' ]]; then
  DOWN_TARGET='none'
else
  DOWN_TARGET="$PRE_HEAD"
fi

STEP_START="$(date +%s%N)"
PLAN_JSON="$(POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" plan --json)"
SET_SHA256="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["set_sha256"])' <<< "$PLAN_JSON")"
DURATIONS+=("plan=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" apply --allow-downtime
DURATIONS+=("apply_first=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" down --to "$DOWN_TARGET"
DURATIONS+=("down=$(elapsed_seconds "$STEP_START")")

STEP_START="$(date +%s%N)"
POSTGRES_DB="$DB_NAME" python3 "$RUNNER" --release "$RELEASE_PATH" apply --allow-downtime
DURATIONS+=("apply_second=$(elapsed_seconds "$STEP_START")")

cleanup_resources
RESULT_FILE="$RESULT_DIR/$SET_SHA256.json"
RESULT_TMP="$RESULT_FILE.tmp.$$"
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$RESULT_FILE" "$RESULT_TMP" "$SET_SHA256" "$FINISHED_AT" "${DURATIONS[@]}" <<'PY'
import json
import os
import sys

result_path, temporary_path, set_sha256, finished_at, *duration_entries = sys.argv[1:]
durations = {
    key: float(value)
    for key, value in (entry.split("=", 1) for entry in duration_entries)
}
with open(temporary_path, "w", encoding="utf-8") as result_file:
    json.dump(
        {
            "set_sha256": set_sha256,
            "result": "pass",
            "durations": durations,
            "finished_at": finished_at,
        },
        result_file,
        separators=(",", ":"),
    )
    result_file.write("\n")
os.chmod(temporary_path, 0o644)
os.replace(temporary_path, result_path)
PY
RESULT_TMP=''
printf 'migration rehearsal passed: %s\n' "$SET_SHA256"
