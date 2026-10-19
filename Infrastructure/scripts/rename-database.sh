#!/usr/bin/env bash
# Single-use production helper; delete after this database-rename deploy is finalized.
# Guarded rename used only by the explicitly authorized deploy wrapper.
set -euo pipefail
set +x 2>/dev/null || true

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly OLD_DATABASE='cea_sensors'
readonly NEW_DATABASE='sj_cea'
readonly SYSTEMD_DIRECTORY="${DB_RENAME_SYSTEMD_DIR:-/etc/systemd/system}"
readonly MONITORING_ENV_FILE="${DB_RENAME_MONITORING_ENV_FILE:-/opt/projectcea/shared/env/monitoring.env}"
readonly PGPASS_FILE="${DB_RENAME_PGPASS_FILE:-${HOME:-/home/antoine}/.pgpass}"
readonly STATE_ROOT="${DB_RENAME_STATE_ROOT:-/var/lib/projectcea/db-rename}"
readonly ACTIVE_STATE_FILE="${DB_RENAME_ACTIVE_STATE_FILE:-$STATE_ROOT/active}"
readonly SERVICE_LIST="${DB_RENAME_SERVICE_LIST:-$SCRIPT_DIRECTORY/service_list.py}"
readonly PSQL="${DB_RENAME_PSQL:-psql}"
readonly SUDO="${DB_RENAME_SUDO:-sudo}"
readonly SYSTEMCTL="${DB_RENAME_SYSTEMCTL:-systemctl}"
readonly ADMIN_HOST="${DB_RENAME_ADMIN_HOST:-/var/run/postgresql}"
readonly ADMIN_PORT="${DB_RENAME_ADMIN_PORT:-5432}"
readonly ADMIN_DATABASE="${DB_RENAME_ADMIN_DATABASE:-postgres}"
readonly SESSION_TIMEOUT="${DB_RENAME_SESSION_TIMEOUT_SECONDS:-90}"
readonly POLL_INTERVAL="${DB_RENAME_POLL_INTERVAL_SECONDS:-2}"

usage() {
  cat <<'USAGE'
Usage: rename-database.sh [preflight]
       rename-database.sh apply --confirm cea_sensors [--terminate-remaining]
       rename-database.sh rollback [--terminate-remaining]
       rename-database.sh postcheck

With no subcommand, run the read-only preflight. apply and rollback require
local PostgreSQL superuser access and stop database clients; apply never starts
services (the deploy restart step owns that). Only explicit
--terminate-remaining may terminate non-worker client sessions.
USAGE
}

fail() {
  printf '[db-rename] %s\n' "$1" >&2
  log_event 'db_rename_error' "$1" || true
  return 1
}

log_event() {
  local event="$1" detail="${2:-}" line
  export DLOG_EVENT="$event"
  export DLOG_DETAIL="$detail"
  export DLOG_SERVICE=''
  export DLOG_HTTP=''
  line="$(python3 "$SCRIPT_DIRECTORY/deploy_emit_event.py" | python3 "$SCRIPT_DIRECTORY/deploy_json_line.py")"
  if [[ -n "${DEPLOY_LOG:-}" ]]; then
    printf '%s\n' "$line" | "$SUDO" tee -a "$DEPLOY_LOG" >/dev/null
  fi
  printf '%s\n' "$line"
}

run_root() {
  "$SUDO" "$@"
}

database_psql() {
  local database="$1"
  shift
  run_root -u postgres "$PSQL" -X --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --field-separator=$'\t' \
    --host="$ADMIN_HOST" --port="$ADMIN_PORT" --username=postgres \
    --dbname="$database" "$@"
}

admin_psql() {
  database_psql "$ADMIN_DATABASE" "$@"
}

query_scalar() {
  admin_psql --command="$1" | tr -d '[:space:]'
}

query_database_scalar() {
  local database="$1" sql="$2"
  database_psql "$database" --command="$sql" | tr -d '[:space:]'
}

database_presence() {
  local name="$1"
  query_scalar "SELECT count(*) FROM pg_database WHERE datname = '${name}'"
}

session_count() {
  local name="$1"
  query_scalar "SELECT count(*) FROM pg_stat_activity WHERE datname = '${name}' AND pid <> pg_backend_pid()"
}

timescale_worker_count() {
  local name="$1"
  query_scalar "SELECT count(*) FROM pg_stat_activity WHERE datname = '${name}' AND backend_type = 'background worker'"
}

set_database_allow_connections() {
  local database="$1" allow_connections="$2"
  [[ "$allow_connections" == 'true' || "$allow_connections" == 'false' ]] || return 2
  admin_psql --command="ALTER DATABASE \"$database\" WITH ALLOW_CONNECTIONS $allow_connections" >/dev/null
}

timescaledb_worker_action() {
  local database="$1" action="$2"
  [[ "$action" == 'stop' || "$action" == 'restart' ]] || return 2
  database_psql "$database" --command="SELECT _timescaledb_functions.${action}_background_workers()" >/dev/null
}

terminate_timescale_worker_sessions() {
  local database="$1"
  admin_psql --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${database}' AND backend_type = 'background worker' AND pid <> pg_backend_pid()" >/dev/null
}

resume_timescaledb_database() {
  local database="$1"
  set_database_allow_connections "$database" true || return 1
  timescaledb_worker_action "$database" restart
}

require_integer_setting() {
  local label="$1" value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] && ((10#$value > 0)) || {
    fail "$label must be a positive integer"
    return 1
  }
}

