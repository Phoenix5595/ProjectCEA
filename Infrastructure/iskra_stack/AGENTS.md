# CEA Iskra Stack

Docker Compose stack that runs only on `iskraprojectcea`: TimescaleDB replica, Redis, Grafana, and `redis_sync`.

## Compose Ownership

`docker-compose.yml` defines four services on `projectcea_network`:

| Service | Image | Role |
|---------|-------|------|
| `projectcea_database` | `timescale/timescaledb:2.28.3-pg15` | Streaming standby for time-series/aggregates |
| `projectcea_redis` | `redis:7-alpine` | Live values for Grafana |
| `projectcea_redis_sync` | `projectcea_redis_sync:local` | Replica DB `latest_sensor_values` → Redis |
| `projectcea_grafana` | `grafana/grafana:11.6.0` | Visualization, host port `3001` |

Images are pinned; bump them explicitly and only after validating WAL/dashboard compatibility.

## Data Sources

- **Redis live tables**: `sensor:<name>`, `sensor:<name>:ts`, and Grafana hash keys `cea:grafana:flower_averages/front/back`, populated by `projectcea_redis_sync`.
- **PostgreSQL time series**: `measurement` hypertable and `get_sensor_data_optimized()` continuous aggregates on the replica.
- Grafana reads Redis for current-value panels and Postgres for historical panels.

## Provisioning Source of Truth

- Dashboards: `dashboards/` (mounted read-only into the container).
- Datasources: `provisioning/datasources/datasources.yaml.template`.
- Alerting: `provisioning/alerting/replication_slot.yaml`.
- Dashboard provider config: `provisioning/dashboards/dashboards.yaml`.

## Safe Sync / Verify Commands

```bash
cd Infrastructure/iskra_stack
# View stack health
sg docker -c "docker compose ps"
# Restart Grafana after datasource or DB restart
sg docker -c "docker compose restart projectcea_grafana"
# Sync dashboards from repo to iskra
bash ../scripts/sync_to_iskra.sh
# Verify replica lag and recent ingest
bash ../scripts/verify_iskra.sh
```

## Operator-Only Recovery

Replica re-base (dropping the `iskra_recovery` slot, wiping PGDATA, recreating the slot) is destructive and operator-only. It lives in `README.md`, not in automated verification. Agents must never run recovery steps.

## Anti-Patterns

- Edit Grafana's SQLite DB directly (`sqlite3 /var/lib/grafana/grafana.db`).
- Delete provisioned dashboards manually inside the container.
- Use ad-hoc `sudo cp` to deploy dashboards; use `sync_to_iskra.sh` instead.
- Claim `localhost:3000` or `iskradocker:3000` are current Grafana hosts.

---

*Last updated: 2026-08-10*
