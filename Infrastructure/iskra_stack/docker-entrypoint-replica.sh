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
# REPLICATION_SLOT is mandatory: a slot-less standby silently breaks Grafana
# the moment WAL needed by the standby is recycled past wal_keep_size on the
# primary. See Infrastructure/REQUIREMENTS.md "Replication durability".
REPLICATION_SLOT="${REPLICATION_SLOT:?REPLICATION_SLOT required (must match a physical slot created on the primary, e.g. iskra_recovery)}"

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
  # REPLICATION_SLOT is guaranteed non-empty by the :? guard above; always
  # pin the standby to it so the primary throttles WAL recycling for us.
  echo "primary_slot_name = '$REPLICATION_SLOT'" >> "$PGDATA/postgresql.auto.conf"
  chown postgres:postgres "$PGDATA/postgresql.auto.conf" 2>/dev/null || true
  echo "Base backup done; starting standby."
fi

# If primary uses config outside PGDATA (e.g. /etc/postgresql), replica may have no postgresql.conf
if [ ! -f "$PGDATA/postgresql.conf" ]; then
  echo "Creating minimal postgresql.conf for standby..."
  cat > "$PGDATA/postgresql.conf" << 'PGCONF'
data_directory = '/var/lib/postgresql/data'
hot_standby = on
listen_addresses = '*'
port = 5432
PGCONF
  chown postgres:postgres "$PGDATA/postgresql.conf" 2>/dev/null || true
fi

# Same situation for pg_hba.conf: Debian/Ubuntu primaries keep it under
# /etc/postgresql/, so pg_basebackup ships PGDATA without it. Without a file
# here PostgreSQL refuses to start ("could not load pg_hba.conf"). Provide a
# minimal-but-functional one: trust local socket (used by the container's
# healthcheck), scram-sha-256 over TCP for the docker network and Tailscale.
if [ ! -f "$PGDATA/pg_hba.conf" ]; then
  echo "Creating minimal pg_hba.conf for standby..."
  cat > "$PGDATA/pg_hba.conf" << 'HBACONF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             all                                     trust
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
# Docker bridge network: Grafana + redis_sync connect via service names that
# resolve to 172.x addresses inside the projectcea_network bridge.
host    all             all             172.16.0.0/12           scram-sha-256
host    replication     all             172.16.0.0/12           scram-sha-256
HBACONF
  chown postgres:postgres "$PGDATA/pg_hba.conf" 2>/dev/null || true
fi

# Start postgres server. Iskra is the Grafana read replica, so allow a higher
# connection ceiling than the Pi primary while keeping the setting explicit in
# compose. The replica must also use Quebec local time, matching the primary:
# timestamptz storage remains UTC internally, but text formatting and ::time
# casts used by dashboards/alert SQL follow the session timezone.
#
# Hot-standby parameter rule (PostgreSQL 15): max_connections,
# max_worker_processes, max_locks_per_transaction and max_prepared_transactions
# on the standby MUST be >= the primary or recovery aborts with
# "insufficient parameter settings". Pi primary uses max_worker_processes=23
# (Timescale background workers + parallel query) and max_locks_per_transaction=128;
# bump comfortably past that here. They are passed via -c so they override
# postgresql.conf left behind by an earlier pg_basebackup.
export PGDATA
exec gosu postgres postgres \
  -c "max_connections=${POSTGRES_MAX_CONNECTIONS:-150}" \
  -c "max_worker_processes=${POSTGRES_MAX_WORKER_PROCESSES:-32}" \
  -c "max_locks_per_transaction=${POSTGRES_MAX_LOCKS_PER_XACT:-256}" \
  -c "max_prepared_transactions=${POSTGRES_MAX_PREPARED_TRANSACTIONS:-0}" \
  -c "timezone=${POSTGRES_TIMEZONE:-America/Toronto}"
