#!/bin/sh
# Run on iskra with sudo. Fixes PGDATA ownership so the postgres container (UID 999) can start.
# Usage: sudo ./scripts/fix-pgdata-ownership.sh [PGDATA_PATH]
# Default PGDATA_PATH: /srv/storage1/projectcea_database/data

set -e
PGDATA_PATH="${1:-/srv/storage1/projectcea_database/data}"
if [ ! -d "$PGDATA_PATH" ]; then
  echo "Error: $PGDATA_PATH does not exist"
  exit 1
fi
chown -R 999:999 "$PGDATA_PATH"
echo "Ownership set to 999:999 for $PGDATA_PATH"
