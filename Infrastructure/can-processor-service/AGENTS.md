# CAN Processor Service - Agent Documentation


---

## CAN-PROCESSOR ARCHITECTURE

### Data Flow

1. CAN message received from ESP32 nodes
2. Decoded by processor
3. Written to Redis live state (`sensor:{name}`, 10s TTL) — instant
4. Written to Redis stream — instant (for automation-service)
5. Queued for TimescaleDB `measurement` table batch write
6. Batch flushed every 100ms OR 50 messages (whichever first)

Constraint: 100ms max DB delay. Dashboard live values come from Redis (instant); only historical graphs have the 100ms delay (imperceptible).

### Why Async Batching

If the database is slow (vacuum, checkpoint), the CAN buffer can overflow. The async queue absorbs slowdowns and prevents message loss. Queue capacity: 10,000 messages. At 100 msgs/sec, that survives 100 seconds of DB unavailability.

### Node Configuration

Current nodes:
- Node 1: Flower Room / back (`_b` suffix)
- Node 2: Flower Room / front (`_f` suffix)
- Node 3: Veg Room / main (`_v` suffix)

The service emits canonical topology names (`back`, `front`, `main`) and derives sensor-name suffixes through `shared.cluster_topology`; do not revive the legacy firmware-era `clusterA` / `clusterB` labels in Redis or Postgres.

### Output Destinations

| Destination | Key / Table | Latency | Purpose |
|-------------|-------------|---------|---------|
| Redis state | `sensor:{name}` | Instant | Live values for frontend (10s TTL) |
| Redis stream | `sensor:raw` | Instant | Recent history buffer for automation-service |
| TimescaleDB | `measurement` | ≤100ms | Historical data, Grafana queries |

---

*Last updated: 2026-07-12*
