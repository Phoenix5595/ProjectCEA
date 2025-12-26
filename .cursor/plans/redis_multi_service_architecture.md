# Redis Multi-Service Architecture

## How Multiple Services Can Subscribe to Redis

### The Pattern

```
Main Backend (Publisher)
        ↓
    Redis Channels
        ↓
    ┌────┴────┬──────────┬──────────┐
    ↓         ↓          ↓          ↓
Automation  Logging   Analytics  Frontend
Service     Service   Service    Service
```

**Key Point**: When main backend publishes to Redis, **ALL subscribed services receive the message** simultaneously.

## Example: Multiple Services Subscribing

### Service 1: Automation Service (Current)
```python
# Subscribes to sensor updates
await redis_sub.subscribe("sensor:update", on_sensor_update)

# When message arrives:
async def on_sensor_update(data):
    # Control devices based on sensor data
    await control_engine.process(data)
```

### Service 2: Logging Service (Future)
```python
# Subscribes to ALL updates
await redis_sub.subscribe("sensor:update", log_sensor_data)
await redis_sub.subscribe("device:update", log_device_state)

# When message arrives:
async def log_sensor_data(data):
    # Write to log file or database
    await logger.write(data)
```

### Service 3: Analytics Service (Future)
```python
# Subscribes to sensor updates
await redis_sub.subscribe("sensor:update", analyze_data)

# When message arrives:
async def analyze_data(data):
    # Calculate trends, predictions
    await analytics.process(data)
```

### Service 4: Frontend WebSocket Service (Future)
```python
# Subscribes to updates
await redis_sub.subscribe("sensor:update", broadcast_to_clients)
await redis_sub.subscribe("device:update", broadcast_to_clients)

# When message arrives:
async def broadcast_to_clients(data):
    # Send to connected WebSocket clients
    await websocket_manager.broadcast(data)
```

## Redis Pub/Sub Behavior

### One Publisher, Many Subscribers

**When main backend publishes:**
```python
await redis.publish("sensor:update", json.dumps(data))
```

**ALL subscribed services receive it:**
- ✅ Automation service gets it
- ✅ Logging service gets it
- ✅ Analytics service gets it
- ✅ Frontend service gets it
- ✅ Any other service subscribed gets it

### Message Delivery

- **Fan-out**: One message → many receivers
- **Real-time**: All services get message immediately
- **Decoupled**: Services don't know about each other
- **Scalable**: Add more services without changing existing ones

## Example Architecture

### Current Services

```
┌─────────────────┐
│  Main Backend   │
│   (Port 8000)   │
│                 │
│  - Reads CAN    │
│  - Publishes to │
│    Redis        │
└────────┬────────┘
         │
         │ Publishes: sensor:update
         │
    ┌────▼────┐
    │  Redis  │
    │ Pub/Sub │
    └────┬────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌─────────┐ ┌──────────────┐
│Automation│ │  (Future)   │
│ Service  │ │  Services   │
│(Port 8001)│ │             │
│          │ │             │
│Subscribes│ │             │
│to sensor │ │             │
│updates   │ │             │
└──────────┘ └──────────────┘
```

### Future Services You Could Add

#### 1. Logging Service
```python
# Infrastructure/logging-service/
# Subscribes to all channels
# Writes to log files, database, or external logging system

async def on_any_update(data):
    await write_to_log_file(data)
    await write_to_database(data)
```

#### 2. Analytics Service
```python
# Infrastructure/analytics-service/
# Subscribes to sensor updates
# Calculates trends, predictions, statistics

async def on_sensor_update(data):
    await calculate_trends(data)
    await generate_predictions(data)
    await update_dashboard(data)
```

#### 3. Alert Service
```python
# Infrastructure/alert-service/
# Subscribes to sensor updates
# Sends alerts via SMS, push notifications, etc.

async def on_sensor_update(data):
    if data['temperature'] > 30:
        await send_sms_alert("Temperature too high!")
```

#### 4. Data Export Service
```python
# Infrastructure/export-service/
# Subscribes to all updates
# Exports to external systems (cloud, API, etc.)

async def on_any_update(data):
    await export_to_cloud(data)
    await send_to_external_api(data)
```

#### 5. Frontend WebSocket Service
```python
# Infrastructure/websocket-service/
# Subscribes to updates
# Broadcasts to connected frontend clients

async def on_sensor_update(data):
    await websocket_manager.broadcast_to_all(data)
```

## Redis Channel Strategy

### Option 1: Broad Channels (Recommended)

