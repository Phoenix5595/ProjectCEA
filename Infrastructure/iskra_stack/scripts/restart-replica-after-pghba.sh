#!/bin/sh
# Run on iskra after mothernode has updated pg_hba.conf and reloaded PostgreSQL.
# Restarts projectcea_database so it retries pg_basebackup and streaming replication.
# Usage: ./scripts/restart-replica-after-pghba.sh
# Or from stack dir: sg docker -c "docker compose restart projectcea_database"

set -e
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker not found. Run this script on iskra (projectcea VM)."
  exit 1
fi
if [ -n "$(id -nG 2>/dev/null | tr ' ' '\n' | grep -x docker)" ]; then
  docker compose restart projectcea_database
else
  sg docker -c "docker compose restart projectcea_database"
fi
echo "projectcea_database restart requested. Check 'docker compose ps' in ~1–2 min."
