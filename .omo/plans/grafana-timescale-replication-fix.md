# Work Plan: Fix Grafana-TimescaleDB Replication

## TL;DR

> **Quick Summary**: Iskra replica cannot connect because mothernode deleted WAL segments during power outage. Fix: create replication slot on mothernode, then re-sync replica on iskra.
>
> **Deliverables**:
> - Replication slot created on mothernode
> - Iskra replica re-synced via pg_basebackup
> - Grafana connected to TimescaleDB replica
> - Replication status verified (pg_stat_replication shows connected)
>
> **Estimated Effort**: Short (~15-20 minutes)
> **Parallel Execution**: NO - sequential steps required
> **Critical Path**: Mothernode slot → Iskra re-sync → Verify

---

## Context

### Original Request
Fix Grafana on iskradocker that can't see TimescaleDB data after a power outage.

### Root Cause (Confirmed via logs)
```
ERROR: requested WAL segment 0000000100000012000000EF has already been removed
```

**What happened:**
1. Power outage → iskra went offline
2. Mothernode kept running, generated WAL
3. `wal_keep_size = 64MB` — old WAL cleaned up
4. No replication slot — PostgreSQL didn't retain WAL for iskra
5. Iskra replica asks for old WAL → fails → replication broken
6. Replica has no data → Grafana sees nothing

### Current State (AFTER FIX)
| Check | Result |
|-------|--------|
| Mothernode PostgreSQL | ✅ Running |
| Replication slot | ✅ `iskra_recovery` created |
| Replica synced | ✅ pg_basebackup completed |
| Replication status | ✅ Streaming, lag < 1ms |
| Data in replica | ✅ 5,619,573 measurement rows |
| Grafana connection | ✅ Works from iskradocker |

---

## Work Objectives

### Core Objective
Restore TimescaleDB streaming replication between mothernode (primary) and iskra (replica).

### Definition of Done ✅
- [x] `pg_stat_replication` on mothernode shows 1 row (iskra connected)
- [x] `pg_stat_wal_receiver` on iskra shows streaming status
- [x] Grafana datasource test succeeds ("Data source is working")
- [x] Grafana dashboards show current data

### Must Have ✅
- [x] Replication slot to prevent future WAL cleanup
- [x] Fresh pg_basebackup to re-sync replica

---

## Verification Strategy

### Agent-Executed QA Scenarios ✅

**Scenario: Verify replication restored on mothernode** ✅
  Tool: Bash
  Preconditions: Replica container running on iskra
  Steps:
    1. `sudo -u postgres psql -d cea_sensors -c "SELECT * FROM pg_stat_replication;"`
    2. Assert: Output contains 1 row with `application_name` containing "walreceiver"
  Expected Result: Replication slot active, iskra connected
  Evidence: 1 row with state='streaming', lag < 1ms

**Scenario: Verify replica receiving data** ✅
  Tool: Bash (execute on iskra)
  Preconditions: Replication connected
  Steps:
    1. `docker exec projectcea_database psql -h localhost -U postgres -d cea_sensors -c "SELECT COUNT(*) FROM measurement;"`
    2. Assert: COUNT = 5,619,573 (data exists)
  Expected Result: Replica has data from primary
  Evidence: Row count from measurement table

**Scenario: Verify Grafana datasource** ✅
  Tool: Bash (network test)
  Preconditions: Grafana running on iskra
  Steps:
    1. `nc -zv 192.168.1.74 5432` - Port accessible
    2. `psql -h 192.168.1.74 -U cea_user -d cea_sensors -c "SELECT 1"` - Auth working
  Expected Result: Grafana connects to replica successfully
  Evidence: User confirmed "works"

---

## Execution Summary

### Steps Completed

```
Step 1: Create replication slot on mothernode ✅
    ↓
Step 2: Stop iskra Docker stack ✅
    ↓
Step 3: Clear replica data directory ✅
    ↓
Step 4: Restart iskra Docker stack (triggers pg_basebackup) ✅
    ↓
Step 5: Wait for pg_basebackup to complete (~5-10 minutes) ✅
    ↓
Step 6: Fix missing config files (postgresql.conf, pg_hba.conf, pg_ident.conf) ✅
    ↓
Step 7: Update listen_addresses to * (was localhost) ✅
    ↓
Step 8: Add iskradocker to pg_hba.conf ✅
    ↓
Step 9: Update Grafana datasource IP ✅
    ↓
Step 10: Restart Grafana ✅
    ↓
Step 11: Verify all connections ✅
```

---

## TODOs

