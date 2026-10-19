#!/usr/bin/env bash
# Roll back to a release, restoring its schema, systemd files, and Caddy config
# when those differ from the currently active release.

set -euo pipefail

SOURCE="${DEPLOY_GIT_REPO:-${SOURCE:-/home/antoine/ProjectCEA}}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
RELEASES="${DEPLOY_RELEASES:-$DEPLOY_ROOT/releases}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"
POSTGRES_ENV_FILE="${DEPLOY_POSTGRES_ENV_FILE:-/opt/projectcea/shared/env/postgres.env}"
MIGRATION_BACKUP_DIR="${DEPLOY_BACKUP_DIR:-/var/lib/projectcea/backups}"
SYSTEMD_BACKUP_ROOT="${DEPLOY_SYSTEMD_BACKUP_ROOT:-/var/lib/projectcea/systemd-backup}"
SYSTEMD_DIR="${DEPLOY_SYSTEMD_DIR:-/etc/systemd/system}"
CADDY_FILE="${DEPLOY_CADDY_FILE:-/etc/caddy/Caddyfile}"
DB_MIGRATE_OVERRIDE="${DEPLOY_DB_MIGRATE:-}"
PSQL_BIN="${DEPLOY_PSQL:-${DB_MIGRATE_PSQL:-psql}}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"

usage() {
  echo "Usage: $0 [--with-down-migrations] [<release_id_or_path>]" >&2
}

WITH_DOWN_MIGRATIONS=0
RELEASE_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-down-migrations)
      [[ "$WITH_DOWN_MIGRATIONS" -eq 0 ]] || { usage; exit 2; }
      WITH_DOWN_MIGRATIONS=1
      shift
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      [[ -z "$RELEASE_ARG" ]] || { usage; exit 2; }
      RELEASE_ARG="$1"
      shift
      ;;
  esac
done

sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chown antoine:antoine "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 0644 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -n 9; then
  echo "[rollback] a deploy/rollback/finalize is already running; aborting" >&2
  exit 1
fi

log_rollback() {
  export DLOG_EVENT="$1"
  export DLOG_DETAIL="${2:-}"
  export DLOG_SERVICE=""
  export DLOG_HTTP=""
  export RELEASE_ID="$(basename "$3")"
  export TARGET="$3"
  export PREVIOUS_RELEASE="${4:-}"
  local line
  line=$(python3 "$EMIT" | python3 "$JSON_LINE")
  echo "$line" | sudo tee -a "$DEPLOY_LOG" >/dev/null
  echo "$line"
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
  if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then export PGPASSWORD="$POSTGRES_PASSWORD"; fi
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
      log_rollback "service_skipped" "load_state_not_found:$unit" "$RB_TGT" "$CURRENT" >/dev/null
      continue
    fi
    if ! sudo systemctl stop "$unit"; then
      log_rollback "service_stop_failed" "$unit" "$RB_TGT" "$CURRENT" >/dev/null
      rc=1
    fi
  done
  return "$rc"
}

