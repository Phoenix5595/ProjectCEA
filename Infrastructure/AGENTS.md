# CEA Infrastructure

## Active Service Map

| Service | Port | Unit | Role |
|---|---|---|---|
| cea-backend | 8000 | cea-backend.service | Sensor API, WebSocket |
| automation-service | 8001 | automation-service.service | Control loop, registry, SPA host |
| soil-sensor-service | 8002 | soil-sensor-service.service | Modbus soil sensors |
| weather-service | 8003 | weather-service.service | DB/cache-backed METAR weather data |
| onewire-worker | 8004 | onewire-worker.service | 1-Wire temperatures |
| monitoring-service | 8005 | monitoring-service.service | Read-only monitoring API |
| grow-archive-service | 8006 | grow-archive-service.service | Grow archive storage/lifecycle API |

Source: [`services.yaml`](services.yaml).

## Shared Cross-Service Flow

```
Sensors → can-processor / soil-sensor / onewire / weather
        → Redis `sensor:raw` (maxlen 100,000)
        → TimescaleDB `measurement`
        → backend / automation / monitoring-service
```

Grow/Veg source-row archives are owned by `grow-archive-service` (`:8006`): it reads source history directly, publishes verified ZIPs, and only deletes rows through enabled, ZIP-verified lifecycle work. It does not own a separate database cold store or Redis writes.

Config changes flow from automation-service to backend to frontend via Redis Streams (`cea:events:config`) and WebSocket. Source: `automation-service/app/events/__init__.py`.

Monitoring range validation is intentionally split: monitoring-service sensor/control reads validate ranges down to 1 second (`sensor_models.MINIMUM_RANGE = timedelta(seconds=1)`), while automation-service projection snapshot construction independently requires ranges of at least 5 minutes (`app/schemas/monitoring_models.py` `MINIMUM_RANGE`). These are separate validators serving different purposes; neither is being changed by the plan.

Deploys are exact-commit candidates (`deploy.sh [--commit <rev>] | deploy.sh major|minor|patch`): the script exports committed `Infrastructure/`, runs read-only Ruff, applies Alembic and SQL migrations, synchronizes repo-managed units/drop-ins and Caddy, then requires each configured `/health` and `/ready` endpoint. Downtime migrations require a fresh passing rehearsal and a pre-deploy dump. Rollback restores the prior systemd/Caddy files; SQL migrations run down only for an older target (`--with-down-migrations` for manual rollback), and Alembic is never downgraded. Finalize restarts the candidate's deploy-managed units and rechecks health/readiness before promotion.

`deploy.sh` is the only way to deploy and to version a release. Run it from the main checkout:

- `./deploy.sh patch|minor|major`: a versioned release. All preflight checks run first (clean tracked inputs, no active candidate, NTP sync), so a blocked deploy changes nothing. The script then runs `npm run release:<tier>` in `Infrastructure/frontend` and commits only `package.json`/`package-lock.json` on the checked-out branch as `release: vX.Y.Z (<tier>)`. That commit is deployed. A failed bump or commit restores both files and deploys nothing. `--commit` cannot be combined with a tier.
- `./deploy.sh [--commit <rev>]`: redeploys an exact existing commit without changing the version (hotfix or re-deploy of a known commit).
- Output steps are numbered `[1/11]`…`[11/11]`. After `candidate-active`, inspect the site, then `./finalize-deploy.sh --confirm` or `./rollback-deploy.sh`.
- Never bump `package.json` by hand or run `npm version` outside `deploy.sh`.

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

## Editable Service Settings

Editable service values are managed through automation-service: inspect them with `GET /api/settings` and update a service with `PUT /api/settings/{service}`.

- Soil polling and discovery intervals (`poll_interval_seconds`, `discovery_interval_seconds`) use the soil sensor service's YAML intervals only as DB/cache fallbacks.
- The 1-Wire polling interval (`poll_interval_seconds`) is managed there; `onewire_config.yaml` retains the device-ID-to-sensor-name mapping and polling fallback.
- Weather station (`station_icao`), polling interval (`poll_interval_seconds`), and API URL (`api_url`) are sourced only from DB/cache settings. The weather room/device names are also DB/cache-backed and read-only; weather has no YAML settings file.

## Child Guidance

| Topic | Document |
|---|---|
| Automation service | [`automation-service/AGENTS.md`](automation-service/AGENTS.md) |
| Backend | [`backend/AGENTS.md`](backend/AGENTS.md) |
| CAN processor | [`can-processor-service/AGENTS.md`](can-processor-service/AGENTS.md) |
| Database | [`database/AGENTS.md`](database/AGENTS.md) |
| Frontend | [`frontend/AGENTS.md`](frontend/AGENTS.md) |
| Monitoring service | [`monitoring-service/AGENTS.md`](monitoring-service/AGENTS.md) |
| Grow archive service | [`grow-archive-service/AGENTS.md`](grow-archive-service/AGENTS.md) |
| Shared code | [`shared/AGENTS.md`](shared/AGENTS.md) |

## Anti-Patterns

- Never skip Redis Stream writes; recent-data queries fail.
- Never invalidate only per-cluster schedule caches after a global `get_schedules()` consumer; clear `schedules:all` too.
- Never edit `.service` files without `sudo systemctl daemon-reload`.

---

*Last updated: 2026-10-18*
