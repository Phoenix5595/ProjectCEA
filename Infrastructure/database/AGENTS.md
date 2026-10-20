# Database Layer

PostgreSQL + TimescaleDB for normalized metadata, time-series measurements, continuous aggregates, monitoring read models, and the grow archive lifecycle.

## Schema and query ownership

| File | Responsibility |
|---|---|
| `cea_schema.sql` | Core tables: `room`, `rack`, `device`, `sensor`, `measurement` hypertable, `climate_periods`; `measurement_with_metadata` view |
| `migrate_latest_sensor_values_lateral.sql` | `latest_sensor_values` view, retained with no in-repo consumer |
| `monitoring_read_models.sql` | Canonical base read models for the native monitoring feature: `monitoring_measurement_1min/5min`, `monitoring_effective_setpoints_1min/5min`, `monitoring_automation_state_1min/5min` aggregates, photoperiod coverage, and monitoring helpers |
| `monitoring_history_overview.sql` | Canonical hour/day overview aggregates (`monitoring_measurement_1hour/1day`, `monitoring_light_intensity_1hour/1day`, `monitoring_requested_pid_1hour/1day`), coverage/watermark tables; includes `monitoring_relay_history_overview.sql` |
| `timescaledb_config.sql` | Compression and retention policies |
| `operational_history_retention.sql` | Operator-only retention policies for operational-event durability (raw control histories 7 days, aggregates and control_history 30 days) |
| `grow_data_lifecycle.sql` | Grow/Veg lifecycle catalog, archive jobs and availability records; source rows stay in their normal tables |
| `grow_data_lifecycle_expiry_cursor.sql` | Nongrow expiry cursor and archive-load/artifact indexes |
| `grow_data_source_compression.sql` | Owner-applied source policy change: remove retention jobs and set three-month compression policies on existing source hypertables, preserving enabled settings and skipping plain relations with `NOTICE` |

## Versioned migrations

Schema changes ship as `migrations/NNNN_name.sql` files applied by `Infrastructure/scripts/db_migrate.py` during `deploy.sh` (rules in [`migrations/README.md`](migrations/README.md)); the ledger is `public.schema_migration`. Each deploy also runs `alembic upgrade heads` for automation-service; automation startup only verifies the head. `0001_alembic_stamp_side_branch` records `010_canonicalize_device_names` on databases (production) that applied the `009→010` alembic branch without recording it, so alembic never re-runs `009_seed_device_registry`; alembic `011_merge_heads` then joins the two heads. Do not edit applied migration files (SHA-256 checked).
- Alembic `001_baseline` still describes `control_history` with an id-only primary key; migration `0002_control_history_hypertable` rebuilds it as a 7-day hypertable with `PRIMARY KEY (id, "timestamp")`. Migration `0007_time_column_rename` renames that event column to `"time"` without changing the key order or uniqueness rule. IDs remain sequence-generated; do not use id-only conflict targets. Do not edit the applied Alembic revision to resolve this schema transition.
- Phase 4 migration `0007_time_column_rename` renames the event-time columns of the eight history tables to `"time"`; `measurement.time`, aggregate `bucket`/`last_observed_at`, live fields and publication contracts are unchanged. Compression-enabled hypertables keep the bounds CHECK `NOT VALID`: the migration verifies every existing non-NULL event time before adding it, PostgreSQL enforces it on new writes, and TimescaleDB 2.28.3 cannot validate a CHECK on a columnstore-enabled hypertable.
- Monitoring `last(x, "time")` ordering uses the production one-row-per-device-per-tick invariant. The 2026-10-19 disposable 2.28.3 proof refreshed three daily chunks successfully, matched all 18 rows against raw latest rows with zero mismatches, and found zero duplicate device/tick groups; `monitoring_order_key` failed to refresh, while the reported anonymous-`ROW` failure was not reproduced.
- `0007_time_column_rename` rebuilds eleven affected aggregates, including the measurement hierarchy, keeps `monitoring_measurement_1min` and `monitoring_relay_duration_1day`, and enables three-month compression for all thirteen. Its preflight commits an unschedule of targeted Timescale jobs and waits for in-flight workers before DDL; the scheduled backfill refreshes one elapsed 24-hour UTC window per run, oldest-first, and retries indefinitely after transient failures. Completion schedules the eleven rebuilt refresh policies and restores the snapshotted overview, source and retained-CAGG jobs. The overview watermark and checkpoint guards include measurement's 1min ancestor so historical source invalidations cannot leave hourly/daily reads marked fresh. Readers keep using `max(bucket)` as the materialized frontier and serve raw rows above it. The down migration restores the saved pre-0007 canonical SQL and rematerializes its `ROW(...)` aggregates one elapsed UTC day at a time; its committed phases can be resumed after failure.
- `monitoring_measurement_5min` and `monitoring_measurement_1hour` are hierarchical on `monitoring_measurement_1min`; `monitoring_measurement_1day` remains on the hourly parent. `test-time-rename-migration.sh` proves the rename, compressed-source CHECK behavior, three-month aggregate compression, oldest-first backfill parity, scheduler failure recovery, DST-safe windows, ancestor invalidation guard, quiescence cancellation/drain and synthetic timeout DETAIL/HINT diagnostics, restartable down, job restoration, and down/apply cycle in a disposable database.
- Production had untracked hotfix views `monitoring_cagg_materialization_coverage` and a modified `monitoring_cagg_watermark`. Migration `0007_time_column_rename` snapshots transitive dependent-view definitions, dependency order, owner and grants, drops dependents deepest-first, and lets its canonical SQL recreate canonical objects only; the orphan coverage view stays absent after up. The down migration restores the saved views shallowest-first with their exact pre-0007 `pg_get_viewdef`, owner and grants, including replacing the canonical watermark definition with the production hotfix.
- Never drop or alter a CAGG or hypertable while a TimescaleDB job targeting it is in flight. Snapshot and unschedule refresh, compression/columnstore, retention, overview and backfill jobs; commit the pause, then wait until every cancelled worker has exited before DDL. On TimescaleDB 2.28.3, `alter_job(..., scheduled => false)` cancels a running worker and rolls back its transaction. The 2026-10-20 test-cluster segfault occurred when a job target was deleted while its worker was executing (`job was deleted during execution`); never delete a job or drop/alter its target before the wait completes.
- Phase 4 index-name exception: automation_state index names intentionally keep "timestamp".

