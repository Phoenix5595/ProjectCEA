# SQL migrations

`db_migrate.py` applies the versioned SQL files in this directory. Name each
migration `NNNN_snake_name.sql`, where `NNNN` is a unique four-digit numeric
prefix starting at `0001`; the full filename stem (for example,
`0001_create_sensor_table`) is the migration version. Migrations run in numeric
prefix order.

The first line must be exactly one of:

```sql
-- migration: downtime=false
-- migration: downtime=true
```

Every migration marked `downtime=true` must have a sibling
`NNNN_snake_name.down.sql`. SQL files own their transaction boundaries: the
runner does not wrap them in a transaction. It records a migration in
`public.schema_migration` only after `psql` has completed the file successfully.
Applied migration bytes are protected by a SHA-256 check; changing an applied
file is an integrity error.

The `apply` command refuses the entire pending set if any pending migration
requires downtime and `--allow-downtime` was not supplied. It applies no earlier
non-downtime migrations in that case. A migration whose name contains
`alembic_stamp` runs before Alembic; all other pending SQL migrations run after
`alembic upgrade heads`. SQL rollback uses the matching `.down.sql` files
newest-first; Alembic revisions are never downgraded by this command.

Disposable proof and rehearsal databases live only on PostgreSQL cluster
`15/test` at port `5433`, never production cluster `15/main` (`sj_cea`) at
port `5432`. `CEA_TEST_PGPORT` defaults to `5433`; harnesses check the target's
`data_directory` and port before creating or dropping any disposable database.
This isolation is mandatory after a disposable migration proof segfaulted a
TimescaleDB worker and reset the shared production cluster on 2026-10-20.
`rehearse_migrations.sh` uses read-only `pg_dump` against `sj_cea` on port
`5432`, then restores, migrates, and drops only the guarded target on port
`5433`.

If a 0007 prephase reports `migration_jobs_still_running`, its committed job
snapshots remain; let workers finish (do not kill them), then rerun the migration.

A downtime migration set must have a successful migration rehearsal before
production deployment. `rehearse_migrations.sh <release_path>` restores a
production dump into a guarded disposable database, applies the set, rolls it
back to the starting SQL migration head, applies it again, and writes a
pass-result file keyed by the pending set's SHA-256. Deploys require that
result to be less than seven days old. The rehearsal restores with a single-job
`pg_restore`; parallel restore is not supported for TimescaleDB.
