#!/usr/bin/env bash
# Deploy Infrastructure/ to /opt/projectcea/releases/<id> and switch current symlink.
# Active-candidate semantics:
#   - A successful deploy records the new release as candidate without overwriting last_good.
#   - A failed health check immediately restores the previous active release.
#   - finalize-deploy.sh --confirm promotes the candidate to last-good.
#   - rollback-deploy.sh (no arg) during an active candidate returns to last-good and rejects it.
# NDJSON log: /var/lib/projectcea/deploy.log

set -euo pipefail

SOURCE="${DEPLOY_GIT_REPO:-${SOURCE:-/home/antoine/ProjectCEA}}"

VERSION_TIER=""
COMMIT_REV="main"
COMMIT_SEEN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --commit)
      if [[ $# -lt 2 || -z "$2" || "$COMMIT_SEEN" -ne 0 ]]; then
        echo "Usage: $0 [--commit <rev>] [major|minor|patch]" >&2
        exit 2
      fi
      COMMIT_REV="$2"
      COMMIT_SEEN=1
      shift 2
      ;;
    major|minor|patch)
      if [[ -n "$VERSION_TIER" ]]; then
        echo "Usage: $0 [--commit <rev>] [major|minor|patch]" >&2
        exit 2
      fi
      VERSION_TIER="$1"
      shift
      ;;
    *)
      echo "Usage: $0 [--commit <rev>] [major|minor|patch]" >&2
      exit 2
      ;;
  esac
done

# Overridable paths for sandbox testing and non-standard layouts.
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
DEPLOY_PACKAGE_LOG="${DEPLOY_PACKAGE_LOG:-$DEPLOY_STATE_DIR/deploy-packages.log}"
RELEASES="${DEPLOY_RELEASES:-$DEPLOY_ROOT/releases}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE_JSON="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"
MAX_RELEASES="${DEPLOY_MAX_RELEASES:-10}"
DEPLOY_SKIP_STAGING="${DEPLOY_SKIP_STAGING:-0}"
API_KEY_ENV_FILE="${API_KEY_ENV_FILE:-/opt/projectcea/shared/env/api_key.env}"
POSTGRES_ENV_FILE="${DEPLOY_POSTGRES_ENV_FILE:-/opt/projectcea/shared/env/postgres.env}"
MIGRATION_REHEARSAL_DIR="${DEPLOY_REHEARSAL_DIR:-/var/lib/projectcea/migration-rehearsals}"
MIGRATION_BACKUP_DIR="${DEPLOY_BACKUP_DIR:-/var/lib/projectcea/backups}"
SYSTEMD_BACKUP_ROOT="${DEPLOY_SYSTEMD_BACKUP_ROOT:-/var/lib/projectcea/systemd-backup}"
SYSTEMD_DIR="${DEPLOY_SYSTEMD_DIR:-/etc/systemd/system}"
CADDY_FILE="${DEPLOY_CADDY_FILE:-/etc/caddy/Caddyfile}"
CADDY_HEALTH_URL="${DEPLOY_CADDY_HEALTH_URL:-http://127.0.0.1:8080/}"
CADDY_BIN="${DEPLOY_CADDY_BIN:-caddy}"
DB_MIGRATE_OVERRIDE="${DEPLOY_DB_MIGRATE:-}"
PSQL_BIN="${DEPLOY_PSQL:-${DB_MIGRATE_PSQL:-psql}}"
PG_DUMP_BIN="${DEPLOY_PG_DUMP:-pg_dump}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/var/cache/projectcea/pip}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"
SERVICE_LIST=""
FULL_SHA=""
RELEASE_ID=""
TARGET="${TARGET:-}"
PREVIOUS_RELEASE=""
SYSTEMD_BACKUP_DIR=""
STAGE=""

cleanup_stage() {
  if [[ -n "${STAGE:-}" && -d "$STAGE" ]]; then
    rm -rf -- "$STAGE"
  fi
}

trap cleanup_stage EXIT

# Concurrency guard: one deploy, finalize, or rollback at a time.
sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chown antoine:antoine "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 0644 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -n 9; then
  echo "[deploy] another deploy/rollback/finalize is already running; aborting" >&2
  exit 1
fi

export SOURCE

log_event() {
  # Args: event [detail [service [http_code]]]
  export DLOG_EVENT="$1"
  export DLOG_DETAIL="${2:-}"
  export DLOG_SERVICE="${3:-}"
  export DLOG_HTTP="${4:-}"
  local line
  line=$(python3 "$EMIT" | python3 "$JSON_LINE")
  echo "$line" | sudo tee -a "$DEPLOY_LOG" >/dev/null
  echo "$line"
}

