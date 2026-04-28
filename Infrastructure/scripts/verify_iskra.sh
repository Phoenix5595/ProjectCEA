#!/usr/bin/env bash
# Verify the offsite ProjectCEA stack on iskraprojectcea.

set -euo pipefail

ISKRA_HOST="${ISKRA_HOST:-iskraprojectcea}"
ISKRA_DIR="${ISKRA_DIR:-/home/antoine/ProjectCEA/Infrastructure/iskra_stack}"
GRAFANA_URL="${GRAFANA_URL:-http://iskraprojectcea:3001}"

echo "[verify_iskra] checking remote compose stack on $ISKRA_HOST"
ssh "$ISKRA_HOST" "cd '$ISKRA_DIR' && docker compose ps"

echo "[verify_iskra] checking container health"
ssh "$ISKRA_HOST" "docker inspect -f '{{.Name}} {{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' projectcea_database projectcea_redis projectcea_grafana"

echo "[verify_iskra] checking Grafana API: $GRAFANA_URL/api/health"
curl -fsS "$GRAFANA_URL/api/health" >/dev/null

echo "[verify_iskra] checking primary replication lag view"
if command -v psql >/dev/null 2>&1; then
  psql_cmd=(psql -X -tA -d cea_sensors -c "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM replay_lag)), 0)::numeric(12,3) FROM pg_stat_replication;")
  if sudo -n -u postgres true >/dev/null 2>&1; then
    sudo -n -u postgres "${psql_cmd[@]}" | awk '{print "[verify_iskra] max_replay_lag_seconds=" $0}'
  else
    "${psql_cmd[@]}" | awk '{print "[verify_iskra] max_replay_lag_seconds=" $0}'
  fi
else
  echo "[verify_iskra] psql not available locally; skipped pg_stat_replication check" >&2
fi

echo "[verify_iskra] ok"
