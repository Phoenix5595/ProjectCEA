# Shared Code

This directory holds cross-service code. It has no runtime service of its own; every change here affects every consumer.

## Topology Mirror Contract

[`cluster_topology.py`](cluster_topology.py) is the Python source of truth. [`Infrastructure/frontend/src/config/clusterTopology.ts`](../frontend/src/config/clusterTopology.ts) is the frontend mirror. They must be changed together.

- Device cluster is always `main`.
- Sensor sub-clusters `front` and `back` exist only for Flower Room.
- For unsplit rooms (Veg, Lab, Outside) the sensor URL reuses `main` as a sentinel; the rooms have no physical sub-clusters.
- `assert_device_cluster()` and `assert_sensor_cluster()` return HTTP 400 hints on cross-type misuse.

## Redis Key Builders

All new Redis keys must use builders from [`redis_keys.py`](redis_keys.py). Do not introduce new top-level namespaces without updating [`Infrastructure/REQUIREMENTS.md`](../REQUIREMENTS.md).

| Concept | API |
|---|---|
| Telemetry stream | `SENSOR_RAW_STREAM` (`sensor:raw`), maxlen 100,000 |
| Current sensor values | `sensor_full()`, `sensor_full_ts()` (topology-qualified key and epoch-ms timestamp) |
| Soil RS-485 bus state | `SOIL_BUS_STATUS_KEY` (`cea:soil:bus_status`), JSON `{state, address, at}`, TTL `SOIL_BUS_STATUS_TTL_SEC` (300 s) |

## Relay Bijection

[`relay_topology.py`](relay_topology.py) owns the canonical mapping between MCP23017 channel numbers and operator-facing physical relay numbers. Frontend must not derive physical relay labels from `channel + 1`.

## DB, Logging and Middleware Shared Boundaries

- [`db_batch_writer.py`](db_batch_writer.py): `BatchQueue` for sync high-rate producers (CAN), `insert_measurements_async()` for asyncpg consumers (soil, weather).
- [`base_repository.py`](base_repository.py): connection pool, 30 s query cache, `clear_cache()`.
- [`infra_logging.py`](infra_logging.py): structured logging with secret redaction and `LoggingContext`.
- [`middleware.py`](middleware.py): CORS setup driven by `FRONTEND_ORIGINS`; never sets `allow_origins=["*"]` with `allow_credentials=True`.

## Blast-Radius Guidance

Changing any file here requires checking every consumer. Use `git grep` before editing (plain grep misses tracked tests hidden by `.gitignore`). Run `python3 Infrastructure/scripts/validate_cluster_topology.py` after topology changes.

---

*Last updated: 2026-08-10*