run_package_command() {
  local command_name="$1"
  shift
  local stdout_file="" stderr_file="" command_status log_status argument
  local previous_exit_trap previous_hup_trap previous_int_trap previous_term_trap
  local restore_traps exit_cleanup_trap
  declare -g __projectcea_package_stdout_file=""
  declare -g __projectcea_package_stderr_file=""

  previous_exit_trap="$(trap -p EXIT)"
  previous_hup_trap="$(trap -p HUP)"
  previous_int_trap="$(trap -p INT)"
  previous_term_trap="$(trap -p TERM)"
  restore_traps='
    if [[ -n "${previous_exit_trap:-}" ]]; then eval "$previous_exit_trap"; else trap - EXIT; fi
    if [[ -n "${previous_hup_trap:-}" ]]; then eval "$previous_hup_trap"; else trap - HUP; fi
    if [[ -n "${previous_int_trap:-}" ]]; then eval "$previous_int_trap"; else trap - INT; fi
    if [[ -n "${previous_term_trap:-}" ]]; then eval "$previous_term_trap"; else trap - TERM; fi
  '
  exit_cleanup_trap='
    if [[ -n "${__projectcea_package_stdout_file:-}" ]]; then rm -f -- "$__projectcea_package_stdout_file"; fi
    if [[ -n "${__projectcea_package_stderr_file:-}" ]]; then rm -f -- "$__projectcea_package_stderr_file"; fi
  '
  trap "$exit_cleanup_trap" EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  stdout_file="$(mktemp)"
  __projectcea_package_stdout_file="$stdout_file"
  stderr_file="$(mktemp)"
  __projectcea_package_stderr_file="$stderr_file"

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
    sed "s/^/[package-command] command=$command_name stdout=/" "$stdout_file"
    printf '[package-command] command=%s stdout_end\n' "$command_name"
    printf '[package-command] command=%s stderr_begin\n' "$command_name"
    sed "s/^/[package-command] command=$command_name stderr=/" "$stderr_file"
    printf '[package-command] command=%s stderr_end\n' "$command_name"
    printf '[package-command] command=%s exit_status=%d\n' "$command_name" "$command_status"
  } | sudo tee -a "$DEPLOY_PACKAGE_LOG"
  log_status=${PIPESTATUS[1]}
  set -e

  rm -f "$stdout_file" "$stderr_file"
  unset __projectcea_package_stdout_file __projectcea_package_stderr_file
  eval "$restore_traps"
  if [[ "$command_status" -ne 0 ]]; then
    return "$command_status"
  fi
  return "$log_status"
}

systemd_load_state() {
  local unit="$1" state
  if state="$(sudo systemctl show -p LoadState --value "$unit" 2>/dev/null)"; then
    printf '%s\n' "$state"
  else
    printf 'unknown\n'
  fi
}

