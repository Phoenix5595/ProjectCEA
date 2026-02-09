# ProjectCEA TimescaleDB replica (iskra)

**Docker only on iskra.** This directory runs on **iskra** only. Mothernode runs PostgreSQL + TimescaleDB natively (systemd); there is no Docker on mothernode. The container `projectcea_database` is a streaming standby that replicates from mothernode; Grafana on iskra can use it as a read-only datasource.

You can **SSH into iskra** to perform all iskra-side setup: create the PGDATA directory on Storage 1, copy `.env`, run `docker compose`, and verify replication.

---

## Before implementing: check these

Verify and document the values you use (e.g. in `.env` and this README).

| Check | Where | Why |
|-------|--------|-----|
| **Storage 1 mount path** | Iskra | Actual path for PGDATA bind mount (e.g. `/mnt/storage1`, `/mnt/data`, `/srv/storage1`). Set `PGDATA_HOST_PATH` in `.env`. |
| **PostgreSQL major version** | Mothernode | Replica image must match (e.g. `timescale/timescaledb:latest-pg16`). Check with `psql --version` or `SELECT version();`. |
| **listen_addresses** | Mothernode | Primary must accept TCP from iskra. If only `localhost`, set `listen_addresses = '*'` in `postgresql.conf` and restart PostgreSQL. |
| **Port 5432 on iskra** | Iskra | If another service uses 5432, set `POSTGRES_PORT=5433` in `.env` and use that port in Grafana. |

---

## Mothernode setup (no Docker)

On **mothernode** (Raspberry Pi), configure PostgreSQL for streaming replication.

1. **Replication user**  
   Run on mothernode:
   ```bash
   sudo -u postgres psql -d cea_sensors -f mothernode_replication_user.sql
   ```
   Edit the SQL file first to set a secure password, then put the same password in iskra’s `.env` as `REPLICATION_PASSWORD`.

2. **postgresql.conf**  
   Set:
   - `wal_level = replica`
   - `max_wal_senders = 2`
   - `wal_keep_size = 64MB`
   - `listen_addresses = '*'` (or the primary’s LAN/Tailscale IP)  
   Restart: `sudo systemctl restart postgresql`.

3. **pg_hba.conf**  
   Add (use iskra’s LAN or Tailscale IP):
   ```
   host replication cea_repl <iskra_ip>/32 scram-sha-256
   ```
   Reload: `sudo systemctl reload postgresql`.

4. **(Optional) Replication slot**  
   On mothernode: `SELECT * FROM pg_create_physical_replication_slot('iskra_1');`  
   On iskra: set `REPLICATION_SLOT=iskra_1` in `.env`. Recommended for Tailscale or unstable links.

---

## Iskra setup (Docker)

All steps below are on **iskra** (e.g. via SSH).

1. **Storage 1**  
   Create PGDATA directory and set ownership (Postgres in image typically uses UID 999):
   ```bash
   sudo mkdir -p /mnt/storage1/projectcea_database/data
   sudo chown -R 999:999 /mnt/storage1/projectcea_database/data
   ```
   Use your actual Storage 1 mount path if different.

2. **Compose project**  
   Clone or copy this repo (or at least `Infrastructure/database-replica/`) to iskra (e.g. on boot SSD). Enter the directory:
   ```bash
   cd /path/to/ProjectCEA/Infrastructure/database-replica
   ```

3. **Environment**  
   Copy `.env.example` to `.env` and set:
   - `PRIMARY_HOST` – mothernode hostname or IP (LAN or Tailscale)
   - `REPLICATION_PASSWORD` – same as on mothernode
   - `PGDATA_HOST_PATH` – path on Storage 1 (e.g. `/mnt/storage1/projectcea_database/data`)
   - `POSTGRES_PORT` – 5432 or 5433 if 5432 is in use

4. **Entrypoint**  
   Make the script executable:
   ```bash
   chmod +x docker-entrypoint-replica.sh
   ```

5. **Start**  
   ```bash
   docker compose up -d
   ```
   First run runs `pg_basebackup` (can take several minutes); then Postgres starts as a standby and keeps streaming.

---

## Verify

- **Mothernode:** `SELECT * FROM pg_stat_replication;` – one row when iskra is connected.
- **Iskra (in container or from host):** `SELECT * FROM pg_stat_wal_receiver;` – shows connection to primary.
- **Grafana on iskra:** Add PostgreSQL datasource: host `localhost`, port as in `POSTGRES_PORT`, database `cea_sensors`, user `cea_user`, same password as on mothernode. Enable TimescaleDB if available. Run e.g. `SELECT COUNT(*) FROM measurement;` to confirm read-only access to the replica.

---

## Start/stop and logs

- Start: `docker compose up -d`
- Stop: `docker compose down`
- Logs: `docker compose logs -f projectcea_database`

The container uses `restart: unless-stopped` so it starts automatically on iskra boot.
