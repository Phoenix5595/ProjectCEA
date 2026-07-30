# CEA INFRASTRUCTURE

## OVERVIEW

6 Python FastAPI microservices + React frontend. Unified data pattern: CAN/Modbus → Redis Stream + State → TimescaleDB.

## STRUCTURE

```
Infrastructure/
├── automation-service/    # Control logic, PID, schedules (8001)
├── backend/               # Sensor data API (8000)
├── frontend/              # React + Vite + Tailwind
├── can-processor-service/ # CAN bus → Redis/DB
├── soil-sensor-service/   # RS485 Modbus (8002)
├── weather-service/       # YUL weather data (8003)
├── database/              # TimescaleDB schema + docs
├── docs/                  # Architecture documentation
└── *.service              # systemd unit files
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Full setup guide | `README.md` |
| TODO tracking | `TODO_TRACKING.md` |
| Control refactor | `docs/control_engine_refactoring.md` |

## DATA FLOW

```
Sensors → CAN/Modbus → [can-processor/soil-sensor] 
    → Redis Stream (sensor:raw, MAXLEN 100K)
    → Redis State Keys (sensor:*, TTL 10s)
    → TimescaleDB (measurement hypertable)
    → Backend API → WebSocket → Frontend
```

## SHARED CODE

| Component | Location | Used By |
|-----------|----------|---------|
| `shared/infra_logging.py` | `automation-service/shared/` | All Python services |
| `shared/cluster_topology.py` | `Infrastructure/shared/` | Backend, automation; **canonical** room→cluster registry. See **Cluster Topology Contract** in `ProjectCEA/AGENTS.md`. |
| Pydantic models | Each `app/models.py` | Routes, database |
| Config loader | Each `app/config.py` | Service startup |

> **Cluster topology** (strict separation):
>
> - `main` is a **device-cluster name only** — never registered as a
>   sensor sub-cluster.
> - `front` / `back` are **sensor sub-cluster names only** — Flower
>   Room is the only room that has any.
> - Hierarchy: device `main` is the parent; sensor `front` / `back`
>   are children of Flower's `main`.
> - For unsplit rooms (Veg / Lab / Outside) the
>   `/api/sensors/{room}/{cluster}` URL slot reuses `main` as a
>   *room-wide sentinel* — a transport detail, not a sensor sub-cluster
>   registration. `sensor_subclusters_for("Veg Room")` returns `()`.
>
> Two source-of-truth files must stay in sync:
> `Infrastructure/shared/cluster_topology.py` (Python) and
> `Infrastructure/frontend/src/config/clusterTopology.ts` (frontend).
> See `ProjectCEA/AGENTS.md` → "Cluster Topology Contract" for the
> full table and validation rules.

### Control Snapshot

The automation service exposes `GET /api/devices/control-snapshot` as the single composite read model for device identity, MCP relay observations, assigned-device command state, and DFR commanded/acknowledged intensity. It does not own control and is rebuilt on demand from the same strict registry snapshot, relay observation, and command caches used by the control loop.

## EVENT BUS (Cross-Service)

Config changes propagate across services via Redis Streams:

```
Automation Service → Redis Stream (cea:events:config) → Backend Service → WebSocket → Frontend
```

| Component | Location | Purpose |
|-----------|----------|---------|
| ConfigEventBus | `automation-service/app/events/__init__.py` | Dual-publish (memory + Redis) |
| RedisStreamPublisher | `app/events/redis_streams.py` | Publish to Redis Streams |
| RedisEventConsumer | `app/events/consumer.py` | Read from stream |

## SERVICE DEPENDENCIES

```
postgresql.service
redis-server.service
    ↓
can-setup.service (oneshot)
    ↓
can-processor.service
soil-sensor-service.service
weather-service.service
    ↓
cea-backend.service
automation-service.service
```

## ANTI-PATTERNS (Infrastructure-specific)

| Never | Reason |
|-------|--------|
| Skip Redis Stream writes | Recent data queries fail |
| Invalidate only per-cluster schedule cache after a global `get_schedules()` consumer | `schedules:all` must be cleared when schedule rows change (e.g. light `target_intensity`); otherwise automation’s merged scheduler lags the DB |
| Use different TTL per service | 10s standard, except schedule state (no TTL) |
| Start services out of order | Dependencies will fail |
| Edit `.service` without daemon-reload | Config won't apply |

## COMMANDS

```bash
# Install service files
sudo cp *.service /etc/systemd/system/ && sudo systemctl daemon-reload

# Start all in order
sudo systemctl start postgresql redis-server can-setup can-processor soil-sensor-service cea-backend automation-service

# View all logs
journalctl -u can-processor -u cea-backend -u automation-service -f
```
