#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly RENAME_SCRIPT="$SCRIPT_DIRECTORY/../rename-database.sh"
readonly SANDBOX="$(mktemp -d)"
readonly BIN_DIRECTORY="$SANDBOX/bin"
readonly SYSTEMD_DIRECTORY="$SANDBOX/systemd"
readonly STATE_ROOT="$SANDBOX/rename-state"
readonly PGPASS_FILE="$SANDBOX/.pgpass"
readonly MONITORING_ENV_FILE="$SANDBOX/monitoring.env"
readonly DATABASE_STATE="$SANDBOX/database-name"
readonly WORKER_COUNT_FILE="$SANDBOX/timescale-worker-count"
readonly ALLOW_CONNECTIONS_FILE="$SANDBOX/allow-connections"
readonly SESSION_COUNT_FILE="$SANDBOX/session-count"
readonly SQL_LOG="$SANDBOX/sql.log"
readonly SYSTEMCTL_LOG="$SANDBOX/systemctl.log"
readonly ORIGINALS="$SANDBOX/originals"
readonly SERVICE_LIST="$SANDBOX/service_list.py"
readonly PASSWORD_MARKER='pgpass-secret-marker-do-not-print'
readonly MONITORING_PASSWORD_MARKER='monitoring-dsn-secret-marker-do-not-print'

cleanup() {
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'PASS: %s\n' "$1"
}

expect_failure() {
  set +e
  "$@" >/dev/null 2>&1
  local status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "expected command to fail: $*"
}

mkdir -p "$BIN_DIRECTORY" "$SYSTEMD_DIRECTORY/automation-service.service.d" "$ORIGINALS"
printf 'cea_sensors\n' > "$DATABASE_STATE"
printf '0\n' > "$SESSION_COUNT_FILE"
printf '1\n' > "$WORKER_COUNT_FILE"
printf 't\n' > "$ALLOW_CONNECTIONS_FILE"
printf 'cea_user|true|false\n' > "$SANDBOX/role-result"
printf 'localhost:5432:cea_sensors:cea_user:%s\n127.0.0.1:5432:other_db:cea_user:other-secret\n' \
  "$PASSWORD_MARKER" > "$PGPASS_FILE"
chmod 0600 "$PGPASS_FILE"
printf 'MONITORING_POSTGRES_DSN="postgresql://read_user:%s@localhost:5432/cea_sensors?sslmode=require&application_name=monitoring"\n' \
  "$MONITORING_PASSWORD_MARKER" > "$MONITORING_ENV_FILE"
chmod 0600 "$MONITORING_ENV_FILE"
cat > "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" <<'UNIT'
[Service]
Environment=POSTGRES_DB=cea_sensors
UNIT
cat > "$SYSTEMD_DIRECTORY/backend.service" <<'UNIT'
[Service]
Environment=POSTGRES_DB=cea_sensors
UNIT
cat > "$SYSTEMD_DIRECTORY/legacy-other.service" <<'UNIT'
[Service]
Environment=POSTGRES_DB=cea_sensors
UNIT
cat > "$SYSTEMD_DIRECTORY/unrelated.service" <<'UNIT'
[Service]
Environment=POSTGRES_DB=sj_cea
UNIT
cp -a "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" "$ORIGINALS/override.conf"
cp -a "$SYSTEMD_DIRECTORY/backend.service" "$ORIGINALS/backend.service"
cp -a "$SYSTEMD_DIRECTORY/legacy-other.service" "$ORIGINALS/legacy-other.service"
cp -a "$MONITORING_ENV_FILE" "$ORIGINALS/monitoring.env"
cp -a "$PGPASS_FILE" "$ORIGINALS/.pgpass"
cat > "$SERVICE_LIST" <<'PY'
#!/usr/bin/env python3
import sys
if sys.argv[1:] == ["--list-deploy-managed-units"]:
    print("automation-service.service")
    print("can-processor.service")
    print("monitoring-service.service")
else:
    raise SystemExit(2)
PY

cat > "$BIN_DIRECTORY/sudo" <<'SUDO'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == '-u' ]]; then shift 2; fi
if [[ "${1:-}" == 'chown' ]]; then exit 0; fi
exec "$@"
SUDO

