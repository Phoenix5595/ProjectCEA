# CAN Processor Service - Agent Documentation


---

## CAN-PROCESSOR ARCHITECTURE

### Data Flow (Current)

1. CAN message received from ESP32 nodes
2. Decoded by processor
3. Written to Redis live state (INSTANT)
4. Written to Redis stream (INSTANT)
5. Written to TimescaleDB (INSTANT currently)

### Data Flow (Target with 100ms Batching)

1. CAN message received from ESP32 nodes
2. Decoded by processor
3. Written to Redis live state (INSTANT - non-negotiable)
4. Written to Redis stream (INSTANT - for automation-service)
5. Queued for TimescaleDB batch write
6. Batch flushed every 100ms OR 50 messages (whichever first)

CONSTRAINT: 100ms maximum delay for database writes
This is acceptable because:
- Dashboard live values come from Redis (instant)
- Control loop reads from Redis stream (instant)
- Only historical graphs have 100ms delay (imperceptible)

### Why Async Batching

Current risk: If database is slow (vacuum, checkpoint), CAN buffer can overflow
Solution: Async queue absorbs slowdowns, prevents message loss

Queue capacity: 10,000 messages
At 100 msgs/sec: Can survive 100 seconds of DB unavailability

### Node Configuration

Current nodes:
- Node 1: Flower Room Back (_b suffix)
- Node 2: Flower Room Front (_f suffix)  
- Node 3: Veg Room Main (_v suffix)

Future nodes:
- Node 4: Flower Room Secondary cluster
- Node 5: Lab
- Node 6: Water Management

---

*Last updated: 2026-01-13 - Async batching and future nodes*


---

## FUTURE: MQTT INTEGRATION REMINDER

**TRIGGER**: When adding new nodes (Lab, Water Management, additional clusters)

When the time comes, consider MQTT for:

### Why MQTT
- Decoupled publish/subscribe model
- New devices just publish to topics, no code changes elsewhere
- Multiple subscribers can listen to same data
- Works over WiFi/Ethernet (no CAN wiring needed for distant nodes)

### Proposed Topic Structure


### Migration Path
1. Keep CAN bus for existing Flower/Veg clusters (working well)
2. Add Mosquitto MQTT broker on Pi or home server
3. New nodes (Lab, Water) use ESP32 with WiFi + MQTT
4. can-processor publishes to MQTT as bridge
5. Automation-service subscribes to MQTT instead of Redis Stream
6. Gradual migration, not big bang

### Hardware Options for New Nodes
- ESP32-S3 with Ethernet (more reliable than WiFi)
- ESP32-C6 with WiFi 6 (lower power)
- Same sensor cluster design (PT100, SCD30, BME280, VL53x)

---

*MQTT reminder added 2026-01-13 - evaluate when expanding beyond current 2 rooms*
