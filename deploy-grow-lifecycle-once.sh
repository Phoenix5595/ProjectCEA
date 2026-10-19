#!/usr/bin/env bash
# SINGLE-USE deployment helper for the grow data lifecycle cutover.
# Delete this script after finalize-deploy.sh --confirm, together with
# Infrastructure/scripts/rename-database.sh and its test.
#
# Usage:
#   SOURCE=/path/to/new/source/tree ./deploy-grow-lifecycle-once.sh --confirm sj_cea
#   SOURCE=/path/to/new/source/tree ./deploy-grow-lifecycle-once.sh rollback
#
# This script intentionally does not edit deploy.sh, rollback-deploy.sh, or
# finalize-deploy.sh. Schema changes are forward-only and remain applied after
# rollback; database rename and the new archive unit are reversed on rollback.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  SOURCE=<new source tree> ./deploy-grow-lifecycle-once.sh --confirm sj_cea
  SOURCE=<new source tree> ./deploy-grow-lifecycle-once.sh rollback
EOF
}

MODE=""
if [[ $# -eq 2 && "$1" == "--confirm" && "$2" == "sj_cea" ]]; then
  MODE="deploy"
elif [[ $# -eq 1 && "$1" == "rollback" ]]; then
  MODE="rollback"
else
  usage
  exit 2
fi

if [[ -z "${SOURCE:-}" || ! -d "$SOURCE" ]]; then
  echo '[grow-lifecycle-once] SOURCE must name the new source tree' >&2
  exit 2
fi
SOURCE="$(cd -- "$SOURCE" && pwd)"
SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
RELEASES="${RELEASES:-${DEPLOY_RELEASES:-$DEPLOY_ROOT/releases}}"
CURRENT="${CURRENT:-${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
STATE_JSON="${STATE_JSON:-${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}}"
ONCE_STATE="${DEPLOY_GROW_ONCE_STATE:-$DEPLOY_STATE_DIR/grow-lifecycle-deploy-once.json}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
CHILD_LOCK_FILE="${DEPLOY_GROW_CHILD_LOCK_FILE:-$DEPLOY_LOCK_FILE.grow-lifecycle-child}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
PACKAGE_LOG="${DEPLOY_PACKAGE_LOG:-$DEPLOY_STATE_DIR/deploy-grow-lifecycle-packages.log}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
ARCHIVE_DATA_DIR="${ARCHIVE_DATA_DIR:-/var/lib/projectcea/archives}"
PROJECT_STATE_DIR="${PROJECT_STATE_DIR:-/var/lib/projectcea}"
NOTES_DATA_DIR="${NOTES_DATA_DIR:-/var/lib/projectcea/notes}"
NOTES_USER="${NOTES_USER:-antoine}"
NOTES_GROUP="${NOTES_GROUP:-antoine}"
API_KEY_ENV_FILE="${API_KEY_ENV_FILE:-/opt/projectcea/shared/env/api_key.env}"
POSTGRES_ENV_FILE="${POSTGRES_ENV_FILE:-/opt/projectcea/shared/env/postgres.env}"
RENAME_SCRIPT="${RENAME_SCRIPT:-$SOURCE/Infrastructure/scripts/rename-database.sh}"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-$SOURCE/deploy.sh}"
ROLLBACK_SCRIPT="${ROLLBACK_SCRIPT:-$SOURCE/rollback-deploy.sh}"
STATE_HELPER="${DEPLOY_STATE_HELPER:-$SOURCE/Infrastructure/scripts/deploy_state.py}"
INSTALL_SUDOERS_SCRIPT="${INSTALL_SUDOERS_SCRIPT:-$SOURCE/Infrastructure/scripts/install-sudoers.sh}"
SERVICE_LIST="${DEPLOY_SERVICE_LIST:-}"

resolve_binary() {
  local configured="$1" resolved
  if [[ "$configured" == */* ]]; then
    [[ -x "$configured" ]] || { echo "Required executable not found: $configured" >&2; return 1; }
    printf '%s\n' "$configured"
    return 0
  fi
  resolved="$(command -v -- "$configured" 2>/dev/null || true)"
  [[ -n "$resolved" ]] || { echo "Required executable not found on PATH: $configured" >&2; return 1; }
  printf '%s\n' "$resolved"
}

SUDO_BIN="$(resolve_binary "${DEPLOY_SUDO:-sudo}")"
SYSTEMCTL_BIN="$(resolve_binary "${DEPLOY_SYSTEMCTL:-systemctl}")"
PYTHON_BIN="$(resolve_binary "${DEPLOY_PYTHON:-python3}")"
CURL_BIN="$(resolve_binary "${DEPLOY_CURL:-curl}")"
if [[ "$MODE" == deploy ]]; then
  RUFF_BIN="$(resolve_binary "${DEPLOY_RUFF:-ruff}")"
  RSYNC_BIN="$(resolve_binary "${DEPLOY_RSYNC:-rsync}")"
  NPM_BIN="$(resolve_binary "${DEPLOY_NPM:-npm}")"
  PSQL_BIN="$(resolve_binary "${DEPLOY_PSQL:-psql}")"
  INSTALL_BIN="$(resolve_binary "${DEPLOY_INSTALL:-install}")"
fi
say() {
  printf '[grow-lifecycle-once] %s\n' "$*"
}

fail() {
  echo "[grow-lifecycle-once] ERROR: $*" >&2
  return 1
}

mkdir_with_sudo() {
  "$SUDO_BIN" mkdir -p "$1"
}

# Use the same separate-stream package log format as deploy.sh. Sensitive
# frontend build arguments are redacted before they are persisted.
run_package_command() {
  local command_name="$1"
  shift
  local stdout_file stderr_file command_status log_status argument line
  stdout_file="$(mktemp)"
  stderr_file="$(mktemp)"
  if "$@" >"$stdout_file" 2>"$stderr_file"; then
    command_status=0
  else
    command_status=$?
  fi
  set +e
  {
    printf '[package-command] command=%s argv=' "$command_name"
    for argument in "$@"; do
      case "$argument" in
        VITE_CEA_API_KEY=*) printf '%q ' 'VITE_CEA_API_KEY=<redacted>' ;;
        *) printf '%q ' "$argument" ;;
      esac
    done
    printf '\n'
    printf '[package-command] command=%s stdout_begin\n' "$command_name"
    while IFS= read -r line || [[ -n "$line" ]]; do
      printf '[package-command] command=%s stdout=%s\n' "$command_name" "$line"
    done < "$stdout_file"
    printf '[package-command] command=%s stdout_end\n' "$command_name"
    printf '[package-command] command=%s stderr_begin\n' "$command_name"
    while IFS= read -r line || [[ -n "$line" ]]; do
      printf '[package-command] command=%s stderr=%s\n' "$command_name" "$line"
    done < "$stderr_file"
    printf '[package-command] command=%s stderr_end\n' "$command_name"
    printf '[package-command] command=%s exit_status=%d\n' "$command_name" "$command_status"
  } | "$SUDO_BIN" tee -a "$PACKAGE_LOG" >/dev/null
  log_status=${PIPESTATUS[1]}
  set -e
  rm -f -- "$stdout_file" "$stderr_file"
  if [[ "$command_status" -ne 0 ]]; then
    return "$command_status"
  fi
  return "$log_status"
}

write_once_state() {
  local phase="$1" target="$2" previous="$3" renamed="$4" schema="$5" unit="$6" compression="$7"
  "$PYTHON_BIN" - "$ONCE_STATE" "$phase" "$target" "$previous" "$renamed" "$schema" "$unit" "$compression" <<'PY'
import json
import os
import pathlib
import sys
from datetime import UTC, datetime

path = pathlib.Path(sys.argv[1])
state = {
    "phase": sys.argv[2],
    "target_release": sys.argv[3],
    "previous_release": sys.argv[4],
    "rename_attempted": sys.argv[5] == "1",
    "schema_attempted": sys.argv[6] == "1",
    "archive_unit_installed": sys.argv[7] == "1",
    "compression_attempted": sys.argv[8] == "1",
    "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, path)
PY
}

read_once_state() {
  "$PYTHON_BIN" - "$ONCE_STATE" "$1" <<'PY'
import json
import pathlib
import sys

try:
    state = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    state = {}
value = state.get(sys.argv[2], "")
if isinstance(value, bool):
    print("1" if value else "0")
else:
    print(value or "")
PY
}

run_state_helper() {
  "$PYTHON_BIN" "$STATE_HELPER" "$@"
}

make_command_shims() {
  COMMAND_SHIMS="$(mktemp -d "${TMPDIR:-/tmp}/grow-lifecycle-once.XXXXXX")"
  printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$SUDO_BIN" > "$COMMAND_SHIMS/sudo"
  cat > "$COMMAND_SHIMS/systemctl" <<'SHIM'
#!/usr/bin/env bash
if [[ "${1:-}" == "restart" && "${DEPLOY_ONCE_SUPPRESS_ARCHIVE_RESTART:-0}" == "1" && "${2%.service}" == "grow-archive-service" ]]; then
  printf 'suppressed new archive unit restart: %s\n' "$*" >> "${DEPLOY_ONCE_EVENT_LOG:-/dev/null}"
  exit 0
fi
if [[ "${1:-}" == "restart" && "${DEPLOY_ONCE_SUPPRESS_PREVIOUS_RESTARTS:-0}" == "1" ]]; then
  active="$(readlink -f "${DEPLOY_CURRENT:-${CURRENT:-}}" 2>/dev/null || true)"
  if [[ "$active" == "${DEPLOY_ONCE_PREVIOUS_RELEASE:-}" ]]; then
    printf 'suppressed previous-release restart: %s\n' "$*" >> "${DEPLOY_ONCE_EVENT_LOG:-/dev/null}"
    exit 0
  fi
fi
exec "${DEPLOY_ONCE_SYSTEMCTL_BIN:?}" "$@"
SHIM
  chmod 0700 "$COMMAND_SHIMS/sudo" "$COMMAND_SHIMS/systemctl"
  for name in python3 curl; do
    local binary
    case "$name" in
      python3) binary="$PYTHON_BIN" ;;
      curl) binary="$CURL_BIN" ;;
    esac
    printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$binary" > "$COMMAND_SHIMS/$name"
    chmod 0700 "$COMMAND_SHIMS/$name"
  done
}

systemd_unit_path() {
  printf '%s/grow-archive-service.service\n' "$SYSTEMD_DIR"
}

restart_previous_services() {
  local previous="$1" service_list_file helper unit
  helper="$SERVICE_LIST"
  if [[ -z "$helper" ]]; then
    helper="$previous/Infrastructure/scripts/service_list.py"
  fi
  [[ -f "$helper" ]] || { echo "[grow-lifecycle-once] missing previous-release service_list.py: $helper" >&2; return 1; }
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    service_list_file="${unit%.service}"
    [[ "$service_list_file" == "grow-archive-service" ]] && continue
    "$SUDO_BIN" "$SYSTEMCTL_BIN" restart "$unit" || return
  done < <("$PYTHON_BIN" "$helper" --list-deploy-managed-units)
}

check_previous_health() {
  local previous="$1" helper unit url code attempt max_attempts
  helper="$SERVICE_LIST"
  if [[ -z "$helper" ]]; then
    helper="$previous/Infrastructure/scripts/service_list.py"
  fi
  max_attempts="${DEPLOY_HEALTH_ATTEMPTS:-90}"
  while IFS=$'\t' read -r unit url; do
    [[ -n "$unit" && -n "$url" ]] || continue
    code="000"
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
      code="$($CURL_BIN -sS -m 10 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
      [[ "$code" == "200" ]] && break
      sleep 1
    done
    if [[ "$code" != "200" ]]; then
      echo "[grow-lifecycle-once] previous release health check failed: $unit ($code)" >&2
      return 1
    fi
  done < <("$PYTHON_BIN" "$helper" --list-health)
}

remove_archive_unit() {
  local unit_path
  unit_path="$(systemd_unit_path)"
  if [[ -e "$unit_path" ]]; then
    # It is safe to stop this newly-installed unit before reversing the DB name.
    "$SUDO_BIN" "$SYSTEMCTL_BIN" stop grow-archive-service.service || true
    "$SUDO_BIN" rm -f -- "$unit_path" || return
  fi
  "$SUDO_BIN" "$SYSTEMCTL_BIN" daemon-reload
}

print_schema_note() {
  echo >&2
  echo '*** IMPORTANT: grow lifecycle schema changes are forward-only and remain applied.' >&2
  echo '*** The previous release is running against the additive schema (including calendar columns and sj_calendar_bind_pending).' >&2
  echo >&2
}
print_compression_note() {
  echo >&2
  echo '*** IMPORTANT: grow source retention/compression policy changes are not reversed by rollback.' >&2
  echo '*** Any policy change committed by grow_data_source_compression.sql remains after rename-back to cea_sensors.' >&2
  echo >&2
}
stop_release_services() {
  local release="$1" helper unit
  helper="$release/Infrastructure/scripts/service_list.py"
  [[ -f "$helper" ]] || { echo "[grow-lifecycle-once] missing service_list.py for $release" >&2; return 1; }
  local load_state
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    # The new release lists grow-archive-service before its unit is installed.
    load_state="$("$SUDO_BIN" "$SYSTEMCTL_BIN" show -p LoadState --value "$unit" 2>/dev/null || true)"
    [[ "$load_state" == 'not-found' ]] && continue
    "$SUDO_BIN" "$SYSTEMCTL_BIN" stop "$unit" || return
  done < <("$PYTHON_BIN" "$helper" --list-deploy-managed-units)
}

apply_schema() {
  local sql_file
  if [[ ! -r "$POSTGRES_ENV_FILE" ]]; then
    echo "[grow-lifecycle-once] PostgreSQL environment file is not readable: $POSTGRES_ENV_FILE" >&2
    return 1
  fi
  # shellcheck disable=SC1090
  source "$POSTGRES_ENV_FILE" || return 1
  if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
    echo "[grow-lifecycle-once] POSTGRES_PASSWORD is empty in $POSTGRES_ENV_FILE" >&2
    return 1
  fi
  for sql_file in \
    "$TARGET/Infrastructure/database/grow_data_lifecycle.sql" \
    "$TARGET/Infrastructure/database/grow_data_lifecycle_expiry_cursor.sql"; do
    schema_attempted=1
    write_once_state schema_applying "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted" || return 1
    # cea_user is a database role only (no OS account); connect over TCP with
    # its password as the invoking operator.
    env "PGPASSWORD=$POSTGRES_PASSWORD" "$PSQL_BIN" \
      -X --set=ON_ERROR_STOP=1 \
      --host="${POSTGRES_HOST:-localhost}" --port="${POSTGRES_PORT:-5432}" \
      --username=cea_user --dbname=sj_cea --file="$sql_file" || return 1
  done
}

restore_after_failed_deploy() {
  local previous="$1" rename_attempted="$2" schema_attempted="$3" unit_installed="$4" target="$5" compression_attempted="$6"
  local cleanup_failed=0 safe_to_restart=1 phase
  say 'restoring the previous release after the one-time window failed'
  if [[ -n "$previous" && -d "$previous" ]]; then
    "$SUDO_BIN" ln -sfn "$previous" "$CURRENT" || { cleanup_failed=1; safe_to_restart=0; }
  else
    echo '[grow-lifecycle-once] previous release is missing; cannot restore the current symlink' >&2
    cleanup_failed=1
    safe_to_restart=0
  fi
  if [[ -d "$target" ]] && ! stop_release_services "$target"; then
    # A failed stop must not leave lights/climate down: the previous release is
    # still restarted below once the rename rollback has restored cea_sensors.
    echo '[grow-lifecycle-once] unable to stop all candidate units; continuing recovery' >&2
    cleanup_failed=1
  fi
  if [[ "$rename_attempted" == 1 ]] && ! "$RENAME_SCRIPT" rollback; then
    echo '[grow-lifecycle-once] database rename rollback failed; previous services will remain stopped' >&2
    cleanup_failed=1
    safe_to_restart=0
  fi
  if [[ "$unit_installed" == 1 || -e "$(systemd_unit_path)" ]]; then
    if ! remove_archive_unit; then
      cleanup_failed=1
      safe_to_restart=0
    fi
  elif ! "$SUDO_BIN" "$SYSTEMCTL_BIN" daemon-reload; then
    cleanup_failed=1
    safe_to_restart=0
  fi
  if [[ "$safe_to_restart" == 1 && -n "$previous" && -d "$previous" ]]; then
    restart_previous_services "$previous" || cleanup_failed=1
    check_previous_health "$previous" || cleanup_failed=1
  fi
  if [[ "$schema_attempted" == 1 ]]; then
    print_schema_note
  fi
  if [[ "$compression_attempted" == 1 ]]; then
    print_compression_note
  fi
  if [[ "$safe_to_restart" == 1 ]]; then
    run_state_helper clear-candidate "$STATE_JSON" >/dev/null 2>&1 || true
  fi
  phase=rolled_back
  [[ "$cleanup_failed" -eq 0 ]] || phase=recovery_required
  write_once_state "$phase" "$target" "$previous" "$rename_attempted" "$schema_attempted" 0 "$compression_attempted" || cleanup_failed=1
  if [[ "$cleanup_failed" -ne 0 ]]; then
    echo '[grow-lifecycle-once] automatic recovery was incomplete; inspect the current symlink, DB rename state, systemd unit, and service health before retrying.' >&2
    return 1
  fi
  return 0
}

acquire_lock() {
  "$SUDO_BIN" mkdir -p "$(dirname -- "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
  "$SUDO_BIN" touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
  "$SUDO_BIN" chmod 0666 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
  exec 9>"$DEPLOY_LOCK_FILE"
  if ! flock -n 9; then
    echo '[grow-lifecycle-once] another deploy, rollback, or finalize is active' >&2
    return 1
  fi
}

make_command_shims
trap '[[ -z "${COMMAND_SHIMS:-}" ]] || rm -rf -- "$COMMAND_SHIMS"' EXIT
acquire_lock

if [[ "$MODE" == "rollback" ]]; then
  if [[ ! -s "$ONCE_STATE" ]]; then
    echo "[grow-lifecycle-once] no single-use deployment state exists at $ONCE_STATE" >&2
    exit 1
  fi
  PHASE="$(read_once_state phase)"
  TARGET="$(read_once_state target_release)"
  PREVIOUS_RELEASE="$(read_once_state previous_release)"
  RENAME_ATTEMPTED="$(read_once_state rename_attempted)"
  SCHEMA_ATTEMPTED="$(read_once_state schema_attempted)"
  UNIT_INSTALLED="$(read_once_state archive_unit_installed)"
  COMPRESSION_ATTEMPTED="$(read_once_state compression_attempted)"
  if [[ "$PHASE" != active ]]; then
    echo "[grow-lifecycle-once] rollback is available only before finalize (state: $PHASE)" >&2
    exit 1
  fi
  CURRENT_TARGET="$(readlink -f "$CURRENT" 2>/dev/null || true)"
  CANDIDATE_PATH="$(run_state_helper read "$STATE_JSON" candidate_release_path)"
  if [[ "$CURRENT_TARGET" != "$TARGET" || "$CANDIDATE_PATH" != "$TARGET" ]]; then
    echo '[grow-lifecycle-once] no active candidate for this one-time release; finalize may already have completed, so refusing rollback' >&2
    exit 1
  fi
  if [[ ! -d "$PREVIOUS_RELEASE" ]]; then
    echo "[grow-lifecycle-once] previous release is missing: $PREVIOUS_RELEASE" >&2
    exit 1
  fi
  [[ -x "$ROLLBACK_SCRIPT" ]] || { echo "[grow-lifecycle-once] rollback script is missing: $ROLLBACK_SCRIPT" >&2; exit 1; }
  [[ -x "$RENAME_SCRIPT" ]] || { echo "[grow-lifecycle-once] rename script is missing: $RENAME_SCRIPT" >&2; exit 1; }
  [[ -r "$STATE_HELPER" ]] || { echo "[grow-lifecycle-once] state helper is missing: $STATE_HELPER" >&2; exit 1; }

  say 'stopping deploy-managed units before reversing the database rename'
  stop_release_services "$TARGET"
  if [[ "$RENAME_ATTEMPTED" == 1 ]]; then
    "$RENAME_SCRIPT" rollback
  fi
  print_schema_note
  if [[ "$COMPRESSION_ATTEMPTED" == 1 ]]; then
    print_compression_note
  fi
  if [[ "$UNIT_INSTALLED" == 1 ]]; then
    remove_archive_unit
  fi
  # rollback-deploy.sh now switches to last-good and restarts only after the
  # DB name is restored and the new archive unit is removed.
  DEPLOY_LOCK_FILE="$CHILD_LOCK_FILE" \
  DEPLOY_ROOT="$DEPLOY_ROOT" DEPLOY_RELEASES="$RELEASES" DEPLOY_CURRENT="$CURRENT" \
  DEPLOY_STATE_JSON="$STATE_JSON" DEPLOY_STATE_DIR="$DEPLOY_STATE_DIR" \
  DEPLOY_LOG="$DEPLOY_LOG" DEPLOY_ONCE_SYSTEMCTL_BIN="$SYSTEMCTL_BIN" \
  DEPLOY_ONCE_SUPPRESS_PREVIOUS_RESTARTS=0 DEPLOY_ONCE_SUPPRESS_ARCHIVE_RESTART=1 DEPLOY_ONCE_PREVIOUS_RELEASE="$PREVIOUS_RELEASE" \
  DEPLOY_CURRENT="$CURRENT" PATH="$COMMAND_SHIMS:$PATH" \
    bash "$ROLLBACK_SCRIPT"
  if [[ "$(readlink -f "$CURRENT" 2>/dev/null || true)" != "$PREVIOUS_RELEASE" ]]; then
    echo '[grow-lifecycle-once] rollback-deploy.sh did not restore the expected previous release' >&2
    exit 1
  fi
  write_once_state rolled_back "$TARGET" "$PREVIOUS_RELEASE" "$RENAME_ATTEMPTED" "$SCHEMA_ATTEMPTED" 0 "$COMPRESSION_ATTEMPTED"
  say "manual rollback complete; current -> $PREVIOUS_RELEASE"
  exit 0
fi

# The one-time sequence is deliberately non-interleavable with normal deploy,
# finalize, and rollback operations.
say 'running read-only database rename preflight'
"$RENAME_SCRIPT" preflight
run_state_helper require-no-candidate "$STATE_JSON" >/dev/null
[[ -L "$CURRENT" ]] || { fail "current symlink is missing: $CURRENT"; exit 1; }
PREVIOUS_RELEASE="$(readlink -f "$CURRENT" 2>/dev/null || true)"
[[ -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE/Infrastructure" ]] || { fail 'current symlink does not resolve to a valid previous release'; exit 1; }
[[ -s "$SOURCE/Infrastructure/grow-archive-service.service" ]] || { fail 'new source tree is missing Infrastructure/grow-archive-service.service'; exit 1; }
[[ -s "$SOURCE/Infrastructure/database/grow_data_lifecycle.sql" ]] || { fail 'new source tree is missing grow_data_lifecycle.sql'; exit 1; }
[[ -s "$SOURCE/Infrastructure/database/grow_data_lifecycle_expiry_cursor.sql" ]] || { fail 'new source tree is missing grow_data_lifecycle_expiry_cursor.sql'; exit 1; }
[[ -s "$SOURCE/Infrastructure/database/grow_data_source_compression.sql" ]] || { fail 'new source tree is missing grow_data_source_compression.sql'; exit 1; }
[[ -r "$STATE_HELPER" ]] || { fail "deploy_state.py is missing: $STATE_HELPER"; exit 1; }
[[ -x "$RENAME_SCRIPT" ]] || { fail "rename script is missing or not executable: $RENAME_SCRIPT"; exit 1; }
[[ -x "$DEPLOY_SCRIPT" ]] || { fail "deploy script is missing or not executable: $DEPLOY_SCRIPT"; exit 1; }
if [[ -e "$(systemd_unit_path)" ]]; then
  fail "$(systemd_unit_path) already exists; this single-use operation only supports installing a previously absent archive unit"
  exit 1
fi
if [[ -e "$ONCE_STATE" ]]; then
  fail "single-use state already exists: $ONCE_STATE"
  exit 1
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
GIT_SHA="$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || printf 'nogit')"
RELEASE_ID="${STAMP}-${GIT_SHA}-grow-lifecycle"
TARGET="${TARGET:-$RELEASES/$RELEASE_ID}"
if [[ -e "$TARGET" ]]; then
  fail "staging target already exists: $TARGET"
  exit 1
fi

say 'staging the new release without changing SOURCE'
mkdir_with_sudo "$TARGET"
RUFF_PATHS=(
  Infrastructure/backend/app
  Infrastructure/automation-service/app
  Infrastructure/can-processor-service/app
  Infrastructure/soil-sensor-service/app
  Infrastructure/onewire-worker-service/app
  Infrastructure/weather-service/app
  Infrastructure/shared
  Infrastructure/scripts
)
(
  cd "$SOURCE"
  run_package_command ruff-check "$RUFF_BIN" check "${RUFF_PATHS[@]}"
)
"$SUDO_BIN" "$RSYNC_BIN" -a --delete --exclude '.env.production' "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"
"$SUDO_BIN" chown -R root:root "$TARGET"

for service in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service monitoring-service grow-archive-service; do
  service_dir="$TARGET/Infrastructure/$service"
  [[ -f "$service_dir/requirements.txt" ]] || continue
  venv="$service_dir/.venv"
  say "building Python environment: $service"
  "$SUDO_BIN" rm -rf -- "$venv"
  "$SUDO_BIN" "$PYTHON_BIN" -m venv "$venv"
  pip_binary="${DEPLOY_PIP:-$venv/bin/pip}"
  run_package_command "$service-pip-upgrade" "$SUDO_BIN" "$pip_binary" install -q --upgrade pip
  run_package_command "$service-build-tools" "$SUDO_BIN" "$pip_binary" install -q --upgrade 'setuptools>=69' 'packaging>=24.2'
  run_package_command "$service-requirements" "$SUDO_BIN" "$pip_binary" install -q -r "$service_dir/requirements.txt"
done

frontend_dir="$TARGET/Infrastructure/frontend"
[[ -d "$frontend_dir" ]] || { fail 'staged frontend directory is missing'; exit 1; }
"$SUDO_BIN" rm -rf -- "$frontend_dir/dist"
(
  cd "$frontend_dir"
  run_package_command npm-ci "$SUDO_BIN" env CI=true "$NPM_BIN" ci --silent --no-audit --no-fund
)
[[ -r "$API_KEY_ENV_FILE" ]] || { fail "API key environment file is not readable: $API_KEY_ENV_FILE"; exit 1; }
(
  set -a
  # shellcheck disable=SC1090
  source "$API_KEY_ENV_FILE"
  set +a
  [[ -n "${CEA_API_KEY:-}" ]] || { fail "CEA_API_KEY is empty in $API_KEY_ENV_FILE"; exit 1; }
  cd "$frontend_dir"
  run_package_command npm-build "$SUDO_BIN" env CI=true "VITE_CEA_API_KEY=$CEA_API_KEY" "$NPM_BIN" run build
)

mkdir_with_sudo "$NOTES_DATA_DIR"
"$SUDO_BIN" chown -R "${NOTES_USER}:${NOTES_GROUP}" "$PROJECT_STATE_DIR" 2>/dev/null || true
[[ -x "$INSTALL_SUDOERS_SCRIPT" ]] || { fail "sudoers installer is missing or not executable: $INSTALL_SUDOERS_SCRIPT"; exit 1; }
"$INSTALL_SUDOERS_SCRIPT"

for required_file in \
  Infrastructure/services.yaml \
  Infrastructure/can-processor-service/setup_can.sh \
  Infrastructure/grow-archive-service/requirements.txt \
  Infrastructure/frontend/package.json \
  Infrastructure/frontend/dist/index.html \
  Infrastructure/grow-archive-service.service \
  Infrastructure/database/grow_data_lifecycle.sql \
  Infrastructure/database/grow_data_lifecycle_expiry_cursor.sql \
  Infrastructure/database/grow_data_source_compression.sql; do
  [[ -s "$TARGET/$required_file" ]] || { fail "staged release is incomplete: $TARGET/$required_file"; exit 1; }
done

unit_path="$(systemd_unit_path)"
rename_attempted=1
schema_attempted=0
unit_installed=0
compression_attempted=0
write_once_state renaming "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"
say 'entering the downtime window and applying the database rename'
if ! "$RENAME_SCRIPT" apply --confirm cea_sensors; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi

if ! write_once_state renamed "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
if ! apply_schema; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
schema_attempted=1
if ! write_once_state schema_applied "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
compression_attempted=1
if ! write_once_state compression_applying "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
if ! env "PGPASSWORD=$POSTGRES_PASSWORD" "$PSQL_BIN" \
  -X --set=ON_ERROR_STOP=1 \
  --host="${POSTGRES_HOST:-localhost}" --port="${POSTGRES_PORT:-5432}" \
  --username=cea_user --dbname=sj_cea \
  --file="$TARGET/Infrastructure/database/grow_data_source_compression.sql"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
if ! write_once_state compression_applied "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi

say 'installing the new grow archive systemd unit'
if ! mkdir_with_sudo "$SYSTEMD_DIR" || ! "$SUDO_BIN" "$INSTALL_BIN" -o root -g root -m 0644 \
  "$TARGET/Infrastructure/grow-archive-service.service" "$unit_path"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
unit_installed=1
if ! write_once_state unit_installed "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
if ! mkdir_with_sudo "$ARCHIVE_DATA_DIR" || ! "$SUDO_BIN" "$SYSTEMCTL_BIN" daemon-reload; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi

say 'switching to the staged release with deploy.sh'
set +e
DEPLOY_SKIP_STAGING=1 TARGET="$TARGET" SOURCE="$SOURCE" \
DEPLOY_ROOT="$DEPLOY_ROOT" DEPLOY_RELEASES="$RELEASES" DEPLOY_CURRENT="$CURRENT" \
DEPLOY_STATE_DIR="$DEPLOY_STATE_DIR" DEPLOY_STATE_JSON="$STATE_JSON" \
DEPLOY_LOCK_FILE="$CHILD_LOCK_FILE" DEPLOY_LOG="$DEPLOY_LOG" \
DEPLOY_ONCE_SUPPRESS_PREVIOUS_RESTARTS=1 DEPLOY_ONCE_PREVIOUS_RELEASE="$PREVIOUS_RELEASE" \
DEPLOY_ONCE_SYSTEMCTL_BIN="$SYSTEMCTL_BIN" DEPLOY_ONCE_EVENT_LOG="${DEPLOY_ONCE_EVENT_LOG:-/dev/null}" \
PATH="$COMMAND_SHIMS:$PATH" bash "$DEPLOY_SCRIPT"
deploy_status=$?
set -e
if [[ "$deploy_status" -ne 0 ]]; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  echo "[grow-lifecycle-once] deploy.sh failed with status $deploy_status" >&2
  exit "$deploy_status"
fi

if ! write_once_state active "$TARGET" "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$compression_attempted"; then
  restore_after_failed_deploy "$PREVIOUS_RELEASE" "$rename_attempted" "$schema_attempted" "$unit_installed" "$TARGET" "$compression_attempted" || true
  exit 1
fi
say "candidate is active at $TARGET; finalize with $SOURCE/finalize-deploy.sh --confirm"
say "before finalization, rollback with SOURCE=$SOURCE $SCRIPT_DIRECTORY/deploy-grow-lifecycle-once.sh rollback"
