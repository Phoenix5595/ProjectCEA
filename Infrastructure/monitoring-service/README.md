# CEA Monitoring Service

Read-only monitoring API foundation. It owns monitoring reads from TimescaleDB
and Redis; `automation-service` remains the sole control and hardware authority.

## Service contract

- Binds to port **8005** when started with Uvicorn.
- `GET /health` is a liveness response and does not contact Postgres or Redis.
- `GET /ready` returns `200` only when both read dependencies are available.
  It returns `503` otherwise, with distinct `database` and `redis` checks.
- The service performs no database or Redis writes. Readiness uses only
  Postgres `SELECT 1` and Redis `PING` through `shared.health`.
- The monitoring-service sensor/control read API validates ranges down to
  1 second (`sensor_models.MINIMUM_RANGE = timedelta(seconds=1)`). The
  automation-service projection snapshot construction independently requires
  ranges of at least 5 minutes (`app/schemas/monitoring_models.py`
  `MINIMUM_RANGE`). These are separate validators serving different purposes;
  neither is being changed by the plan.

## Configuration

| Variable | Purpose |
|---|---|
| `MONITORING_POSTGRES_DSN` | Postgres/TimescaleDB read connection DSN |
| `MONITORING_REDIS_URL` | Redis read connection URL |
| `MONITORING_POSTGRES_POOL_SIZE` | Maximum read-pool size (default: `8`, range `1`–`8`) |
| `MONITORING_POSTGRES_ACQUIRE_TIMEOUT_SECONDS` | Read-pool acquire timeout in seconds (default: `10`, range greater than `0` through `60`) |
| `MONITORING_POSTGRES_STATEMENT_TIMEOUT_MS` | Postgres statement timeout in milliseconds (default: `8000`, range `1`–`60000`) |

Run locally from this directory:

```bash
PYTHONPATH=.. uvicorn monitoring_service.main:app --host 0.0.0.0 --port 8005
```

## Verification

```bash
python3 -m pytest -q tests/test_smoke.py
ruff check monitoring_service tests
python3 -m compileall -q monitoring_service
```

The package uses direct `monitoring_service.*` imports. It must not import
automation control, hardware drivers, relay control, or write-capable adapters.