cat > "$BIN_DIRECTORY/psql" <<'PSQL'
#!/usr/bin/env bash
set -euo pipefail
command_text=''
for argument in "$@"; do
  case "$argument" in
    --command=*) command_text="${argument#--command=}" ;;
    -c) ;;
  esac
done
if [[ "$command_text" == *"ALTER DATABASE"* && " $* " != *" --username=postgres "* ]]; then
  printf 'database rename was not issued through the postgres admin connection\n' >&2
  exit 1
fi
printf '%s\n' "$command_text" >> "$SQL_LOG"
case "$command_text" in
  *"SELECT count(*) FROM pg_database WHERE datname = 'cea_sensors'"*)
    [[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] && printf '1\n' || printf '0\n'
    ;;
  *"SELECT count(*) FROM pg_database WHERE datname = 'sj_cea'"*)
    [[ "$(cat "$DATABASE_STATE")" == 'sj_cea' ]] && printf '1\n' || printf '0\n'
    ;;
  *"SELECT datallowconn FROM pg_database WHERE datname = 'cea_sensors'"*|*"SELECT datallowconn FROM pg_database WHERE datname = 'sj_cea'"*)
    cat "$ALLOW_CONNECTIONS_FILE"
    ;;
  *"pg_get_userbyid(d.datdba)"*) cat "$SANDBOX/role-result" ;;
  *"SELECT pg_is_in_recovery()"*) printf 'f\n' ;;
  *"pg_stat_replication"*) printf 'standby\t127.0.0.2\tstreaming\tasync\n' ;;
  *"SELECT COALESCE((SELECT extversion FROM pg_extension"*) printf '2.28.0\n' ;;
  *"to_regprocedure('_timescaledb_functions.stop_background_workers()')"*) printf 't\n' ;;
  *"SELECT _timescaledb_functions.stop_background_workers()"*)
    printf '0\n' > "$WORKER_COUNT_FILE"
    printf 't\n'
    ;;
  *"SELECT _timescaledb_functions.restart_background_workers()"*)
    printf '1\n' > "$WORKER_COUNT_FILE"
    printf 't\n'
    ;;
  *"ALTER DATABASE \"cea_sensors\" WITH ALLOW_CONNECTIONS false"*)
    printf 'f\n' > "$ALLOW_CONNECTIONS_FILE"
    ;;
  *"ALTER DATABASE \"sj_cea\" WITH ALLOW_CONNECTIONS false"*)
    printf 'f\n' > "$ALLOW_CONNECTIONS_FILE"
    ;;
  *"ALTER DATABASE \"cea_sensors\" WITH ALLOW_CONNECTIONS true"*)
    printf 't\n' > "$ALLOW_CONNECTIONS_FILE"
    ;;
  *"ALTER DATABASE \"sj_cea\" WITH ALLOW_CONNECTIONS true"*)
    printf 't\n' > "$ALLOW_CONNECTIONS_FILE"
    ;;
  *"SELECT pg_terminate_backend(pid)"*"backend_type = 'background worker'"*)
    printf '0\n' > "$WORKER_COUNT_FILE"
    ;;
  *"SELECT pg_terminate_backend(pid)"*"AND pid <> pg_backend_pid()"*)
    printf '0\n' > "$SESSION_COUNT_FILE"
    ;;
  *"count(*) FROM pg_stat_activity WHERE datname = 'cea_sensors' AND backend_type = 'background worker'"*)
    cat "$WORKER_COUNT_FILE"
    ;;
  *"count(*) FROM pg_stat_activity WHERE datname = 'sj_cea' AND backend_type = 'background worker'"*)
    cat "$WORKER_COUNT_FILE"
    ;;
  *"WHERE datname = 'cea_sensors' AND backend_type = 'background worker' ORDER BY application_name, pid"*)
    [[ "$(cat "$WORKER_COUNT_FILE")" == '0' ]] || printf 'timescaledb scheduler\tbackground worker\tpostgres\tactive\n'
    ;;
  *"count(*) FROM pg_stat_activity WHERE datname = 'cea_sensors' AND pid"*) cat "$SESSION_COUNT_FILE" ;;
  *"count(*) FROM pg_stat_activity WHERE datname = 'sj_cea' AND pid"*) cat "$SESSION_COUNT_FILE" ;;
  *"pg_stat_activity WHERE datname = 'cea_sensors' GROUP BY"*) printf 'automation\tcea_user\tclient backend\t1\n' ;;
  *"ALTER DATABASE \"cea_sensors\" RENAME TO \"sj_cea\""*)
    [[ "$(cat "$SESSION_COUNT_FILE")" == '0' ]] || { printf 'database is being accessed\n' >&2; exit 1; }
    [[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] || exit 1
    printf 'sj_cea\n' > "$DATABASE_STATE"
    ;;
  *"ALTER DATABASE \"sj_cea\" RENAME TO \"cea_sensors\""*)
    [[ "$(cat "$SESSION_COUNT_FILE")" == '0' ]] || { printf 'database is being accessed\n' >&2; exit 1; }
    [[ "$(cat "$DATABASE_STATE")" == 'sj_cea' ]] || exit 1
    printf 'cea_sensors\n' > "$DATABASE_STATE"
    ;;
  *"SELECT current_database()"*)
    database=''
    for argument in "$@"; do
      case "$argument" in --dbname=*) database="${argument#--dbname=}" ;; esac
    done
    [[ -n "$database" ]] || database="$(cat "$DATABASE_STATE")"
    [[ "$database" == "$(cat "$DATABASE_STATE")" ]] || exit 1
    printf '%s\n' "$database"
    ;;
  *)
    printf 'unexpected psql query in sandbox: %s\n' "$command_text" >&2
    exit 1
    ;;
