# Database Layer

PostgreSQL + TimescaleDB for normalized metadata, time-series measurements, continuous aggregates, monitoring read models, and the grow archive lifecycle.

## Schema and query ownership

| File | Responsibility |
|---|---|
| `cea_schema.sql` | Core tables: `room`, `rack`, `device`, `sensor`, `measurement` hypertable, `climate_periods`, `actuator_events`; `measurement_with_metadata` view |
| `migrate_latest_sensor_values_lateral.sql` | `latest_sensor_values` view, retained with no in-repo consumer |
| `monitoring_read_models.sql` | Canonical base read models for the native monitoring feature: `monitoring_measurement_1min/5min`, `monitoring_effective_setpoints_1min/5min`, `monitoring_automation_state_1min/5min` aggregates, photoperiod coverage, and monitoring helpers |
| `monitoring_history_overview.sql` | Canonical hour/day overview aggregates (`monitoring_measurement_1hour/1day`, `monitoring_light_intensity_1hour/1day`, `monitoring_requested_pid_1hour/1day`), coverage/watermark tables; includes `monitoring_relay_history_overview.sql` |
| `timescaledb_config.sql` | Compression and retention policies |
| `operational_history_retention.sql` | Operator-only retention policies for operational-event durability (raw control histories 7 days, aggregates and control_history 30 days) |
| `grow_data_lifecycle.sql` | Grow/Veg lifecycle catalog, archive jobs and availability records; source rows stay in their normal tables |
| `grow_data_lifecycle_expiry_cursor.sql` | Nongrow expiry cursor and archive-load/artifact indexes |
| `grow_data_source_compression.sql` | Owner-applied source policy change: remove retention jobs and set three-month compression policies on existing source hypertables, preserving enabled settings and skipping plain relations with `NOTICE` |
| `operator_purge_history_before_2026_08.sql` | Dry-run-first owner purge of only the ten approved timestamped history streams before 2026-08-01 00:00 America/Toronto; drops eligible full hypertable chunks while restoring protected rows, uses row-wise fallback for cutoff chunks/plain tables, and refreshes canonical/legacy aggregates plus relay read models |

Historical note (2026-10-18): retired Grafana-only SQL migrations, views and verification scripts were removed: `grafana_performance_migration.sql`, `grafana_optimized_views.sql`, `grow_history_guards.sql`, `migrate_get_sensor_routing_1min_through_6h.sql`, `verify_get_sensor_routing.sql`, `verify_performance.sql`, `timescaledb_continuous_aggregates.sql`, and `timescaledb_compression.sql`. `latest_sensor_values` remains defined but has no in-repo consumer.

## Owner-run replica decommission

The production procedure for the former Iskra standby, replication slot, role,
`pg_hba.conf` rule, and primary settings is
[`ISKRA_REPLICA_DECOMMISSION.md`](ISKRA_REPLICA_DECOMMISSION.md). It is for the
database owner; agents and automated tests must not execute it.

## Owner-run grow-lifecycle deploy with database rename

The single-use procedure that deploys the grow lifecycle release, renames
`cea_sensors` to `sj_cea`, applies the lifecycle schema and source compression
policy changes, and installs `grow-archive-service` is
[`DATABASE_RENAME_RUNBOOK.md`](DATABASE_RENAME_RUNBOOK.md)
(`deploy-grow-lifecycle-once.sh`, `Infrastructure/scripts/rename-database.sh`).
Owner-run only; all of these files are deleted after finalize.

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
| >= 2 h | `measurement_1min` |
| >= 24 h | `measurement_5min` |
| >= 7 d | `measurement_hourly` |
| >= 30 d | `measurement_daily` |

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

## Rollback irreversibility

Removing or disabling a retention policy stops future deletion, but it cannot restore rows the policy already dropped. Plan separate backups before enabling either policy set in production.

## Min/max preservation

Every continuous aggregate keeps `avg_value`, `min_value`, and `max_value`. Removing min/max would hide fast swings (for example, humidity spikes), so all consumers expose the envelope.

## Owner-operated history purge before August 2026

