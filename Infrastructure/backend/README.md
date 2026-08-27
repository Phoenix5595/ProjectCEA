# CEA Backend Service

FastAPI service on port 8000. Serves historical sensor data from TimescaleDB, live values from Redis, and real-time updates over WebSocket. The frontend is served separately; `/` returns a small API status payload.

## Setup

Run inside the service virtual environment:

```bash
cd Infrastructure/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

The service loads `Infrastructure/backend/config.yaml`. The loader also searches legacy fallback paths if they exist, but the canonical path is the only one used in current deployments. Database credentials come from environment variables; the password is read from a systemd credential file if available, otherwise from `POSTGRES_PASSWORD` (`shared/db_credentials.py`).

Environment variables used:

- `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PORT`
- `POSTGRES_PASSWORD` (fallback)
- `REDIS_URL`
- `FRONTEND_ORIGINS`

## Running

```bash
# Development
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production (run via systemd)
sudo systemctl restart cea-backend.service
```

## Health checks

- `GET /health` — liveness; does not touch Postgres or Redis.
- `GET /ready` — readiness; checks the Postgres pool and the broadcast task.

## Data flow

```
CAN / soil / weather / one-wire ingestion
    -> Redis state keys (sensor:*, 10 s TTL)
    -> Redis Stream (sensor:raw)
    -> TimescaleDB (measurement hypertable)
Backend
    -> /api/sensors/{location}/{cluster}
    -> /api/sensors/{location}/{cluster}/live
    -> /ws/{location}
    -> Frontend
```

## Key endpoints

- `GET /api/sensors/{location}/{cluster}` — historical sensor data
- `GET /api/sensors/{location}/{cluster}/live` — current Redis values
- `GET /api/live/all` — all live sensor values
- `GET /api/config/locations` — available locations
- `WS /ws/{location}` — real-time sensor stream

Cluster names are validated against `shared/cluster_topology.py`.

## Monitoring

```bash
journalctl -u cea-backend.service -f
```
