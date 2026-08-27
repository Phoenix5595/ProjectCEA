# Iskra Grafana and replica runbook

Canonical operator runbook for the ProjectCEA stack that runs on `iskraprojectcea`. It is the only active Grafana/replica deployment document. Durable query, aggregate, setpoint, and photoperiod contracts live in [`Infrastructure/database/REQUIREMENTS.md`](../database/REQUIREMENTS.md). Frontend embedding and API contracts live in [`Infrastructure/frontend/REQUIREMENTS.md`](../frontend/REQUIREMENTS.md) and [`Infrastructure/frontend/DESIGN.md`](../frontend/DESIGN.md).

## What runs here

| Service | Image | Role |
|---|---|---|
| `projectcea_database` | `timescale/timescaledb:2.28.3-pg15` | Streaming standby of mothernode primary |
| `projectcea_redis` | `redis:7-alpine` | Local live-value cache |
| `projectcea_redis_sync` | `projectcea_redis_sync:local` | Copies `latest_sensor_values` from replica DB to Redis |
| `projectcea_grafana` | `grafana/grafana:11.6.0` | Dashboards and alerting; host port `3001` |

Mothernode runs PostgreSQL primary, Redis, and automation/backend services natively. The Pi has no local Grafana; the SPA embeds `http://iskraprojectcea:3001`.

## Setup

1. On `iskraprojectcea`, ensure `ProjectCEA/Infrastructure/iskra_stack/` exists.
2. Copy `.env.example` to `.env` and fill:
   - `PRIMARY_HOST` — mothernode hostname.
   - `REPLICATION_SLOT=iskra_recovery` — must match a physical slot on the primary.
   - `REPLICATION_PASSWORD`, `POSTGRES_CEA_USER_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_SMTP_PASSWORD`.
   - `PGDATA_HOST_PATH` — host directory for replica data (default `/srv/storage1/projectcea_database/data`).
3. Create the slot on the primary before first start: `SELECT pg_create_physical_replication_slot('iskra_recovery');`
4. Ensure PGDATA is owned by `999:999`: `sudo chown -R 999:999 $PGDATA_HOST_PATH`.
5. `chmod +x docker-entrypoint-replica.sh && docker compose up -d --build`.
6. Wait up to ~60 s for `projectcea_database` to become healthy; Grafana takes ~30 s.

## Normal sync and verify

Push repo-canonical stack files to iskra:

```bash
Infrastructure/scripts/sync_to_iskra.sh
```

([`Infrastructure/scripts/sync_to_iskra.sh`](../scripts/sync_to_iskra.sh))

Check health end-to-end:

```bash
Infrastructure/scripts/verify_iskra.sh
```

([`Infrastructure/scripts/verify_iskra.sh`](../scripts/verify_iskra.sh))

`verify_iskra.sh` checks container health, Grafana `/api/health`, primary `replay_lag`, a timed `latest_sensor_values` count on the replica, and recent `dry_bulb_b` rows. A timeout on the replica query fails the check because WAL lag alone does not prove ingest.

After changing provisioning YAML or dashboard JSON, restart Grafana:

```bash
sg docker -c "docker compose restart projectcea_grafana"
```

After mothernode `pg_hba.conf` changes, restart the replica:

```bash
sg docker -c "docker compose restart projectcea_database"
```

## Datasource and dashboard ownership

All datasources and dashboards are provisioned from the repo. Do not edit datasources in the Grafana UI; change `provisioning/datasources/datasources.yaml.template` instead.

| Datasource | UID | Target |
|---|---|---|
| CEA Sensors | `bf6vebq5ipybke` | `projectcea_database:5432` (replica) |
| CEA PostgreSQL | `cea_postgres` | `projectcea_database:5432` (replica) |
| Redis | `bf9yw6nuqt81sa` | `projectcea_redis:6379` |
| CEA Primary (ops) | `cea_primary_ops` | Mothernode primary for ops alerts |

The PostgreSQL datasources set `database: cea_sensors` in both top-level `database` and `jsonData` (required for Grafana 12+). Connection pooling is 48 max open / 12 idle / 90 s max lifetime.

Dashboard JSON lives in `dashboards/`; providers are configured in `provisioning/dashboards/dashboards.yaml`.

## Redis live tables

