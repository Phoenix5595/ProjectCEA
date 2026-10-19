#!/usr/bin/env bash
set -euo pipefail

HARNESS_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MIGRATOR="$HARNESS_DIRECTORY/../db_migrate.py"
HARNESS_LOCK='/tmp/grow-lifecycle-harness.lock'
DB_NAME="monitoring_test_dbmigrate_$$"
TMP_ROOT=''
DB_CREATED=0

fail() {
  printf 'db migration proof: %s\n' "$1" >&2
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
    DB_MIGRATE_ALEMBIC="$TMP_ROOT/alembic-stub" \
    python3 "$MIGRATOR" --release "$TMP_ROOT/release" "$@"
}

assert_equal() {
  local actual="$1" expected="$2" description="$3"
  if [[ "$actual" != "$expected" ]]; then
    fail "$description (expected '$expected', got '$actual')"
    return 1
  fi
}

assert_migration_error() {
  local expected_code="$1" command_output="$2" actual_status="$3"
  assert_equal "$actual_status" '4' "integrity command exit status"
  if [[ "$command_output" != *"$expected_code"* ]]; then
    fail "expected error '$expected_code', got: $command_output"
    return 1
  fi
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if (( DB_CREATED )); then
    if [[ "$DB_NAME" =~ ^monitoring_test_dbmigrate_[0-9]+$ ]]; then
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
    rm -rf -- "$TMP_ROOT" || status=1
  fi
  exit "$status"
}

if [[ ! "$DB_NAME" =~ ^monitoring_test_dbmigrate_[0-9]+$ ]]; then
  fail "refusing unsafe disposable database name: $DB_NAME"
  exit 1
fi
reject_inherited_database_environment
for required_command in flock sudo psql python3 mktemp; do
  command -v "$required_command" >/dev/null 2>&1 || {
    fail "required command not found: $required_command"
    exit 1
  }
done
[[ -f "$MIGRATOR" ]] || {
  fail "migration runner not found: $MIGRATOR"
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

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/projectcea-db-migrate.XXXXXX")"
trap cleanup EXIT
mkdir -p \
  "$TMP_ROOT/release/Infrastructure/database/migrations" \
  "$TMP_ROOT/release/Infrastructure/automation-service"
chmod 755 "$TMP_ROOT" "$TMP_ROOT/release" \
  "$TMP_ROOT/release/Infrastructure" \
  "$TMP_ROOT/release/Infrastructure/database" \
  "$TMP_ROOT/release/Infrastructure/database/migrations" \
  "$TMP_ROOT/release/Infrastructure/automation-service"

if [[ "$(psql_as_postgres --dbname=postgres --tuples-only --no-align \
  --command="SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" != '' ]]; then
  fail "disposable database already exists: $DB_NAME"
  exit 1
fi

CEA_USER_EXISTS="$(psql_as_postgres --dbname=postgres --tuples-only --no-align \
  --command="SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cea_user')")"
if [[ "$CEA_USER_EXISTS" == 't' ]]; then
  DATABASE_OWNER='cea_user'
else
  DATABASE_OWNER='postgres'
  printf 'local database owner choice: postgres (cea_user role unavailable)\n'
fi
DB_CREATED=1
psql_as_postgres --dbname=postgres \
  --command="CREATE DATABASE \"$DB_NAME\" OWNER \"$DATABASE_OWNER\"" >/dev/null
printf 'local database auth: sudo -u postgres over the local Unix socket (peer)\n'

cat > "$TMP_ROOT/release/Infrastructure/database/migrations/0001_alembic_stamp_fixture.sql" <<'SQL'
-- migration: downtime=false
CREATE TABLE public.dbmigrate_order (
    seq BIGSERIAL PRIMARY KEY,
    event TEXT NOT NULL
);
INSERT INTO public.dbmigrate_order (event) VALUES ('stamp');
CREATE TABLE public.dbmigrate_base (id INTEGER PRIMARY KEY);
SQL

cat > "$TMP_ROOT/release/Infrastructure/database/migrations/0002_downtime_fixture.sql" <<'SQL'
-- migration: downtime=true
CREATE TABLE public.dbmigrate_later (id INTEGER PRIMARY KEY);
INSERT INTO public.dbmigrate_order (event) VALUES ('post');
SQL

cat > "$TMP_ROOT/release/Infrastructure/database/migrations/0002_downtime_fixture.down.sql" <<'SQL'
DELETE FROM public.dbmigrate_order WHERE event = 'post';
DROP TABLE public.dbmigrate_later;
SQL

cat > "$TMP_ROOT/alembic-stub" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" -ne 4 || "$1" != '-c' || "$2" != */alembic.ini || "$3" != 'upgrade' || "$4" != 'heads' ]]; then
  printf 'unexpected Alembic arguments: %s\n' "$*" >&2
  exit 1
fi
psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$POSTGRES_DB" \
  --command="INSERT INTO public.dbmigrate_order (event) VALUES ('alembic')" >/dev/null
SH
chmod 755 "$TMP_ROOT/alembic-stub"

PLAN_JSON="$(run_migrator plan --json)"
python3 -c '
import json, sys
plan = json.loads(sys.argv[1])
assert [row["version"] for row in plan["pending"]] == [
    "0001_alembic_stamp_fixture", "0002_downtime_fixture"
], plan
assert [row["downtime"] for row in plan["pending"]] == [False, True], plan
assert plan["requires_downtime"] is True, plan
assert plan["head"] is None, plan
' "$PLAN_JSON" || {
  fail "plan did not report both pending migrations and downtime: $PLAN_JSON"
  exit 1
}
printf 'PASS plan reports both pending migrations and downtime\n'

if APPLY_OUTPUT="$(run_migrator apply 2>&1)"; then
  fail 'apply without --allow-downtime unexpectedly succeeded'
  exit 1
else
  APPLY_STATUS=$?
fi
assert_equal "$APPLY_STATUS" '3' 'downtime refusal exit status'
if [[ "$APPLY_OUTPUT" != *'migration_requires_downtime:0002_downtime_fixture'* ]]; then
  fail "downtime refusal did not identify migration: $APPLY_OUTPUT"
  exit 1
fi
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command="SELECT to_regclass('public.dbmigrate_order') IS NULL")" \
't' 'refusal leaves all pending migrations unapplied'
printf 'PASS downtime refusal applies none of the pending set\n'

APPLY_JSON="$(run_migrator apply --allow-downtime --json)"
python3 -c '
import json, sys
result = json.loads(sys.argv[1])
assert result["applied"] == [
    "0001_alembic_stamp_fixture", "0002_downtime_fixture"
], result
assert result["head"] == "0002_downtime_fixture", result
assert result["alembic_heads"] == [], result
' "$APPLY_JSON" || {
  fail "apply JSON did not report the expected result: $APPLY_JSON"
  exit 1
}
ORDER="$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command="SELECT string_agg(event, ',' ORDER BY seq) FROM public.dbmigrate_order")"
assert_equal "$ORDER" 'stamp,alembic,post' 'alembic_stamp ordering'
printf 'PASS alembic_stamp SQL runs before Alembic and regular SQL after it\n'

