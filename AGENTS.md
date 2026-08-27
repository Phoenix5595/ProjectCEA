# ProjectCEA - Agent Guidelines

## Safety Rules

- NEVER call POST, PUT, PATCH, or DELETE against a production endpoint, database, or Redis instance. No exception.
- NEVER modify production data unless explicitly requested.
- NEVER run TRUNCATE, DELETE, DROP, or other destructive SQL against `cea_sensors`.
- NEVER restart, reset, deploy, or reconfigure production services or hardware unless the owner explicitly asks.

The 2026-07-07 `TRUNCATE TABLE device_registry` incident showed that a single destructive call can take lights and climate offline. Violation is a severe failure.

## Architecture Summary

Sensors → CAN/Modbus/1-Wire/weather ingestion → Redis live state + TimescaleDB history → automation-service control loop → MCP23017 relays (I2C bus 0) + DFR0971 dimming (I2C bus 1) → React frontend + Grafana.

Current source: [`ARCHITECTURE.md`](ARCHITECTURE.md). Deployment state may lag; the deployed snapshot is external at `/opt/projectcea/current/deploy_manifest.json`.

## Authority Map

| Topic | Canonical Source |
|---|---|
| Service inventory, ports | [`Infrastructure/services.yaml`](Infrastructure/services.yaml) |
| Caddy reverse-proxy routing | [`Infrastructure/caddy/Caddyfile`](Infrastructure/caddy/Caddyfile) |
| Cluster topology | [`Infrastructure/shared/cluster_topology.py`](Infrastructure/shared/cluster_topology.py) + [`Infrastructure/frontend/src/config/clusterTopology.ts`](Infrastructure/frontend/src/config/clusterTopology.ts) |
| Redis keys / retention | [`Infrastructure/shared/redis_keys.py`](Infrastructure/shared/redis_keys.py) |
| Hardware addresses | [`Infrastructure/automation-service/automation_config.yaml`](Infrastructure/automation-service/automation_config.yaml) |
| Control cadence | `automation_config.yaml` `control.update_interval` (1 s, valid 1–5 s) |
| Heating↔exhaust interlock | Removed / unconfigured per `automation_config.yaml` line 77 |
| Aggregate ladders | Backend: `Infrastructure/backend/app/repositories/sensor_repository.py`; Grafana: `Infrastructure/database/grafana_performance_migration.sql` |

## Where to Look

| Component | Document |
|---|---|
| Root architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Infrastructure overview | [`Infrastructure/AGENTS.md`](Infrastructure/AGENTS.md) |
| Automation service | [`Infrastructure/automation-service/AGENTS.md`](Infrastructure/automation-service/AGENTS.md) |
| Control subsystem | [`Infrastructure/automation-service/app/control/AGENTS.md`](Infrastructure/automation-service/app/control/AGENTS.md) |
| Backend / sensor API | [`Infrastructure/backend/AGENTS.md`](Infrastructure/backend/AGENTS.md) |
| CAN processor | [`Infrastructure/can-processor-service/AGENTS.md`](Infrastructure/can-processor-service/AGENTS.md) |
| Database / schema | [`Infrastructure/database/AGENTS.md`](Infrastructure/database/AGENTS.md) |
| Frontend | [`Infrastructure/frontend/AGENTS.md`](Infrastructure/frontend/AGENTS.md) |
| Monitoring feature | Created by T6 |
| Sensor nodes | [`Sensor_Nodes/AGENTS.md`](Sensor_Nodes/AGENTS.md) |
| Iskra / Grafana | [`Infrastructure/iskra_stack/AGENTS.md`](Infrastructure/iskra_stack/AGENTS.md) |
| Shared code | [`Infrastructure/shared/AGENTS.md`](Infrastructure/shared/AGENTS.md) |

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

## Working Tree Discipline

- Do not stage, commit, reset, clean, stash, or overwrite files outside the task allowlist.
- Documentation edits are limited to the paths listed in `.omo/evidence/agents-knowledge-base-refresh/T1/tracked-allowlist.txt`.
- Local instruction changes in `.opencode/` and `.cursor/` are never staged.

---

*Last updated: 2026-08-10*
