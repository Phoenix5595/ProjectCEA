# ProjectCEA – System Architecture

**Last updated (deployed):** 2026-07-12

**Prepared update:** 2026-07-29 — relay registry canonicalization is locally verified and awaiting owner-approved deployment. The prior deployed narrative is retained as `archive/ARCHITECTURE_2026-07-12.md`.

**Plan-style schematic:** `ARCHITECTURE_SCHEMATIC.md`.

---

## System at a Glance

```text
Sensors → ingestion services → Redis + TimescaleDB → automation-service
       → immutable per-tick runtime snapshot → MCP23017 / DFR0971
       → frontend and Grafana
```

| Constraint | Contract |
|---|---|
| Sensor sampling | 1 Hz minimum |
| Redis hot-path operations | under 1 ms |
| Database batch delay | at most 100 ms |
| Control tick | 1–5 seconds |
| Relay hardware | MCP23017 only, I2C bus 0 |
| Dimming hardware | DFR0971 only, I2C bus 1 |

The backend on port 8000 serves sensor data. The automation service on port 8001 serves the control API and frontend build. Redis holds live state; TimescaleDB holds history and configuration.

---

## Canonical Device and Output Design

### One source of assignment truth

`device_registry` is the sole source for device identity, room/cluster placement, relay channel bindings, DFR board/channel bindings, and device capabilities. No YAML device definitions, duplicate assignment maps, or commissioning subsystem participate in runtime device control.

`automation_config.yaml` remains limited to non-device service configuration such as hardware buses, control timing, sensor metadata, and safety configuration. It must not define devices, relay assignments, or DFR assignments.

### Startup and runtime projection

1. `ServiceContainer` initializes the database and Redis boundaries.
2. `RuntimeDeviceRegistry.load_startup()` reads registry and light projections before exposing a snapshot.
3. `RuntimeDeviceSnapshot` freezes hierarchy, device indexes, mode parameters, light intensity anchors, and light programs into one immutable reference.
4. Every control tick captures that reference once. It never reads the device registry while processing the tick.
5. The empty registry is valid: startup installs a ready empty snapshot, the API and control loop start, and no relay-ON or nonzero DFR command is emitted.

Registry mutations rebuild the complete pending projection in the same transaction, then publish it only after commit. A failed load or mutation retains the previous snapshot.

### Sole responsibility boundaries

| Component | Sole responsibility |
|---|---|
| `device_registry` | Persistent device, relay, and DFR assignments |
| `RuntimeDeviceRegistry` | Build and atomically publish the current snapshot |
| `RuntimeDeviceSnapshot` | Immutable control-tick device projection |
| `DeviceRegistryService` | Only supported mutation path for registry assignments; validates uniqueness and performs safe output actions before mutation |
| `RelayBoardStateManager` | Only owner and sampler of observed MCP23017 board state; persists `cea:relay:board_snapshot` |
| `RelayManager` | Applies relay writes against the captured snapshot and samples after successful writes |
| `Scheduler` | Installs snapshot-derived light and mode caches as one readiness operation |

The `RelayBoardStateManager` samples GPIOA and GPIOB once under the bus lock. On a failed read it retains the last known good sample as stale; it does not invent an energized state.

---

## Scheduling and Control

The control loop uses the immutable snapshot plus live sensor values:

1. Read sensor state from Redis.
2. Capture the current `RuntimeDeviceSnapshot`.
3. Resolve photoperiod from `mode_parameters` and light targets from `light_target_intensity`.
4. Apply supplemental or override light programs from `light_programs`.
5. Evaluate VPD-led climate logic and safety interlocks.
6. Send relay commands through MCP23017 and dimming commands through DFR0971.
7. Persist live state to Redis and history to TimescaleDB.

Photoperiod is room/mode-level and supports overnight windows. Each light’s intensity is keyed by `(device_id, mode_id)`. A missing intensity anchor falls back to a visible 10% failsafe; missing photoperiod configuration is a critical alarm condition.

---

## Operations and Safety

### Registry reset is operator-only

The guarded reset procedure is `Infrastructure/scripts/reset-device-registry.sh`. It is not a service feature and it creates no devices. It deletes only reset-scoped registry-dependent tables after explicit `--confirm`; it must never be run by automated tests or agents.

Required owner sequence after local gates are green:

```bash
./deploy.sh
Infrastructure/scripts/reset-device-registry.sh --confirm
```

Deployment and destructive reset require explicit owner approval. Until then, no production DB, Redis, I2C hardware, or mutation endpoint is used.

### Hardware safety

- Startup forces MCP23017 relays OFF before restoration or control work.
- Shutdown invokes the safe-output helper to command relays OFF and configured DFR channels to 0%.
- MCP23017 is relay-only; DFR0971 is dimming-only. Their I2C buses remain separate.
- The control loop refuses to use partial device projections; invalid registry state fails loudly rather than applying guessed bindings.

### Cluster topology

Device cluster `main` is distinct from Flower Room sensor sub-clusters `front` and `back`. Device API requests use `main`; sensor API requests use the sensor URL slugs. `Infrastructure/shared/cluster_topology.py` and `Infrastructure/frontend/src/config/clusterTopology.ts` are the joint source of truth.

---

## Local Verification

The automation pure tests use only fakes for database, Redis, and I2C. The canonical empty-registry test `app/tests/pure/test_empty_registry_startup.py` exercises the actual service container lifespan and an empty control tick without connecting to production resources.

Before approval for deployment, run:

```bash
cd Infrastructure/automation-service && ruff check . && ruff format --check . && python3 -m compileall -q app && pytest -q app/tests/pure
cd Infrastructure/frontend && npx tsc --noEmit && npm run build && npx vitest run src/components/devices/__tests__/targetValidation.test.ts src/components/devices/__tests__/relaySnapshot.test.ts
python3 Infrastructure/scripts/validate_cluster_topology.py
git diff --check
bash Infrastructure/scripts/tests/test-reset-device-registry.sh
```
