#!/usr/bin/env bash
# Deploy Infrastructure/ to /opt/projectcea/releases/<id> and switch current symlink.
# On failed health checks: revert symlink to previous release, restart services, exit 1.
# NDJSON log: /var/lib/projectcea/deploy.log

set -euo pipefail

# Concurrency guard: one deploy or rollback at a time. Shared with rollback-deploy.sh
# so you cannot rollback while a deploy is running and vice versa.
LOCK_FILE="/var/lock/projectcea-deploy.lock"
sudo touch "$LOCK_FILE" 2>/dev/null || true
sudo chmod 666 "$LOCK_FILE" 2>/dev/null || true
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[deploy] another deploy/rollback is already running; aborting" >&2
  exit 1
fi

SOURCE="/home/antoine/ProjectCEA"
RELEASES="/opt/projectcea/releases"
MAX_RELEASES=10
DEPLOY_LOG="/var/lib/projectcea/deploy.log"
STATE_JSON="/var/lib/projectcea/deploy_state.json"
JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"

RELEASE_ID=$(date +%Y%m%d-%H%M%S)-$(git -C "$SOURCE" rev-parse --short HEAD 2>/dev/null || echo "nogit")
TARGET="$RELEASES/$RELEASE_ID"

PREVIOUS_RELEASE=""
if [[ -L /opt/projectcea/current ]] || [[ -e /opt/projectcea/current ]]; then
  PREVIOUS_RELEASE=$(readlink -f /opt/projectcea/current 2>/dev/null || true)
fi

export RELEASE_ID TARGET PREVIOUS_RELEASE SOURCE

log_event() {
  # Args: event [detail [service [http_code]]]
  export DLOG_EVENT="$1"
  export DLOG_DETAIL="${2:-}"
  export DLOG_SERVICE="${3:-}"
  export DLOG_HTTP="${4:-}"
  local line
  line=$(python3 "$SOURCE/Infrastructure/scripts/deploy_emit_event.py" | python3 "$JSON_LINE")
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
  sleep 2
  local rc=0
  local pair url name code curl_rc attempt body
  # Per-run, per-service body file so we never read a stale body from a
  # prior deploy's successful curl (that is how 2025-04-17's deploy ended
  # up logging onewire's body on the backend-health-fail line).
  local bodydir="/tmp/cea_health_$$"
  mkdir -p "$bodydir"
  trap 'rm -rf "$bodydir" 2>/dev/null || true' RETURN
  for pair in "http://127.0.0.1:8000/health|backend" "http://127.0.0.1:8001/health|automation" "http://127.0.0.1:8004/health|onewire"; do
    url="${pair%%|*}"
    name="${pair##*|}"
    code="000"
    for attempt in {1..90}; do
      : > "$bodydir/$name.body"
      # Capture curl's exit code explicitly. The previous form used
      # `|| echo "000"`, which concatenated curl's partial `%{http_code}`
      # output with "000" on timeout (producing "200000"). That triggered
      # a false-negative health_fail -> auto-rollback even when services
      # were green.
      set +e
      code=$(curl -sS -m 10 -o "$bodydir/$name.body" -w "%{http_code}" "$url" 2>/dev/null)
      curl_rc=$?
      set -e
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
  return "$rc"
}

rollback_previous() {
  if [[ -z "${PREVIOUS_RELEASE:-}" ]] || [[ ! -d "$PREVIOUS_RELEASE" ]]; then
    log_event "rollback_auto_fail" "no_previous_release_or_missing_dir"
    return 1
  fi
  sudo ln -sfn "$PREVIOUS_RELEASE" /opt/projectcea/current
  log_event "rollback_auto_symlink" "reverted_to_previous"
  restart_services
  log_event "rollback_auto_done" "services_restarted_after_revert"
  return 0
}

echo "=== Deploying release: $RELEASE_ID ==="
sudo mkdir -p /var/lib/projectcea
log_event "deploy_start" ""

echo "[0/7] Running Ruff (lint + format) on Infrastructure..."
cd "$SOURCE"
ruff check --fix Infrastructure/
ruff format Infrastructure/
cd - >/dev/null

echo "[1/7] Copying code..."
sudo mkdir -p "$TARGET"
sudo rsync -a --delete "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"
sudo chown -R root:root "$TARGET"

echo "[2/7] Building Python venvs..."
for svc in backend automation-service can-processor-service soil-sensor-service onewire-worker-service weather-service; do
  if [[ -f "$TARGET/Infrastructure/$svc/requirements.txt" ]]; then
    echo "  - $svc"
    cd "$TARGET/Infrastructure/$svc"
    sudo python3 -m venv .venv
    sudo .venv/bin/pip install -q --upgrade pip
    sudo .venv/bin/pip install -q -r requirements.txt
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

echo "[5/7] Switching symlink..."
sudo ln -sfn "$TARGET" /opt/projectcea/current

echo "[6/7] Restarting services..."
restart_services

echo "[7/7] Health checks..."
set +e
run_health_checks
HEALTH_RC=$?
set -e

if [[ "$HEALTH_RC" -ne 0 ]]; then
  rollback_previous || true
  log_event "deploy_fail" "health_checks_failed"
  echo "=== DEPLOY FAILED (health); symlink restored when possible ===" >&2
  exit 1
fi

MANIFEST_PATH="/opt/projectcea/current/deploy_manifest.json"
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

sudo env RELEASE_ID="$RELEASE_ID" TARGET="$TARGET" PREVIOUS_RELEASE="${PREVIOUS_RELEASE:-}" STATE_JSON="$STATE_JSON" python3 - <<'PY'
import json, pathlib, datetime, os
prev = os.environ.get("PREVIOUS_RELEASE") or ""
state = {
  "last_good_release_id": os.environ["RELEASE_ID"],
  "last_good_release_path": os.environ["TARGET"],
  "rollback_to_path": prev or None,
  "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
path = pathlib.Path(os.environ["STATE_JSON"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(state, indent=2) + "\n")
PY
sudo chmod 644 "$STATE_JSON" 2>/dev/null || true

log_event "deploy_success" ""

echo "Cleaning old releases..."
cd "$RELEASES"
ls -1t | tail -n +$((MAX_RELEASES + 1)) | xargs -r sudo rm -rf

echo ""
echo "=== Deploy complete: $RELEASE_ID ==="
echo "Current release: $(readlink /opt/projectcea/current)"
echo "Deploy log (NDJSON): $DEPLOY_LOG"
echo "Manual rollback: $SOURCE/rollback-deploy.sh"
