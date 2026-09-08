#!/usr/bin/env bash
# Point /opt/projectcea/current at a prior release and restart services.
# Active-candidate semantics:
#   - No argument during an active candidate returns to last_good, clears the candidate,
#     and emits a 'rejected' event. PostgreSQL is left untouched.
#   - No argument with no active candidate uses rollback_to_path from deploy_state.json.
# Usage:
#   ./rollback-deploy.sh                    # candidate-aware rollback
#   ./rollback-deploy.sh <release_id_or_subdir>

set -euo pipefail

SOURCE="/home/antoine/ProjectCEA"

# Overridable paths for sandbox testing and non-standard layouts.
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
RELEASES="${DEPLOY_RELEASES:-$DEPLOY_ROOT/releases}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"
SERVICE_LIST="$SOURCE/Infrastructure/scripts/service_list.py"

# Concurrency guard: shared with deploy.sh/finalize-deploy.sh.
sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 666 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -n 9; then
  echo "[rollback] a deploy/rollback/finalize is already running; aborting" >&2
  exit 1
fi

log_rollback() {
  # $1=event $2=detail $3=new_target_path $4=previous_path
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

run_health_checks() {
  local timeout="${ROLLBACK_HEALTH_TIMEOUT_SECONDS:-15}"
  if ! [[ "$timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "[rollback] ROLLBACK_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
    return 1
  fi

  local deadline=$(( $(date +%s) + timeout ))
  local now remaining code
  local -a units=() urls=()
  local -A healthy=()
  local unit url

  while IFS=$'\t' read -r unit url; do
    units+=("$unit")
    urls+=("$url")
  done < <(python3 "$SERVICE_LIST" --list-health)

  while true; do
    for index in "${!units[@]}"; do
      unit="${units[$index]}"
      [[ -n "${healthy[$unit]:-}" ]] && continue

      now=$(date +%s)
      remaining=$(( deadline - now ))
      [[ "$remaining" -gt 0 ]] || break 2

      set +e
      code=$(curl -sS -m "$remaining" -o /tmp/cea_rb_health.txt -w "%{http_code}" "${urls[$index]}" 2>/dev/null || echo "000")
      set -e
      [[ "$code" == "200" ]] && healthy["$unit"]=1
    done

    if [[ "${#healthy[@]}" -eq "${#units[@]}" ]]; then
      return 0
    fi

    now=$(date +%s)
    [[ "$now" -lt "$deadline" ]] || break
    sleep 1
  done

  local failed=""
  for unit in "${units[@]}"; do
    [[ -n "${healthy[$unit]:-}" ]] || failed="${failed:+$failed,}$unit"
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
    # Candidate-aware no-arg path: return to last_good when a candidate exists.
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
    return
  fi
  if [[ -d "$RELEASES/$arg" ]]; then
    echo "$RELEASES/$arg"
    return
  fi
  echo "Release not found: $arg" >&2
  exit 1
}

RB_TGT="$(resolve_target "${1:-}")"
CURRENT="$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)"

REJECT_CANDIDATE=0
if [[ -z "${1:-}" ]] && [[ "$(python3 "$STATE_HELPER" has-candidate "$STATE")" == "yes" ]]; then
  REJECT_CANDIDATE=1
fi

log_rollback "rollback_manual_start" "switching_symlink" "$RB_TGT" "$CURRENT"

sudo mkdir -p "$DEPLOY_STATE_DIR"
sudo ln -sfn "$RB_TGT" "$CURRENT_SYMLINK"

sudo systemctl daemon-reload
while IFS= read -r unit; do
  sudo systemctl restart "$unit"
done < <(python3 "$SERVICE_LIST" --list-deploy-managed-units)

if ! FAILED_SERVICES="$(run_health_checks)"; then
  log_rollback "rollback_manual_health_fail" "failed_services:$FAILED_SERVICES" "$RB_TGT" "$CURRENT"
  exit 1
fi

if [[ "$REJECT_CANDIDATE" -eq 1 ]]; then
  ACTIVE_CANDIDATE_ID="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_id)"
  ACTIVE_CANDIDATE_PATH="$(python3 "$STATE_HELPER" read "$STATE" candidate_release_path)"
  # Return to last_good and clear the candidate. PostgreSQL is intentionally untouched.
  python3 "$STATE_HELPER" clear-candidate "$STATE" >/dev/null
  export CANDIDATE_RELEASE_ID="$ACTIVE_CANDIDATE_ID"
  export CANDIDATE_RELEASE_PATH="$ACTIVE_CANDIDATE_PATH"
  log_rollback "rejected" "health_ok" "$RB_TGT" "$CURRENT"
else
  log_rollback "rollback_manual_ok" "health_ok" "$RB_TGT" "$CURRENT"
fi

echo "Rollback complete: current -> $RB_TGT"