require_commands() {
  local command
  for command in python3 mktemp "$PSQL" "$SUDO" "$SYSTEMCTL"; do
    command -v "$command" >/dev/null 2>&1 || {
      fail "required command not found: $command"
      return 1
    }
  done
  [[ -f "$SERVICE_LIST" ]] || { fail "service list not found: $SERVICE_LIST"; return 1; }
  list_managed_units >/dev/null || { fail 'could not load deploy-managed units'; return 1; }
}

list_managed_units() {
  python3 "$SERVICE_LIST" --list-deploy-managed-units
}

systemd_matches_for() {
  local database="$1"
  run_root python3 - "$SYSTEMD_DIRECTORY" "$database" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
needle = sys.argv[2].encode()
if not root.is_dir():
    raise SystemExit(0)
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    for number, line in enumerate(path.read_bytes().splitlines(), 1):
        if needle in line:
            print(f"{path}\t{number}")
PY
}

systemd_matches() {
  systemd_matches_for "$OLD_DATABASE"
}

rewrite_candidates() {
  run_root python3 - "$SYSTEMD_DIRECTORY" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
pattern = re.compile(rb"POSTGRES_DB=cea_sensors(?=$|[\s\"'])")
if not root.is_dir():
    raise SystemExit(0)
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    if pattern.search(path.read_bytes()):
        print(path)
PY
}

pgpass_matches() {
  python3 - "$PGPASS_FILE" "$OLD_DATABASE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(0)
needle = sys.argv[2].encode()
for number, raw in enumerate(path.read_bytes().splitlines(), 1):
    if raw.startswith(b"#"):
        continue
    delimiters = []
    escaped = False
    for index, byte in enumerate(raw):
        if escaped:
            escaped = False
        elif byte == 92:
            escaped = True
        elif byte == 58:
            delimiters.append(index)
    if len(delimiters) >= 3 and raw[delimiters[1] + 1:delimiters[2]] == needle:
        print(number)
PY
}

monitoring_env_rows() {
  run_root python3 - "$MONITORING_ENV_FILE" "$OLD_DATABASE" <<'PY'
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(0)
for number, raw in enumerate(path.read_bytes().splitlines(), 1):
    match = re.match(rb"^\s*MONITORING_POSTGRES_DSN\s*=\s*(.*?)\s*$", raw)
    if not match:
        continue
    value = match.group(1)
    if len(value) >= 2 and value[:1] in (b"'", b'"') and value[-1:] == value[:1]:
        value = value[1:-1]
    try:
        parsed = urlsplit(value.decode("utf-8"))
        segment = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
        if parsed.scheme not in {"postgres", "postgresql"} or not segment or "/" in segment:
            segment = "<unparseable>"
    except (UnicodeDecodeError, ValueError):
        segment = "<unparseable>"
    print(f"{number}\t{segment}")
PY
}

monitoring_env_has_old_database() {
  monitoring_env_rows | awk -F '\t' -v database="$OLD_DATABASE" '$2 == database { found=1 } END { exit !found }'
}

monitoring_dsn_current_database() {
  local pgpass_file connection host port user database actual
  pgpass_file="$(run_root mktemp "${DB_RENAME_TEMP_DIR:-/run}/db-rename-pgpass.XXXXXX")" || return 1
  if ! connection="$(run_root python3 - "$MONITORING_ENV_FILE" "$pgpass_file" "$NEW_DATABASE" <<'PY'
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

env_path = Path(sys.argv[1])
pgpass_path = Path(sys.argv[2])
expected = sys.argv[3]
dsn = None
for raw in env_path.read_bytes().splitlines():
    match = re.match(rb"^\s*MONITORING_POSTGRES_DSN\s*=\s*(.*?)\s*$", raw)
    if not match:
        continue
    value = match.group(1)
    if len(value) >= 2 and value[:1] in (b"'", b'"') and value[-1:] == value[:1]:
        value = value[1:-1]
    dsn = value.decode("utf-8")
    break
if dsn is None:
    raise SystemExit(2)
try:
    parsed = urlsplit(dsn)
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
except (UnicodeDecodeError, ValueError):
    raise SystemExit("cannot safely parse monitoring PostgreSQL DSN")
if parsed.scheme not in {"postgres", "postgresql"} or database != expected or not user or not password:
    raise SystemExit(2)
if any(character in field for field in (database, user, password, host) for character in "\r\n"):
    raise SystemExit("monitoring PostgreSQL DSN contains an unsupported line break")
def escape(field: str) -> str:
    return field.replace("\\", "\\\\").replace(":", "\\:")
pgpass_path.write_text(f"*:{port}:{escape(database)}:{escape(user)}:{escape(password)}\n")
print(f"{host}\t{port}\t{user}\t{database}")
PY
)"; then
    run_root rm -f -- "$pgpass_file" || true
    return 1
  fi
  IFS=$'\t' read -r host port user database <<<"$connection"
  if ! run_root chown postgres:postgres "$pgpass_file"; then
    run_root rm -f -- "$pgpass_file" || true
    return 1
  fi
  actual="$(run_root -u postgres env -u PGPASSWORD -u PGSERVICE -u PGSERVICEFILE \
    PGPASSFILE="$pgpass_file" "$PSQL" -X --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --host="$host" --port="$port" --username="$user" \
    --dbname="$database" --command='SELECT current_database()' 2>/dev/null | tr -d '[:space:]')" || {
      run_root rm -f -- "$pgpass_file" || true
      return 1
    }
  run_root rm -f -- "$pgpass_file" || return 1
  [[ "$actual" == "$NEW_DATABASE" ]] || return 1
  printf '%s\n' "$actual"
}

