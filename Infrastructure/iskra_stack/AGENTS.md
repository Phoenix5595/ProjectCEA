# CEA Iskra Replica Stack

Docker Compose on `iskraprojectcea` runs a TimescaleDB replica, Redis, and `redis_sync`.

## Compose ownership

`docker-compose.yml` defines three services on `projectcea_network`:

| Service | Image | Role |
|---|---|---|
| `projectcea_database` | `timescale/timescaledb:2.28.3-pg15` | Streaming standby for time-series and aggregates |
| `projectcea_redis` | `redis:7-alpine` | Local replicated sensor cache |
| `projectcea_redis_sync` | `projectcea_redis_sync:local` | Replica DB `latest_sensor_values` to Redis |

Images are pinned; validate WAL compatibility before changing the TimescaleDB image.

## Data contracts

- Redis keys: `cea:sensor:global:main:{sensor}` and timestamp companions are populated by `projectcea_redis_sync`.
- PostgreSQL data: the `measurement` hypertable and continuous aggregates are replicated from mothernode.
- `REPLICATION_SLOT`, `PGDATA_HOST_PATH`, and the replica entrypoint mount are required durability settings.

## Safe commands

```bash
cd Infrastructure/iskra_stack
sg docker -c "docker compose ps"
bash ../scripts/sync_to_iskra.sh
bash ../scripts/verify_iskra.sh
```

## Operator-only recovery

Replica re-base is destructive and operator-only. It is described in `README.md` and must never run from automated verification.
