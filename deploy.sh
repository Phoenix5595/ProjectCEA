#!/usr/bin/env bash
# Deploy Infrastructure/ to /opt/projectcea/releases/<id> and switch current symlink.
# Active-candidate semantics:
#   - A successful deploy records the new release as candidate without overwriting last_good.
#   - A failed health check immediately restores the previous active release.
#   - finalize-deploy.sh --confirm promotes the candidate to last-good.
#   - rollback-deploy.sh (no arg) during an active candidate returns to last-good and rejects it.
# NDJSON log: /var/lib/projectcea/deploy.log

set -euo pipefail

SOURCE="/home/antoine/ProjectCEA"

# Overridable paths for sandbox testing and non-standard layouts.
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/projectcea}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/projectcea}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/var/lock/projectcea-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-$DEPLOY_STATE_DIR/deploy.log}"
RELEASES="${DEPLOY_RELEASES:-$DEPLOY_ROOT/releases}"
CURRENT_SYMLINK="${DEPLOY_CURRENT:-$DEPLOY_ROOT/current}"
STATE_JSON="${DEPLOY_STATE_JSON:-$DEPLOY_STATE_DIR/deploy_state.json}"
MAX_RELEASES="${DEPLOY_MAX_RELEASES:-10}"
DEPLOY_SKIP_STAGING="${DEPLOY_SKIP_STAGING:-0}"

JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"
STATE_HELPER="$SOURCE/Infrastructure/scripts/deploy_state.py"

RELEASE_ID=$(date +%Y%m%d-%H%M%S)-$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "nogit")
TARGET="${TARGET:-$RELEASES/$RELEASE_ID}"
# When TARGET is supplied externally (e.g. sandbox tests), align RELEASE_ID with it.
if [[ "$TARGET" != "$RELEASES/$RELEASE_ID" ]]; then
  RELEASE_ID=$(basename "$TARGET")
fi

# Concurrency guard: one deploy, finalize, or rollback at a time.
sudo mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")" 2>/dev/null || true
sudo touch "$DEPLOY_LOCK_FILE" 2>/dev/null || true
sudo chmod 666 "$DEPLOY_LOCK_FILE" 2>/dev/null || true
exec 9>"$DEPLOY_LOCK_FILE"
if ! flock -n 9; then
  echo "[deploy] another deploy/rollback/finalize is already running; aborting" >&2
  exit 1
fi

PREVIOUS_RELEASE=""
if [[ -L "$CURRENT_SYMLINK" ]] || [[ -e "$CURRENT_SYMLINK" ]]; then
  PREVIOUS_RELEASE=$(readlink -f "$CURRENT_SYMLINK" 2>/dev/null || true)
fi

export RELEASE_ID TARGET PREVIOUS_RELEASE SOURCE

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

restart_services() {
  sudo systemctl daemon-reload
  sudo systemctl restart can-setup
  sleep 1
  sudo systemctl restart can-processor cea-backend onewire-worker
  sleep 2
  sudo systemctl restart automation-service soil-sensor-service weather-service
}

run_health_checks() {
  # Preserve the caller's exit-on-error preference; this function intentionally
  # captures curl failures without triggering an immediate shell exit.
  local _had_e=0
  [[ $- == *e* ]] && _had_e=1
  set +e

  sleep 2
  local rc=0
  local pair url name code curl_rc attempt body
  local bodydir="/tmp/cea_health_$$"
  mkdir -p "$bodydir"
  trap 'rm -rf "$bodydir" 2>/dev/null || true' RETURN
  for pair in "http://127.0.0.1:8000/health|backend" "http://127.0.0.1:8001/health|automation" "http://127.0.0.1:8004/health|onewire"; do
    url="${pair%%|*}"
    name="${pair##*|}"
    code="000"
    local max_attempts="${DEPLOY_HEALTH_ATTEMPTS:-90}"
    for attempt in $(seq 1 "$max_attempts"); do
      : > "$bodydir/$name.body"
      code=$(curl -sS -m 10 -o "$bodydir/$name.body" -w "%{http_code}" "$url" 2>/dev/null)
      curl_rc=$?
      if [[ $curl_rc -ne 0 ]] || [[ -z "$code" ]]; then
        code="000"
      fi
      if [[ "$code" == "200" ]]; then
        log_event "health_ok" "" "$name" "$code"
        break
      fi
      sleep 1
    done
    if [[ "$code" != "200" ]]; then
      body=$(head -c 400 "$bodydir/$name.body" 2>/dev/null | tr '\n' ' ' || true)
      log_event "health_fail" "$body" "$name" "$code"
      rc=1
    fi
  done

  if [[ $_had_e -eq 1 ]]; then set -e; fi
  return "$rc"
}

rollback_previous() {
  if [[ -z "${PREVIOUS_RELEASE:-}" ]] || [[ ! -d "$PREVIOUS_RELEASE" ]]; then
    log_event "rollback_auto_fail" "no_previous_release_or_missing_dir"
    return 1
  fi
  sudo ln -sfn "$PREVIOUS_RELEASE" "$CURRENT_SYMLINK"
  log_event "rollback_auto_symlink" "reverted_to_previous"
  restart_services
  log_event "rollback_auto_done" "services_restarted_after_revert"
  return 0
}

sudo mkdir -p "$DEPLOY_STATE_DIR"