Historical note (2026-10-18): retired Grafana-only SQL migrations, views and verification scripts were removed: `grafana_performance_migration.sql`, `grafana_optimized_views.sql`, `grow_history_guards.sql`, `migrate_get_sensor_routing_1min_through_6h.sql`, `verify_get_sensor_routing.sql`, `verify_performance.sql`, `timescaledb_continuous_aggregates.sql`, and `timescaledb_compression.sql`. `latest_sensor_values` remains defined but has no in-repo consumer.

## Completed one-off operations (2026-10-19)

- Pre-August purge: all pre-2026-08-01 (Toronto) rows of the ten time-series streams were removed from production, keeping the latest/predecessor anchors; static/config/registry/Calendar tables untouched. The one-off script and its proof were removed afterwards.
- Database rename: production is now `sj_cea` (was `cea_sensors`); deployed with the grow-lifecycle release, which also applied `grow_data_lifecycle.sql`, `grow_data_lifecycle_expiry_cursor.sql` and `grow_data_source_compression.sql`, installed `grow-archive-service`, and added the `/api/archives` Caddy route. The single-use deploy/rename helpers and runbook were removed after finalize.
- Iskra/Grafana decommission: replication slot `iskra_recovery`, roles `cea_repl`, `grafana`, `probe_role` and `monitoring_role_probe_rsnd`, the Iskra `pg_hba.conf` rules (backup `pg_hba.conf.bak-20261019-iskra`) and the leftover test databases (`cea_sensors_test`, `fullinstall_*`, `syntaxcheck*`, `monitoring_test_probe2`, `monitoring_test_rsnd_probe`) were removed. Only `postgres` and `sj_cea` remain.
- Retired Grafana-era objects dropped from production: legacy CAGGs `measurement_1min/5min/hourly/daily`, views `measurement_*_grafana`, functions `get_sensor_data_optimized`/`get_sensor_stats`. Fresh installs still recreate the legacy CAGGs via alembic `005_phase5a_reconcile` and `cea_schema.sql` until a new revision removes them.
- Restoring a dump of this TimescaleDB database: create an empty database owned by `cea_user`, `CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();`, then `sudo -u postgres pg_restore -d <db> <dump>` **single-job** (`-j 2` breaks TimescaleDB catalog foreign keys), then `SELECT timescaledb_post_restore();` (~22 min for a 737 MB dump). Errors about the retired `grafana` role are expected.
- TimescaleDB 2.28.3 ordering proof (2026-10-19): a named `monitoring_order_key` failed with `could not find a > operator`; the earlier anonymous `ROW(...)` failure was not reproduced in the canonical multi-chunk test. The tested `last(value, "time")` fallback succeeded and matched the raw latest rows; keep the one-row-per-device-per-tick invariant explicit.

