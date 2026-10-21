# Grow Archive Service

`grow-archive-service` owns source-row grow/Veg archives and the explicit Historical ZIP API. It runs as `grow-archive-service.service` on port `8006` (`/health`; see `../services.yaml` and `../grow-archive-service.service`). It has no hardware or Redis-write authority. The service reads PostgreSQL history directly, writes verified ZIP artifacts to the configured archive store, and records availability for removed ranges.

## Module map

| Module | Responsibility |
|---|---|
| `capture.py` | Capture raw source rows directly from resolved dataset windows; assemble manifest and capture parts |
| `deletion.py` | Recheck ZIP part counts/digests and delete eligible source rows within the deleting transaction |
| `exporter.py` | Coordinate capture, ZIP writing, verification, publication, and catalog state |
| `webdav.py` | Nextcloud/WebDAV transport |
| `cache.py` | Verified local artifact cache, pins, and staging cleanup |
| `loads.py` | Explicit ZIP-only Historical selection and load lifecycle |
| `annual.py`, `annual_codec.py` | Annual containers around verified quarterly components |
| `parquet_codec.py` | Typed Parquet archive members, metadata, and validation |
| `lifecycle.py` | `LifecycleService` construction, tick, and job entrypoints |
| `lifecycle_types.py` | Lifecycle data contracts, identity-room resolution, and held-scope deduplication |
| `lifecycle_calendar.py` | Toronto quarter/date helpers |
| `lifecycle_constants.py` | Lifecycle timing, expiry, and planning constants |
| `lifecycle_planning.py` | Grow, Veg-quarter, and annual archive planning |
| `lifecycle_expiry.py` | Identity/stream binding, room locks, holds, and cursor/slice expiry |
| `dispatcher.py`, `jobs.py` | Leased job dispatch, queue operations, and fencing |
| `derived_refresh.py` | Refresh derived history after source deletion |
| `policy_gate.py` | Block source retention policies; allow source compression only at a three-month interval or longer |
| `streams.py` | Source stream bindings, hypertable list, and nullable-time lookup |
| `schema.py` | Source catalog and live schema preflight |
| `membership.py` | Resolved scope windows and shared source-row selection predicates |
| `graphs.py`, `graph_builder.py` | Read ZIP graph data and add derived graph members; graph data is supplemental |
| `execution.py` | Dedicated low-priority heavy-work worker and duty-cycle throttling |

## Archive lifecycle and data contract

- Closing a Flower grow queues an export. Source rows remain in their normal tables until a verified ZIP exists and the applicable deletion cutoff arrives: the Flower anniversary, the Veg twelve-month cutoff, or nongrow twelve-month expiry.
- Before deletion, the service re-reads each source slice and compares its row count and canonical digest with the verified ZIP inside the deleting transaction. Only matching removable rows are deleted. Removed ranges are recorded in `archive_availability`, and derived history is refreshed.
- Only time-series observation tables are ever deleted: `measurement`, `automation_state`, `effective_setpoints`, `control_history`, `relay_observation`, `monitoring_room_photoperiod`, `monitoring_photoperiod_coverage`, `mode_transition_history`, and `pid_parameter_history` (`source_retention.ANCHOR_COLUMNS`, catalog role `observation`). Every source DELETE goes through `removable_observation_sql`, which raises `archive_source_protected` for anything else; registries/ids, setpoint and schedule config, Calendar periods and context streams (`config_versions`, `calendar_mode_application`) are never deleted, and the latest/predecessor row per anchor key survives.
- Capture includes every selected raw row from every defined stream: `measurement`, `automation_state`, `effective_setpoints`, `control_history`, `relay_observation`, `monitoring_room_photoperiod`, `monitoring_photoperiod_coverage`, `mode_transition_history`, `pid_parameter_history`, `config_versions`, and `calendar_mode_application`. In particular, per-second measurements and device-control history are not replaced by aggregates. Graph buckets are additional members, never a substitute for raw rows. A present source without a descriptor blocks export at schema preflight rather than being silently skipped.
- Current source descriptors use `time` for renamed event-time columns; manifests preserve each export's `time_column`, so older `timestamp`, `observed_at`, and `triggered_at` ZIPs remain readable. Annual schema comparisons normalize only those legacy event-time labels and keep the newest child's descriptor. Canonical aggregate ordering now uses `last(value, "time")`, so derived refresh no longer needs the former `ROW(...)`-specific chunk split.
- Historical loads use verified ZIPs only; there is no local-database load mode. Native Timescale compression is scheduled after three months on the five source hypertables; it is separate from archive publication and deletion.
- After source removal, a correction rebuilds only the affected time window from the prior verified ZIP plus current source rows. A room or identity reassignment touching deleted rows is rejected; the service does not rewrite archived ownership.

## Activation, resource limits, and errors

`ARCHIVE_LIFECYCLE_ENABLED` defaults to `false`. Exports and explicit ZIP loads remain available while scheduled source deletion stays disabled. Enabling deletion is an independently authorized production operation.

`ARCHIVE_CPU_DUTY_CYCLE` defaults to `0.25` and accepts values from `0.05` through `1.0`. Export work uses a dedicated worker at nice priority 19, separate from the shared executor; the duty-cycle cap throttles worker work and database-side heavy statements. Lower duty cycles reduce CPU demand at the cost of longer exports.

- `archive_source_changed`: source rows no longer match the verified ZIP digest. The dataset is marked dirty with an incremented revision; the error is non-transient and the dataset must be exported again before deletion.
- `archive_correction_requires_source`: HTTP 409 when a scope correction intersects source rows already removed from the database.
- `archive_export_required`: no verified ZIP is available for the requested Historical load.

## Disposable proof harness

Run from inside the implementation worktree. The path expression is worktree-relative; do not run DB proofs concurrently because they share disposable PostgreSQL resources and parallel runs can cause false timeouts.

```bash
W="$(git rev-parse --show-toplevel)"
export PYTHONPATH="$W/Infrastructure/grow-archive-service:$W/Infrastructure/automation-service:$W/Infrastructure:$W/Infrastructure/grow-archive-service/tests"
bash "$W/Infrastructure/database/tests/test-grow-data-lifecycle.sh" --case <case>
```

Cases: `schema`, `lossless`, `compressed-source`, `source-compression-policy`, `deletion-fence`, `annual-correction`, `enabled-lifecycle`, `membership`, `calendar-sync`, `history-availability`, `files-export`, `archive-api`, `derived-repair`, `native-readers`, `rename`, and `all`.

The harness is guarded for disposable `monitoring_test_*` databases. Never direct it at `cea_sensors`, `sj_cea`, a production service, Redis, or a production Nextcloud destination. Do not apply lifecycle/compression SQL, enable source deletion, publish/delete production artifacts, or mutate production source rows without separate explicit owner authorization.