# Reconcile a stale state file to the currently active healthy symlink only when
# no candidate exists. The known stale last_good_release_id must not be trusted
# without checking the live symlink.
if [[ -n "$PREVIOUS_RELEASE" ]]; then
  python3 "$STATE_HELPER" reconcile "$STATE_JSON" "$PREVIOUS_RELEASE" >/dev/null || true
fi

# Never overwrite a live candidate with another deploy.
if ! python3 "$STATE_HELPER" require-no-candidate "$STATE_JSON" >/dev/null; then
  log_event "deploy_blocked" "candidate_already_active"
  echo "=== DEPLOY BLOCKED: a candidate release is already active; finalize or roll it back first ===" >&2
  exit 1
fi

echo "=== Deploying release: $RELEASE_ID ==="
log_event "deploy_start" ""

if [[ "$DEPLOY_SKIP_STAGING" != "1" ]]; then
  echo "[0/7] Running Ruff (lint + format) on Infrastructure..."
  cd "$SOURCE"
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
  ruff check --fix "${RUFF_PATHS[@]}"
  ruff format "${RUFF_PATHS[@]}"
  cd - >/dev/null

  echo "[1/7] Copying code..."
  sudo mkdir -p "$TARGET"
  sudo rsync -a --delete "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"
  sudo chown -R root:root "$TARGET"

  echo "[2/7] Building Python venvs..."
  for svc in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service; do
    SVC_DIR="$TARGET/Infrastructure/$svc"
    if [[ -f "$SVC_DIR/requirements.txt" ]]; then
      echo "  - $svc"
      sudo rm -rf "$SVC_DIR/.venv"
      sudo python3 -m venv "$SVC_DIR/.venv"
      sudo "$SVC_DIR/.venv/bin/pip" install -q --upgrade pip
      sudo "$SVC_DIR/.venv/bin/pip" install -q --upgrade "setuptools>=69" "packaging>=24.2"
      sudo "$SVC_DIR/.venv/bin/pip" install -q -r "$SVC_DIR/requirements.txt"
    fi
  done

  echo "[3/7] Building frontend..."
  cd "$TARGET/Infrastructure/frontend"
  sudo rm -rf dist/
  sudo env CI=true npm ci --silent --no-audit --no-fund
  sudo env CI=true npm run build

  echo "[4/7] Ensuring data directories..."
  NOTES_DIR="${NOTES_DATA_DIR:-/var/lib/projectcea/notes}"
  sudo mkdir -p "$NOTES_DIR"
  sudo chown -R "${NOTES_USER:-antoine}:${NOTES_GROUP:-antoine}" /var/lib/projectcea 2>/dev/null || true

  echo "[4b/7] Ensuring sudoers rule for cea user..."
  "$SOURCE/Infrastructure/scripts/install-sudoers.sh"
fi

echo "[5/7] Switching symlink..."
sudo mkdir -p "$RELEASES"
sudo ln -sfn "$TARGET" "$CURRENT_SYMLINK"

echo "[6/7] Restarting services..."
restart_services

echo "[7/7] Health checks..."
set +e
run_health_checks
HEALTH_RC=$?
set -e

if [[ "$HEALTH_RC" -ne 0 ]]; then
  # Restore the previous active release and clear any candidate state.
  rollback_previous || true
  python3 "$STATE_HELPER" clear-candidate "$STATE_JSON" >/dev/null || true
  export CANDIDATE_RELEASE_ID="$RELEASE_ID"
  export CANDIDATE_RELEASE_PATH="$TARGET"
  log_event "candidate-rejected" "health_checks_failed"
  echo "=== DEPLOY FAILED (health); symlink restored when possible ===" >&2
  exit 1
fi

if [[ "${DEPLOY_ISKRA:-0}" == "1" ]]; then
  echo "[7b/7] Syncing and verifying iskraprojectcea stack..."
  log_event "iskra_sync_start" ""
  "$SOURCE/Infrastructure/scripts/sync_to_iskra.sh"
  "$SOURCE/Infrastructure/scripts/verify_iskra.sh"
  log_event "iskra_sync_ok" ""
fi

MANIFEST_PATH="$CURRENT_SYMLINK/deploy_manifest.json"
echo "Writing deploy manifest to $MANIFEST_PATH..."
sudo tee "$MANIFEST_PATH" >/dev/null <<EOF
{
  "release_id": "$RELEASE_ID",
  "git_sha": "$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "nogit")",
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "services": ["can-processor","cea-backend","onewire-worker","automation-service","soil-sensor-service","weather-service"],
  "health_ok": true
}
EOF

# Record the active release as candidate; do NOT overwrite last_good yet.
python3 "$STATE_HELPER" set-candidate "$STATE_JSON" "$RELEASE_ID" "$TARGET" >/dev/null

export CANDIDATE_RELEASE_ID="$RELEASE_ID"
export CANDIDATE_RELEASE_PATH="$TARGET"
log_event "candidate-active" "health_checks_passed"

echo "Cleaning old releases..."
python3 "$STATE_HELPER" cleanup "$RELEASES" "$STATE_JSON" "$MAX_RELEASES" | xargs -r sudo rm -rf

echo ""
echo "=== Deploy complete: $RELEASE_ID (candidate) ==="
echo "Current release: $(readlink "$CURRENT_SYMLINK")"
echo "Deploy log (NDJSON): $DEPLOY_LOG"
echo "Finalize: $SOURCE/finalize-deploy.sh --confirm"
echo "Manual rollback: $SOURCE/rollback-deploy.sh"