restart_services() {
  local service_list="$1"
  local rc=0 state unit
  sudo systemctl daemon-reload || rc=1
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      log_rollback "service_skipped" "load_state_not_found:$unit" "$RB_TGT" "$CURRENT" >/dev/null
      continue
    fi
    if ! sudo systemctl restart "$unit"; then
      log_rollback "service_restart_failed" "$unit" "$RB_TGT" "$CURRENT" >/dev/null
      rc=1
    fi
  done < <(python3 "$service_list" --list-deploy-managed-units)
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

restore_release_config_chain() {
  local current_release="$1"
  local target_release="$2"
  local current_index=-1 target_index=-1 index release_id backup_dir
  local -a release_ids=() restore_ids=()
  if [[ "$current_release" == "$RELEASES/"* && "$target_release" == "$RELEASES/"* ]]; then
    local release_names
    if ! release_names="$(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort)"; then
      return 1
    fi
    mapfile -t release_ids <<< "$release_names"
    for index in "${!release_ids[@]}"; do
      if [[ "${release_ids[$index]}" == "$(basename "$current_release")" ]]; then
        current_index="$index"
      fi
      if [[ "${release_ids[$index]}" == "$(basename "$target_release")" ]]; then
        target_index="$index"
      fi
    done
  fi
  if (( current_index >= 0 && target_index >= 0 && target_index < current_index )); then
    for ((index = current_index; index > target_index; index--)); do
      restore_ids+=("${release_ids[$index]}")
    done
  elif [[ "$current_release" != "$target_release" ]]; then
    restore_ids+=("$(basename "$current_release")")
  fi

  for release_id in "${restore_ids[@]}"; do
    backup_dir="$SYSTEMD_BACKUP_ROOT/$release_id"
    if [[ ! -d "$backup_dir" ]]; then
      log_rollback "rollback_blocked" "configuration_backup_missing:$release_id" "$RB_TGT" "$CURRENT" >/dev/null
      echo "rollback_blocked:configuration_backup_missing:$release_id" >&2
      return 1
    fi
    if ! restore_systemd_backup "$backup_dir"; then
      log_rollback "rollback_manual_fail" "systemd_restore_failed:$release_id" "$RB_TGT" "$CURRENT" >/dev/null
      return 1
    fi
    if ! restore_caddyfile "$backup_dir"; then
      log_rollback "rollback_manual_fail" "caddy_restore_failed:$release_id" "$RB_TGT" "$CURRENT" >/dev/null
      return 1
    fi
  done
}

run_health_checks() {
  local service_list="$1"
  local timeout="${ROLLBACK_HEALTH_TIMEOUT_SECONDS:-15}"
  if ! [[ "$timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "[rollback] ROLLBACK_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
    return 1
  fi
  local deadline=$(( $(date +%s) + timeout ))
  local now remaining code kind unit url key failed=""
  local -a units=() urls=() kinds=()
  local -A healthy=()
  for kind in health ready; do
    while IFS=$'\t' read -r unit url; do
      [[ -n "$unit" && -n "$url" ]] || continue
      units+=("$unit")
      urls+=("$url")
      kinds+=("$kind")
    done < <(python3 "$service_list" "--list-$kind")
  done
  while true; do
    for index in "${!units[@]}"; do
      key="${kinds[$index]}:${units[$index]}"
      [[ -n "${healthy[$key]:-}" ]] && continue
      now=$(date +%s)
      remaining=$((deadline - now))
      [[ "$remaining" -gt 0 ]] || break 2
      if code=$(curl -sS -m "$remaining" -o /tmp/cea_rb_health.txt -w "%{http_code}" "${urls[$index]}" 2>/dev/null); then
        [[ "$code" == "200" ]] && healthy["$key"]=1
      fi
    done
    if [[ "${#healthy[@]}" -eq "${#units[@]}" ]]; then
      return 0
    fi
    now=$(date +%s)
    [[ "$now" -lt "$deadline" ]] || break
    sleep 1
  done
  for index in "${!units[@]}"; do
    key="${kinds[$index]}:${units[$index]}"
    [[ -n "${healthy[$key]:-}" ]] || failed="${failed:+$failed,}${kinds[$index]}:${units[$index]}"
  done
  echo "$failed"
  return 1
}

resolve_target() {
  local arg="${1:-}"
  if [[ -z "$arg" ]]; then
    if [[ ! -f "$STATE" ]]; then
      echo "No $STATE — pass a release id or path under $RELEASES" >&2
      exit 1
    fi
    if [[ "$(python3 "$STATE_HELPER" has-candidate "$STATE")" == "yes" ]]; then
      local last_good
      last_good="$(python3 "$STATE_HELPER" get-last-good-path "$STATE")"
      if [[ -z "$last_good" || "$last_good" == "null" ]]; then
        echo "deploy_state.json has an active candidate but no last_good release path" >&2
        exit 1
      fi
      echo "$last_good"
      return
    fi
    arg="$(python3 "$STATE_HELPER" get-rollback-path "$STATE")"
    if [[ -z "$arg" || "$arg" == "None" || "$arg" == "null" ]]; then
      echo "deploy_state.json has no rollback_to_path and no active candidate. Pass a release directory." >&2
      exit 1
    fi
  fi
  if [[ -d "$arg" ]]; then
    echo "$arg"
  elif [[ -d "$RELEASES/$arg" ]]; then
    echo "$RELEASES/$arg"
  else
    echo "Release not found: $arg" >&2
    exit 1
  fi
}

RB_TGT="$(resolve_target "$RELEASE_ARG")"
CURRENT="$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)"
if [[ -z "$CURRENT" || ! -d "$CURRENT/Infrastructure" ]]; then
  echo "[rollback] current release is unavailable; cannot safely compare schema state" >&2
  exit 1
fi
SERVICE_LIST="$RB_TGT/Infrastructure/scripts/service_list.py"
if [[ ! -x "$SERVICE_LIST" ]]; then
  echo "[rollback] target release service_list.py is missing: $SERVICE_LIST" >&2
  exit 1
fi
REJECT_CANDIDATE=0
if [[ -z "$RELEASE_ARG" ]] && [[ "$(python3 "$STATE_HELPER" has-candidate "$STATE")" == "yes" ]]; then
  REJECT_CANDIDATE=1
fi

sudo mkdir -p "$DEPLOY_STATE_DIR"
log_rollback "rollback_manual_start" "checking_schema_and_switching" "$RB_TGT" "$CURRENT" >/dev/null
load_postgres_environment
if ! CURRENT_HEAD="$(db_migrate "$CURRENT" head)"; then
  log_rollback "rollback_blocked" "migration_head_unavailable" "$RB_TGT" "$CURRENT" >/dev/null
  echo "rollback_blocked:migration_head_unavailable" >&2
  exit 1
fi
TARGET_METADATA="$(manifest_schema_metadata "$RB_TGT" current)"
TARGET_METADATA_KNOWN="$(schema_metadata_known "$TARGET_METADATA")"
if [[ "$TARGET_METADATA_KNOWN" != "true" ]]; then
  TARGET_METADATA="$(manifest_schema_metadata "$CURRENT" previous)"
  TARGET_METADATA_KNOWN="$(schema_metadata_known "$TARGET_METADATA")"
fi
if [[ "$TARGET_METADATA_KNOWN" != "true" ]]; then
  log_rollback "rollback_blocked" "schema_metadata_unknown" "$RB_TGT" "$CURRENT" >/dev/null
  echo "rollback_blocked:schema_metadata_unknown" >&2
  exit 1
fi
TARGET_HEAD="$(schema_metadata_head "$TARGET_METADATA")"
TARGET_ALEMBIC="$(schema_metadata_alembic_heads "$TARGET_METADATA")"
if ! CURRENT_ALEMBIC="$(current_alembic_heads)" || ! alembic_heads_equal "$TARGET_ALEMBIC" "$CURRENT_ALEMBIC"; then
  BACKUP_DUMP="$MIGRATION_BACKUP_DIR/pre-$(basename "$CURRENT").dump"
  log_rollback "rollback_blocked" "alembic_heads_mismatch" "$RB_TGT" "$CURRENT" >/dev/null
  echo "rollback_blocked:alembic_heads_mismatch" >&2
  echo "Restore with: sudo -u postgres pg_restore -d $POSTGRES_DB $BACKUP_DUMP" >&2
  exit 1
fi
SCHEMA_RELATION="$(migration_head_relation "$CURRENT_HEAD" "$TARGET_HEAD")"
if [[ "$SCHEMA_RELATION" == "incomparable" ]]; then
  log_rollback "rollback_blocked" "migration_heads_incomparable" "$RB_TGT" "$CURRENT" >/dev/null
  echo "rollback_blocked:migration_heads_incomparable" >&2
  exit 1
fi
if [[ "$SCHEMA_RELATION" == "newer" && "$WITH_DOWN_MIGRATIONS" -ne 1 ]]; then
  log_rollback "rollback_blocked" "schema_newer_than_target" "$RB_TGT" "$CURRENT" >/dev/null
  echo "rollback_blocked:schema_newer_than_target" >&2
  exit 1
fi

if ! stop_services_reverse "$SERVICE_LIST"; then
  log_rollback "rollback_manual_fail" "stop_services_failed" "$RB_TGT" "$CURRENT" >/dev/null
  exit 1
fi
if [[ "$SCHEMA_RELATION" == "newer" ]]; then
  RESTORE_TO="$TARGET_HEAD"
  [[ "$RESTORE_TO" != "null" ]] || RESTORE_TO="none"
  if ! db_migrate "$CURRENT" down --to "$RESTORE_TO"; then
    log_rollback "rollback_manual_fail" "down_migrations_failed" "$RB_TGT" "$CURRENT" >/dev/null
    exit 1
  fi
  if ! RESTORED_HEAD="$(db_migrate "$CURRENT" head)" || [[ "$RESTORED_HEAD" != "$TARGET_HEAD" ]]; then
    log_rollback "rollback_manual_fail" "migration_head_after_down_mismatch" "$RB_TGT" "$CURRENT" >/dev/null
    exit 1
  fi
fi
if ! restore_release_config_chain "$CURRENT" "$RB_TGT"; then
  log_rollback "rollback_manual_fail" "configuration_restore_failed" "$RB_TGT" "$CURRENT" >/dev/null
  exit 1
fi
sudo ln -sfn "$RB_TGT" "$CURRENT_SYMLINK"
log_rollback "rollback_manual_symlink" "switched_to_target" "$RB_TGT" "$CURRENT" >/dev/null

if ! restart_services "$SERVICE_LIST"; then
  log_rollback "rollback_manual_restart_fail" "restart_failed" "$RB_TGT" "$CURRENT" >/dev/null
  exit 1
fi
if ! FAILED_SERVICES="$(run_health_checks "$SERVICE_LIST")"; then
  log_rollback "rollback_manual_health_fail" "failed_services:$FAILED_SERVICES" "$RB_TGT" "$CURRENT" >/dev/null
  exit 1
fi

if [[ "$REJECT_CANDIDATE" -eq 1 ]]; then
  ACTIVE_CANDIDATE_ID="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_id)"
  ACTIVE_CANDIDATE_PATH="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_path)"
  python3 "$STATE_HELPER" clear-candidate "$STATE" >/dev/null
  export CANDIDATE_RELEASE_ID="$ACTIVE_CANDIDATE_ID"
  export CANDIDATE_RELEASE_PATH="$ACTIVE_CANDIDATE_PATH"
  log_rollback "rejected" "health_and_readiness_ok" "$RB_TGT" "$CURRENT"
else
  log_rollback "rollback_manual_ok" "health_and_readiness_ok" "$RB_TGT" "$CURRENT"
fi

echo "Rollback complete: current -> $RB_TGT"
