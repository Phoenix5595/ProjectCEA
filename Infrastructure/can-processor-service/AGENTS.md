# CAN Processor Service

Reads CAN bus frames from ESP32 sensor nodes, decodes them once, and writes to Redis live state, the Redis stream, and TimescaleDB.

## Entry and data flow

```
app/main.py
    ├── app/processor.py   # Decode + validate + sensor-name suffixing
    └── app/writer.py      # Redis state, Redis stream, DB batch
```

The processor maps node IDs to canonical topology names via `shared.cluster_topology`:

| Node ID | Location | Cluster |
|---|---|---|
| 1 | Flower Room | back |
| 2 | Flower Room | front |
| 3 | Veg Room | main |

Use `front`, `back`, and `main` in Redis and Postgres. Do not revive legacy `clusterA` / `clusterB` labels.

## Outputs

| Destination | Key / Table | Purpose |
|---|---|---|
| Redis state | `cea:sensor:{location}:{cluster}:{sensor_name}` and its `_ts` companion (no TTL) | Live current values |
| Redis stream | `sensor:raw` | Recent history buffer for automation |
| TimescaleDB | `measurement` | Historical time series |

Only frames of a node with a room position (a cached registry assignment) reach Redis state and `measurement`, under canonical location-suffixed channel names. Frames of an unplaced node, including every node before the metadata worker's first assignment load, enter `sensor:raw` only, marked `assigned=false`; no bare `dry_bulb`/`wet_bulb`/`rh`/`vpd` channel or measurement row is written. Bare names appear only in the console display.

## Batching

DB writes are queued in `shared.db_batch_writer.BatchQueue` and flushed on whichever comes first:

- 50 queued messages, or
- 100 ms flush interval.

Queue capacity is 10,000 messages. The stream `sensor:raw` caps at 100,000 entries (`shared/redis_keys.py`).

The metadata worker refreshes `last_seen` only from received frames. Each
measurement flush resolves registry devices and channels and inserts its batch
in one transaction holding shared per-registry advisory locks; same-identity
merges take the matching exclusive locks.

## Anti-patterns

| Never | Reason |
|---|---|
| Skip Redis Stream writes | Recent-data queries and the control loop depend on them |
| Create CAN devices by address/name in the writer | Resolve each measurement through `sensor_registry.device_id`; the metadata worker creates registry-owned devices with `SENSOR-<sensor_uid>` serials |
| Revive legacy cluster labels | Breaks backend sensor-name filtering |

See `Infrastructure/can-processor-service/README.md` for operator setup and `ARCHITECTURE.md` for service boundaries.
