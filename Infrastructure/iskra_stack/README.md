# ProjectCEA stack on iskraprojectcea

Single Docker Compose stack on **iskraprojectcea** that groups: **projectcea_database** (TimescaleDB replica), **projectcea_redis**, **projectcea_grafana**, and **projectcea_redis_sync** (syncs latest sensor values from replica DB to Redis). Grafana reads **current values from Redis** and **time-series from PostgreSQL** so the primary DB is not hammered for live panels.

**Docker only on iskraprojectcea.** Mothernode runs PostgreSQL, Redis, and the automation/backend services natively (systemd). The Pi has **no** local Grafana — the SPA embeds the Grafana running here (Phase 5b relocation).

> **Phase 5b note**: the host port for Grafana is **3001** (not 3000) because port 3000 is taken by another homelab service on this VM. The container still listens on 3000 internally; only the host-side mapping changed. The Pi's pg_hba on the replica must include `host all all 172.18.0.0/16 scram-sha-256` so containers on the projectcea_network can authenticate as `cea_user`. Datasource secret interpolation uses Grafana's **native** `$VAR` syntax in the provisioning YAML — no envsubst entrypoint required.

## Before implementing

- [ ] **Storage 1 path**: Set `PGDATA_HOST_PATH` in `.env` (e.g. `/srv/storage1/projectcea_database/data`). PGDATA must exist and be owned by `999:999`.
- [ ] **Mothernode replication**: Replication user and `postgresql.conf` / `pg_hba.conf` on mothernode must allow replication from iskra (Tailscale IP 100.72.106.76 for `cea_repl`). See `Infrastructure/database-replica/README.md`. After changing `pg_hba.conf` on mothernode, **reload PostgreSQL** then **on iskra** run: `sg docker -c "docker compose restart projectcea_database"` (or `./scripts/restart-replica-after-pghba.sh`).
- [ ] **Replication slot (mandatory)**: On the primary, `SELECT pg_create_physical_replication_slot('iskra_recovery');`. Then in iskra `.env`, set `REPLICATION_SLOT=iskra_recovery`. The standby entrypoint refuses to start with this unset. Without an active slot the primary will recycle WAL the standby still needs (capped by `wal_keep_size` + `max_slot_wal_keep_size` on mothernode), Grafana will silently freeze, and you will need to re-base from scratch (see "Recovery: standby fell behind / WAL removed" below).
- [ ] **Passwords**: Copy `.env.example` to `.env` and set `REPLICATION_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` (e.g. `openssl rand -hex 16`), `POSTGRES_CEA_USER_PASSWORD` (same as `cea_user` on mothernode).

## On iskra (step-by-step)

Do these steps **on iskra** (e.g. via SSH).

1. **Get the stack**  
   Sync from mothernode (or clone the repo) so `ProjectCEA/Infrastructure/iskra_stack/` exists, e.g.:
   ```bash
   # From mothernode: rsync -avz --exclude=.env Infrastructure/iskra_stack/ iskra:ProjectCEA/Infrastructure/iskra_stack/
   # On iskra: ensure the directory exists first: mkdir -p ProjectCEA/Infrastructure/iskra_stack
   ```

2. **Environment**  
   In `ProjectCEA/Infrastructure/iskra_stack/`:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set:
   - `PRIMARY_HOST` – mothernode hostname (e.g. `mothernode.tail7a351e.ts.net`). Also used by Grafana’s **CEA Primary (ops)** datasource (alert rules).
   - `REPLICATION_SLOT` – must be `iskra_recovery` (matches the physical slot on the primary).
   - `REPLICATION_PASSWORD` – same as on mothernode for `cea_repl`
   - `POSTGRES_CEA_USER_PASSWORD` – same as `cea_user` password on mothernode (for Grafana + redis_sync). Replication keeps `pg_authid` byte-for-byte identical, so this **must** match the rotated value on the primary.
   - `GRAFANA_ADMIN_PASSWORD` – Grafana admin password (use `openssl rand -hex 16`)
   - `PGDATA_HOST_PATH` – path on iskra for DB data (e.g. `/srv/storage1/projectcea_database/data`)

3. **PGDATA ownership**  
   The Postgres process in the container runs as UID 999. The data directory on the host must be owned by `999:999`:
   ```bash
   sudo chown -R 999:999 /srv/storage1/projectcea_database/data
   ```
   (Use your actual `PGDATA_HOST_PATH` if different.)

