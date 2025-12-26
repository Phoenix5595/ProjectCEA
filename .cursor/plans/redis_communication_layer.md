# Using Redis as Communication Layer - Implementation Plan

## Why Start with Redis Today?

### Benefits
1. **Better architecture from the start** - No need to refactor later
2. **Real-time communication** - No polling delay
3. **Industry standard** - Common pattern for microservices
4. **Scalable** - Easy to add more services later
5. **Decoupled** - Services don't know about each other
6. **Simple setup** - Redis is easy to install and configure

### Why Not Database?
- ❌ Polling overhead (5 second delay)
- ❌ Not real-time
- ❌ Tight coupling
- ❌ Harder to scale

## Architecture with Redis

```
CAN Bus → Database (can_messages table)
           ↓
    Main Backend (port 8000)
           ↓
    Redis Pub/Sub
           ↓
    Automation Service (port 8001)
           ↓
    MCP23017 Hardware
```

### Communication Flow

1. **Sensor data arrives** → Stored in database (persistence)
2. **Main backend** → Publishes to Redis channel `sensor:update`
3. **Automation service** → Subscribes to `sensor:update` → Reacts immediately
4. **Automation service** → Controls device → Publishes to `device:update`
5. **Main backend** → Subscribes to `device:update` (optional, for logging)

## Redis Setup

### Installation

**On Raspberry Pi:**
```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

**Verify:**
```bash
redis-cli ping
# Should return: PONG
```

### Configuration

**Default config**: `/etc/redis/redis.conf`

**For local use (default is fine):**
- Bind to localhost: `bind 127.0.0.1`
- No password needed (local network)
- Persistence enabled (default)

**Optional: Set password** (if you want):
```bash
# Edit /etc/redis/redis.conf
requirepass your_password_here
```

## Implementation

### Dependencies

**Both services need:**
```python
# requirements.txt
redis>=5.0.0
hiredis>=2.2.0  # Faster Redis client (optional but recommended)
```

### Main Backend Changes

**Add Redis publisher:**
```python
# Infrastructure/backend/app/redis_client.py
import redis.asyncio as redis
import json
from typing import Dict, Any