esac
PSQL

cat > "$BIN_DIRECTORY/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  is-active)
    printf 'active\n'
    ;;
  show)
    printf 'POSTGRES_DB=sj_cea POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=cea_user\n'
    ;;
  stop|restart|start|daemon-reload)
    printf 'systemctl %s\n' "$*" >> "$SYSTEMCTL_LOG"
    ;;
  *)
    printf 'unexpected systemctl invocation: %s\n' "$*" >&2
    exit 1
    ;;
esac
SYSTEMCTL
chmod 0700 "$BIN_DIRECTORY"/* "$SERVICE_LIST"

base_environment() {
  export PATH="$BIN_DIRECTORY:$PATH"
  export DB_RENAME_SYSTEMD_DIR="$SYSTEMD_DIRECTORY"
  export DB_RENAME_PGPASS_FILE="$PGPASS_FILE"
  export DB_RENAME_MONITORING_ENV_FILE="$MONITORING_ENV_FILE"
  export DB_RENAME_STATE_ROOT="$STATE_ROOT"
  export DB_RENAME_TEMP_DIR="$SANDBOX"
  export DB_RENAME_SERVICE_LIST="$SERVICE_LIST"
  export DB_RENAME_ADMIN_HOST='sandbox-socket'
  export DB_RENAME_PSQL=psql
  export DB_RENAME_SUDO=sudo
  export DB_RENAME_SYSTEMCTL=systemctl
  export DB_RENAME_SESSION_TIMEOUT_SECONDS=1
  export DB_RENAME_POLL_INTERVAL_SECONDS=1
  export SQL_LOG SYSTEMCTL_LOG DATABASE_STATE SESSION_COUNT_FILE WORKER_COUNT_FILE ALLOW_CONNECTIONS_FILE SANDBOX
  unset DEPLOY_LOG
}

base_environment
"$RENAME_SCRIPT" preflight > "$SANDBOX/preflight.out"
[[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] || fail 'preflight changed database state'
[[ "$(cat "$SYSTEMCTL_LOG" 2>/dev/null || true)" == '' ]] || fail 'preflight invoked systemctl'
[[ "$(cat "$SQL_LOG" 2>/dev/null || true)" != *'ALTER DATABASE'* ]] || fail 'preflight ran ALTER DATABASE'
grep -Fq "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" "$SANDBOX/preflight.out" || fail 'preflight omitted the drop-in'
grep -Fq "$SYSTEMD_DIRECTORY/backend.service" "$SANDBOX/preflight.out" || fail 'preflight omitted the legacy unit'
grep -Fq 'password=<redacted>' "$SANDBOX/preflight.out" || fail 'preflight did not redact pgpass'
grep -Fq 'owns_database=true createdb=false' "$SANDBOX/preflight.out" || fail 'preflight did not report the owner CREATEDB requirement'
grep -Fq 'postgres superuser is required' "$SANDBOX/preflight.out" || fail 'preflight did not report the postgres admin path'
grep -Fq "$MONITORING_ENV_FILE:1 database=cea_sensors" "$SANDBOX/preflight.out" || fail 'preflight omitted the monitoring DSN database path'
grep -Fq 'Database ALLOW_CONNECTIONS: t' "$SANDBOX/preflight.out" || fail 'preflight omitted the database connection setting'
grep -Fq 'TimescaleDB extension version: 2.28.0' "$SANDBOX/preflight.out" || fail 'preflight omitted the TimescaleDB version'
grep -Fq 'TimescaleDB scheduler/bgworker session attached: yes' "$SANDBOX/preflight.out" || fail 'preflight omitted the worker-session status'
grep -Fq 'timescaledb scheduler' "$SANDBOX/preflight.out" || fail 'preflight omitted worker application_name'
if grep -Fq "$PASSWORD_MARKER" "$SANDBOX/preflight.out" || grep -Fq "$MONITORING_PASSWORD_MARKER" "$SANDBOX/preflight.out"; then
  fail 'preflight printed a database password'
fi
pass 'preflight reports sessions and configuration without modifying files or printing secrets'

expect_failure "$RENAME_SCRIPT" apply
expect_failure "$RENAME_SCRIPT" apply --confirm wrong
[[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] || fail 'confirmation refusal changed the database'
[[ "$(cat "$SYSTEMCTL_LOG" 2>/dev/null || true)" == '' ]] || fail 'confirmation refusal stopped services'
pass 'apply refuses without the exact database confirmation'

# Remaining sessions must abort without termination and bring previously active
# services back online with byte-identical pre-sync configuration.
printf '1\n' > "$SESSION_COUNT_FILE"
expect_failure "$RENAME_SCRIPT" apply --confirm cea_sensors
[[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] || fail 'session timeout renamed the database'
[[ "$(cat "$SYSTEMCTL_LOG")" == *'systemctl stop automation-service.service'* ]] || fail 'session timeout did not stop managed services'
[[ "$(cat "$SYSTEMCTL_LOG")" == *'systemctl restart automation-service.service'* ]] || fail 'session timeout did not restart previously active services'
[[ "$(cat "$SYSTEMCTL_LOG")" == *'systemctl stop monitoring-service.service'* ]] || fail 'session timeout did not stop monitoring-service'
[[ "$(cat "$SYSTEMCTL_LOG")" == *'systemctl restart monitoring-service.service'* ]] || fail 'session timeout did not restart monitoring-service'
if grep -F "pg_terminate_backend(pid)" "$SQL_LOG" | grep -F "WHERE datname = 'cea_sensors' AND pid" >/dev/null; then
  fail 'session timeout terminated an unknown client session'
fi
[[ "$(cat "$ALLOW_CONNECTIONS_FILE")" == 't' ]] || fail 'session timeout did not restore ALLOW_CONNECTIONS'
[[ "$(cat "$WORKER_COUNT_FILE")" == '1' ]] || fail 'session timeout did not restart the TimescaleDB scheduler'
cmp "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" "$ORIGINALS/override.conf" || fail 'session abort changed the drop-in'
cmp "$PGPASS_FILE" "$ORIGINALS/.pgpass" || fail 'session abort changed pgpass'
cmp "$MONITORING_ENV_FILE" "$ORIGINALS/monitoring.env" || fail 'session abort changed monitoring.env'
[[ ! -L "$STATE_ROOT/active" ]] || fail 'session abort left an active rename pointer'
pass 'remaining sessions abort and restore/restart without terminating sessions'
printf '0\n' > "$SESSION_COUNT_FILE"
: > "$SYSTEMCTL_LOG"

"$RENAME_SCRIPT" apply --confirm cea_sensors > "$SANDBOX/apply.out"
[[ "$(cat "$DATABASE_STATE")" == 'sj_cea' ]] || fail 'apply did not rename the database'
[[ "$(cat "$ALLOW_CONNECTIONS_FILE")" == 't' ]] || fail 'apply did not restore ALLOW_CONNECTIONS after rename'
[[ "$(cat "$WORKER_COUNT_FILE")" == '1' ]] || fail 'apply did not restart TimescaleDB workers in the renamed database'
grep -Fq 'ALTER DATABASE "cea_sensors" RENAME TO "sj_cea"' "$SQL_LOG" || fail 'apply did not issue the expected ALTER'
[[ "$(cat "$SANDBOX/role-result")" == 'cea_user|true|false' ]] || fail 'rename changed the owner role privileges'
[[ "$(cat "$SQL_LOG")" != *'ALTER ROLE'* ]] || fail 'rename granted role privileges instead of using postgres admin'
python3 - "$SQL_LOG" <<'PY'
from pathlib import Path
import sys

lines = Path(sys.argv[1]).read_text().splitlines()
rename = next(i for i, line in enumerate(lines) if 'ALTER DATABASE "cea_sensors" RENAME TO "sj_cea"' in line)
stop = max(i for i, line in enumerate(lines[:rename]) if "SELECT _timescaledb_functions.stop_background_workers()" in line)
disable = max(i for i, line in enumerate(lines[:rename]) if 'ALTER DATABASE "cea_sensors" WITH ALLOW_CONNECTIONS false' in line)
enable = next(i for i, line in enumerate(lines[rename + 1:], rename + 1) if 'ALTER DATABASE "sj_cea" WITH ALLOW_CONNECTIONS true' in line)
restart = next(i for i, line in enumerate(lines[enable + 1:], enable + 1) if "SELECT _timescaledb_functions.restart_background_workers()" in line)
if not stop < disable < rename < enable < restart:
    raise SystemExit("TimescaleDB workers/connections were not quiesced and restored around rename")
if any("pg_terminate_backend(pid)" in line and "backend_type = 'background worker'" not in line for line in lines):
    raise SystemExit("the default apply path terminated a non-worker client session")
PY
grep -Fq 'POSTGRES_DB=sj_cea' "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" || fail 'apply did not rewrite the drop-in'
grep -Fq 'POSTGRES_DB=sj_cea' "$SYSTEMD_DIRECTORY/backend.service" || fail 'apply did not rewrite backend.service'
python3 - "$MONITORING_ENV_FILE" "$MONITORING_PASSWORD_MARKER" <<'PY'
from pathlib import Path
import sys
from urllib.parse import urlsplit

line = Path(sys.argv[1]).read_text().strip()
value = line.split("=", 1)[1].strip().strip('"')
parsed = urlsplit(value)
if parsed.path != "/sj_cea" or parsed.query != "sslmode=require&application_name=monitoring":
    raise SystemExit("monitoring DSN rewrite changed its database path or query string")
if parsed.password != sys.argv[2] or parsed.username != "read_user":
    raise SystemExit("monitoring DSN rewrite changed credentials")
PY
grep -Fq 'sj_cea:cea_user:' "$PGPASS_FILE" || fail 'apply did not rewrite the pgpass database field'
grep -Fq "$PASSWORD_MARKER" "$PGPASS_FILE" || fail 'apply changed the pgpass password field'
grep -Fq '127.0.0.1:5432:other_db:cea_user:other-secret' "$PGPASS_FILE" || fail 'apply changed an unrelated pgpass line'
  "$RENAME_SCRIPT" postcheck > "$SANDBOX/postcheck.out"
grep -Fq 'monitoring-service.service: effective POSTGRES_DB=sj_cea' "$SANDBOX/postcheck.out" || fail 'postcheck omitted monitoring-service'
if grep -Fq "$PASSWORD_MARKER" "$SANDBOX/postcheck.out" || grep -Fq "$MONITORING_PASSWORD_MARKER" "$SANDBOX/postcheck.out"; then
  fail 'postcheck printed a database password'
fi
[[ -z "$(find "$SANDBOX" -maxdepth 1 -name 'db-rename-pgpass.*' -print -quit)" ]] || fail 'postcheck left a credential temp file'
[[ -L "$STATE_ROOT/active" ]] || fail 'apply did not create its rollback pointer'
state_directory="$(readlink -f "$STATE_ROOT/active")"
[[ -s "$state_directory/files.tsv" ]] || fail 'apply did not record backup manifest'
while IFS=$'\t' read -r original backup_name; do
  case "$original" in
    "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf") expected="$ORIGINALS/override.conf" ;;
    "$SYSTEMD_DIRECTORY/backend.service") expected="$ORIGINALS/backend.service" ;;
    "$SYSTEMD_DIRECTORY/legacy-other.service") expected="$ORIGINALS/legacy-other.service" ;;
    "$PGPASS_FILE") expected="$ORIGINALS/.pgpass" ;;
    "$MONITORING_ENV_FILE") expected="$ORIGINALS/monitoring.env" ;;
    *) fail "unexpected backup target: $original" ;;
  esac
  cmp "$state_directory/files/$backup_name" "$expected" || fail "backup is not byte-identical for $original"
  if [[ "$original" == "$MONITORING_ENV_FILE" ]]; then
    [[ "$(stat -c '%a' "$state_directory/files/$backup_name")" == '600' ]] || fail 'monitoring.env backup did not retain mode 0600'
  fi
done < "$state_directory/files.tsv"
pass 'apply renames, rewrites affected files, and records byte-identical backups'

alter_count="$(grep -c 'RENAME TO' "$SQL_LOG")"
"$RENAME_SCRIPT" apply --confirm cea_sensors > "$SANDBOX/reapply.out"
grep -Fxq 'DB_RENAME_STATUS=already-renamed' "$SANDBOX/reapply.out" || fail 're-apply was not idempotent'
[[ "$(grep -c 'RENAME TO' "$SQL_LOG")" == "$alter_count" ]] || fail 'idempotent apply repeated ALTER DATABASE RENAME'
pass 're-apply is a successful no-op after the database is renamed'

"$RENAME_SCRIPT" rollback > "$SANDBOX/rollback.out"
[[ "$(cat "$DATABASE_STATE")" == 'cea_sensors' ]] || fail 'rollback did not rename back'
[[ "$(cat "$ALLOW_CONNECTIONS_FILE")" == 't' ]] || fail 'rollback left database connections disabled'
[[ "$(cat "$WORKER_COUNT_FILE")" == '1' ]] || fail 'rollback did not restart TimescaleDB workers'
cmp "$SYSTEMD_DIRECTORY/automation-service.service.d/override.conf" "$ORIGINALS/override.conf" || fail 'rollback did not restore drop-in bytes'
cmp "$SYSTEMD_DIRECTORY/backend.service" "$ORIGINALS/backend.service" || fail 'rollback did not restore backend unit bytes'
cmp "$SYSTEMD_DIRECTORY/legacy-other.service" "$ORIGINALS/legacy-other.service" || fail 'rollback did not restore other unit bytes'
cmp "$PGPASS_FILE" "$ORIGINALS/.pgpass" || fail 'rollback did not restore pgpass bytes'
cmp "$MONITORING_ENV_FILE" "$ORIGINALS/monitoring.env" || fail 'rollback did not restore monitoring.env byte-for-byte'
[[ "$(stat -c '%a' "$MONITORING_ENV_FILE")" == '600' ]] || fail 'rollback did not restore monitoring.env mode 0600'
[[ ! -L "$STATE_ROOT/active" ]] || fail 'rollback did not clear the active rename pointer'
"$RENAME_SCRIPT" rollback > "$SANDBOX/re-rollback.out"
grep -Fq 'already rolled back' "$SANDBOX/re-rollback.out" || fail 'rollback was not idempotent'
[[ "$(grep -c 'RENAME TO' "$SQL_LOG")" -eq 2 ]] || fail 'rollback/idempotence issued an unexpected rename count'
pass 'rollback restores byte-identical files and idempotently renames back'

printf 'database rename sandbox test passed\n'
