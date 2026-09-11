# Iskra replica runbook

This runbook covers the ProjectCEA replica stack on `iskraprojectcea`. It runs a TimescaleDB streaming standby, Redis, and `redis_sync`. Query, aggregate, setpoint, and photoperiod contracts are in [`Infrastructure/database/REQUIREMENTS.md`](../database/REQUIREMENTS.md).

## Services

| Service | Image | Role |
|---|---|---|
| `projectcea_database` | `timescale/timescaledb:2.28.3-pg15` | Streaming standby of the mothernode primary |
| `projectcea_redis` | `redis:7-alpine` | Local replicated sensor-value cache |
| `projectcea_redis_sync` | `projectcea_redis_sync:local` | Copies `latest_sensor_values` from the replica to Redis |

Mothernode runs PostgreSQL primary, Redis, and the CEA services natively.

## Setup

1. On `iskraprojectcea`, ensure `ProjectCEA/Infrastructure/iskra_stack/` exists.
2. Copy `.env.example` to `.env` and set `PRIMARY_HOST`, `REPLICATION_SLOT`, `REPLICATION_PASSWORD`, `POSTGRES_CEA_USER_PASSWORD`, and `PGDATA_HOST_PATH`.
3. Create the physical slot on the primary before the first start: `SELECT pg_create_physical_replication_slot('iskra_recovery');`.
4. Ensure PGDATA is owned by `999:999`: `sudo chown -R 999:999 $PGDATA_HOST_PATH`.
5. Run `chmod +x docker-entrypoint-replica.sh && docker compose up -d --build`.
6. Wait for `projectcea_database` to become healthy before relying on the replica.

## Sync and verification

Push the canonical stack files with:

```bash
Infrastructure/scripts/sync_to_iskra.sh
```

Verify container health, replication lag, and replica ingest with:

```bash
Infrastructure/scripts/verify_iskra.sh
```

`redis_sync` reads `latest_sensor_values` every `SYNC_INTERVAL_SEC` (Compose default `10`) and writes `cea:sensor:global:main:{sensor}` and `cea:sensor:global:main:{sensor}_ts` with a 30-second TTL.

## Operator-only replica recovery

Replica re-base drops the replication slot, wipes PGDATA, and rebuilds the standby. It is destructive and requires an operator.

1. Stop `projectcea_redis_sync` and `projectcea_database`.
2. Confirm `.env` has `REPLICATION_SLOT=iskra_recovery`.
3. Wipe the configured PGDATA path.
4. On mothernode, drop and recreate the physical slot.
5. Start `projectcea_database`, wait for base backup completion, then start `projectcea_redis_sync`.
6. Verify the primary reports active streaming replication.

Do not run these recovery actions from an agent or automation.
