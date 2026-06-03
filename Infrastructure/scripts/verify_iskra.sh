#!/usr/bin/env bash
# Verify the offsite ProjectCEA stack on iskraprojectcea.

set -euo pipefail

ISKRA_HOST="${ISKRA_HOST:-iskraprojectcea}"
ISKRA_DIR="${ISKRA_DIR:-/home/antoine/ProjectCEA/Infrastructure/iskra_stack}"
GRAFANA_URL="${GRAFANA_URL:-http://iskraprojectcea:3001}"
ISKRA_REPLICA_QUERY_TIMEOUT_SEC="${ISKRA_REPLICA_QUERY_TIMEOUT_SEC:-15}"

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

echo "[verify_iskra] replica read smoke: SELECT count(*) FROM latest_sensor_values (timeout ${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}s)"
set +e
replica_rows="$(
  ssh "$ISKRA_HOST" "timeout '${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}'s docker exec projectcea_database psql -U cea_user -d cea_sensors -v ON_ERROR_STOP=1 -tA -c 'SELECT count(*) FROM latest_sensor_values;'" 2>/dev/null
)"
replica_ec=$?
set -e
if [[ "$replica_ec" -eq 124 ]]; then
  echo "[verify_iskra] ERROR: replica query timed out after ${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}s (replay_lag alone is not sufficient — see Infrastructure/REQUIREMENTS.md)" >&2
  exit 1
fi
if [[ "$replica_ec" -ne 0 ]] || [[ -z "${replica_rows// /}" ]]; then
  echo "[verify_iskra] ERROR: replica latest_sensor_values query failed (exit $replica_ec)" >&2
  exit 1
fi
echo "[verify_iskra] replica latest_sensor_values_count=${replica_rows// /}"

echo "[verify_iskra] replica ingest smoke: dry_bulb_b rows last 15 min (timeout ${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}s)"
set +e
fresh_n="$(
  ssh "$ISKRA_HOST" "timeout '${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}'s docker exec projectcea_database psql -U cea_user -d cea_sensors -v ON_ERROR_STOP=1 -tA -c \"SELECT COUNT(*) FROM measurement m JOIN sensor s ON s.sensor_id = m.sensor_id WHERE s.name = 'dry_bulb_b' AND m.time > now() - interval '15 minutes';\"" 2>/dev/null
)"
fresh_ec=$?
set -e
if [[ "$fresh_ec" -eq 124 ]]; then
  echo "[verify_iskra] ERROR: replica freshness query timed out after ${ISKRA_REPLICA_QUERY_TIMEOUT_SEC}s" >&2
  exit 1
fi
if [[ "$fresh_ec" -ne 0 ]] || [[ -z "${fresh_n// /}" ]]; then
  echo "[verify_iskra] ERROR: replica freshness query failed (exit $fresh_ec)" >&2
  exit 1
fi
fresh_trim="$(printf '%s' "$fresh_n" | tr -d '[:space:]')"
if [[ "$fresh_trim" -eq 0 ]]; then
  echo "[verify_iskra] ERROR: no dry_bulb_b measurements in last 15 minutes on replica — Pi ingest or replication path broken (see Infrastructure/iskra_stack/README.md)" >&2
  exit 1
fi
echo "[verify_iskra] replica dry_bulb_b_rows_last_15m=${fresh_trim}"

echo "[verify_iskra] ok"
