# ProjectCEA - Agent Guidelines

## Safety Rules

- NEVER call POST, PUT, PATCH, or DELETE against a production endpoint, database, or Redis instance. No exception.
- NEVER modify production data unless explicitly requested.
- NEVER run TRUNCATE, DELETE, DROP, or other destructive SQL against either protected database name or case-insensitive substring alias: `cea_sensors` or `sj_cea`.
- NEVER restart, reset, deploy, or reconfigure production services or hardware unless the owner explicitly asks.

The 2026-07-07 `TRUNCATE TABLE device_registry` incident showed that a single destructive call can take lights and climate offline. Violation is a severe failure.

## Architecture Summary

Sensors → CAN/Modbus/1-Wire/weather ingestion → Redis live state + TimescaleDB history → automation-service control loop → MCP23017 relays (I2C bus 0) + DFR0971 dimming (I2C bus 1) → React frontend. Read-only monitoring is served by `monitoring-service` (`:8005`); source-row grow/Veg archives are owned by `grow-archive-service` (`:8006`).

Current source: [`ARCHITECTURE.md`](ARCHITECTURE.md). Deployment state may lag; the deployed snapshot is external at `/opt/projectcea/current/deploy_manifest.json`.

## Authority Map

| Topic                       | Canonical Source                                                                                                                                                                                            |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Service inventory, ports    | [`Infrastructure/services.yaml`](Infrastructure/services.yaml)                                                                                                                                              |
| Caddy reverse-proxy routing | [`Infrastructure/caddy/Caddyfile`](Infrastructure/caddy/Caddyfile)                                                                                                                                          |
| Cluster topology            | [`Infrastructure/shared/cluster_topology.py`](Infrastructure/shared/cluster_topology.py) + [`Infrastructure/frontend/src/config/clusterTopology.ts`](Infrastructure/frontend/src/config/clusterTopology.ts) |
| Redis keys / retention      | [`Infrastructure/shared/redis_keys.py`](Infrastructure/shared/redis_keys.py)                                                                                                                                |
| Hardware addresses          | [`Infrastructure/automation-service/automation_config.yaml`](Infrastructure/automation-service/automation_config.yaml)                                                                                      |
| Control cadence             | `automation_config.yaml` `control.update_interval` (1 s, valid 1–5 s)                                                                                                                                       |
| Heating↔exhaust interlock   | Removed / unconfigured per `automation_config.yaml` line 77                                                                                                                                                 |
| Aggregate ladders           | Monitoring: `Infrastructure/monitoring-service/monitoring_service/sensor_models.py` (`compute_tier`); Backend: `Infrastructure/backend/app/repositories/sensor_repository.py` |

## Where to Look

| Component               | Document                                                                                                             |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Root architecture       | [`ARCHITECTURE.md`](ARCHITECTURE.md)                                                                                 |
| Infrastructure overview | [`Infrastructure/AGENTS.md`](Infrastructure/AGENTS.md)                                                               |
| Automation service      | [`Infrastructure/automation-service/AGENTS.md`](Infrastructure/automation-service/AGENTS.md)                         |
| Control subsystem       | [`Infrastructure/automation-service/app/control/AGENTS.md`](Infrastructure/automation-service/app/control/AGENTS.md) |
| Backend / sensor API    | [`Infrastructure/backend/AGENTS.md`](Infrastructure/backend/AGENTS.md)                                               |
| CAN processor           | [`Infrastructure/can-processor-service/AGENTS.md`](Infrastructure/can-processor-service/AGENTS.md)                   |
| Database / schema       | [`Infrastructure/database/AGENTS.md`](Infrastructure/database/AGENTS.md)                                             |
| Frontend                | [`Infrastructure/frontend/AGENTS.md`](Infrastructure/frontend/AGENTS.md)                                             |
| Monitoring feature      | [`Infrastructure/frontend/src/features/monitoring/AGENTS.md`](Infrastructure/frontend/src/features/monitoring/AGENTS.md) |
| Monitoring service      | [`Infrastructure/monitoring-service/AGENTS.md`](Infrastructure/monitoring-service/AGENTS.md)                         |
| Grow archive service  | [`Infrastructure/grow-archive-service/AGENTS.md`](Infrastructure/grow-archive-service/AGENTS.md)                           |
| Sensor nodes            | [`Sensor_Nodes/AGENTS.md`](Sensor_Nodes/AGENTS.md)                                                                   |
| Shared code             | [`Infrastructure/shared/AGENTS.md`](Infrastructure/shared/AGENTS.md)                                                 |

## Approved Local Commands

```bash
python3 Infrastructure/scripts/validate_cluster_topology.py
git diff --check
cd Infrastructure/automation-service && ruff check . && ruff format --check . && python3 -m compileall -q app && pytest -q app/tests/pure
cd Infrastructure/frontend && npx tsc --noEmit && npm run build && npx vitest run src/components/devices/__tests__/targetValidation.test.ts src/components/devices/__tests__/relaySnapshot.test.ts
bash Infrastructure/scripts/tests/test-reset-device-registry.sh
bash Infrastructure/scripts/tests/test-deploy-candidate.sh
```

Do not extend these commands to contact production endpoints, databases, Redis, or hardware.

## Browser QA viewport rule

- Dashboard and frontend desktop QA use exactly **1920x1080** and **1280x1440** in this pass. The 960px redesign is deferred; no phone or mobile-web QA project is supported.
- **R7 No-scroll.** Overview pages (`/`, `/flower`, `/vegetation`, `/laboratory`) never document-scroll at viewports ≥ 1280×720. From 768 px wide up to that size the page keeps a 1280×720 minimum layout and the document scrolls; phones below 768 px keep today's stacked layout (unsupported). Only three regions may scroll internally: the event-log body, the dashboard calendar inspector and a room overview's inline day detail; dialogs remain overlays.
- The web application uses a permanent 30px icon rail on every route and has no mobile drawer/layout guarantee; a separate mobile app does not exist yet.
- Use the isolated, guarded loopback fixture preview only. Port 4187 is the default; if occupied, select a free port from 4188–4199. Never set `BASE_URL`, contact production, or reuse, navigate, or stop an unrelated listener.

## Working Tree Discipline

- Keep changes scoped to the current task and preserve unrelated user changes. Do not stage, commit, reset, clean, stash, or overwrite unrelated files.
- All plans MUST be executed on a dedicated non-main branch in a separate Git worktree. Before editing application code, record and verify the base commit, branch and worktree path. Never implement a plan directly in the main checkout or reuse an unrelated worktree. Preserve unrelated changes; integration into main requires explicit user authorization.
- Local instruction changes in `.opencode/` and `.cursor/` are never staged.
- **R12 Documentation.** Never skip updating documentation. Every behaviour, contract, config or workflow change updates its canonical docs (AGENTS.md, REQUIREMENTS.md, DESIGN.md, README) in the same change.

---

_Last updated: 2026-10-25_
