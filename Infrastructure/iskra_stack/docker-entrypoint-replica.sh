#!/usr/bin/env bash
set -e

# ProjectCEA TimescaleDB streaming standby entrypoint (iskra only).
# If PGDATA is empty, run pg_basebackup from primary then start postgres.
# Otherwise start postgres (standby reconnects via primary_conninfo).

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
# Replication connection (from env)
PRIMARY_HOST="${PRIMARY_HOST:?PRIMARY_HOST required}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-cea_repl}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:?REPLICATION_PASSWORD required}"
REPLICATION_SLOT="${REPLICATION_SLOT:-}"

# .pgpass in postgres home so standby can connect to primary
pgpass_file="/var/lib/postgresql/.pgpass"
mkdir -p "$(dirname "$pgpass_file")"
echo "${PRIMARY_HOST}:${PRIMARY_PORT}:*:${REPLICATION_USER}:${REPLICATION_PASSWORD}" > "$pgpass_file"
chmod 600 "$pgpass_file"
chown postgres:postgres "$pgpass_file" 2>/dev/null || true

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "PGDATA empty; running pg_basebackup from ${PRIMARY_HOST}:${PRIMARY_PORT}..."
  export PGPASSWORD="$REPLICATION_PASSWORD"
  gosu postgres pg_basebackup -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPLICATION_USER" -D "$PGDATA" -Fp -Xs -P -R
  unset PGPASSWORD
  if [ -n "$REPLICATION_SLOT" ]; then
    echo "primary_slot_name = '$REPLICATION_SLOT'" >> "$PGDATA/postgresql.auto.conf"
    chown postgres:postgres "$PGDATA/postgresql.auto.conf" 2>/dev/null || true
  fi
  echo "Base backup done; starting standby."
fi

# If primary uses config outside PGDATA (e.g. /etc/postgresql), replica may have no postgresql.conf
if [ ! -f "$PGDATA/postgresql.conf" ]; then
  echo "Creating minimal postgresql.conf for standby..."
  cat > "$PGDATA/postgresql.conf" << 'PGCONF'
data_directory = '/var/lib/postgresql/data'
hot_standby = on
listen_addresses = 'localhost'
port = 5432
PGCONF
  chown postgres:postgres "$PGDATA/postgresql.conf" 2>/dev/null || true
fi

# Start postgres server (no args: use PGDATA from env)
export PGDATA
exec gosu postgres postgres
