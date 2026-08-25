# Automation Service

Core control service on port 8001: device registry, 1-second control loop, PID/VPD climate logic, schedules, and light control. Also serves the React SPA via Caddy at `:8080`.

## Startup and container boundaries

`app/main.py` builds the FastAPI app; `app/container.py` wires dependencies. `app/models/config_schema.py` validates `automation_config.yaml` at startup, and invalid config fails launch. Key config facts:

- `control.update_interval`: 1 s (validated range 1–5 s).
- MCP23017 relays: I2C bus 0, address 39 decimal (`0x27`).
- DFR0971 dimmers: I2C bus 1, decimal 88/89/90 (`0x58/0x59/0x5A`).

## Registry and hardware ownership

`device_registry` is the sole source of truth for device identity, relay channels, and DFR board/channel bindings. No YAML device definitions or commissioning path participates in runtime control.

| Component | Responsibility |
|---|---|
| `RuntimeDeviceRegistry` | Builds and atomically installs one immutable `RuntimeDeviceSnapshot` per tick |
| `DeviceRegistryService` | Sole supported mutation path; performs safe output sequencing and confirmed relay steals |
| `RelayBoardStateManager` | Sole MCP23017 board sampler and owner of `cea:relay:board_snapshot` |
| `DeviceCommandService` | Assigned-device command state (`AUTO`, `MANUAL_OFF`, `TIMED_ON`) |

An empty registry is valid: it installs a ready empty snapshot and emits no relay-ON or nonzero DFR commands.

## Control cadence and safety

The control loop runs every 1 s. VPD is the master climate controller; PID is used for heating, cooling, and CO2 only. The global heating-failure↔exhaust interlock is **not currently configured**; it was removed per `automation_config.yaml` line 77.

`GET /api/devices/control-snapshot` joins the registry snapshot, relay observation, assigned-device command state, and DFR commanded/acknowledged intensity into one read model.

## Repositories and routes

Repositories live in `app/repositories/` and own all DB access. Routes live in `app/routes/` and cover schedules, lights, climate periods, devices/registry, hardware, PID, room modes, alarms, and system config. See `Infrastructure/automation-service/REQUIREMENTS.md` for normative behavior and `ARCHITECTURE.md` for service boundaries.

## Local verification

```bash
cd Infrastructure/automation-service && ruff check . && ruff format --check . && python3 -m compileall -q app && pytest -q app/tests/pure
```

Production endpoints, I2C, and the production database must never be contacted by automated tests.

## Where to Look

| Topic | Document |
|---|---|
| Control layer details | [`app/control/AGENTS.md`](app/control/AGENTS.md) |
