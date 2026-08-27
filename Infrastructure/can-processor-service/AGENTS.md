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
| Redis state | `sensor:{name}` (10 s TTL) | Live current values |
| Redis stream | `sensor:raw` | Recent history buffer for automation |
| TimescaleDB | `measurement` | Historical time series |

## Batching

DB writes are queued in `shared.db_batch_writer.BatchQueue` and flushed on whichever comes first:

- 50 queued messages, or
- 100 ms flush interval.

Queue capacity is 10,000 messages. The stream `sensor:raw` caps at 100,000 entries (`shared/redis_keys.py`).

## Anti-patterns

| Never | Reason |
|---|---|
| Skip Redis Stream writes | Recent-data queries and the control loop depend on them |
| Query DB from the CAN processor | Writer is write-only on the hot path |
| Revive legacy cluster labels | Breaks backend sensor-name filtering |

See `Infrastructure/can-processor-service/README.md` for operator setup and `ARCHITECTURE.md` for service boundaries.