rewrite_monitoring_env_file() {
  run_root python3 - "$MONITORING_ENV_FILE" "$OLD_DATABASE" "$NEW_DATABASE" <<'PY'
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

path = Path(sys.argv[1])
old = sys.argv[2]
new = sys.argv[3]
content = path.read_bytes()
updated_lines = []
changed = False
for raw in content.splitlines(keepends=True):
    body = raw.rstrip(b"\r\n")
    ending = raw[len(body):]
    match = re.match(rb"^(\s*MONITORING_POSTGRES_DSN\s*=\s*)(.*)$", body)
    if not match:
        updated_lines.append(raw)
        continue
    prefix, rhs = match.groups()
    trimmed = rhs.strip()
    leading = rhs[:len(rhs) - len(rhs.lstrip())]
    trailing = rhs[len(rhs.rstrip()):]
    quote = trimmed[:1] if trimmed[:1] in (b"'", b'"') else b""
    value = trimmed[1:-1] if quote and trimmed.endswith(quote) else trimmed
    try:
        parsed = urlsplit(value.decode("utf-8"))
        database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    except (UnicodeDecodeError, ValueError):
        raise SystemExit("cannot safely parse monitoring PostgreSQL DSN path")
    if parsed.scheme not in {"postgres", "postgresql"} or database == "<unparseable>":
        raise SystemExit("cannot safely parse monitoring PostgreSQL DSN path")
    if database == old and parsed.path != "/" + old:
        raise SystemExit("monitoring PostgreSQL DSN uses a non-canonical old database path")
    if parsed.path != "/" + old or database != old:
        updated_lines.append(raw)
        continue
    replacement = re.sub(rb"(?<=/)cea_sensors(?=[?#]|$)", b"sj_cea", value, count=1)
    if replacement == value:
        raise SystemExit("cannot safely rewrite monitoring PostgreSQL DSN path")
    updated_rhs = leading + quote + replacement + quote + trailing
    updated_lines.append(prefix + updated_rhs + ending)
    changed = True
if changed:
    path.write_bytes(b"".join(updated_lines))
PY
}

print_config_matches() {
  local match line_number database monitor_rows
  printf 'Systemd files containing %s (paths and line numbers only):\n' "$OLD_DATABASE"
  while IFS=$'\t' read -r match line_number; do
    [[ -n "$match" ]] && printf '  %s:%s\n' "$match" "$line_number"
  done < <(systemd_matches)
  if [[ -f "$PGPASS_FILE" ]]; then
    printf '  %s lines naming the old database (password fields redacted):\n' "$PGPASS_FILE"
    while IFS= read -r line_number; do
      [[ -n "$line_number" ]] && printf '    line %s: database=%s password=<redacted>\n' "$line_number" "$OLD_DATABASE"
    done < <(pgpass_matches)
  else
    printf '  %s (not present)\n' "$PGPASS_FILE"
  fi
  printf 'Monitoring EnvironmentFile DSN paths (DSN and credentials omitted):\n'
  if ! run_root test -f "$MONITORING_ENV_FILE"; then
    printf '  %s (not present)\n' "$MONITORING_ENV_FILE"
    return
  fi
  monitor_rows="$(monitoring_env_rows)"
  if [[ -z "$monitor_rows" ]]; then
    printf '  %s: MONITORING_POSTGRES_DSN not set\n' "$MONITORING_ENV_FILE"
  else
    while IFS=$'\t' read -r line_number database; do
      printf '  %s:%s database=%s\n' "$MONITORING_ENV_FILE" "$line_number" "$database"
    done <<<"$monitor_rows"
  fi
}

collect_units() {
  local database="${1:-$OLD_DATABASE}" state_directory="${2:-}" unit match relative discovered
  declare -A seen=()
  while IFS= read -r unit; do
    [[ -n "$unit" ]] && seen["$unit"]=1
  done < <(list_managed_units)
  while IFS=$'\t' read -r match _; do
    [[ -n "$match" ]] || continue
    relative="${match#"$SYSTEMD_DIRECTORY"/}"
    case "$relative" in
      *.service.d/*) discovered="${relative%%.service.d/*}.service" ;;
      *.service) discovered="${relative##*/}" ;;
      *) continue ;;
    esac
    seen["$discovered"]=1
  done < <(systemd_matches_for "$database")
  if [[ -n "$state_directory" ]] && run_root test -f "$state_directory/all-units.txt"; then
    while IFS= read -r unit; do
      [[ -n "$unit" ]] && seen["$unit"]=1
    done < <(run_root cat "$state_directory/all-units.txt")
  fi
  for unit in "${!seen[@]}"; do printf '%s\n' "$unit"; done | sort
}

role_rename_capability() {
  local role="${POSTGRES_USER:-cea_user}" result owner owns createdb
  result="$(query_scalar "SELECT pg_get_userbyid(d.datdba) || '|' || (d.datdba = r.oid)::text || '|' || r.rolcreatedb::text FROM pg_database d JOIN pg_roles r ON r.rolname = '${role}' WHERE d.datname = '${OLD_DATABASE}'")"
  IFS='|' read -r owner owns createdb <<<"$result"
  [[ -n "$owner" ]] || { fail "could not inspect owner privileges for role $role"; return 1; }
  printf 'Rename role check: role=%s database_owner=%s owns_database=%s createdb=%s\n' "$role" "$owner" "$owns" "$createdb"
  if [[ "$owns" == 'true' && "$createdb" == 'true' ]]; then
    printf '  %s can rename the database; the guarded tool uses the postgres superuser for the rename.\n' "$role"
  else
    printf '  %s cannot satisfy owner+CREATEDB; the postgres superuser is required.\n' "$role"
  fi
}