4. **Optional: replace standalone database**  
   If you previously ran only the database container from `projectcea_database/`:
   ```bash
   cd ~/projectcea_database && docker compose down
   ```
   Then use the **same** `PGDATA_HOST_PATH` in the new stack so the replica data is reused.

5. **Start the stack**  
   ```bash
   cd ~/ProjectCEA/Infrastructure/iskra_stack
   chmod +x docker-entrypoint-replica.sh
   docker compose up -d --build
   ```

6. **Check**  
   ```bash
   docker compose ps
   ```
   All four services should be Up (projectcea_database may take up to ~60 s to become healthy; Grafana ~30 s).  
   **Grafana**: `http://<iskraprojectcea>:3001`. Login: admin / `GRAFANA_ADMIN_PASSWORD`.

## After mothernode pg_hba.conf change

When mothernode adds or changes replication entries (e.g. for iskra Tailscale IP 100.72.106.76), **on iskra** restart the replica so it retries `pg_basebackup` and streaming replication:

```bash
cd ~/ProjectCEA/Infrastructure/iskra_stack
sg docker -c "docker compose restart projectcea_database"
# Or: ./scripts/restart-replica-after-pghba.sh
```

Or restart the whole stack: `sg docker -c "docker compose up -d"`. Within ~1–2 minutes `projectcea_database` should become healthy.

## Quick start (if already set up)

1. **Replace standalone database** (if you previously ran only `projectcea_database`): stop that compose, then start this stack using the **same** `PGDATA_HOST_PATH` so the replica data is reused.
2. From this directory:
   ```bash
   cp .env.example .env
   # Edit .env: PRIMARY_HOST, REPLICATION_PASSWORD, POSTGRES_CEA_USER_PASSWORD, GRAFANA_ADMIN_PASSWORD, PGDATA_HOST_PATH
   docker compose up -d
   ```
3. **Grafana**: `http://<iskraprojectcea>:3001`. Admin password from `GRAFANA_ADMIN_PASSWORD`.
4. **Datasources**: Provisioned automatically. PostgreSQL → `projectcea_database` (CEA Sensors, uid `bf6vebq5ipybke`; CEA PostgreSQL, uid `cea_postgres`). Redis → `projectcea_redis` (uid `bf9yw6nuqt81sa`). The `cea_user` password is interpolated by Grafana's native `$VAR` substitution from `POSTGRES_CEA_USER_PASSWORD` directly inside the provisioning YAML — no rendered file is committed.

## Redis population

Redis is filled by **projectcea_redis_sync** (sync-from-DB):

- Every `SYNC_INTERVAL_SEC` (default 10 s), the sync service queries the replica DB view `latest_sensor_values` and writes `sensor:<name>` and `sensor:<name>:ts` keys to Redis (TTL 30 s).
- Grafana can use Redis for current-value panels (stat, table) so the DB is not queried every second for “latest”.

**Alternative (optional):** If mothernode Redis is reachable from iskra (e.g. Tailscale), you can configure iskra Redis as a **replica of mothernode Redis** so `sensor:*` keys are replicated. Then you can stop or disable `projectcea_redis_sync`. Document this in your runbook; this stack implements sync-from-DB by default.

## Services

| Service                | Role                                                                 |
|------------------------|----------------------------------------------------------------------|
| projectcea_database    | TimescaleDB streaming standby; Grafana time-series and aggregates.  |
| projectcea_redis       | Current/live values for Grafana; populated by redis_sync (or replication). |
| projectcea_grafana     | Same dashboards/provisioning as mothernode; PostgreSQL + Redis datasources. |
| projectcea_redis_sync  | Periodic read of `latest_sensor_values` from replica DB → Redis.     |

## Files

- `docker-compose.yml`: All four services; shared network.
- `.env.example` / `.env`: DB replica, Grafana, Redis sync config.
- `docker-entrypoint-replica.sh`: Replica entrypoint; creates minimal `postgresql.conf` / `pg_hba.conf` in PGDATA when missing. Always starts `postgres` with `-c listen_addresses=*` so Grafana and `redis_sync` can connect over the compose network (base backups often inherit `listen_addresses = localhost` from the primary’s data directory stub).
- `provisioning/datasources/datasources.yaml.template`: Grafana datasources. The `${POSTGRES_CEA_USER_PASSWORD}` placeholder is interpolated by Grafana itself at provisioning time using its built-in env-var support — the file is mounted into the container directly as `cea-datasources.yaml`, no envsubst step required.
- `provisioning/alerting/replication_slot.yaml`: Provisioned alert against the Pi primary (`CEA Primary (ops)` datasource) for the `iskra_recovery` replication slot. Notifications go to contact point **Tony** (email migrated from the Pi Grafana). If you rename that contact point in Grafana, update `notification_settings.receiver` in this YAML.
- `provisioning/dashboards/dashboards.yaml`: Dashboard providers (veg_sector, flower_sector, flower_sector_soil, laboratory).
- `dashboards/`: Dashboard JSONs (veg_sector, flower_sector, flower_sector_soil; laboratory is a placeholder folder).
- `scripts/redis_sync.py`: Sync script (used by built image).
- `Dockerfile.redis_sync`: Builds image for projectcea_redis_sync.