stop_services_reverse() {
  local service_list="$1"
  local rc=0 state unit index
  local -a units=()
  mapfile -t units < <(python3 "$service_list" --list-deploy-managed-units)
  for ((index=${#units[@]} - 1; index >= 0; index--)); do
    unit="${units[$index]}"
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      log_event "service_skipped" "load_state_not_found" "$unit" >/dev/null
      continue
    fi
    if ! sudo systemctl stop "$unit"; then
      log_event "service_stop_failed" "" "$unit" >/dev/null
      rc=1
    fi
  done
  return "$rc"
}

restart_services() {
  local service_list="${1:-$SERVICE_LIST}"
  local rc=0 state unit
  sudo systemctl daemon-reload || rc=1
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      log_event "service_skipped" "load_state_not_found" "$unit" >/dev/null
      continue
    fi
    if ! sudo systemctl restart "$unit"; then
      log_event "service_restart_failed" "" "$unit" >/dev/null
      rc=1
    fi
  done < <(python3 "$service_list" --list-deploy-managed-units)
  return "$rc"
}

run_health_checks() {
  local service_list="$1"
  local _had_e=0 rc=0 kind flag unit url code attempt body
  [[ $- == *e* ]] && _had_e=1
  set +e
  sleep 2
  local bodydir="/tmp/cea_health_$$"
  local max_attempts="${DEPLOY_HEALTH_ATTEMPTS:-90}"
  mkdir -p "$bodydir"
  for kind in health ready; do
    flag="--list-$kind"
    while IFS=$'\t' read -r unit url; do
      [[ -n "$unit" && -n "$url" ]] || continue
      code="000"
      for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        : > "$bodydir/${kind}_${unit}.body"
        if code=$(curl -sS -m 10 -o "$bodydir/${kind}_${unit}.body" -w "%{http_code}" "$url" 2>/dev/null); then
          :
        else
          code="000"
        fi
        [[ -n "$code" ]] || code="000"
        if [[ "$code" == "200" ]]; then
          log_event "${kind}_ok" "" "$unit" "$code" >/dev/null
          break
        fi
        if [[ "$attempt" -lt "$max_attempts" ]]; then sleep 1; fi
      done
      if [[ "$code" != "200" ]]; then
        body=$(head -c 400 "$bodydir/${kind}_${unit}.body" 2>/dev/null | tr '\n' ' ' || true)
        log_event "${kind}_fail" "$body" "$unit" "$code" >/dev/null
        rc=1
      fi
    done < <(python3 "$service_list" "$flag")
  done
  rm -rf "$bodydir"
  if [[ $_had_e -eq 1 ]]; then set -e; fi
  return "$rc"
}

restore_systemd_backup() {
  local backup_dir="$1"
  local relative backed_up_file
  [[ -d "$backup_dir" ]] || return 0
  if ! sudo mkdir -p "$SYSTEMD_DIR"; then
    return 1
  fi
  if [[ -f "$backup_dir/created.txt" ]]; then
    if [[ ! -r "$backup_dir/created.txt" ]]; then
      return 1
    fi
    while IFS= read -r relative; do
      [[ -n "$relative" && "$relative" != /* && "$relative" != *".."* ]] || continue
      if ! sudo rm -f -- "$SYSTEMD_DIR/$relative"; then
        return 1
      fi
    done < "$backup_dir/created.txt"
  fi
  if ! backed_up_file="$(find "$backup_dir" -type f ! -name created.txt ! -name Caddyfile ! -name Caddyfile.created -print -quit)"; then
    return 1
  fi
  if [[ -n "$backed_up_file" ]] && ! sudo rsync -a --exclude=created.txt --exclude=Caddyfile --exclude=Caddyfile.created "$backup_dir/" "$SYSTEMD_DIR/"; then
    return 1
  fi
  sudo systemctl daemon-reload || return 1
  return 0
}

restore_caddyfile() {
  local backup_dir="$1"
  if [[ -f "$backup_dir/Caddyfile" ]]; then
    sudo install -d -o root -g root -m 0755 "$(dirname "$CADDY_FILE")" || return 1
    sudo install -m 0644 -o root -g root "$backup_dir/Caddyfile" "$CADDY_FILE" || return 1
    sudo systemctl restart caddy || return 1
  elif [[ -f "$backup_dir/Caddyfile.created" ]]; then
    sudo rm -f -- "$CADDY_FILE" || return 1
    sudo systemctl restart caddy || return 1
  fi
  return 0
}

load_postgres_environment() {
  local name
  local -A original=()
  for name in POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    if [[ -v "$name" ]]; then original["$name"]="${!name}"; fi
  done
  if [[ -r "$POSTGRES_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$POSTGRES_ENV_FILE"
    set +a
  fi
  for name in "${!original[@]}"; do
    printf -v "$name" '%s' "${original[$name]}"
  done
  : "${POSTGRES_HOST:=127.0.0.1}"
  : "${POSTGRES_PORT:=5432}"
  : "${POSTGRES_DB:=sj_cea}"
  : "${POSTGRES_USER:=cea_user}"
  export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER
  if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    export PGPASSWORD="$POSTGRES_PASSWORD"
  fi
}

db_migrate() {
  local release_root="$1"
  shift
  local runner="${DB_MIGRATE_OVERRIDE:-$release_root/Infrastructure/scripts/db_migrate.py}"
  python3 "$runner" --release "$release_root" "$@"
}

current_alembic_heads() {
  local exists rows
  local -a psql_args=(
    -X --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align
    --host "$POSTGRES_HOST" --port "$POSTGRES_PORT"
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
  )
  if ! exists=$("$PSQL_BIN" "${psql_args[@]}" --command="SELECT to_regclass('public.alembic_version') IS NOT NULL"); then
    return 1
  fi
  exists="$(printf '%s' "$exists" | tr -d '[:space:]')"
  if [[ "$exists" == "f" ]]; then
    printf '[]\n'
    return 0
  fi
  [[ "$exists" == "t" ]] || return 1
  rows=$("$PSQL_BIN" "${psql_args[@]}" --command="SELECT version_num FROM public.alembic_version ORDER BY 1") || return 1
  printf '%s\n' "$rows" | python3 -c 'import json, sys; print(json.dumps(sorted(line.strip() for line in sys.stdin if line.strip())))'
}

manifest_schema_metadata() {
  python3 - "$1/deploy_manifest.json" "${2:-current}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
section = sys.argv[2]
manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
metadata = manifest if section == "current" else manifest.get("previous_schema")
if (
    not isinstance(metadata, dict)
    or "migration_head" not in metadata
    or "alembic_heads" not in metadata
    or not isinstance(metadata["alembic_heads"], list)
):
    print(json.dumps({"known": False}, separators=(",", ":")))
else:
    print(
        json.dumps(
            {
                "known": True,
                "migration_head": metadata["migration_head"],
                "alembic_heads": sorted(str(head) for head in metadata["alembic_heads"]),
            },
            separators=(",", ":"),
        )
    )
PY
}

schema_metadata_known() {
  python3 - "$1" <<'PY'
import json
import sys

print("true" if json.loads(sys.argv[1]).get("known") is True else "false")
PY
}

schema_metadata_head() {
  python3 - "$1" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])["migration_head"]
print("null" if value is None else str(value))
PY
}

schema_metadata_alembic_heads() {
  python3 - "$1" <<'PY'
import json
import sys

print(json.dumps(sorted(json.loads(sys.argv[1])["alembic_heads"])))
PY
}

alembic_heads_equal() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

raise SystemExit(0 if sorted(json.loads(sys.argv[1])) == sorted(json.loads(sys.argv[2])) else 1)
PY
}

migration_head_relation() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

current, target = sys.argv[1:]
current = None if current in ("", "null", "None") else current
target = None if target in ("", "null", "None") else target
if current == target:
    print("equal")
else:
    current_version = re.match(r"^(\d+)", current or "")
    target_version = re.match(r"^(\d+)", target or "")
    if current is None:
        print("older")
    elif target is None:
        print("newer")
    elif not current_version or not target_version:
        print("incomparable")
    elif int(current_version.group(1)) > int(target_version.group(1)):
        print("newer")
    else:
        print("older")
PY
}

rollback_previous() {
  local target_release="${PREVIOUS_RELEASE:-}"
  local migration_head target_head relation target_alembic current_alembic db_release backup_dir restore_to target_metadata metadata_known
  if [[ -z "$target_release" || ! -d "$target_release/Infrastructure" ]]; then
    log_event "rollback_auto_fail" "no_previous_release_or_missing_dir" >/dev/null
    return 1
  fi
  db_release="$TARGET"
  load_postgres_environment
  if ! migration_head="$(db_migrate "$db_release" head)"; then
    log_event "rollback_auto_fail" "migration_head_unavailable" >/dev/null
    return 1
  fi
  target_metadata="$(manifest_schema_metadata "$target_release" current)" || return 1
  metadata_known="$(schema_metadata_known "$target_metadata")" || return 1
  if [[ "$metadata_known" != "true" ]]; then
    if [[ "${PREDEPLOY_MIGRATION_HEAD+x}" == x && "${PREDEPLOY_ALEMBIC_HEADS+x}" == x ]]; then
      target_head="$PREDEPLOY_MIGRATION_HEAD"
      target_alembic="$PREDEPLOY_ALEMBIC_HEADS"
      metadata_known="true"
    else
      target_metadata="$(manifest_schema_metadata "$db_release" previous)" || return 1
      metadata_known="$(schema_metadata_known "$target_metadata")" || return 1
      if [[ "$metadata_known" == "true" ]]; then
        target_head="$(schema_metadata_head "$target_metadata")" || return 1
        target_alembic="$(schema_metadata_alembic_heads "$target_metadata")" || return 1
      fi
    fi
  else
    target_head="$(schema_metadata_head "$target_metadata")" || return 1
    target_alembic="$(schema_metadata_alembic_heads "$target_metadata")" || return 1
  fi
  if [[ "$metadata_known" != "true" ]]; then
    log_event "rollback_auto_blocked" "schema_metadata_unknown" >/dev/null
    echo "rollback_blocked:schema_metadata_unknown" >&2
    return 1
  fi
  if ! current_alembic="$(current_alembic_heads)" || ! alembic_heads_equal "$target_alembic" "$current_alembic"; then
    backup_dir="$MIGRATION_BACKUP_DIR/pre-$(basename "$db_release").dump"
    log_event "rollback_auto_blocked" "alembic_heads_mismatch" >/dev/null
    echo "[deploy] Alembic heads differ; restore with: sudo -u postgres pg_restore -d $POSTGRES_DB $backup_dir" >&2
    return 1
  fi
  relation="$(migration_head_relation "$migration_head" "$target_head")"
  if [[ "$relation" == "incomparable" ]]; then
    log_event "rollback_auto_blocked" "migration_heads_incomparable" >/dev/null
    echo "rollback_blocked:migration_heads_incomparable" >&2
    return 1
  fi
  if ! stop_services_reverse "$target_release/Infrastructure/scripts/service_list.py"; then
    log_event "rollback_auto_fail" "stop_services_failed" >/dev/null
    return 1
  fi
  if [[ "$relation" == "newer" ]]; then
    restore_to="$target_head"
    [[ "$restore_to" != "null" ]] || restore_to="none"
    if ! db_migrate "$db_release" down --to "$restore_to"; then
      log_event "rollback_auto_fail" "down_migrations_failed" >/dev/null
      return 1
    fi
  fi
  backup_dir="$SYSTEMD_BACKUP_ROOT/$(basename "$db_release")"
  restore_systemd_backup "$backup_dir" || return 1
  restore_caddyfile "$backup_dir" || return 1
  sudo ln -sfn "$target_release" "$CURRENT_SYMLINK" || return 1
  log_event "rollback_auto_symlink" "reverted_to_previous" >/dev/null
  SERVICE_LIST="$target_release/Infrastructure/scripts/service_list.py"
  restart_services "$SERVICE_LIST" || return 1
  log_event "rollback_auto_done" "services_restarted_after_revert" >/dev/null
  return 0
}

# A version bump mutates the checkout. It must be committed separately so the
# exact-commit release archive can include it.
sudo mkdir -p "$DEPLOY_STATE_DIR"
if [[ -n "$VERSION_TIER" ]]; then
  echo "[deploy] bumping frontend $VERSION_TIER version in $SOURCE/Infrastructure/frontend"
  if ! (cd "$SOURCE/Infrastructure/frontend" && run_package_command "npm-release-$VERSION_TIER" npm run "release:$VERSION_TIER"); then
    exit 1
  fi
  echo "commit frontend/package.json and package-lock.json, then rerun with --commit" >&2
  exit 1
fi

if ! FULL_SHA="$(git -C "$SOURCE" rev-parse --verify "${COMMIT_REV}^{commit}")"; then
  echo "[deploy] cannot resolve commit: $COMMIT_REV" >&2
  exit 1
fi
if [[ ! "$FULL_SHA" =~ ^[[:xdigit:]]{40}$ ]]; then
  echo "[deploy] git returned an invalid commit SHA for $COMMIT_REV" >&2
  exit 1
fi
RELEASE_ID="$(date +%Y%m%d-%H%M%S)-${FULL_SHA:0:7}"
TARGET="${TARGET:-$RELEASES/$RELEASE_ID}"
if [[ "$TARGET" != "$RELEASES/$RELEASE_ID" ]]; then
  RELEASE_ID="$(basename "$TARGET")"
fi
SYSTEMD_BACKUP_DIR="$SYSTEMD_BACKUP_ROOT/$RELEASE_ID"
if [[ -L "$CURRENT_SYMLINK" || -e "$CURRENT_SYMLINK" ]]; then
  PREVIOUS_RELEASE="$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)"
fi
export RELEASE_ID TARGET PREVIOUS_RELEASE SOURCE FULL_SHA

dirty_status="$(git -C "$SOURCE" status --porcelain=v1 -- Infrastructure pyproject.toml deploy.sh rollback-deploy.sh finalize-deploy.sh)" || {
  log_event "deploy_blocked" "dirty_tracked_inputs"
  echo "deploy_blocked:dirty_tracked_inputs" >&2
  exit 1
}
dirty_tracked=0
while IFS= read -r status_line; do
  [[ -n "$status_line" ]] || continue
  [[ "$status_line" == "??"* ]] && continue
  dirty_tracked=1
done <<< "$dirty_status"
if [[ "$dirty_tracked" -ne 0 ]]; then
  log_event "deploy_blocked" "dirty_tracked_inputs"
  echo "deploy_blocked:dirty_tracked_inputs" >&2
  exit 1
fi

# Reconcile a stale state file only when no candidate exists and the current
# symlink still names a complete release.
if [[ -n "$PREVIOUS_RELEASE" ]]; then
  if [[ ! -d "$PREVIOUS_RELEASE/Infrastructure" ]]; then
    log_event "deploy_blocked" "previous_release_missing"
    echo "[deploy] current release is missing; restore a valid release before deploying" >&2
    exit 1
  fi
  python3 "$STATE_HELPER" reconcile "$STATE_JSON" "$PREVIOUS_RELEASE" >/dev/null
fi
if ! python3 "$STATE_HELPER" require-no-candidate "$STATE_JSON" >/dev/null; then
  log_event "deploy_blocked" "candidate_already_active"
  echo "=== DEPLOY BLOCKED: a candidate release is already active; finalize or roll it back first ===" >&2
  exit 1
fi

echo "=== Deploying release: $RELEASE_ID ($FULL_SHA) ==="
log_event "deploy_start" ""

if [[ "$DEPLOY_SKIP_STAGING" != "1" ]]; then
  echo "[1/7] Exporting exact commit $FULL_SHA..."
  STAGE="$(mktemp -d)"
  if ! git -C "$SOURCE" archive "$FULL_SHA" Infrastructure pyproject.toml | tar -x -C "$STAGE"; then
    log_event "deploy_blocked" "git_archive_failed"
    echo "[deploy] cannot export Infrastructure from $FULL_SHA" >&2
    exit 1
  fi
  sudo mkdir -p "$TARGET"
  sudo rsync -a --delete "$STAGE/Infrastructure/" "$TARGET/Infrastructure/"
  sudo chown -R root:root "$TARGET"
  sudo install -m 0644 -o root -g root "$STAGE/pyproject.toml" "$TARGET/pyproject.toml"
fi

SERVICE_LIST="$TARGET/Infrastructure/scripts/service_list.py"
if [[ ! -s "$SERVICE_LIST" ]]; then
  log_event "deploy_blocked" "staged_release_incomplete:Infrastructure/scripts/service_list.py"
  echo "[deploy] staged service_list.py is missing: $SERVICE_LIST" >&2
  exit 1
fi

echo "[2/7] Running read-only Ruff checks inside the release..."
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
# The staged release is root-owned and has no git metadata: skip the cache, and
# exclude the Python patterns .gitignore lists (ruff honours .gitignore only inside
# a git checkout), so the lint scope equals the checkout's.
RUFF_OPTS=(--no-cache --config 'extend-exclude = ["test_*.py", "*_test.py", "debug_*.py", "*_debug.py"]')
if ! (cd "$TARGET" && ruff check "${RUFF_OPTS[@]}" "${RUFF_PATHS[@]}" && ruff format --check "${RUFF_OPTS[@]}" "${RUFF_PATHS[@]}"); then
  log_event "deploy_blocked" "lint_failed"
  echo "deploy_blocked:lint_failed" >&2
  exit 1
fi

if [[ "$DEPLOY_SKIP_STAGING" != "1" ]]; then
  echo "[3/7] Building Python virtual environments..."
  sudo mkdir -p "$PIP_CACHE_DIR"
  for svc in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service monitoring-service grow-archive-service; do
    SVC_DIR="$TARGET/Infrastructure/$svc"
    if [[ -f "$SVC_DIR/requirements.txt" ]]; then
      echo "  - $svc"
      sudo rm -rf "$SVC_DIR/.venv"
      sudo python3 -m venv "$SVC_DIR/.venv"
      run_package_command "$svc-pip-upgrade" sudo env "PIP_CACHE_DIR=$PIP_CACHE_DIR" "$SVC_DIR/.venv/bin/pip" install -q --upgrade pip
      run_package_command "$svc-build-tools" sudo env "PIP_CACHE_DIR=$PIP_CACHE_DIR" "$SVC_DIR/.venv/bin/pip" install -q --upgrade "setuptools>=69" "packaging>=24.2"
      run_package_command "$svc-requirements" sudo env "PIP_CACHE_DIR=$PIP_CACHE_DIR" "$SVC_DIR/.venv/bin/pip" install -q -r "$SVC_DIR/requirements.txt"
    fi
  done

  echo "[4/7] Building frontend..."
  cd "$TARGET/Infrastructure/frontend"
  sudo rm -rf dist/
  run_package_command "npm-ci" sudo env CI=true npm ci --silent --no-audit --no-fund
  if [[ ! -r "$API_KEY_ENV_FILE" ]]; then
    echo "[deploy] API key environment file is not readable: $API_KEY_ENV_FILE" >&2
    exit 1
  fi
  (
    set -a
    # shellcheck disable=SC1090
    source "$API_KEY_ENV_FILE"
    set +a
    if [[ -z "${CEA_API_KEY:-}" ]]; then
      echo "[deploy] CEA_API_KEY is empty in $API_KEY_ENV_FILE" >&2
      exit 1
    fi
    run_package_command "npm-build" sudo env CI=true "VITE_CEA_API_KEY=$CEA_API_KEY" npm run build
  )

  echo "[5/7] Ensuring data directories..."
  NOTES_DIR="${NOTES_DATA_DIR:-/var/lib/projectcea/notes}"
  sudo mkdir -p "$NOTES_DIR"
  sudo mkdir -p /var/lib/projectcea/settings-cache
  sudo chown antoine:antoine /var/lib/projectcea/settings-cache
  sudo chown -R "${NOTES_USER:-antoine}:${NOTES_GROUP:-antoine}" "$NOTES_DIR" 2>/dev/null || true

  echo "[5b/7] Ensuring sudoers rule for cea user..."
  "$TARGET/Infrastructure/scripts/install-sudoers.sh"
fi

for required_file in \
  Infrastructure/services.yaml \
  Infrastructure/scripts/service_list.py \
  Infrastructure/scripts/sync_systemd_units.sh \
  Infrastructure/service-error-handler@.service \
  Infrastructure/caddy/Caddyfile \
  pyproject.toml \
  Infrastructure/can-processor-service/setup_can.sh \
  Infrastructure/grow-archive-service/requirements.txt \
  Infrastructure/frontend/package.json \
  Infrastructure/frontend/dist/index.html; do
  if [[ ! -s "$TARGET/$required_file" ]]; then
    log_event "deploy_blocked" "staged_release_incomplete:$required_file"
    echo "[deploy] incomplete staged release: $TARGET/$required_file" >&2
    exit 1
  fi
done
if [[ -z "$DB_MIGRATE_OVERRIDE" && ! -s "$TARGET/Infrastructure/scripts/db_migrate.py" ]]; then
  log_event "deploy_blocked" "staged_release_incomplete:Infrastructure/scripts/db_migrate.py"
  echo "[deploy] migration runner is missing from the release" >&2
  exit 1
fi

load_postgres_environment
if ! PREDEPLOY_MIGRATION_HEAD="$(db_migrate "$TARGET" head)"; then
  log_event "deploy_blocked" "predeploy_migration_head_failed"
  echo "[deploy] cannot read pre-deploy SQL migration head" >&2
  exit 1
fi
if ! PREDEPLOY_ALEMBIC_HEADS="$(current_alembic_heads)"; then
  log_event "deploy_blocked" "predeploy_alembic_heads_failed"
  echo "[deploy] cannot read pre-deploy Alembic heads" >&2
  exit 1
fi
export PREDEPLOY_MIGRATION_HEAD PREDEPLOY_ALEMBIC_HEADS
echo "[6/7] Planning and applying database migrations..."
if ! MIGRATION_PLAN="$(db_migrate "$TARGET" plan --json)"; then
  log_event "deploy_blocked" "migration_plan_failed"
  echo "[deploy] migration plan failed" >&2
  exit 1
fi
if ! PLAN_REQUIRES_DOWNTIME="$(python3 -c 'import json,sys; print(str(bool(json.load(sys.stdin)["requires_downtime"])).lower())' <<< "$MIGRATION_PLAN")" || \
   ! PLAN_SET_SHA="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["set_sha256"])' <<< "$MIGRATION_PLAN")"; then
  log_event "deploy_blocked" "migration_plan_invalid"
  echo "[deploy] migration plan returned invalid JSON" >&2
  exit 1
fi
if [[ "$PLAN_REQUIRES_DOWNTIME" == "true" ]]; then
  REHEARSAL_FILE="$MIGRATION_REHEARSAL_DIR/$PLAN_SET_SHA.json"
  if ! python3 - "$REHEARSAL_FILE" "$PLAN_SET_SHA" <<'PY'
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
age = time.time() - path.stat().st_mtime
if age < 0 or age >= 7 * 24 * 60 * 60:
    raise SystemExit(1)
record = json.loads(path.read_text(encoding="utf-8"))
raise SystemExit(0 if record.get("set_sha256") == sys.argv[2] and record.get("result") == "pass" else 1)
PY
  then
    log_event "deploy_blocked" "rehearsal_missing"
    echo "deploy_blocked:rehearsal_missing" >&2
    exit 1
  fi

  BACKUP_DUMP="$MIGRATION_BACKUP_DIR/pre-$RELEASE_ID.dump"
  sudo install -d -o antoine -g antoine -m 0700 "$MIGRATION_BACKUP_DIR"
  if ! "$PG_DUMP_BIN" -Fc --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" --file "$BACKUP_DUMP"; then
    log_event "deploy_blocked" "database_backup_failed"
    echo "[deploy] pg_dump failed; downtime migration not started" >&2
    exit 1
  fi
  if ! stop_services_reverse "$SERVICE_LIST"; then
    log_event "deploy_blocked" "stop_services_failed"
    if [[ -n "$PREVIOUS_RELEASE" ]]; then restart_services "$PREVIOUS_RELEASE/Infrastructure/scripts/service_list.py" || true; fi
    exit 1
  fi
  if ! APPLY_RESULT="$(db_migrate "$TARGET" apply --json --allow-downtime)"; then
    log_event "deploy_blocked" "migration_apply_failed"
    rollback_previous || true
    exit 1
  fi
else
  if ! APPLY_RESULT="$(db_migrate "$TARGET" apply --json)"; then
    log_event "deploy_blocked" "migration_apply_failed"
    rollback_previous || true
    exit 1
  fi
fi
if ! MIGRATION_HEAD="$(python3 -c 'import json,sys; value=json.load(sys.stdin).get("head"); print("null" if value is None else value)' <<< "$APPLY_RESULT")" || \
   ! ALEMBIC_HEADS="$(python3 -c 'import json,sys; print(json.dumps(sorted(json.load(sys.stdin).get("alembic_heads", []))))' <<< "$APPLY_RESULT")"; then
  log_event "deploy_blocked" "migration_result_invalid"
  rollback_previous || true
  exit 1
fi

echo "[7/7] Synchronizing unit files and Caddy..."
if ! DEPLOY_SYSTEMD_DIR="$SYSTEMD_DIR" bash "$TARGET/Infrastructure/scripts/sync_systemd_units.sh" \
  --release "$TARGET" --backup-dir "$SYSTEMD_BACKUP_DIR"; then
  log_event "deploy_blocked" "systemd_sync_failed"
  rollback_previous || true
  exit 1
fi

CADDY_CONFIG="${DEPLOY_CADDY_CONFIG:-$TARGET/Infrastructure/caddy/Caddyfile}"
if ! sudo "$CADDY_BIN" validate --config "$CADDY_CONFIG" --adapter caddyfile; then
  log_event "deploy_blocked" "caddy_validation_failed"
  rollback_previous || true
  exit 1
fi
if ! cmp -s "$CADDY_CONFIG" "$CADDY_FILE"; then
  if ! sudo mkdir -p "$(dirname "$CADDY_FILE")" "$SYSTEMD_BACKUP_DIR"; then
    log_event "deploy_blocked" "caddy_backup_directory_failed"
    rollback_previous || true
    exit 1
  fi
  if [[ -f "$CADDY_FILE" ]]; then
    if ! sudo cp -a "$CADDY_FILE" "$SYSTEMD_BACKUP_DIR/Caddyfile"; then
      log_event "deploy_blocked" "caddy_backup_failed"
      rollback_previous || true
      exit 1
    fi
  elif ! sudo touch "$SYSTEMD_BACKUP_DIR/Caddyfile.created"; then
    log_event "deploy_blocked" "caddy_backup_marker_failed"
    rollback_previous || true
    exit 1
  fi
  if ! sudo install -m 0644 -o root -g root "$CADDY_CONFIG" "$CADDY_FILE"; then
    log_event "deploy_blocked" "caddy_install_failed"
    rollback_previous || true
    exit 1
  fi
  if ! sudo systemctl restart caddy; then
    log_event "deploy_blocked" "caddy_restart_failed"
    rollback_previous || true
    exit 1
  fi
fi

echo "[8/7] Switching release symlink..."
sudo mkdir -p "$RELEASES"
if ! sudo ln -sfn "$TARGET" "$CURRENT_SYMLINK"; then
  log_event "deploy_blocked" "symlink_switch_failed"
  rollback_previous || true
  exit 1
fi
log_event "deploy_symlink_switched" "$TARGET" >/dev/null

echo "[9/7] Restarting services..."
if ! restart_services "$SERVICE_LIST"; then
  if rollback_previous; then
    python3 "$STATE_HELPER" clear-candidate "$STATE_JSON" >/dev/null || true
    export CANDIDATE_RELEASE_ID="$RELEASE_ID"
    export CANDIDATE_RELEASE_PATH="$TARGET"
    log_event "candidate-rejected" "service_restart_failed" >/dev/null
  else
    if [[ "$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)" == "$TARGET" ]]; then
      python3 "$STATE_HELPER" set-candidate "$STATE_JSON" "$RELEASE_ID" "$TARGET" >/dev/null || true
    fi
    log_event "candidate-rollback-blocked" "service_restart_failed" >/dev/null
  fi
  echo "[deploy] service restart failed; automatic rollback was attempted" >&2
  exit 1
fi
echo "[10/10] Health and readiness checks..."
if ! run_health_checks "$SERVICE_LIST"; then
  if rollback_previous; then
    python3 "$STATE_HELPER" clear-candidate "$STATE_JSON" >/dev/null || true
    export CANDIDATE_RELEASE_ID="$RELEASE_ID"
    export CANDIDATE_RELEASE_PATH="$TARGET"
    log_event "candidate-rejected" "health_checks_failed" >/dev/null
  else
    if [[ "$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)" == "$TARGET" ]]; then
      python3 "$STATE_HELPER" set-candidate "$STATE_JSON" "$RELEASE_ID" "$TARGET" >/dev/null || true
    fi
    log_event "candidate-rollback-blocked" "health_checks_failed" >/dev/null
  fi
  echo "=== DEPLOY FAILED (health/readiness); automatic rollback was attempted ===" >&2
  exit 1
fi

# Caddy proxies / to automation-service, so check it only after every backend is
# healthy and ready; retry briefly to absorb Caddy's own upstream reconnect.
CADDY_CODE="000"
for _attempt in $(seq 1 "${DEPLOY_CADDY_HEALTH_ATTEMPTS:-10}"); do
  CADDY_CODE="$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$CADDY_HEALTH_URL" 2>/dev/null || echo "000")"
  [[ "$CADDY_CODE" == "200" ]] && break
  sleep 1
done
if [[ "$CADDY_CODE" != "200" ]]; then
  log_event "deploy_blocked" "caddy_health_failed" "caddy" "$CADDY_CODE"
  if rollback_previous; then
    python3 "$STATE_HELPER" clear-candidate "$STATE_JSON" >/dev/null || true
    export CANDIDATE_RELEASE_ID="$RELEASE_ID"
    export CANDIDATE_RELEASE_PATH="$TARGET"
    log_event "candidate-rejected" "caddy_health_failed" >/dev/null
  else
    if [[ "$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)" == "$TARGET" ]]; then
      python3 "$STATE_HELPER" set-candidate "$STATE_JSON" "$RELEASE_ID" "$TARGET" >/dev/null || true
    fi
    log_event "candidate-rollback-blocked" "caddy_health_failed" >/dev/null
  fi
  exit 1
fi

MANIFEST_PATH="$CURRENT_SYMLINK/deploy_manifest.json"
SERVICES_JSON="$(python3 "$SERVICE_LIST" --list-deploy-managed-units | python3 -c 'import json, sys; print(json.dumps([unit.removesuffix(".service") for unit in map(str.strip, sys.stdin) if unit]))')"
MIGRATION_HEAD_JSON="$(python3 -c 'import json,sys; value=sys.argv[1]; print(json.dumps(None if value == "null" else value))' "$MIGRATION_HEAD")"
PREDEPLOY_MIGRATION_HEAD_JSON="$(python3 -c 'import json,sys; value=sys.argv[1]; print(json.dumps(None if value == "null" else value))' "$PREDEPLOY_MIGRATION_HEAD")"
echo "Writing deploy manifest to $MANIFEST_PATH..."
sudo tee "$MANIFEST_PATH" >/dev/null <<EOF
{
  "release_id": "$RELEASE_ID",
  "git_sha": "$FULL_SHA",
  "alembic_heads": $ALEMBIC_HEADS,
  "migration_head": $MIGRATION_HEAD_JSON,
  "previous_schema": {
    "migration_head": $PREDEPLOY_MIGRATION_HEAD_JSON,
    "alembic_heads": $PREDEPLOY_ALEMBIC_HEADS
  },
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "services": $SERVICES_JSON,
  "health_ok": true
}
EOF

python3 "$STATE_HELPER" set-candidate "$STATE_JSON" "$RELEASE_ID" "$TARGET" >/dev/null
export CANDIDATE_RELEASE_ID="$RELEASE_ID"
export CANDIDATE_RELEASE_PATH="$TARGET"
log_event "candidate-active" "health_checks_passed"

echo "Cleaning old releases..."
python3 "$STATE_HELPER" cleanup "$RELEASES" "$STATE_JSON" "$MAX_RELEASES" "$CURRENT_SYMLINK" | xargs -r sudo rm -rf

echo ""
echo "=== Deploy complete: $RELEASE_ID (candidate) ==="
echo "Current release: $(readlink "$CURRENT_SYMLINK")"
echo "Deploy log (NDJSON): $DEPLOY_LOG"
echo "Finalize: $SOURCE/finalize-deploy.sh --confirm"
echo "Manual rollback: $SOURCE/rollback-deploy.sh [--with-down-migrations]"