replica_status() {
  local recovery rows
  recovery="$(query_scalar 'SELECT pg_is_in_recovery()')"
  [[ "$recovery" == 'f' ]] || { fail 'connected PostgreSQL server is in recovery; run this only on the writable primary'; return 1; }
  printf 'Primary status: writable primary (pg_is_in_recovery()=false)\n'
  rows="$(admin_psql --command="SELECT COALESCE(application_name, '(unknown)') || E'\\t' || COALESCE(client_addr::text, '(local)') || E'\\t' || state || E'\\t' || sync_state FROM pg_stat_replication ORDER BY application_name")"
  if [[ -z "$rows" ]]; then
    printf 'Streaming replicas: none currently connected\n'
  else
    printf 'Streaming replicas (application, client, state, sync_state):\n%s\n' "$rows"
  fi
}

timescaledb_preflight() {
  local allow version functions workers
  allow="$(query_scalar "SELECT datallowconn FROM pg_database WHERE datname = '${OLD_DATABASE}'")"
  printf 'Database ALLOW_CONNECTIONS: %s\n' "$allow"
  [[ "$allow" == 't' ]] || { fail 'database connections are disabled; the TimescaleDB worker cannot be safely quiesced'; return 1; }
  version="$(query_database_scalar "$OLD_DATABASE" "SELECT COALESCE((SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'), 'missing')")"
  functions="$(query_database_scalar "$OLD_DATABASE" "SELECT (to_regprocedure('_timescaledb_functions.stop_background_workers()') IS NOT NULL AND to_regprocedure('_timescaledb_functions.restart_background_workers()') IS NOT NULL)")"
  workers="$(admin_psql --command="SELECT COALESCE(application_name, '(unknown)') || E'\\t' || COALESCE(backend_type, '(unknown)') || E'\\t' || COALESCE(usename, '(unknown)') || E'\\t' || COALESCE(state, '(unknown)') FROM pg_stat_activity WHERE datname = '${OLD_DATABASE}' AND backend_type = 'background worker' ORDER BY application_name, pid")"
  printf 'TimescaleDB extension version: %s\n' "$version"
  [[ "$version" != 'missing' && "$functions" == 't' ]] || { fail 'TimescaleDB 2.28 worker stop/restart functions are unavailable'; return 1; }
  if [[ -z "$workers" ]]; then
    printf 'TimescaleDB scheduler/bgworker session attached: no\n'
  else
    printf 'TimescaleDB scheduler/bgworker session attached: yes (application, backend_type, usename, state):\n%s\n' "$workers"
  fi
}

timescaledb_worker_api_available() {
  local database="$1" available allow
  allow="$(query_scalar "SELECT datallowconn FROM pg_database WHERE datname = '${database}'")"
  [[ "$allow" == 't' ]] || return 1
  available="$(query_database_scalar "$database" "SELECT (to_regprocedure('_timescaledb_functions.stop_background_workers()') IS NOT NULL AND to_regprocedure('_timescaledb_functions.restart_background_workers()') IS NOT NULL)")"
  [[ "$available" == 't' ]]
}
preflight() {
  require_commands
  local old_count new_count grouped monitor_rows
  old_count="$(database_presence "$OLD_DATABASE")"
  new_count="$(database_presence "$NEW_DATABASE")"
  if [[ "$old_count" == '0' && "$new_count" == '1' ]]; then
    printf 'Database rename preflight: already renamed (%s exists; %s does not).\n' "$NEW_DATABASE" "$OLD_DATABASE"
    log_event 'db_rename_preflight_already_renamed' 'old database absent; new database present'
    return 0
  fi
  if [[ "$old_count" != '1' || "$new_count" != '0' ]]; then
    fail "expected exactly one of $OLD_DATABASE and $NEW_DATABASE to exist; old=$old_count new=$new_count"
    return 1
  fi
  monitor_rows="$(monitoring_env_rows)"
  if [[ "$monitor_rows" == *$'\t<unparseable>'* ]]; then
    fail 'cannot safely determine the monitoring-service DSN database path'
    return 1
  fi
  printf 'Database preflight: %s exists; %s does not.\n' "$OLD_DATABASE" "$NEW_DATABASE"
  printf 'Sessions connected to %s grouped by application_name/usename:\n' "$OLD_DATABASE"
  grouped="$(admin_psql --command="SELECT COALESCE(application_name, '(unknown)') || E'\\t' || COALESCE(usename, '(unknown)') || E'\\t' || COALESCE(backend_type, '(unknown)') || E'\\t' || count(*)::text FROM pg_stat_activity WHERE datname = '${OLD_DATABASE}' GROUP BY 1,2,3 ORDER BY 1,2,3")"
  if [[ -z "$grouped" ]]; then printf '  none\n'; else printf '%s\n' "$grouped"; fi
  print_config_matches
  role_rename_capability
  timescaledb_preflight
  replica_status
  printf 'Exact deploy plan:\n'
  printf '  1. Stage and validate the candidate release.\n'
  printf '  2. Back up affected units/drop-ins, monitoring.env, and ~/.pgpass; stop deploy-managed and DB-referencing units.\n'
  printf '  3. Stop the TimescaleDB background workers, disable new connections, and terminate only remaining background-worker backends.\n'
  printf '  4. Wait up to %s seconds for all sessions on %s to drain; terminate client sessions only with --terminate-remaining.\n' "$SESSION_TIMEOUT" "$OLD_DATABASE"
  printf '  5. Rename %s to %s as postgres, re-enable connections, restart TimescaleDB workers, and rewrite saved PostgreSQL settings.\n' "$OLD_DATABASE" "$NEW_DATABASE"
  printf '  6. Let the deploy wrapper switch the symlink, restart services, and run health checks.\n'
  log_event 'db_rename_preflight_ok' 'old database exists; new database absent'
}

write_active_state() {
  local state_directory="$1"
  run_root mkdir -p "$STATE_ROOT"
  run_root chmod 0700 "$STATE_ROOT"
  run_root ln -sfn "$state_directory" "$ACTIVE_STATE_FILE"
}

active_state_directory() {
  local resolved
  run_root test -L "$ACTIVE_STATE_FILE" || return 1
  resolved="$(run_root readlink -f "$ACTIVE_STATE_FILE" 2>/dev/null || true)"
  [[ -n "$resolved" && "$resolved" == "$STATE_ROOT"/* ]] || return 1
  run_root test -d "$resolved" || return 1
  printf '%s\n' "$resolved"
}

remove_active_state() {
  local state_directory="$1" current
  current="$(active_state_directory 2>/dev/null || true)"
  if [[ "$current" == "$state_directory" ]]; then run_root rm -f -- "$ACTIVE_STATE_FILE"; fi
}

backup_file() {
  local source_path="$1" state_directory="$2" index backup_name
  index="$(run_root find "$state_directory/files" -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d '[:space:]')"
  printf -v backup_name '%06d' "$((index + 1))"
  run_root cp -a -- "$source_path" "$state_directory/files/$backup_name"
  printf '%s\t%s\n' "$source_path" "$backup_name" | run_root tee -a "$state_directory/files.tsv" >/dev/null
}

backup_all_files() {
  local state_directory="$1" path sync_backup="${DB_RENAME_SYNC_BACKUP_DIR:-}" backup_path target_path entry
  while IFS= read -r path; do
    if [[ -n "$path" ]]; then backup_file "$path" "$state_directory" || return 1; fi
  done < <(rewrite_candidates)
  if [[ -f "$PGPASS_FILE" ]] && [[ -n "$(pgpass_matches)" ]]; then
    backup_file "$PGPASS_FILE" "$state_directory" || return 1
  fi
  if monitoring_env_has_old_database; then
    backup_file "$MONITORING_ENV_FILE" "$state_directory" || return 1
  fi
  if [[ -n "$sync_backup" ]]; then
    [[ -d "$sync_backup" ]] || { fail "systemd sync backup directory not found: $sync_backup"; return 1; }
    while IFS= read -r backup_path; do
      [[ -f "$backup_path" ]] || continue
      target_path="$SYSTEMD_DIRECTORY/$(basename "$backup_path")"
      backup_file "$target_path" "$state_directory" || return 1
      # Replace the copy made from the new unit with the pre-sync original.
      entry="$(run_root awk -F '\t' -v path="$target_path" '$1 == path { value=$2 } END { print value }' "$state_directory/files.tsv")"
      [[ -n "$entry" ]] || { fail "missing systemd sync backup manifest entry for $(basename "$backup_path")"; return 1; }
      run_root cp -a -- "$backup_path" "$state_directory/files/$entry" || return 1
    done < <(find "$sync_backup" -mindepth 1 -maxdepth 1 -type f -print | sort)
  fi
}

restore_backups() {
  local state_directory="$1" target backup_name
  run_root test -f "$state_directory/files.tsv" || return 0
  while IFS=$'\t' read -r target backup_name; do
    [[ -n "$target" && -n "$backup_name" ]] || continue
    run_root cp -a -- "$state_directory/files/$backup_name" "$target" || return 1
  done < <(run_root cat "$state_directory/files.tsv")
}

save_units() {
  local state_directory="$1" unit active_file="$state_directory/active-units.txt" all_file="$state_directory/all-units.txt"
  local active_tmp="${TMPDIR:-/tmp}/db-rename-active-$$" all_tmp="${TMPDIR:-/tmp}/db-rename-all-$$"
  : > "$active_tmp"
  : > "$all_tmp"
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    printf '%s\n' "$unit" >> "$all_tmp"
    if run_root "$SYSTEMCTL" is-active --quiet "$unit" >/dev/null 2>&1; then
      printf '%s\n' "$unit" >> "$active_tmp"
    fi
  done < <(collect_units "$OLD_DATABASE" "$state_directory")
  if ! run_root test -f "$active_file"; then run_root install -m 0600 "$active_tmp" "$active_file"; fi
  if ! run_root test -f "$all_file"; then run_root install -m 0600 "$all_tmp" "$all_file"; fi
  rm -f "$active_tmp" "$all_tmp"
}

stop_units() {
  local state_directory="$1" unit
  save_units "$state_directory"
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    run_root "$SYSTEMCTL" stop "$unit" || { fail "could not stop $unit"; return 1; }
  done < <(run_root cat "$state_directory/all-units.txt")
}

restart_previously_active_units() {
  local state_directory="$1" unit active_file="$state_directory/active-units.txt"
  run_root test -f "$active_file" || return 0
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    run_root "$SYSTEMCTL" restart "$unit" || { fail "could not restart previously active unit $unit"; return 1; }
  done < <(run_root cat "$active_file")
}

wait_for_zero_sessions() {
  local database="$1" timeout_seconds="$2" started count elapsed
  require_integer_setting 'DB_RENAME_SESSION_TIMEOUT_SECONDS' "$timeout_seconds" || return 1
  require_integer_setting 'DB_RENAME_POLL_INTERVAL_SECONDS' "$POLL_INTERVAL" || return 1
  started="$(date +%s)"
  while true; do
    count="$(session_count "$database")" || { fail "could not inspect sessions on $database"; return 1; }
    [[ "$count" =~ ^[0-9]+$ ]] || { fail "PostgreSQL returned an invalid session count for $database"; return 1; }
    if ((10#$count == 0)); then return 0; fi
    elapsed=$(( $(date +%s) - started ))
    if ((elapsed >= timeout_seconds)); then
      printf '[db-rename] waiting on %s sessions for %s: %s\n' "$count" "$database" "$elapsed" >&2
      return 1
    fi
    sleep "$POLL_INTERVAL"
  done
}

quiesce_database_for_rename() {
  local database="$1" terminate="$2" timeout_seconds="$3"
  set_database_allow_connections "$database" true || return 1
  timescaledb_worker_action "$database" stop || return 1
  set_database_allow_connections "$database" false || return 1
  terminate_timescale_worker_sessions "$database" || return 1
  if wait_for_zero_sessions "$database" "$timeout_seconds"; then return 0; fi
  if [[ "$terminate" == '1' ]]; then
    log_event 'db_rename_terminate_remaining' 'explicit --terminate-remaining confirmation supplied'
    terminate_remaining_sessions "$database" || return 1
    wait_for_zero_sessions "$database" "$timeout_seconds"
    return $?
  fi
  return 1
}

terminate_remaining_sessions() {
  local database="$1"
  admin_psql --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${database}' AND pid <> pg_backend_pid()" >/dev/null
}

rewrite_systemd_file() {
  local path="$1"
  run_root python3 - "$path" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
content = path.read_bytes()
updated = re.sub(rb"POSTGRES_DB=cea_sensors(?=$|[\s\"'])", b"POSTGRES_DB=sj_cea", content)
if updated != content:
    path.write_bytes(updated)
PY
}

rewrite_pgpass_file() {
  [[ -f "$PGPASS_FILE" ]] || return 0
  python3 - "$PGPASS_FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old = b"cea_sensors"
new = b"sj_cea"
output = []
changed = False
for raw in path.read_bytes().splitlines(keepends=True):
    line = raw[:-1] if raw.endswith(b"\n") else raw
    if line.endswith(b"\r"):
        body, ending = line[:-1], raw[-2:]
    else:
        body, ending = line, (b"\n" if raw.endswith(b"\n") else b"")
    if body.startswith(b"#"):
        output.append(raw)
        continue
    delimiters = []
    escaped = False
    for index, byte in enumerate(body):
        if escaped:
            escaped = False
        elif byte == 92:
            escaped = True
        elif byte == 58:
            delimiters.append(index)
    if len(delimiters) >= 3 and body[delimiters[1] + 1:delimiters[2]] == old:
        start, end = delimiters[1] + 1, delimiters[2]
        body = body[:start] + new + body[end:]
        changed = True
    output.append(body + ending)
if changed:
    path.write_bytes(b"".join(output))
PY
}

restore_and_restart_after_apply_failure() {
  local state_directory="$1" reason="$2"
  printf '[db-rename] apply aborted: %s\n' "$reason" >&2
  if ! restore_backups "$state_directory"; then
    fail 'could not restore backed-up systemd or pgpass files; services remain stopped'
    return 1
  fi
  run_root "$SYSTEMCTL" daemon-reload || { fail 'daemon-reload failed while restoring apply state; services remain stopped'; return 1; }
  if ! resume_timescaledb_database "$OLD_DATABASE"; then
    fail "could not re-enable $OLD_DATABASE and restart TimescaleDB workers; services remain stopped"
    return 1
  fi
  remove_active_state "$state_directory"
  restart_previously_active_units "$state_directory"
}

apply_rename() {
  local confirm='' terminate=0 arg old_count new_count db_recovery state_directory timestamp path result monitor_rows
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --confirm) [[ $# -ge 2 ]] || { fail 'apply requires --confirm cea_sensors'; return 2; }; confirm="$2"; shift 2 ;;
      --terminate-remaining) terminate=1; shift ;;
      *) fail "unknown apply argument: $1"; return 2 ;;
    esac
  done
  [[ "$confirm" == "$OLD_DATABASE" ]] || { fail "apply requires the exact confirmation: --confirm $OLD_DATABASE"; return 2; }
  require_commands
  require_integer_setting 'DB_RENAME_SESSION_TIMEOUT_SECONDS' "$SESSION_TIMEOUT" || return 1
  old_count="$(database_presence "$OLD_DATABASE")"
  new_count="$(database_presence "$NEW_DATABASE")"
  if [[ "$old_count" == '0' && "$new_count" == '1' ]]; then
    printf 'Database rename: already renamed; no operation needed.\nDB_RENAME_STATUS=already-renamed\n'
    log_event 'db_rename_apply_already_renamed' 'no database or configuration changes made'
    return 0
  fi
  [[ "$old_count" == '1' && "$new_count" == '0' ]] || { fail "expected old database only; old=$old_count new=$new_count"; return 1; }
  monitor_rows="$(monitoring_env_rows)"
  if [[ "$monitor_rows" == *$'\t<unparseable>'* ]]; then
    fail 'cannot safely determine the monitoring-service DSN database path; no change was made'
    return 1
  fi
  db_recovery="$(query_scalar 'SELECT pg_is_in_recovery()')"
  [[ "$db_recovery" == 'f' ]] || { fail 'refusing to rename a database on a standby'; return 1; }
  if active_state_directory >/dev/null 2>&1; then
    fail 'a previous database rename state is still active; run rollback or inspect it before applying again'
    return 1
  fi
  run_root mkdir -p "$STATE_ROOT"
  run_root chmod 0700 "$STATE_ROOT"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  state_directory="$STATE_ROOT/$timestamp"
  run_root mkdir -m 0700 -p "$state_directory/files"
  run_root chmod 0700 "$state_directory" "$state_directory/files"
  if ! backup_all_files "$state_directory"; then
    restore_backups "$state_directory" || true
    if [[ -n "${DB_RENAME_SYNC_BACKUP_DIR:-}" && -d "${DB_RENAME_SYNC_BACKUP_DIR:-}" ]]; then
      while IFS= read -r path; do
        [[ -f "$path" ]] && run_root cp -a -- "$path" "$SYSTEMD_DIRECTORY/" || true
      done < <(find "$DB_RENAME_SYNC_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type f -print)
    fi
    run_root "$SYSTEMCTL" daemon-reload >/dev/null 2>&1 || true
    return 1
  fi
  printf '%s\n%s\n' "$OLD_DATABASE" "$NEW_DATABASE" | run_root tee "$state_directory/databases.txt" >/dev/null
  write_active_state "$state_directory"

  if ! stop_units "$state_directory"; then
    restore_and_restart_after_apply_failure "$state_directory" 'one or more units could not be stopped' || return 1
    return 1
  fi
  if ! timescaledb_worker_api_available "$OLD_DATABASE"; then
    fail 'TimescaleDB stop/restart background-worker functions are unavailable'
    restore_and_restart_after_apply_failure "$state_directory" 'TimescaleDB worker API unavailable' || return 1
    return 1
  fi
  if ! quiesce_database_for_rename "$OLD_DATABASE" "$terminate" "$SESSION_TIMEOUT"; then
    restore_and_restart_after_apply_failure "$state_directory" 'sessions remained or TimescaleDB workers could not be quiesced' || return 1
    return 1
  fi

  log_event 'db_rename_sql_start' "$OLD_DATABASE to $NEW_DATABASE"
  if ! admin_psql --command="ALTER DATABASE \"$OLD_DATABASE\" RENAME TO \"$NEW_DATABASE\""; then
    restore_and_restart_after_apply_failure "$state_directory" 'ALTER DATABASE failed (possibly a new session raced the check)' || return 1
    return 1
  fi
  if ! set_database_allow_connections "$NEW_DATABASE" true || ! timescaledb_worker_action "$NEW_DATABASE" restart; then
    rollback_state_directory "$state_directory" "$terminate" || { fail 'automatic rollback failed while restarting TimescaleDB workers; services remain stopped'; return 1; }
    restart_previously_active_units "$state_directory" || return 1
    return 1
  fi
  while IFS= read -r path; do
    if [[ -n "$path" ]] && ! rewrite_systemd_file "$path"; then
      rollback_state_directory "$state_directory" "$terminate" || { fail 'automatic rollback failed after unit rewrite error; services remain stopped'; return 1; }
      restart_previously_active_units "$state_directory" || return 1
      return 1
    fi
  done < <(rewrite_candidates)
  if ! rewrite_pgpass_file; then
    rollback_state_directory "$state_directory" "$terminate" || { fail 'automatic rollback failed after pgpass rewrite error; services remain stopped'; return 1; }
    restart_previously_active_units "$state_directory" || return 1
    return 1
  fi
  if monitoring_env_has_old_database && ! rewrite_monitoring_env_file; then
    rollback_state_directory "$state_directory" "$terminate" || { fail 'automatic rollback failed after monitoring DSN rewrite error; services remain stopped'; return 1; }
    restart_previously_active_units "$state_directory" || return 1
    return 1
  fi
  if ! run_root "$SYSTEMCTL" daemon-reload; then
    rollback_state_directory "$state_directory" "$terminate" || { fail 'automatic rollback failed after daemon-reload failure; services remain stopped'; return 1; }
    restart_previously_active_units "$state_directory" || return 1
    return 1
  fi
  printf 'Database rename applied: %s -> %s; services remain stopped for deploy restart.\n' "$OLD_DATABASE" "$NEW_DATABASE"
  printf 'DB_RENAME_STATUS=renamed\n'
  log_event 'db_rename_applied' "$OLD_DATABASE to $NEW_DATABASE; services left stopped"
}

rollback_state_directory() {
  local state_directory="$1" terminate="${2:-0}" old_count new_count
  old_count="$(database_presence "$OLD_DATABASE")"
  new_count="$(database_presence "$NEW_DATABASE")"
  if [[ "$new_count" == '1' && "$old_count" == '0' ]]; then
    if ! run_root test -f "$state_directory/databases.txt"; then
      fail 'rename state is missing its database-name record'
      return 1
    fi
    stop_units "$state_directory" || return 1
    if ! quiesce_database_for_rename "$NEW_DATABASE" "$terminate" "$SESSION_TIMEOUT"; then
      resume_timescaledb_database "$NEW_DATABASE" || fail "could not restore $NEW_DATABASE connections or TimescaleDB workers"
      fail "sessions remain connected to $NEW_DATABASE; rollback left services stopped and backups intact"
      return 1
    fi
    log_event 'db_rename_rollback_sql_start' "$NEW_DATABASE to $OLD_DATABASE"
    if ! admin_psql --command="ALTER DATABASE \"$NEW_DATABASE\" RENAME TO \"$OLD_DATABASE\""; then
      resume_timescaledb_database "$NEW_DATABASE" || fail "could not restore $NEW_DATABASE connections or TimescaleDB workers"
      fail 'could not rename the database back; services remain stopped'
      return 1
    fi
    if ! resume_timescaledb_database "$OLD_DATABASE"; then
      fail "could not re-enable $OLD_DATABASE or restart TimescaleDB workers; services remain stopped"
      return 1
    fi
  elif [[ "$old_count" == '1' && "$new_count" == '0' ]]; then
    printf '[db-rename] database is already restored to %s\n' "$OLD_DATABASE"
    resume_timescaledb_database "$OLD_DATABASE" || { fail "could not restore $OLD_DATABASE connections or TimescaleDB workers"; return 1; }
  else
    fail "cannot safely roll back database names; old=$old_count new=$new_count"
    return 1
  fi
  restore_backups "$state_directory" || { fail 'could not restore backed-up files; services remain stopped'; return 1; }
  run_root "$SYSTEMCTL" daemon-reload || { fail 'daemon-reload failed after restoring files; services remain stopped'; return 1; }
  remove_active_state "$state_directory"
  log_event 'db_rename_rolled_back' "$NEW_DATABASE to $OLD_DATABASE; files restored"
}

rollback_rename() {
  local terminate=0 state_directory old_count new_count
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --terminate-remaining) terminate=1; shift ;;
      *) fail "unknown rollback argument: $1"; return 2 ;;
    esac
  done
  require_commands
  state_directory="$(active_state_directory 2>/dev/null || true)"
  old_count="$(database_presence "$OLD_DATABASE")"
  new_count="$(database_presence "$NEW_DATABASE")"
  if [[ -z "$state_directory" ]]; then
    if [[ "$old_count" == '1' && "$new_count" == '0' ]]; then
      printf 'Database rename rollback: already rolled back; no operation needed.\n'
      log_event 'db_rename_rollback_already_done' 'old database is present and no active rename state exists'
      return 0
    fi
    fail 'no active rename backup state is available; refusing to change database names without file backups'
    return 1
  fi
  rollback_state_directory "$state_directory" "$terminate"
}

systemctl_environment() {
  local unit="$1"
  run_root "$SYSTEMCTL" show -p Environment --value "$unit"
}

parse_database_environment() {
  python3 - "$1" <<'PY'
import json
import shlex
import sys

values = {}
for item in shlex.split(sys.argv[1]):
    key, separator, value = item.partition("=")
    if separator and key in {"POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER"}:
        values[key] = value
print(json.dumps(values))
PY
}

postcheck() {
  require_commands
  local new_count old_count unit raw parsed database host port user actual failed=0 monitor_rows monitor_line monitor_database
  new_count="$(database_presence "$NEW_DATABASE")"
  old_count="$(database_presence "$OLD_DATABASE")"
  [[ "$new_count" == '1' && "$old_count" == '0' ]] || { fail "expected only $NEW_DATABASE to exist; old=$old_count new=$new_count"; return 1; }
  printf 'Database catalog: %s exists; %s does not.\n' "$NEW_DATABASE" "$OLD_DATABASE"
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    raw="$(systemctl_environment "$unit")" || { printf '  %s: unable to read effective Environment\n' "$unit"; failed=1; continue; }
    parsed="$(parse_database_environment "$raw")"
    database="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("POSTGRES_DB", ""))' "$parsed")"
    host="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("POSTGRES_HOST", "localhost"))' "$parsed")"
    port="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("POSTGRES_PORT", "5432"))' "$parsed")"
    user="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("POSTGRES_USER", "cea_user"))' "$parsed")"
    if [[ "$unit" == 'monitoring-service.service' ]]; then
      monitor_rows="$(monitoring_env_rows)"
      if [[ -z "$monitor_rows" ]] || [[ "$(printf '%s\n' "$monitor_rows" | wc -l | tr -d '[:space:]')" != '1' ]]; then
        printf '  %s: MONITORING_POSTGRES_DSN is missing or ambiguous\n' "$unit"
        failed=1
      else
        IFS=$'\t' read -r monitor_line monitor_database <<<"$monitor_rows"
        printf '  %s: effective MONITORING_POSTGRES_DSN database=%s\n' "$unit" "$monitor_database"
        if [[ "$monitor_database" != "$NEW_DATABASE" ]]; then
          failed=1
        elif actual="$(monitoring_dsn_current_database)"; then
          printf '    DSN SELECT current_database()=%s\n' "$actual"
        else
          printf '    DSN SELECT current_database() failed for %s\n' "$unit"
          failed=1
        fi
      fi
    fi
    if [[ -z "$database" ]]; then
      if [[ "$unit" != 'monitoring-service.service' ]]; then
        printf '  %s: POSTGRES_DB is not set\n' "$unit"
      fi
      continue
    fi
    printf '  %s: effective POSTGRES_DB=%s\n' "$unit" "$database"
    if [[ "$database" != "$NEW_DATABASE" ]]; then failed=1; continue; fi
    if actual="$(env -u PGPASSWORD -u PGSERVICE -u PGSERVICEFILE PGPASSFILE="$PGPASS_FILE" \
        "$PSQL" -X --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --host="$host" --port="$port" --username="$user" --dbname="$database" \
        --command='SELECT current_database()' 2>/dev/null | tr -d '[:space:]')" && [[ "$actual" == "$NEW_DATABASE" ]]; then
      printf '    SELECT current_database()=%s\n' "$actual"
    else
      printf '    SELECT current_database() failed for %s (credentials or service DB connectivity unavailable)\n' "$unit"
      failed=1
    fi
  done < <(collect_units "$NEW_DATABASE")
  if ((failed)); then
    log_event 'db_rename_postcheck_failed' 'one or more service database settings or connections do not resolve to sj_cea'
    return 1
  fi
  log_event 'db_rename_postcheck_ok' 'all configured service database connections resolve to sj_cea'
}

main() {
  local action="${1:-preflight}"
  if [[ $# -gt 0 ]]; then shift; fi
  case "$action" in
    preflight) [[ $# -eq 0 ]] || { fail 'preflight takes no arguments'; return 2; }; preflight ;;
    apply) apply_rename "$@" ;;
    rollback) rollback_rename "$@" ;;
    postcheck) [[ $# -eq 0 ]] || { fail 'postcheck takes no arguments'; return 2; }; postcheck ;;
    -h|--help|help) usage ;;
    *) fail "unknown subcommand: $action"; usage >&2; return 2 ;;
  esac
}

main "$@"