SECOND_APPLY="$(run_migrator apply --allow-downtime --json)"
python3 -c '
import json, sys
result = json.loads(sys.argv[1])
assert result["applied"] == [], result
assert result["head"] == "0002_downtime_fixture", result
' "$SECOND_APPLY" || {
  fail "second apply was not idempotent: $SECOND_APPLY"
  exit 1
}
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command='SELECT count(*) FROM public.schema_migration')" '2' \
  'idempotent apply ledger row count'
printf 'PASS apply twice is idempotent\n'

MIGRATION_FILE="$TMP_ROOT/release/Infrastructure/database/migrations/0001_alembic_stamp_fixture.sql"
cp -- "$MIGRATION_FILE" "$TMP_ROOT/0001_alembic_stamp_fixture.sql.original"
printf '\n-- tamper probe\n' >> "$MIGRATION_FILE"
if TAMPER_OUTPUT="$(run_migrator status 2>&1)"; then
  fail 'tampered applied migration unexpectedly passed integrity validation'
  exit 1
else
  TAMPER_STATUS=$?
fi
assert_migration_error 'migration_tampered:0001_alembic_stamp_fixture' \
  "$TAMPER_OUTPUT" "$TAMPER_STATUS"
cp -- "$TMP_ROOT/0001_alembic_stamp_fixture.sql.original" "$MIGRATION_FILE"
printf 'PASS applied migration tampering is detected\n'

run_migrator down --to 0001_alembic_stamp_fixture
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command='SELECT count(*) FROM public.schema_migration')" '1' \
  'down removes the reverted ledger row'
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command="SELECT to_regclass('public.dbmigrate_later') IS NULL")" \
't' 'down executes the matching down file'
printf 'PASS down to a version reverts newer migrations and ledger rows\n'

run_migrator apply --allow-downtime >/dev/null
DOWN_FILE="$TMP_ROOT/release/Infrastructure/database/migrations/0002_downtime_fixture.down.sql"
rm -- "$DOWN_FILE"
if DOWN_OUTPUT="$(run_migrator down --to 0001_alembic_stamp_fixture 2>&1)"; then
  fail 'down without a required down file unexpectedly succeeded'
  exit 1
else
  DOWN_STATUS=$?
fi
assert_migration_error 'migration_down_missing:0002_downtime_fixture' \
  "$DOWN_OUTPUT" "$DOWN_STATUS"
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command='SELECT count(*) FROM public.schema_migration')" '2' \
  'missing down file is rejected before any ledger deletion'
assert_equal "$(psql_as_postgres --dbname="$DB_NAME" --tuples-only --no-align \
  --command="SELECT to_regclass('public.dbmigrate_later') IS NOT NULL")" \
't' 'missing down file is rejected before SQL changes'
printf 'PASS missing down file is refused before rollback\n'

printf 'PASS db_migrate disposable-database proof\n'
