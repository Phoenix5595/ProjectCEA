# Monitoring Service

## Ownership

`monitoring_service/` owns read-only monitoring API behavior on port 8005.
It may read TimescaleDB and Redis publication data; it never writes either.
`automation-service` owns control decisions, relay operation, and hardware.

## Map

| Path | Responsibility |
|---|---|
| `monitoring_service/main.py` | FastAPI app and read-client lifecycle |
| `monitoring_service/config.py` | `MONITORING_*` environment boundary |
| `monitoring_service/readiness.py` | Typed Postgres/Redis readiness contract |
| `monitoring_service/control_reads.py` | Runtime facade over the control read repositories (`RuntimeControlReads`) |
| `monitoring_service/control_history_repository.py` | Read-only control history (`ControlHistoryRepository`) |
| `monitoring_service/relay_timeline_repository.py` | Read-only relay timeline (`RelayTimelineRepository`) |
| `monitoring_service/control_publication_repository.py` | Current and future control publications from Redis (`ControlPublicationRepository`) |
| `tests/test_smoke.py` | Endpoint and import-boundary smoke coverage |
| `../shared/health.py` | Shared non-mutating Postgres/Redis probes |

## API contract

- Run with `PYTHONPATH=.. uvicorn monitoring_service.main:app --host 0.0.0.0 --port 8005`.
- `GET /health` is process liveness only.
- `GET /ready` returns independent `database` and `redis` check results and
  uses HTTP 503 when either read dependency is unavailable.
- Recorded control/tail and relay-timeline row facts expose event times as
  `time`; live-publication and sensor-series time fields retain their existing
  contracts.
- `ControlPublicationRepository` discards elapsed future intervals and validates
  the remaining timeline. A leading interval expiring between automation
  publications must not make a still-valid projection unavailable. Current
  snapshot freshness, publication version, and remaining future interval
  validation still apply.
- Aggregate-backed control and relay-PID reads split at each CAGG materialization
  watermark and use raw source rows at or above it while backfill is incomplete.
- Sensor series and long-window statistics use the five-minute CAGG only below
  the minimum watermark of its five-minute/one-minute chain and replace
  above-frontier or parent-invalidated buckets with exact raw rows.
- Keep `source_rows` and `relay_assignment_intervals` declared `MATERIALIZED`.
  Without it PostgreSQL inlines the tiered union under the sensor/node-pattern
  join and re-runs it per room node, and re-executes the relay assignment CTE
  per LATERAL aggregate row (a 7-day sensor or relay read then exceeds the 8 s
  statement deadline).
- The raw fallback of every tiered sensor statement starts at
  `GREATEST(time_bucket(<interval>, start), time_bucket(<interval>,
  LEAST(frontier, <earliest invalidation overlapping the range>)))`. Keep that
  floor parameter-bound: it is what stops a fully materialized 7-day range from
  aggregating raw rows it can never use.
- CPU-bound envelope builds (control history `build_control_history_envelope`,
  including its budget pass) run through `anyio.to_thread.run_sync`; keep new
  whole-window builds off the event loop, since one 7-day room build costs
  seconds of pure Python and stalls every concurrent read.

### Graph read contracts

- Point budgets (R1): sensor range/statistics and control history/tail accept
  `max_points` from 10 to 100000; the UI caps each displayed line at 4000 points
  and at one point per CSS pixel. Sensor and control overview readers accept
  10 to 4000 points and keep their whole-hour bucket ladder.
- `GET /api/sensors/monitoring/range/{location}` accepts optional
  `interval_seconds` only together with `max_points`. It must be one of
  `NICE_INTERVAL_SECONDS` and satisfy
  `ceil(range_seconds / interval_seconds) <= max_points`; otherwise the route
  answers 400 `{"detail": "invalid_interval_seconds"}`. A valid value selects the
  tier through `sensor_models.tier_for_interval` (below 60 s raw, below 300 s
  1-minute, otherwise 5-minute), replaces the derived read interval, and is
  echoed as `metadata.requested_interval_seconds` and `metadata.interval_seconds`.
- Control history/tail `scope` values (R2), always serializing every envelope
  field with empty tuples where out of scope:
  - `all`: climate setpoints, recorded lights, devices, PID, photoperiod.
  - `room`: climate setpoints, recorded lights, photoperiod; devices/PID empty
    and their automation-state source is not queried.
  - `climate`: climate setpoints and photoperiod; lights, devices and PID empty.
  - `photoperiod`: committed phase/coverage facts only; every other timeline
    empty, and no light family or light-derived phase witnesses are read (R5).
- History/tail range limits: `all` and `room` at most 7 days; `climate` and
  `photoperiod` at most 400 days. Longer ranges answer 400
  `{"detail": "range_too_long"}`. The overview reader keeps its own contract:
  scopes `all | lights`, a 5-minute minimum, no upper range limit, and the
  10–4000 point budget.
- `scope=lights` is no longer a history/tail value and is rejected with the same
  400 used for unknown scopes; only the bounded overview still accepts it.
- `interval_seconds` is null on unbudgeted history/tail reads and on the
  photoperiod-only scope; it is the applied read bucket for other budgeted
  scopes.

- Keep package imports direct (`monitoring_service.*`), not sibling-service or
  `sys.path` imports.

## Boundaries

- Do not import automation control, MCP23017/DFR0971 hardware code, or writers.
- Do not add Redis/DB mutation calls, deployment files, Caddy routes, or units.
- Reuse `shared.health` rather than creating custom connectivity probes.
- Keep operational configuration in `MONITORING_*` environment variables.

Ordinary sensor/overview/control/relay history scopes are carried by
`shared.history_availability.local_history_scope`. `ReadOnlyDatabase.fetch`
checks `require_local_history` inside the same repeatable-read transaction
as the requested statement. Source ranges removed after verified archive
deletion are recorded in `archive_availability`; these reads return 409 with
dataset IDs. Expired nongrow ranges return 410. Current Redis publications
and latest/predecessor control snapshots never load archives.

- Native and archive overview readers merge NUMERIC count/sum/squared-sum/extrema
  before final float presentation; never average averages or round source
  contributions through Python floats. Requested PID overview spans instead merge
  LOCF weighted values by covered seconds (sum of average × seconds divided by
  total covered seconds); `sample_count` remains the count of source rows.
- Sensor/light points retain zero-count-null and one-row-equal contracts. A
  requested-PID point may have zero or one in-span source row while a carried
  predecessor provides LOCF coverage; its average may differ from its extrema,
  and `sample_count` remains the honest count of rows inside the span.

## Local checks

```bash
python3 -m pytest -q tests/test_smoke.py
ruff check monitoring_service tests
python3 -m compileall -q monitoring_service
```