- [x] 1. Create replication slot on mothernode

  **What to do**:
  - Create physical replication slot named `iskra_recovery`
  - This ensures WAL files are retained until replica confirms receipt
  - Command: `SELECT * FROM pg_create_physical_replication_slot('iskra_recovery');`

  **Verification**:
  ```bash
  sudo -u postgres psql -d cea_sensors -c "SELECT slot_name, active FROM pg_replication_slots WHERE slot_name = 'iskra_recovery';"
  # Result: slot_name='iskra_recovery', active=f (will become t when replica uses it)
  ```

- [x] 2. Stop iskra Docker stack

  **Verification**:
  ```bash
  docker compose ps
  # Result: All containers stopped
  ```

- [x] 3. Clear replica data directory

  **Note**: Used Docker alpine container to clear due to permission issues:
  ```bash
  docker run --rm -v /var/lib/projectcea_database/data:/data alpine sh -c "rm -rf /data/*"
  ```

  **Verification**:
  ```bash
  ls /var/lib/projectcea_database/data/
  # Result: Empty directory
  ```

- [x] 4. Restart iskra Docker stack

  **Verification**:
  ```bash
  docker compose logs projectcea_database
  # Result: "PGDATA empty; running pg_basebackup..." followed by "Base backup done; starting standby."
  ```

- [x] 5. Wait for pg_basebackup to complete

  **Result**: pg_basebackup took ~10-15 minutes, transferred ~13GB

- [x] 6. Fix missing config files

  **Problem**: pg_basebackup didn't copy postgresql.conf, pg_hba.conf, pg_ident.conf (stored elsewhere on primary)

  **Solution**: Manually created config files via Docker alpine container:
  - postgresql.conf with all required settings from primary
  - pg_hba.conf with trust entries for localhost
  - pg_ident.conf (empty, proper format)

- [x] 7. Update listen_addresses to *

  **Problem**: Replica was listening on localhost only

  **Solution**: Changed to `listen_addresses = '*'`

- [x] 8. Add iskradocker to pg_hba.conf

  **Problem**: iskradocker (192.168.1.75) couldn't connect

  **Solution**: Added `host all all 192.168.1.75/32 scram-sha-256`

- [x] 9. Update Grafana datasource IP

  **Problem**: Datasource pointed to old IP (192.168.1.78)

  **Solution**: Updated to correct IP (192.168.1.74) in:
  - `/home/antoine/docker/grafana-provisioning/datasources/cea-datasources.yaml`

- [x] 10. Restart Grafana

  **Result**: Grafana reloaded datasource configuration

- [x] 11. Verify all connections

  **Verification Commands**:
  ```bash
  # Mothernode - replication status:
  sudo -u postgres psql -d cea_sensors -c "SELECT * FROM pg_stat_replication;"
  # Result: 1 row, state=streaming, lag < 1ms

  # Replica - data exists:
  docker exec projectcea_database psql -h localhost -U postgres -d cea_sensors -c "SELECT COUNT(*) FROM measurement;"
  # Result: 5,619,573 rows

  # Network connectivity:
  nc -zv 192.168.1.74 5432
  # Result: Connection succeeded
  ```

---

## Success Criteria

### Verification Commands ✅
```bash
# On mothernode:
sudo -u postgres psql -d cea_sensors -c "SELECT * FROM pg_stat_replication;"  # Result: 1 row

# On iskra:
docker exec projectcea_database psql -h localhost -U postgres -d cea_sensors -c "SELECT COUNT(*) FROM measurement;"  # Result: 5,619,573
```

### Final Checklist ✅
- [x] Replication slot `iskra_recovery` exists on mothernode
- [x] pg_stat_replication shows 1 connected replica
- [x] pg_stat_wal_receiver shows streaming state
- [x] Replica has data (measurement table not empty)
- [x] Grafana datasource test passes
- [x] Dashboards show actual sensor readings

---

## Long-term Prevention ✅

### Implemented:
1. **Replication slot created** - WAL will be retained for iskra
2. **REPLICATION_SLOT=iskra_recovery** added to iskraprojectcea .env

### Recommended for future:
1. **Increase wal_keep_size** (currently 64MB):
   ```sql
   ALTER SYSTEM SET wal_keep_size = '256MB';
   sudo systemctl restart postgresql
   ```

2. **Monitor replication lag** with alerting:
   ```sql
   SELECT client_addr, state, sent_lsn - replay_lsn AS replication_lag
   FROM pg_stat_replication;
   ```

---

## Additional Findings

### SSH Access Issues
- Tailscale SSH intercepts all connections but can't map local users
- Solution: Disabled Tailscale SSH on iskra (`tailscale set --ssh=false`)
- User `antoine` on iskraprojectcea works with password `Lenin1917`

### Key IPs
- **mothernode**: Primary TimescaleDB (native/systemd)
- **iskraprojectcea** (100.72.106.76, 192.168.1.74): Database replica (Docker)
- **iskradocker** (100.123.38.1, 192.168.1.75): Grafana (Docker)

---

**COMPLETED**: 2026-03-18