**Publish to general channels:**
```python
# Main backend publishes to:
await redis.publish("sensor:update", data)  # All sensor updates
await redis.publish("device:update", data)  # All device updates
```

**Services subscribe to what they need:**
```python
# Automation service
await redis_sub.subscribe("sensor:update", handler)

# Logging service
await redis_sub.subscribe("sensor:update", handler)
await redis_sub.subscribe("device:update", handler)

# Analytics service
await redis_sub.subscribe("sensor:update", handler)
```

**Pros:**
- ✅ Simple
- ✅ All services get all updates
- ✅ Easy to add new services

**Cons:**
- ⚠️ Services receive messages they might not need

### Option 2: Specific Channels

**Publish to specific channels:**
```python
# Main backend publishes to:
await redis.publish("sensor:update:Flower Room:front", data)
await redis.publish("sensor:update:Flower Room:back", data)
await redis.publish("sensor:update:Veg Room:main", data)
```

**Services subscribe to specific channels:**
```python
# Automation service (needs all)
await redis_sub.subscribe("sensor:update:Flower Room:front", handler)
await redis_sub.subscribe("sensor:update:Flower Room:back", handler)
await redis_sub.subscribe("sensor:update:Veg Room:main", handler)

# Analytics service (only Flower Room)
await redis_sub.subscribe("sensor:update:Flower Room:front", handler)
await redis_sub.subscribe("sensor:update:Flower Room:back", handler)
```

**Pros:**
- ✅ Services only get what they need
- ✅ More efficient (less message processing)

**Cons:**
- ⚠️ More complex (need to subscribe to multiple channels)
- ⚠️ Harder to add new locations (need to update subscriptions)

### Option 3: Pattern Matching (Best of Both)

**Use Redis pattern subscriptions:**
```python
# Main backend publishes to specific channels
await redis.publish("sensor:update:Flower Room:front", data)

# Services can subscribe with patterns
await redis_sub.psubscribe("sensor:update:*", handler)  # All sensor updates
await redis_sub.psubscribe("sensor:update:Flower Room:*", handler)  # Only Flower Room
await redis_sub.psubscribe("sensor:update:*:front", handler)  # Only front clusters
```

**Pros:**
- ✅ Flexible (subscribe to what you need)
- ✅ Efficient (pattern matching)
- ✅ Easy to add new locations (pattern handles it)

**Cons:**
- ⚠️ Slightly more complex

## Recommended Approach

**Use both broad and specific channels:**

```python
# Main backend publishes to both:
await redis.publish("sensor:update", data)  # Broad channel
await redis.publish("sensor:update:Flower Room:front", data)  # Specific channel

# Services choose what they need:
# - Simple services: subscribe to broad channel
# - Specific services: subscribe to specific channels or patterns
```

## Example: Adding a New Service

### Step 1: Create Service
```bash
mkdir Infrastructure/analytics-service
cd Infrastructure/analytics-service
# Create FastAPI app
```

### Step 2: Subscribe to Redis
```python
# app/main.py
import redis.asyncio as redis

async def startup():
    redis_client = await redis.from_url("redis://localhost:6379")
    pubsub = redis_client.pubsub()
    
    # Subscribe to sensor updates
    await pubsub.subscribe("sensor:update")
    
    # Listen for messages
    async for message in pubsub.listen():
        if message['type'] == 'message':
            data = json.loads(message['data'])
            await process_sensor_data(data)
```

### Step 3: Start Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8002
```

**That's it!** The new service automatically receives all sensor updates.

## Benefits of This Architecture

### 1. Easy to Add Services
- Just subscribe to Redis channels
- No changes needed to existing services
- No coordination required

### 2. Decoupled
- Services don't know about each other
- Main backend doesn't know who's listening
- Add/remove services without affecting others

### 3. Scalable
- Add as many services as needed
- Each service processes independently
- No performance impact on other services

### 4. Real-Time
- All services get updates immediately
- No polling overhead
- Low latency

## Monitoring

**See who's subscribed:**
```bash
redis-cli PUBSUB CHANNELS
redis-cli PUBSUB NUMSUB sensor:update
redis-cli CLIENT LIST
```

## Summary

**Yes, other services can just read from Redis!**

- ✅ Subscribe to any channel
- ✅ Receive messages in real-time
- ✅ No changes needed to existing services
- ✅ Easy to add new services
- ✅ Decoupled architecture
- ✅ Scalable

**Pattern:**
1. Main backend publishes to Redis
2. Any service subscribes to channels it needs
3. All subscribed services receive messages simultaneously
4. Each service processes independently

This is the power of Redis pub/sub - one publisher, many subscribers, all decoupled!


