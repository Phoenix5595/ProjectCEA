# CEA Dashboard v8 - FastAPI Backend

FastAPI backend for the CEA Dashboard providing REST API and WebSocket endpoints.

## Overview

The backend serves only API and WebSocket endpoints (no frontend UI from FastAPI root); `/` returns an API status payload. CORS must allow only configured frontend origins (localhost:3000, Tailscale/external host as configured via env) while permitting websockets. Favicon route should respond without requiring the React build (fallback to `backend/static/favicon.png` or `frontend/public/favicon.png` if present). Health check is available at `/health` for monitoring.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Or use virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
# Development mode (with auto-reload)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

- `GET /` - API status
- `GET /health` - Health check
- `GET /api/sensors/{location}/{cluster}` - Get sensor data
- `GET /api/sensors/{location}/{cluster}/live` - Get live sensor data from Redis
- `GET /api/statistics/{sensor_type}/{location}/{cluster}` - Get statistics
- `GET /api/config` - Get configuration
- `GET /api/config/locations` - Get available locations
- `WS /ws/{location}` - WebSocket for real-time updates

## Configuration

Uses the same `config.yaml` as v6/v7, located in the parent directory.

## Database

Connects to TimescaleDB database `cea_sensors` for historical data and Redis for live sensor values.
Performance rules:
- Live/short (≤1h) API/WebSocket endpoints read raw data (Timescale `measurement` + Redis).
- ≥12h API requests should favor hourly aggregates; multi-day requests should favor daily aggregates.
- For “latest” values, prefer optimized views/functions (`latest_sensor_values`, `get_latest_by_pattern`) instead of ad-hoc `MAX(time)` queries.

## Alerting

Alerting is handled by Grafana. See `Infrastructure/frontend/grafana/README.md` for alerting setup instructions.
