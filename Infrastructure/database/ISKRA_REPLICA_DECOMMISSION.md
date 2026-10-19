# Iskra Replica Decommission

**Owner-run production procedure only.** These commands alter the live replica stack and the mothernode primary. Agents and automated tests must not run them. The repository change does not stop services, delete PGDATA, change PostgreSQL settings, or contact production.

## 1. Stop the stack on Iskra

On `iskraprojectcea`, run `docker compose down` from the existing stack directory:

```bash
cd /home/antoine/ProjectCEA/Infrastructure/iskra_stack
docker compose down
```

Choose whether to keep or archive the PGDATA data on Storage 1. The old configuration used `/srv/storage1/projectcea_database/data` as its default host path. `docker compose down` without `--volumes` does not request volume removal; do not add `--volumes` or delete the PGDATA path as part of this procedure. Any archive or deletion of that data is a separate owner decision.

## 2. Drop the physical replication slot on mothernode

Do this immediately after step 1. Until the slot is dropped, the inactive slot can make the primary retain WAL and fill its disk.

On the mothernode primary, connect as a PostgreSQL superuser and inspect the slot:

```sql
SELECT slot_name, active,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))
FROM pg_replication_slots;
```

Confirm `iskra_recovery` is inactive after the stack has stopped. If it is still active, stop and establish why before continuing. Then drop it:

```sql
SELECT pg_drop_replication_slot('iskra_recovery');
```

If the inspection query shows no `iskra_recovery` row, the slot is already absent and this step is complete.

## 3. Remove the replication role and its `pg_hba.conf` entry

The former `Infrastructure/database-replica/mothernode_replication_user.sql` created the role **`cea_repl`** and documented this exact `pg_hba.conf` line:

```text
host replication cea_repl <iskra_ip>/32 scram-sha-256
```

On mothernode, remove the matching line from the active `pg_hba.conf`, substituting the actual address used there. Reload PostgreSQL so the removed rule is no longer active:

```bash
sudo systemctl reload postgresql
```

As a superuser, confirm the role is the old replication-only role, then drop it:

```sql
SELECT rolname, rolreplication FROM pg_roles WHERE rolname = 'cea_repl';
DROP ROLE cea_repl;
```

If the role is absent, skip the `DROP ROLE`. If it has unexpected dependencies or appears to serve another purpose, stop and investigate rather than removing unrelated ownership or grants.

## 4. Review primary replication settings

The former database requirements documented these primary values: `wal_level = replica`, `max_wal_senders = 5`, `wal_keep_size = 4GB`, and `max_slot_wal_keep_size = 16GB`. The stack required the `iskra_recovery` physical slot. The old repository documents did not prescribe a numeric `max_replication_slots` value.

Check the current settings on mothernode:

```sql
SHOW wal_level;
SHOW max_wal_senders;
SHOW max_replication_slots;
SHOW wal_keep_size;
SHOW max_slot_wal_keep_size;
```

These settings can stay as-is; reducing or otherwise tuning them is not required to decommission the replica and is a separate owner decision. The former standby-only settings such as `hot_standby_feedback` are not primary settings and need no change here.

## 5. Verify decommissioning on mothernode

Run as a superuser after the slot, role, and authentication rule have been removed:

```sql
SELECT slot_name, active
FROM pg_replication_slots
WHERE slot_name = 'iskra_recovery';
-- Expected: zero rows.

SELECT rolname, rolreplication
FROM pg_roles
WHERE rolname = 'cea_repl';
-- Expected: zero rows.

SELECT line_number, type, database, user_name, address, auth_method, error
FROM pg_hba_file_rules
WHERE user_name = 'cea_repl'
ORDER BY line_number;
-- Expected: zero rows.

SELECT pid, usename, application_name, client_addr, state
FROM pg_stat_replication
WHERE usename = 'cea_repl';
-- Expected: zero rows.
```

Also confirm the `SHOW` results from step 4 are the intended values. Production operations, including this procedure, are owner-run only.
