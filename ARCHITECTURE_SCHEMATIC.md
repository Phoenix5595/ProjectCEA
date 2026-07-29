# ProjectCEA – Architecture Schematic

**Last updated (deployed):** 2026-07-12

**Prepared update:** 2026-07-29 — registry canonicalization is locally verified and deployment is pending explicit owner approval. The prior deployed schematic is retained as `archive/ARCHITECTURE_SCHEMATIC_2026-07-12.md`.

---

## End-to-End Flow

```mermaid
flowchart LR
  Sensors[CAN / Modbus / 1-Wire / Weather] --> Ingest[Ingestion services]
  Ingest --> Redis[Redis live state]
  Ingest --> Timescale[TimescaleDB history]
  Redis --> Automation[automation-service]
  Timescale --> Automation
  Registry[(device_registry)] --> Runtime[RuntimeDeviceRegistry]
  Runtime --> Snapshot[immutable RuntimeDeviceSnapshot]
  Snapshot --> Automation
  Automation --> MCP[MCP23017 relays\nI2C bus 0]
  Automation --> DFR[DFR0971 dimming\nI2C bus 1]
  Redis --> Backend[cea-backend :8000]
  Timescale --> Backend
  Backend --> Frontend[React frontend]
  Automation --> Frontend
  Timescale --> Grafana[Grafana]
```

---

## Canonical Registry Flow

```mermaid
sequenceDiagram
  participant DB as device_registry
  participant RDR as RuntimeDeviceRegistry
  participant Snap as RuntimeDeviceSnapshot
  participant Tick as Control tick
  participant Relay as RelayBoardStateManager
  participant HW as MCP23017 / DFR0971

  DB->>RDR: startup load or committed mutation
  RDR->>RDR: build complete device + light projection
  RDR->>Snap: freeze immutable snapshot
  RDR->>Tick: atomically publish one reference
  Tick->>Tick: capture snapshot once
  Tick->>HW: apply only registry-backed assignments
  Relay->>HW: sample GPIOA + GPIOB once
  Relay->>Relay: persist cea:relay:board_snapshot
```

| Boundary | Contract |
|---|---|
| `device_registry` | Sole device, relay, and DFR source |
| `DeviceRegistryService` | Sole registry mutation path; validates conflicts and safe output sequencing |
| `RuntimeDeviceSnapshot` | Immutable and complete for one control tick |
| `RelayBoardStateManager` | Sole MCP board sampler and snapshot owner |
| `automation_config.yaml` | Service configuration only; no device definitions |
| Commissioning subsystem | Removed; not part of startup or control |

---

## Empty Registry Contract

```mermaid
flowchart TD
  Empty[device_registry has zero rows] --> Load[load_startup builds empty projection]
  Load --> Ready[ready empty RuntimeDeviceSnapshot]
  Ready --> API[API lifespan starts]
  Ready --> Loop[control tick runs with zero devices]
  Loop --> Safe[no relay-ON and no nonzero DFR command]
```

The empty registry is a supported safe state for the operator-controlled reset/rebuild workflow. It is not a cue to recreate YAML devices or run an automatic commissioning process.

---

## Operator-Only Reset Boundary

```mermaid
flowchart LR
  Gates[Local gates pass] --> Approval[Owner explicitly approves]
  Approval --> Deploy[./deploy.sh]
  Deploy --> Reset[reset-device-registry.sh --confirm]
  Reset --> Rebuild[Operator rebuilds registry]
```

`Infrastructure/scripts/reset-device-registry.sh` is intentionally destructive, guarded by `--confirm`, and limited to registry-dependent reset tables. It is never invoked by tests, agents, or the control service. Deployment and reset remain separate owner actions.

---

## Operational Constants

| Item | Value |
|---|---|
| Control tick | 1–5 seconds |
| Sensor update | 1 Hz minimum |
| Redis hot path | under 1 ms |
| DB batch delay | at most 100 ms |
| Relay hardware | MCP23017 only, bus 0 |
| Dimming hardware | DFR0971 only, bus 1 |
| Device cluster | `main` only |
| Flower sensor URL slugs | `front`, `back` |