## Recovery: standby fell behind / WAL removed

Symptoms: Grafana panels show no recent data; primary log repeats `requested WAL segment ... has already been removed` for `cea_repl`; `pg_stat_replication` on mothernode is empty; `pg_replication_slots` shows `iskra_recovery` with `active = f` and often `restart_lsn` NULL if the standby never consumed the slot.

**Order matters.** Use the actual `PGDATA_HOST_PATH` from iskra `.env` (repo default was `/srv/storage1/...`; some installs use `/var/lib/projectcea_database/data`).

1. **On iskraprojectcea**: stop dependents and the DB container:
   ```bash
   cd ~/ProjectCEA/Infrastructure/iskra_stack
   sg docker -c "docker compose stop projectcea_grafana projectcea_redis_sync projectcea_database"
   ```
2. **On iskraprojectcea**: ensure `.env` contains `REPLICATION_SLOT=iskra_recovery` (required by `docker-entrypoint-replica.sh`).
3. **On iskraprojectcea**: wipe PGDATA (example for `/var/lib/projectcea_database/data` — adjust to your `PGDATA_HOST_PATH`):
   ```bash
   docker run --rm -v /var/lib/projectcea_database/data:/data alpine:3.20 sh -c 'rm -rf /data/* /data/.[!.]*'
   ```
   Or `sudo rm -rf ...` if you have host access. Ensure the directory is empty before the next step.
4. **On mothernode** (as `postgres` superuser):
   ```bash
   sudo -u postgres psql -d cea_sensors <<'SQL'
   SELECT pg_drop_replication_slot('iskra_recovery');
   SELECT pg_create_physical_replication_slot('iskra_recovery');
   SQL
   ```
5. **On iskraprojectcea**: start the database and wait for `pg_basebackup` + “ready to accept read-only connections” in `docker logs -f projectcea_database`, then bring the rest up:
   ```bash
   sg docker -c "docker compose up -d projectcea_database"
   # wait for base backup
   sg docker -c "docker compose up -d projectcea_redis_sync projectcea_grafana"
   ```
6. **Verify on mothernode**:
   ```sql
   SELECT application_name, client_addr, state FROM pg_stat_replication;
   SELECT slot_name, active, restart_lsn FROM pg_replication_slots WHERE slot_name = 'iskra_recovery';
   ```
   Expect one streaming row and `active = t` with a non-null `restart_lsn` that advances.

## Architecture docs

See project root **ARCHITECTURE.md** and **ARCHITECTURE_SCHEMATIC.md**: subsection “ProjectCEA stack (iskra)” (database replica + Grafana + Redis; Grafana reads Redis for current values and PostgreSQL for time-series).

---

## Copy-paste checklist (on iskra)

After the stack is synced to iskra (e.g. `ProjectCEA/Infrastructure/iskra_stack/`):

```bash
cd ~/ProjectCEA/Infrastructure/iskra_stack

# 1. .env (create from example, then edit with your values)
cp .env.example .env
# Set: PRIMARY_HOST, REPLICATION_SLOT=iskra_recovery, REPLICATION_PASSWORD,
# POSTGRES_CEA_USER_PASSWORD, GRAFANA_ADMIN_PASSWORD, PGDATA_HOST_PATH

# 2. PGDATA ownership (postgres in container = UID 999)
sudo chown -R 999:999 /srv/storage1/projectcea_database/data

# 3. Optional: stop old standalone database if you had one
# (cd ~/projectcea_database && docker compose down)

# 4. Start stack
chmod +x docker-entrypoint-replica.sh
docker compose up -d --build

# 5. Check (database may take ~60s to become healthy)
docker compose ps
```

Grafana: **http://&lt;iskraprojectcea&gt;:3001**. Login: **admin** / value of `GRAFANA_ADMIN_PASSWORD`.
