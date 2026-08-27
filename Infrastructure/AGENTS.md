# CEA Infrastructure

## Active Service Map

| Service | Port | Unit | Role |
|---|---|---|---|
| cea-backend | 8000 | cea-backend.service | Sensor API, WebSocket |
| automation-service | 8001 | automation-service.service | Control loop, registry, SPA host |
| soil-sensor-service | 8002 | soil-sensor-service.service | Modbus soil sensors |
| weather-service | 8003 | weather-service.service | YUL weather data |
| onewire-worker | 8004 | onewire-worker.service | 1-Wire temperatures |
| monitoring-service | 8005 | monitoring-service.service | Read-only monitoring API |

Source: [`services.yaml`](services.yaml).

## Shared Cross-Service Flow

```
Sensors → can-processor / soil-sensor / onewire / weather
        → Redis `sensor:raw` (maxlen 100,000)
        → TimescaleDB `measurement`
        → backend / automation / Grafana
```

Config changes flow from automation-service to backend to frontend via Redis Streams (`cea:events:config`) and WebSocket. Source: `automation-service/app/events/__init__.py`.

Monitoring range validation is intentionally split: monitoring-service sensor/control reads validate ranges down to 1 second (`sensor_models.MINIMUM_RANGE = timedelta(seconds=1)`), while automation-service projection snapshot construction independently requires ranges of at least 5 minutes (`app/schemas/monitoring_models.py` `MINIMUM_RANGE`). These are separate validators serving different purposes; neither is being changed by the plan.

Deploys restart all managed services as full-service candidates; there is no component-scoped deploy. Rollback restores code/release state but does not revert database schema. This plan performs no schema migrations.

## Shared Code Ownership

| Component | Location | Consumers |
|---|---|---|
| Cluster topology | [`shared/cluster_topology.py`](shared/cluster_topology.py) | Backend, automation, frontend mirror |
| Relay topology | [`shared/relay_topology.py`](shared/relay_topology.py) | Automation control snapshot |
| Redis keys / retention | [`shared/redis_keys.py`](shared/redis_keys.py) | All Python services |
| DB batch helpers | [`shared/db_batch_writer.py`](shared/db_batch_writer.py) | CAN, soil, weather, automation |
| Structured logging | [`shared/infra_logging.py`](shared/infra_logging.py) | All Python services |
| CORS middleware | [`shared/middleware.py`](shared/middleware.py) | All FastAPI services |

See [`shared/AGENTS.md`](shared/AGENTS.md) for boundary details.

## Service Dependencies

```
postgresql.service
redis-server.service
    ↓
can-setup.service
    ↓
can-processor.service
cea-backend.service
    ↓
soil-sensor-service.service
weather-service.service
onewire-worker.service
    ↓
automation-service.service
```

Source: [`services.yaml`](services.yaml) `start_order`.

## Child Guidance

| Topic | Document |
|---|---|
| Automation service | [`automation-service/AGENTS.md`](automation-service/AGENTS.md) |
| Backend | [`backend/AGENTS.md`](backend/AGENTS.md) |
| CAN processor | [`can-processor-service/AGENTS.md`](can-processor-service/AGENTS.md) |
| Database | [`database/AGENTS.md`](database/AGENTS.md) |
| Frontend | [`frontend/AGENTS.md`](frontend/AGENTS.md) |
| Shared code | [`shared/AGENTS.md`](shared/AGENTS.md) |
| Iskra / Grafana | [`iskra_stack/AGENTS.md`](iskra_stack/AGENTS.md) |

## Anti-Patterns

- Never skip Redis Stream writes; recent-data queries fail.
- Never invalidate only per-cluster schedule caches after a global `get_schedules()` consumer; clear `schedules:all` too.
- Never edit `.service` files without `sudo systemctl daemon-reload`.

---

*Last updated: 2026-08-10*
