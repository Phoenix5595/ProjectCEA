# Draft: Grafana-TimescaleDB Connection Issue

## Issue Summary
Grafana on iskradocker/iskra **cannot connect to TimescaleDB after a power issue**. The system worked before the power failure.

User appears to be using Iskra Docker Grafana (URL: `http://iskradocker:3000` per AGENTS.md).

## CRITICAL CONTEXT: Power Issue Aftermath

**"It used to work before the server had a power issue."**

This suggests the issue is likely one of:
1. **Docker volume corruption** - Replica data directory corrupted mid-write during power loss
2. **Docker state corruption** - Container state or Docker internal state damaged
3. **Missing .env file** - If iskra lost power, the `.env` file might be gone or the container might have lost env vars
4. **Re-init needed** - The replica might need to re-run pg_basebackup
5. **Network/host configuration reset** - Power loss might have reset network settings or Docker daemon state

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  ISKRA (Docker stack)                                             │
│  ┌──────────────┐   ┌──────────────────┐   ┌──────────────┐    │
│  │   Grafana    │──→│ projectcea_db    │──→│ projectcea_  │    │
│  │  (port 3000) │   │ (TimescaleDB     │   │ redis       │    │
│  └──────────────┘   │  replica :5432)  │   └──────────────┘    │
│                     └────────┬─────────┘                        │
│                              │ Streaming replication              │
│                              │ (PRIMARY_HOST)                     │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                    mothernode.tail7a351e.ts.net:5432
                    (Primary TimescaleDB - native/systemd)
```

## Key Files Found

### Iskra Stack Configuration
- **Docker Compose**: `Infrastructure/iskra_stack/docker-compose.yml`
  - Grafana connects to `projectcea_database:5432` (Docker internal DNS)
  - Exposes replica on host port 5433 (POSTGRES_PORT=5433)
  
- **Datasource Template**: `Infrastructure/iskra_stack/provisioning/datasources/datasources.yaml.template`
  - Host: `projectcea_database:5432` (local Docker replica)
  - Database: `cea_sensors`
  - User: `cea_user`
  - SSL: disabled
  - TimescaleDB: enabled
  - Password: `${POSTGRES_CEA_USER_PASSWORD}` (from envsubst)

- **Entrypoint Script**: `Infrastructure/iskra_stack/scripts/grafana-entrypoint.sh`
  - Runs `envsubst` to substitute `POSTGRES_CEA_USER_PASSWORD` in template
  - Generates `datasources.yaml` (NOT committed to repo)

### Missing Files
- **NO `.env` file** in `Infrastructure/iskra_stack/` (gitignored or not created)
- **No live `.env`** found for iskra_stack
- Found `.env` only in `Infrastructure/database-replica/` (standalone replica, different setup)

### Mothernode Configuration
- **Primary DB**: `mothernode.tail7a351e.ts.net:5432`
- **Replication user**: `cea_repl` (for iskra streaming)
- **Grafana user**: `cea_user` (for Grafana/backend read access)
- **Database name**: `cea_sensors`

## ROOT CAUSE (CONFIRMED)

**WAL segments removed before replica could catch up.**

From PostgreSQL logs:
```
ERROR: requested WAL segment 0000000100000012000000EF has already been removed
```

**Why this happened:**
1. Power outage - iskra went offline
2. Mothernode kept running and generating new WAL
3. `wal_keep_size = 64MB` - old WAL segments were deleted
4. When iskra came back, it asked for old WAL that no longer exists
5. Replication broken = no data in replica = Grafana sees nothing

**Why no replication slot:**
- No replication slot means PostgreSQL doesn't know the replica needs those WAL files
- With a slot, WAL is retained until replica confirms receipt

**Settings confirmed:**
```
wal_keep_size = 64MB  (too small)
max_wal_senders = 2
No replication slots exist
listen_addresses = *  (good)
pg_hba allows replication from iskra IPs  (good)
```

## Status: PLAN CREATED

**Plan saved to**: `.sisyphus/plans/grafana-timescale-replication-fix.md`

This draft is superseded by the plan. Do not edit further.

1. **Which server(s) had the power issue?**
   - Just iskra?
   - Just mothernode?
   - Both?

2. **What happened to the iskra Docker stack after power restoration?**
   - Did `docker compose up -d` run automatically?
   - Or did you have to manually restart it?
   - Are the containers running?

3. **Can you run these commands on iskra and share the output?**
   ```bash
   # In Infrastructure/iskra_stack/ directory:
   docker compose ps
   docker compose logs projectcea_database --tail 50
   ```

4. **Does the `.env` file still exist on iskra?**
   - `cat Infrastructure/iskra_stack/.env`
   - (Share the content, but NOT the passwords - just confirm it exists)

5. **On mothernode, can you run?**
   ```bash
   sudo -u postgres psql -d cea_sensors -c "SELECT * FROM pg_stat_replication;"
   ```
   This shows if the replica is connected.

6. **Is there data in the replica?**
   On iskra:
   ```bash
   docker exec projectcea_database psql -h localhost -U postgres -d cea_sensors -c "SELECT COUNT(*) FROM measurement LIMIT 1;"
   ```