## Redis live vs Postgres time series

- Redis holds live state (`sensor:{name}`, 10 s TTL) and streams (`sensor:raw`, `stream:control`, `cea:events:operational`). `sensor:raw` and `stream:control` cap at 100,000 entries; `cea:events:operational` caps at 50,000 entries or 24 hours.
- TimescaleDB holds historical `measurement` rows and continuous aggregates.

## Aggregate ladders

Consumer-specific ladders; always filter by time range.

Monitoring service ladder (`monitoring_service/sensor_models.py::compute_tier`, `Infrastructure/monitoring-service/monitoring_service/`):

| Duration | Tier / source |
|---|---|
| ≤ 1 h | raw `measurement` |
| (1 h, 6 h] | `monitoring_measurement_1min` |
| > 6 h | `monitoring_measurement_5min` |

Overview reads use the hour/day aggregates from `monitoring_history_overview.sql` with whole-hour buckets of 1–12 hours chosen by point budget.

Backend API ladder (`app/repositories/sensor_repository.py`):

| Span | Tier |
|---|---|
| < 2 h | raw |
| >= 2 h | `monitoring_measurement_1min` |
| >= 24 h | `monitoring_measurement_5min` |
| >= 7 d | `monitoring_measurement_1hour` |
| >= 30 d | `monitoring_measurement_1day` |

## Operational event durability

Alarm and error lifecycle events are stored in the existing `control_history` hypertable using reserved rows:

- `channel = -1`
- `device_name = alarm:<alarm_name>`
- `mode = alarm:<lifecycle>:<severity>`
- `old_state`/`new_state` encode lifecycle transition
- `reason` carries correlation context where it fits

Normal control-history reads keep `channel >= 0`, so alarm rows do not appear as duplicate relay activity.

## Legacy operational retention fixture

`operational_history_retention.sql` is a legacy age-policy definition exercised
only against an explicitly marked disposable test database. Its source-table
retention jobs are not the grow/Veg lifecycle and must not be enabled as a
production shortcut. The archive lifecycle's read-only policy gate blocks
scheduled `policy_compression` / `policy_retention` jobs on catalogued sources;
it never changes jobs or blocks unrelated non-source compression/CAGG refreshes.

## Activation guardrails

`operational_history_retention.sql` aborts unless all of these are true:

- Database name matches `^monitoring_test_[a-z0-9_]+$`.
- `current_database()` contains neither protected name `cea_sensors` nor `sj_cea` (case-insensitive substring); the `monitoring_test_*` namespace is mandatory.
- `app.operational_retention_disposable` is set to `1`.

The file is referenced only by the disposable test harness (`Infrastructure/database/tests/test-operational-history-retention.sh`). It is not invoked by service startup, `deploy.sh`, `finalize-deploy.sh`, `rollback-deploy.sh`, or `Infrastructure/services.yaml`.

## Monitoring policy activation

`monitoring_read_models_activate_policies.sql` remains a supervised, manually reviewed action (it stays a checked-in file; only its application is gated). It requires all six backfill markers and complete catalog coverage before any `add_continuous_aggregate_policy` call.

- `monitoring_acknowledge_failures.sql` is an owner-run, transactional and idempotent recovery script: it acknowledges current per-logical-name failure totals, resumes paused overview jobs, and rolls back if the shared failure gate still trips.

## Rollback irreversibility

Removing or disabling a retention policy stops future deletion, but it cannot restore rows the policy already dropped. Plan separate backups before enabling either policy set in production.

## Min/max preservation

Every continuous aggregate keeps `avg_value`, `min_value`, and `max_value`. Removing min/max would hide fast swings (for example, humidity spikes), so all consumers expose the envelope.

## Grow archive schema and source lifecycle

`grow_data_lifecycle.sql` defines the archive catalog and lifecycle metadata;
it does not create a cold row store, per-row journal, or source-table journal
triggers. Historical rows remain in their normal source tables until verified
archive deletion. `ARCHIVE_LIFECYCLE_ENABLED` defaults to `false`: exports and
explicit ZIP loads remain available, but scheduled source deletion does not run.

