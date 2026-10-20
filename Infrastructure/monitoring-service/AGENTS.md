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
- Aggregate-backed control and relay-PID reads split at each CAGG materialization
  watermark and use raw source rows at or above it while backfill is incomplete.
- Sensor series and long-window statistics use the five-minute CAGG only below
  the minimum watermark of its five-minute/one-minute chain and replace
  above-frontier or parent-invalidated buckets with exact raw rows.
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
