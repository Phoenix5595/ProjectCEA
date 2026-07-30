#!/usr/bin/env bash
# Promote the active candidate release to last-good after verifying symlink and health.
# Usage:
#   ./finalize-deploy.sh --confirm

set -euo pipefail

SOURCE="/home/antoine/ProjectCEA"

# Overridable paths for sandbox testing and non-standard layouts.
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"

if [[ "${1:-}" != "--confirm" ]]; then
  echo "Usage: $0 --confirm" >&2
  exit 1
fi

# Concurrency guard: shared with deploy.sh/rollback-deploy.sh.
sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 666 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
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

run_health_checks() {
  local FAILED=""
  local pair url name code
  local waited=0
  local max_wait=60
  while [[ "$waited" -lt "$max_wait" ]]; do
    FAILED=""
    for pair in "http://127.0.0.1:8000/health|backend" "http://127.0.0.1:8001/health|automation" "http://127.0.0.1:8004/health|onewire"; do
      url="${pair%%|*}"
      name="${pair##*|}"
      set +e
      code=$(curl -sS -m 5 -o /tmp/cea_finalize_health.txt -w "%{http_code}" "$url" 2>/dev/null || echo "000")
      set -e
      if [[ "$code" != "200" ]]; then
        FAILED="${FAILED:+$FAILED,}$name"
      fi
    done
    if [[ -z "$FAILED" ]]; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "$FAILED"
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
  log_event "finalize_missing_candidate_path" "candidate_path_empty" "$CURRENT"
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

# Restart services before the final health check so the candidate code is running.
sudo systemctl daemon-reload
sudo systemctl restart can-setup
sleep 1
sudo systemctl restart can-processor cea-backend onewire-worker
sleep 2
sudo systemctl restart automation-service soil-sensor-service weather-service

if ! FAILED_SERVICES="$(run_health_checks)"; then
  export RELEASE_ID="$CANDIDATE_ID"
  export TARGET="$CANDIDATE_PATH"
  export PREVIOUS_RELEASE="$CURRENT"
  export CANDIDATE_RELEASE_ID="$CANDIDATE_ID"
  export CANDIDATE_RELEASE_PATH="$CANDIDATE_PATH"
  log_event "finalize_health_fail" "failed_services:$FAILED_SERVICES"
  echo "Health checks failed before finalize; candidate remains active" >&2
  exit 1
fi

# Atomically promote candidate to last-good and set rollback target to former last-good.
FORMER_JSON="$(python3 "$STATE_HELPER" promote "$STATE")"
FORMER_LAST_GOOD_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('former_last_good_id') or '')" "$FORMER_JSON")"

export RELEASE_ID="$CANDIDATE_ID"
export TARGET="$CANDIDATE_PATH"
export PREVIOUS_RELEASE="$CURRENT"
export CANDIDATE_RELEASE_ID="$CANDIDATE_ID"
export CANDIDATE_RELEASE_PATH="$CANDIDATE_PATH"
log_event "finalized" "former_last_good=$FORMER_LAST_GOOD_ID"

echo "Finalized candidate $CANDIDATE_ID as last-good release."
echo "Rollback target is now: $(python3 "$STATE_HELPER" get-rollback-path "$STATE")"
