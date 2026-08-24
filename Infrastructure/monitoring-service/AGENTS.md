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
- Keep package imports direct (`monitoring_service.*`), not sibling-service or
  `sys.path` imports.

## Boundaries

- Do not import automation control, MCP23017/DFR0971 hardware code, or writers.
- Do not add Redis/DB mutation calls, deployment files, Caddy routes, or units.
- Reuse `shared.health` rather than creating custom connectivity probes.
- Keep operational configuration in `MONITORING_*` environment variables.

## Local checks

```bash
python3 -m pytest -q tests/test_smoke.py
ruff check monitoring_service tests
python3 -m compileall -q monitoring_service
```
