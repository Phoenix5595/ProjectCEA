# Grow-lifecycle deploy with database rename (`cea_sensors` → `sj_cea`) — single-use runbook

Owner-run only. This deploy ships the grow data lifecycle branch, renames the production database, applies the lifecycle schema and source compression policy, and installs the new `grow-archive-service` unit in one guarded window. Delete this runbook, `deploy-grow-lifecycle-once.sh`, `Infrastructure/scripts/rename-database.sh` and their tests (`Infrastructure/scripts/tests/test-deploy-grow-lifecycle-once.sh`, `Infrastructure/scripts/tests/test-rename-database.sh`) after `finalize-deploy.sh --confirm`.

The regular `deploy.sh`, `rollback-deploy.sh` and `finalize-deploy.sh` are not changed for this; their known gaps are tracked for a future plan.

## What changes

| Item | Before | After | Reversed on rollback |
|---|---|---|---|
| Database name | `cea_sensors` | `sj_cea` | Yes (renamed back) |
| `POSTGRES_DB` in `/etc/systemd/system/*.service` and `*.service.d/override.conf` (automation, can-processor, cea-backend, soil, weather; legacy `backend.service`) | `cea_sensors` | `sj_cea` | Yes (byte-identical backups restored) |
| Monitoring DSN database path in `/opt/projectcea/shared/env/monitoring.env` | `/cea_sensors` | `/sj_cea` | Yes |
| `~/.pgpass` database field | `cea_sensors` | `sj_cea` | Yes |
| Release | current | new grow-lifecycle release | Yes (previous release restored) |
| `grow-archive-service.service` | not installed | installed, `ARCHIVE_LIFECYCLE_ENABLED=false` | Yes (unit removed) |
| `grow_data_lifecycle.sql`, `grow_data_lifecycle_expiry_cursor.sql` | not applied | applied to `sj_cea` | **No** — forward-only: new archive tables, new `calendar_event` / `calendar_sync_connection` columns with defaults, trigger `sj_calendar_bind_pending` |
| Source retention/compression policies | Existing retention policies (including 2 years on `measurement` and `effective_setpoints`) and compression policies (90 days on `measurement`, 7 days on `effective_setpoints`); `control_history` and `actuator_events` are plain tables | Retention policies removed; compression policy set to 3 months for source hypertables; existing compression settings/chunks retained; plain tables skipped with `NOTICE` | **No** — policy changes are not reversed |

Not part of this window: enabling `ARCHIVE_LIFECYCLE_ENABLED`, dropping `latest_sensor_values`, and the owner-run Iskra replica decommission (`ISKRA_REPLICA_DECOMMISSION.md`). Each needs a separate owner instruction.

## Prerequisites

1. Explicit owner authorization for this deploy and for deploying from the chosen source tree (the branch must be integrated as the owner decides). Integration rule (owner, 2026-10-19): the owner deleted `.agents/`, `.omo/`, `.opencode/`, `.sisyphus/` (and keeps `.claude/` out) in the main checkout; the merge must not bring any of them back. Integrate with `git merge` only (the branch does not modify those paths, so the deletions stay), never by copying/rsyncing the worktree over main; afterwards verify `git ls-files .agents .omo .opencode .sisyphus .claude` is empty on main's working tree (or matches the owner's committed deletion) and that no such folder reappeared on disk.
2. Pre-August purge finished and verified against `cea_sensors` (`operator_purge_history_before_2026_08.sql`; a second dry run reports `rows_to_delete=0` everywhere).
3. Fresh backup taken after the purge:
   ```bash
   ( set -a; . /opt/projectcea/shared/env/postgres.env; set +a; PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -Fc -h localhost -U cea_user -d cea_sensors -f ~/cea_sensors-pre-rename.dump )
   sudo cp -a /etc/systemd/system /root/systemd-before-rename-$(date +%F)
   ```
   Check free disk space first (`df -h ~ /var`).
4. No active deploy candidate (`finalize-deploy.sh --confirm` or `rollback-deploy.sh` any pending one).
5. A quiet time. All database services stop between the rename and the new release's restart (automation-service, which drives relays and dimmers, included). Expected window: a few minutes — rename and schema take seconds; restarts and health checks dominate (each health check retries up to 90 times at 1 s). Not measured on production. Confirm what the lights/relays do while automation-service is stopped before choosing the time.
6. The sandbox proofs pass on the source tree you will deploy:
   ```bash
   bash Infrastructure/scripts/tests/test-rename-database.sh
   bash Infrastructure/scripts/tests/test-deploy-grow-lifecycle-once.sh
   bash Infrastructure/scripts/tests/test-deploy-candidate.sh
   ```
   and the disposable rename/compression proofs: `test-grow-data-lifecycle.sh --case rename` and `test-grow-data-lifecycle.sh --case source-compression-policy` (see `AGENTS.md` for the command with `PYTHONPATH`).

## Procedure

1. Read-only preflight. It reports the current DB name, connected sessions by application, TimescaleDB background-worker sessions, owner/CREATEDB, `datallowconn`, every unit/drop-in/env/pgpass line naming the old DB, and the planned actions. Nothing changes.
   ```bash
   cd <source tree>
   Infrastructure/scripts/rename-database.sh preflight
   ```
   Stop if it reports an unexpected client session, a missing file, or `sj_cea` already existing alongside `cea_sensors`.