class RedisPublisher:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = None
        self.redis_url = redis_url
    
    async def connect(self):
        self.redis = await redis.from_url(self.redis_url)
    
    async def publish_sensor_update(
        self, 
        location: str, 
        cluster: str, 
        sensor_data: Dict[str, Any]
    ):
        """Publish sensor data update to Redis"""
        channel = f"sensor:update:{location}:{cluster}"
        message = {
            "location": location,
            "cluster": cluster,
            "sensor_data": sensor_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.redis.publish(channel, json.dumps(message))
        # Also publish to general channel
        await self.redis.publish("sensor:update", json.dumps(message))
    
    async def close(self):
        if self.redis:
            await self.redis.close()
```

**In background_tasks.py:**
```python
# When sensor data is read, publish to Redis
async def broadcast_latest_sensor_data():
    redis_pub = RedisPublisher()
    await redis_pub.connect()
    
    while True:
        # Read sensor data from database
        sensor_data = await db.get_latest_sensor_data(...)
        
        # Publish to Redis (real-time)
        await redis_pub.publish_sensor_update(
            location, cluster, sensor_data
        )
        
        # Also broadcast via WebSocket (existing)
        await websocket_manager.broadcast(...)
        
        await asyncio.sleep(5)
```

### Automation Service Changes

**Add Redis subscriber:**
```python
# Infrastructure/automation-service/app/redis_client.py
import redis.asyncio as redis
import json
from typing import Callable, Dict, Any

class RedisSubscriber:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = None
        self.pubsub = None
        self.redis_url = redis_url
        self.callbacks = {}
    
    async def connect(self):
        self.redis = await redis.from_url(self.redis_url)
        self.pubsub = self.redis.pubsub()
    
    async def subscribe(self, channel: str, callback: Callable):
        """Subscribe to Redis channel"""
        await self.pubsub.subscribe(channel)
        self.callbacks[channel] = callback
    
    async def listen(self):
        """Listen for messages and call callbacks"""
        async for message in self.pubsub.listen():
            if message['type'] == 'message':
                channel = message['channel'].decode()
                data = json.loads(message['data'])
                
                if channel in self.callbacks:
                    await self.callbacks[channel](data)
    
    async def publish_device_update(
        self,
        location: str,
        cluster: str,
        device: str,
        state: int,
        mode: str
    ):
        """Publish device state update"""
        message = {
            "location": location,
            "cluster": cluster,
            "device": device,
            "state": state,
            "mode": mode,
            "timestamp": datetime.now().isoformat()
        }
        await self.redis.publish("device:update", json.dumps(message))
    
    async def close(self):
        if self.pubsub:
            await self.pubsub.unsubscribe()
        if self.redis:
            await self.redis.close()
```

**In background_tasks.py:**
```python
async def automation_control_loop():
    redis_sub = RedisSubscriber()
    await redis_sub.connect()
    
    # Subscribe to sensor updates
    async def on_sensor_update(data):
        location = data['location']
        cluster = data['cluster']
        sensor_data = data['sensor_data']
        
        # Process immediately (real-time!)
        await control_engine.process_sensor_data(
            location, cluster, sensor_data
        )
    
    await redis_sub.subscribe("sensor:update", on_sensor_update)
    
    # Also subscribe to specific location/cluster if needed
    await redis_sub.subscribe("sensor:update:Flower Room:front", on_sensor_update)
    await redis_sub.subscribe("sensor:update:Flower Room:back", on_sensor_update)
    await redis_sub.subscribe("sensor:update:Veg Room:main", on_sensor_update)
    
    # Listen for messages
    await redis_sub.listen()
```

## Redis Channels

### Channel Naming Convention

```
sensor:update                    # All sensor updates
sensor:update:{location}         # Updates for specific location
sensor:update:{location}:{cluster}  # Updates for specific location/cluster

device:update                    # All device state changes
device:update:{location}         # Device updates for location
device:update:{location}:{cluster}  # Device updates for location/cluster

control:command                  # Control commands (optional)
```

### Message Format

**Sensor Update:**
```json
{
  "location": "Flower Room",
  "cluster": "front",
  "sensor_data": {
    "dry_bulb_f": 24.5,
    "rh_f": 65.0,
    "co2_f": 1200.0
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

**Device Update:**
```json
{
  "location": "Flower Room",
  "cluster": "front",
  "device": "heater_1",
  "state": 1,
  "mode": "auto",
  "timestamp": "2024-01-15T10:30:00"
}
```

## Benefits of Redis Approach

### 1. Real-Time Communication
- **Before**: 5 second polling delay
- **After**: Immediate (< 1ms latency)

### 2. Decoupled Services
- Main backend doesn't know about automation service
- Automation service doesn't know about main backend
- Both just publish/subscribe to Redis

### 3. Scalable
- Easy to add more services (e.g., logging service, analytics service)
- Each service subscribes to what it needs

### 4. Reliable
- Redis queues messages if service is down
- Messages not lost (if using Redis persistence)

### 5. Easy to Debug
```bash
# Monitor Redis messages in real-time
redis-cli MONITOR

# Subscribe to specific channel
redis-cli PSUBSCRIBE "sensor:update:*"
```

## Database Still Used For

1. **Persistence**: Store sensor data (historical)
2. **Device state**: Store current device states (survives restarts)
3. **Control history**: Log all control actions
4. **Setpoints**: Store setpoints
5. **Schedules/Rules**: Store schedules and rules

**Redis is for real-time communication, database is for persistence.**

## Implementation Plan

### Phase 1: Add Redis to Main Backend
1. Install Redis
2. Add `redis_client.py` with publisher
3. Modify `background_tasks.py` to publish sensor updates
4. Test: Verify messages are published

### Phase 2: Add Redis to Automation Service
1. Add `redis_client.py` with subscriber
2. Modify `background_tasks.py` to subscribe and react
3. Test: Verify automation reacts to sensor updates

### Phase 3: Add Device State Publishing
1. Automation service publishes device updates
2. Main backend subscribes (optional, for logging)
3. Test: Verify device updates are published

## Configuration

**Add to both services:**
```yaml
# config.yaml or automation_config.yaml
redis:
  url: "redis://localhost:6379"
  password: null  # Set if you configured password
  db: 0
```

## Error Handling

**Handle Redis connection failures:**
```python
async def connect_with_retry(redis_client, max_retries=5):
    for i in range(max_retries):
        try:
            await redis_client.connect()
            return True
        except Exception as e:
            logger.error(f"Redis connection failed (attempt {i+1}): {e}")
            if i < max_retries - 1:
                await asyncio.sleep(2 ** i)  # Exponential backoff
    return False
```

## Monitoring

**Check Redis status:**
```bash
redis-cli INFO
redis-cli CLIENT LIST
redis-cli PUBSUB CHANNELS
```

## Summary

**Why Redis today:**
- ✅ Better architecture from the start
- ✅ Real-time communication (no polling delay)
- ✅ Industry standard pattern
- ✅ Easy to set up
- ✅ Scalable for future

**What changes:**
- Main backend publishes sensor updates to Redis
- Automation service subscribes and reacts immediately
- Database still used for persistence
- No polling overhead

**Result:**
- Real-time automation (< 1 second response)
- Decoupled services
- Better architecture
- Ready to scale

Let's implement it with Redis from the start!


