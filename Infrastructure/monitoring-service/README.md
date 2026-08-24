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

## Configuration

| Variable | Purpose |
|---|---|
| `MONITORING_POSTGRES_DSN` | Postgres/TimescaleDB read connection DSN |
| `MONITORING_REDIS_URL` | Redis read connection URL |
| `MONITORING_POSTGRES_POOL_SIZE` | Maximum read-pool size (default: `5`) |

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
