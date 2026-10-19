#!/usr/bin/env bash
# Promote the active candidate release to last-good after restarting its
# deploy-managed units and verifying both health and readiness endpoints.

set -euo pipefail

SOURCE="${DEPLOY_GIT_REPO:-${SOURCE:-/home/antoine/ProjectCEA}}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"

if [[ "${1:-}" != "--confirm" || $# -ne 1 ]]; then
  echo "Usage: $0 --confirm" >&2
  exit 1
fi

sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chown antoine:antoine "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 0644 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -n 9; then
  echo "[finalize] a deploy/rollback/finalize is already running; aborting" >&2
  exit 1
fi

log_event() {
  export DLOG_EVENT="$1"
  export DLOG_DETAIL="${2:-}"
  export DLOG_SERVICE=""
  export DLOG_HTTP=""
  local line
  line=$(python3 "$EMIT" | python3 "$JSON_LINE")
  echo "$line" | sudo tee -a "$DEPLOY_LOG" >/dev/null
  echo "$line"
}

systemd_load_state() {
  local unit="$1" state
  if state="$(sudo systemctl show -p LoadState --value "$unit" 2>/dev/null)"; then
    printf '%s\n' "$state"
  else
    printf 'unknown\n'
  fi
}

restart_services() {
  local service_list="$1"
  local rc=0 state unit
  sudo systemctl daemon-reload || rc=1
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    state="$(systemd_load_state "$unit")"
    if [[ "$state" == "not-found" ]]; then
      log_event "service_skipped" "load_state_not_found:$unit" >/dev/null
      continue
    fi
    if ! sudo systemctl restart "$unit"; then
      log_event "service_restart_failed" "$unit" >/dev/null
      rc=1
    fi
  done < <(python3 "$service_list" --list-deploy-managed-units)
  return "$rc"
}

run_health_checks() {
  local service_list="$1"
  local timeout="${FINALIZE_HEALTH_TIMEOUT_SECONDS:-60}"
  if ! [[ "$timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "[finalize] FINALIZE_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
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
      if code=$(curl -sS -m "$remaining" -o /tmp/cea_finalize_health.txt -w "%{http_code}" "${urls[$index]}" 2>/dev/null); then
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

CANDIDATE_ID="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_id)"
CANDIDATE_PATH="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_path)"
if [[ -z "$CANDIDATE_ID" || "$CANDIDATE_ID" == "null" ]]; then
  echo "No active candidate to finalize" >&2
  exit 1
fi

CURRENT="$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)"
if [[ -z "$CANDIDATE_PATH" || "$CANDIDATE_PATH" == "null" ]]; then
  log_event "finalize_missing_candidate_path" "candidate_path_empty"
  echo "Candidate path is missing in deploy_state.json" >&2
  exit 1
fi
if [[ "$CURRENT" != "$CANDIDATE_PATH" ]]; then
  export RELEASE_ID="$CANDIDATE_ID"
  export TARGET="$CANDIDATE_PATH"
  export PREVIOUS_RELEASE="$CURRENT"
  export CANDIDATE_RELEASE_ID="$CANDIDATE_ID"
  export CANDIDATE_RELEASE_PATH="$CANDIDATE_PATH"
  log_event "finalize_symlink_mismatch" "current=$CURRENT candidate=$CANDIDATE_PATH"
  echo "Current symlink does not point to the candidate release; aborting finalize" >&2
  exit 1
fi

SERVICE_LIST="$CANDIDATE_PATH/Infrastructure/scripts/service_list.py"
if [[ ! -x "$SERVICE_LIST" ]]; then
  log_event "finalize_service_list_missing" "$SERVICE_LIST"
  echo "Candidate service_list.py is missing" >&2
  exit 1
fi
export RELEASE_ID="$CANDIDATE_ID"
export TARGET="$CANDIDATE_PATH"
export PREVIOUS_RELEASE="$CURRENT"
export CANDIDATE_RELEASE_ID="$CANDIDATE_ID"
export CANDIDATE_RELEASE_PATH="$CANDIDATE_PATH"

if ! restart_services "$SERVICE_LIST"; then
  log_event "finalize_restart_fail" "one_or_more_services_failed"
  echo "Service restart failed before finalize; candidate remains active" >&2
  exit 1
fi
if ! FAILED_SERVICES="$(run_health_checks "$SERVICE_LIST")"; then
  log_event "finalize_health_fail" "failed_services:$FAILED_SERVICES"
  echo "Health or readiness checks failed before finalize; candidate remains active" >&2
  exit 1
fi

FORMER_JSON="$(python3 "$STATE_HELPER" promote "$STATE")"
FORMER_LAST_GOOD_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('former_last_good_id') or '')" "$FORMER_JSON")"
log_event "finalized" "former_last_good=$FORMER_LAST_GOOD_ID"

echo "Finalized candidate $CANDIDATE_ID as last-good release."
echo "Rollback target is now: $(python3 "$STATE_HELPER" get-rollback-path "$STATE")"