2. Deploy (single command):
   ```bash
   SOURCE=<source tree> ./deploy-grow-lifecycle-once.sh --confirm sj_cea
   ```
   Order inside the script:
   1. preflight again; refuse if a deploy candidate is active;
   2. stage the new release while services keep running (rsync, per-service venvs, frontend build, sudoers) — read-only `ruff check`, no auto-fix of the source;
   3. `rename-database.sh apply --confirm cea_sensors`: back up files, stop the database units, stop TimescaleDB background workers, disallow connections, terminate only background-worker sessions, wait for zero sessions (aborts and restarts services if a client stays connected), rename, re-allow connections, restart background workers, rewrite unit/drop-in/env/pgpass, `daemon-reload`;
   4. apply `grow_data_lifecycle.sql` then `grow_data_lifecycle_expiry_cursor.sql` to `sj_cea`;
   5. apply `grow_data_source_compression.sql` to `sj_cea`: remove source hypertable retention policies and set compression policies to three months; skip existing plain relations with `NOTICE` and keep already-enabled compression settings;
   6. install `grow-archive-service.service`, create `/var/lib/projectcea/archives`, `daemon-reload`;
   7. `deploy.sh` with the pre-staged release: switch symlink, restart services, health checks, record the candidate.
   On any failure in steps 3–7 it restores automatically: previous release symlink, rename back, restore files, remove the archive unit, restart the previous release, health checks. The schema and any committed compression-policy changes remain applied; neither is reversed on rollback.
3. Post-check (read-only):
   ```bash
   Infrastructure/scripts/rename-database.sh postcheck
   ```
   Expect `current_database()` = `sj_cea` and every unit's effective `POSTGRES_DB` = `sj_cea`.
4. Verify (within ~10 minutes):
   - `systemctl --failed` is empty; every deploy-managed unit is `active`;
   - new `measurement` rows arriving (CAN, soil, weather) — e.g. `SELECT max(time) FROM measurement;` within seconds of now;
   - automation control loop ticking: `SELECT max("timestamp") FROM automation_state;` within seconds; relays/lights in their expected state;
   - frontend loads; Historical and Calendar pages work; archive service `/health` and `/ready` return 200 (`/ready` checks the schema);
   - `journalctl -u automation-service -u cea-backend -u monitoring-service -u grow-archive-service --since "15 min ago"` free of database errors.
5. Finalize when satisfied:
   ```bash
   ./finalize-deploy.sh --confirm
   ```
   After finalize, `deploy-grow-lifecycle-once.sh rollback` refuses; older releases expect `cea_sensors`, so returning to them requires a manual rename back.
6. Clean up: delete the single-use files listed at the top and update `~/.pgpass` password if it was stale.
7. Delete the pre-August purge backup once everything is finalized (owner decision 2026-10-19): `rm ~/cea_sensors-before-purge.dump` (2.7 GB, taken 2026-10-19 08:15 before the purge; the only remaining copy of pre-August 2026 history — after this it is unrecoverable). Do it only after: the purge apply completed and a re-run dry run reports `rows_to_delete=0` everywhere, this deploy is finalized, and the post-deploy verification above passed.
8. Drop the retired Grafana-era objects (owner decision 2026-10-19; only after finalize, because the previous release's backend still reads `measurement_1min/5min/hourly/daily`, so a rollback needs them). As `postgres` (their owner) on `sj_cea`: `DROP FUNCTION public.get_sensor_data_optimized, public.get_sensor_stats` (all overloads), `DROP VIEW public.measurement_1min_grafana, measurement_5min_grafana, measurement_hourly_grafana, measurement_daily_grafana`, then `DROP MATERIALIZED VIEW public.measurement_1min, measurement_5min, measurement_hourly, measurement_daily` (their refresh jobs 1001–1004 go with them). First confirm, read-only, that nothing else depends on them (`pg_depend` dependents, function bodies) and the new backend serves history from `monitoring_*`. The new release reads only `monitoring_*`; fresh installs would recreate the legacy views via alembic `005_phase5a_reconcile` and `cea_schema.sql` — removing those definitions is a next-release item (new alembic revision; never edit 005).

## Manual rollback (before finalize)

```bash
SOURCE=<source tree> ./deploy-grow-lifecycle-once.sh rollback
```
It stops the candidate units, renames back to `cea_sensors`, restores the backed-up files, removes the archive unit, then runs `rollback-deploy.sh` to restore and restart the previous release. The lifecycle schema stays applied (additive; the previous release ignores the new tables; the calendar trigger sets sync fields on calendar writes). Retention-policy removals and the three-month compression policy are also not reversed.

If automatic or manual recovery reports incomplete: check the current symlink (`readlink /opt/projectcea/current`), `rename-database.sh preflight` (DB name), `/var/lib/projectcea/db-rename/active` (recorded backups), and `systemctl status` of each unit before acting. Last resort: restore `~/cea_sensors-pre-rename.dump` and `/root/systemd-before-rename-*`.

Restoring a dump of this TimescaleDB database (proven 2026-10-19 on a disposable copy, ~22 min for the 737 MB pre-rename dump): create an empty database owned by `cea_user`, `CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();`, then `sudo -u postgres pg_restore -d <db> <dump>` **single-job** (`-j 2` breaks TimescaleDB catalog foreign keys), then `SELECT timescaledb_post_restore();`. Errors about the retired `grafana` role are expected.

## Do not

- Run `rename-database.sh apply` outside the wrapper, or terminate client sessions with `--terminate-remaining` without knowing what they are.
- Grant CREATEDB to `cea_user` for this; the helper uses the postgres admin path.
- Enable `ARCHIVE_LIFECYCLE_ENABLED` in the same window.
- Run the pre-August purge after the rename against `cea_sensors` (use `sj_cea`).