`operator_purge_history_before_2026_08.sql` is an operator-only purge with a
fixed Toronto cutoff. Its fixed allowlist is exactly the ten
`source_retention.ANCHOR_COLUMNS` observation streams; it preserves their
latest/predecessor anchors and stream-specific guards. IDs and registries,
static setpoint/PID/period configuration, schedules/rules, Calendar/config,
archives, users and current-state tables are never targets. For each
hypertable range wholly before the cutoff, apply preserves every non-removable
row byte-for-byte, drops the exact range with TimescaleDB 2.28 `drop_chunks`,
then restores preserved rows in the same per-range transaction. The cutoff-
containing chunk and plain tables use row-wise deletion; compressed chunks
reaching that path are decompressed first, and the normal three-month policy
owns later recompression. Canonical and legacy measurement CAGGs are refreshed,
and the relay read model rebuilt after source deletion. ROW()-keyed aggregate
refresh windows are split at source-chunk boundaries to avoid the TimescaleDB
2.28 anonymous-composite failure across chunks. Dry-run reports, per stream,
chunk count/bytes to drop, anchors and other protected rows to restore,
row-wise/total deletions, rows kept, and min/max times.
Chunk ranges and daily row-wise batches commit independently; after a timeout
or lock refusal, the owner can review the report and safely rerun apply.
The default mode is read-only:

```bash
DB='cea_sensors'   # production name until the sj_cea rename is deployed; then 'sj_cea'
psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --set=apply=0 \
  --dbname="$DB" \
  --file=Infrastructure/database/operator_purge_history_before_2026_08.sql
```

Review the full dry-run report before any deletion. **Only the database owner
may run apply.** Apply requires the exact `current_database()` confirmation:

```bash
CONFIRM_DATABASE="$(psql -X --no-psqlrc --tuples-only --no-align \
  --dbname="$DB" --command='SELECT current_database()')"
psql -X --no-psqlrc --set=ON_ERROR_STOP=1 --set=apply=1 \
  --set="confirm_database=$CONFIRM_DATABASE" --dbname="$DB" \
  --file=Infrastructure/database/operator_purge_history_before_2026_08.sql
```

Run directly with psql autocommit; do not wrap it in `BEGIN` or use
`--single-transaction`. The guarded disposable proof acquires the shared
database-harness lock itself:

```bash
bash Infrastructure/database/tests/test-purge-history-before-2026-08.sh
```

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
PostgreSQL instance and can cause false timeouts. The lifecycle and purge
harnesses take `/tmp/grow-lifecycle-harness.lock` themselves for the whole run;
do not wrap them in `flock` (a nested lock on the same file deadlocks):

```bash
W="$(git rev-parse --show-toplevel)"
export PYTHONPATH="$W/Infrastructure/grow-archive-service:$W/Infrastructure/automation-service:$W/Infrastructure:$W/Infrastructure/grow-archive-service/tests"
bash "$W/Infrastructure/database/tests/test-grow-data-lifecycle.sh" --case <case>
```

The lifecycle harness cases are `schema`, `lossless`, `compressed-source`,
`source-compression-policy`, `deletion-fence`, `annual-correction`,
`enabled-lifecycle`, `membership`, `calendar-sync`, `history-availability`,
`files-export`, `archive-api`, `derived-repair`, `native-readers`, `rename`,
and `all`.

All database proofs use guarded disposable `monitoring_test_*` databases.
Never contact production services or apply lifecycle/compression SQL to
`cea_sensors` or `sj_cea`. Production schema changes, compression-policy
application, lifecycle enablement, remote publication, and source deletion
require separate explicit owner authorization.

## SQL tests

Database tests live in `Infrastructure/database/tests/`. They validate idempotency, tier edge cases, statistics equivalence, destructive-change rejection, and the operational retention boundaries. The harnesses are:

```bash
bash Infrastructure/database/tests/test-monitoring-read-models.sh --case reject-incompatible-and-destructive
bash Infrastructure/database/tests/test-operational-history-retention.sh
bash Infrastructure/database/tests/test-grow-data-lifecycle.sh --case schema
```

Run these locally against a disposable Postgres instance, never against production.

---

*Last updated: 2026-10-18*
