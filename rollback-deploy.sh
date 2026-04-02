#!/usr/bin/env bash
# Point /opt/projectcea/current at a prior release and restart services.
# Usage:
#   ./rollback-deploy.sh                    # use rollback_to_path from deploy_state.json
#   ./rollback-deploy.sh <release_id_or_subdir>

set -euo pipefail

SOURCE="/home/antoine/ProjectCEA"
RELEASES="/opt/projectcea/releases"
STATE="/var/lib/projectcea/deploy_state.json"
DEPLOY_LOG="/var/lib/projectcea/deploy.log"
JSON_LINE="$SOURCE/Infrastructure/scripts/deploy_json_line.py"
EMIT="$SOURCE/Infrastructure/scripts/deploy_emit_event.py"

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

resolve_target() {
  local arg="${1:-}"
  if [[ -z "$arg" ]]; then
    if [[ ! -f "$STATE" ]]; then
      echo "No $STATE — pass a release id or path under $RELEASES" >&2
      exit 1
    fi
    arg="$(python3 -c "import json, pathlib; print(json.loads(pathlib.Path('$STATE').read_text()).get('rollback_to_path') or '')")"
    if [[ -z "$arg" || "$arg" == "None" ]]; then
      echo "deploy_state.json has no rollback_to_path. Pass a release directory." >&2
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
CURRENT="$(readlink -f /opt/projectcea/current 2>/dev/null || true)"

log_rollback "rollback_manual_start" "switching_symlink" "$RB_TGT" "$CURRENT"

sudo mkdir -p /var/lib/projectcea
sudo ln -sfn "$RB_TGT" /opt/projectcea/current

sudo systemctl daemon-reload
sudo systemctl restart can-setup
sleep 1
sudo systemctl restart can-processor cea-backend onewire-worker
sleep 2
sudo systemctl restart automation-service soil-sensor-service weather-service

sleep 3
FAILED=""
for pair in "http://127.0.0.1:8000/health|backend" "http://127.0.0.1:8001/health|automation" "http://127.0.0.1:8004/health|onewire"; do
  url="${pair%%|*}"
  name="${pair##*|}"
  code=$(curl -sS -m 30 -o /tmp/cea_rb_health.txt -w "%{http_code}" "$url" || echo "000")
  if [[ "$code" != "200" ]]; then
    FAILED="${FAILED:+$FAILED,}$name"
  fi
done

if [[ -n "$FAILED" ]]; then
  log_rollback "rollback_manual_health_fail" "failed_services:$FAILED" "$RB_TGT" "$CURRENT"
  exit 1
fi

log_rollback "rollback_manual_ok" "health_ok" "$RB_TGT" "$CURRENT"
echo "Rollback complete: current -> $RB_TGT"