The source catalog covers all defined streams, including raw measurements,
device-control history, and Calendar/configuration context. Archive capture
exports every source row selected by the resolved scope; graph buckets are
additional members and never replace raw rows. Schema preflight blocks capture
when a live source is present without a descriptor. NULL-time context retains
its original NULL value and is stored separately from observation time.

The owner-applied `grow_data_source_compression.sql` removes source hypertable
retention policies and sets native compression policies to three months for
existing source hypertables. It preserves settings where compression is already
enabled and emits a `NOTICE` for present-but-plain relations. The single-use
database-rename deployment applies it after the lifecycle schema; regular
deployments and service startup do not. The read-only archive policy gate blocks
`policy_retention` for catalogued sources and permits `policy_compression` only
when `compress_after` is at least three months; shorter compression settings
are blocked.

Expiry follows a verified ZIP and the applicable cutoff: a closed Flower grow
is eligible at its anniversary; Veg source rows expire progressively after
twelve months; nongrow source rows use the twelve-month expiry. Before each
deletion, the service re-reads the corresponding source slice and compares its
row count and canonical digest with the verified ZIP inside the deleting
transaction. Only matching, removable rows are deleted, then derived history
is refreshed. `archive_availability.source_rows_removed` records verified
source removal and guards ordinary history reads.

After source removal, a correction rebuilds only the affected time window
from the prior verified ZIP plus current source rows. A correction that would
reassign deleted rows to another room or identity is rejected rather than
rewriting the archived ownership.

Schema and disposable lifecycle proofs are run from the worktree root. Set
`W` to the current worktree root, export the service and test import paths, and
run one DB proof at a time; parallel proofs contend on the shared local
PostgreSQL instance and can cause false timeouts. The lifecycle, time-rename and
purge harnesses take `/tmp/grow-lifecycle-harness.lock` themselves for the whole
run; do not wrap them in `flock` (a nested lock on the same file deadlocks):

```bash
W="$(git rev-parse --show-toplevel)"
export PYTHONPATH="$W/Infrastructure/grow-archive-service:$W/Infrastructure/automation-service:$W/Infrastructure:$W/Infrastructure/grow-archive-service/tests"
bash "$W/Infrastructure/database/tests/test-grow-data-lifecycle.sh" --case <case>
bash "$W/Infrastructure/database/tests/test-time-rename-migration.sh"
```

The lifecycle harness cases are `schema`, `lossless`, `compressed-source`,
`source-compression-policy`, `deletion-fence`, `annual-correction`,
`enabled-lifecycle`, `membership`, `calendar-sync`, `history-availability`,
`files-export`, `archive-api`, `derived-repair`, `native-readers`, `rename`,
and `all`.

All database proofs use guarded disposable `monitoring_test_*` databases.
`CEA_TEST_PGPORT` defaults to `5433`, and `CEA_TEST_PGHOST` defaults to
`127.0.0.1`; administrative peer connections as `postgres` use the local
PostgreSQL socket. Before database creation or deletion, harnesses verify that
the target's `data_directory` ends with `/15/test` and its port is not `5432`.
Disposable databases live only on cluster `15/test` at port `5433`, never on
production cluster `15/main` (`sj_cea`) at port `5432`. This separation is
mandatory after a disposable migration proof segfaulted a TimescaleDB worker
and reset the shared production cluster on 2026-10-20.
Never contact production services or apply lifecycle/compression SQL to
`cea_sensors` or `sj_cea`. Production schema changes, compression-policy
application, lifecycle enablement, remote publication, and source deletion
require separate explicit owner authorization.

## SQL tests

Database tests live in `Infrastructure/database/tests/`. They validate idempotency, tier edge cases, statistics equivalence, destructive-change rejection, and the operational retention boundaries. The harnesses are:

```bash
bash Infrastructure/database/tests/test-monitoring-read-models.sh --case reject-incompatible-and-destructive
bash Infrastructure/database/tests/test-operational-history-retention.sh
bash Infrastructure/database/tests/test-time-rename-migration.sh
bash Infrastructure/database/tests/test-grow-data-lifecycle.sh --case schema
```

Run these locally against a disposable Postgres instance, never against production.

---

*Last updated: 2026-10-19*