`projectcea_redis_sync` reads the `latest_sensor_values` view every `SYNC_INTERVAL_SEC` (compose default `10`) and writes:

- `sensor:{name}` and `sensor:{name}:ts` with 30 s TTL.
- `cea:grafana:flower_averages`, `cea:grafana:flower_front`, `cea:grafana:flower_back` with 30 s TTL for Flower table panels.

The view must be the LATERAL / `ORDER BY time DESC LIMIT 1` implementation in `Infrastructure/database/grafana_optimized_views.sql`. A naive `DISTINCT ON` or `MAX(time)` over history can stall the replica even when `replay_lag` looks healthy.

## Postgres time series

Time-series panels use `get_sensor_data_optimized(...)` on the replica. The aggregate ladder and query guidance are in `Infrastructure/database/REQUIREMENTS.md`. The standby runs with `hot_standby_feedback=on`, `jit=off`, and `max_parallel_workers_per_gather=0` to avoid recovery-conflict cancellations.

## Alerting source

`provisioning/alerting/replication_slot.yaml` is the only provisioned alert rule. It evaluates every minute against `CEA Primary (ops)` and emails contact point `Tony` when the `iskra_recovery` slot is inactive or retains more than 8 GiB of WAL. Do not rename the `Tony` contact point without updating this file.

SMTP is configured via `GF_SMTP_*` env vars from `.env`; do not edit `grafana.ini` inside the container. Other alert rules and contact points live in Grafana's SQLite database (in the `projectcea_grafana_data` volume) and are managed through the Grafana UI or `/api/v1/provisioning`.

## Operator-only: replica recovery

The following is destructive and requires a human operator. Do not run it from an agent or automation. It drops the replication slot, wipes the replica data directory, and re-bases the standby from the primary.

Symptoms: Grafana panels show no recent data; primary log says requested WAL segment was already removed; `pg_stat_replication` on mothernode is empty; `pg_replication_slots` shows `iskra_recovery` inactive.

1. On `iskraprojectcea`, stop dependents and the DB container:
   ```bash
   sg docker -c "docker compose stop projectcea_grafana projectcea_redis_sync projectcea_database"
   ```
2. Confirm `.env` has `REPLICATION_SLOT=iskra_recovery`.
3. Wipe PGDATA using the path from `.env` (example below; substitute your actual `PGDATA_HOST_PATH`):
   ```bash
   docker run --rm -v /srv/storage1/projectcea_database/data:/data alpine:3.20 sh -c 'rm -rf /data/* /data/.[!.]*'
   ```
4. On mothernode, as `postgres`, drop and recreate the slot:
   ```bash
   sudo -u postgres psql -d cea_sensors <<'SQL'
   SELECT pg_drop_replication_slot('iskra_recovery');
   SELECT pg_create_physical_replication_slot('iskra_recovery');
   SQL
   ```
5. On `iskraprojectcea`, start the DB and wait for `pg_basebackup` to finish, then start the rest:
   ```bash
   sg docker -c "docker compose up -d projectcea_database"
   # wait for "ready to accept read-only connections"
   sg docker -c "docker compose up -d projectcea_redis_sync projectcea_grafana"
   ```
6. On mothernode, verify streaming is active:
   ```sql
   SELECT application_name, client_addr, state FROM pg_stat_replication;
   SELECT slot_name, active, restart_lsn FROM pg_replication_slots WHERE slot_name = 'iskra_recovery';
   ```

## Forbidden in this stack

Do not perform any of the following. They belong to superseded Pi Grafana or manual procedures and will break the current deployment.

- Edit Grafana's SQLite database with `sqlite3 /var/lib/grafana/grafana.db`.
- Manually copy dashboards with `sudo cp ... /var/lib/grafana/dashboards/`.
- Use `localhost:3000` or `iskradocker:3000` as the Grafana URL.
- Run `systemctl enable/start grafana-server` or `apt install grafana` on the Pi.
- Delete dashboards from Grafana's database to force a reload; provisioning handles updates.

## See also

- `Infrastructure/database/REQUIREMENTS.md` — aggregate ladders, setpoints, schema contracts.
- `Infrastructure/frontend/REQUIREMENTS.md` — frontend embedding and API contracts.
- `Infrastructure/frontend/DESIGN.md` — monitoring visual/accessibility contract.
