# Backend Implementation Options & API Design Explanation

## Backend Architecture Options

### Option 1: Integrated into Existing FastAPI Backend (Recommended)

**What it means:**
- Add automation features directly to `Infrastructure/backend/app/`
- Share the same database, WebSocket connections, and server process
- All features accessible through the same API base URL (`http://localhost:8000/api/...`)

**Pros:**
- ✅ Single codebase to maintain
- ✅ Shared database connection and configuration
- ✅ Unified WebSocket for sensor data AND device state
- ✅ Easier deployment (one service)
- ✅ Consistent error handling and logging
- ✅ Can reuse existing `DatabaseManager` for sensor reads

**Cons:**
- ❌ Larger codebase (but still manageable)
- ❌ If automation crashes, could affect sensor API (but we can isolate with error handling)

**When to use:** This is what I recommended because you already have a working FastAPI backend and want everything integrated.

---

### Option 2: Separate Microservice

**What it means:**
- Create a new service (e.g., `Infrastructure/automation-service/`)
- Runs on different port (e.g., 8001)
- Communicates with main backend via HTTP API calls
- Has its own database or shares via network

**Pros:**
- ✅ Complete isolation (automation issues don't affect sensor API)
- ✅ Can scale independently
- ✅ Easier to test in isolation
- ✅ Can be written in different language if needed

**Cons:**
- ❌ More complex deployment (two services to manage)
- ❌ Need API calls between services (latency, error handling)
- ❌ Duplicate configuration management
- ❌ Two WebSocket endpoints for frontend to connect to
- ❌ More infrastructure complexity

**When to use:** If you want strict separation, plan to scale automation separately, or have different teams maintaining each service.

---

### Option 3: Integrate Test Scripts Code

**What it means:**
- Take the existing `Test Scripts/climate_control/` code
- Refactor it to work with FastAPI instead of Tkinter GUI
- Keep the same structure but adapt to async/API model

**Pros:**
- ✅ Reuse existing working code
- ✅ Less new code to write
- ✅ Already tested logic

**Cons:**
- ❌ Test Scripts code is synchronous (needs async conversion)
- ❌ Designed for GUI, needs API adaptation
- ❌ May have dependencies not suitable for production backend

**When to use:** If you want to minimize new code and the Test Scripts code is production-ready.

---

## API Endpoint Design Explanation

I suggested these endpoints. Here's what each does and why:

### Device Control Endpoints

#### `GET /api/devices`
**Purpose:** List all devices across all locations with their current state

**Returns:**
```json
{
  "Flower Room": {
    "front": {
      "heater_1": {"state": 1, "mode": "auto", "channel": 0},
      "exhaust_fan": {"state": 0, "mode": "manual", "channel": 1}
    },
    "back": { ... }
  },
  "Veg Room": { ... }
}
```

**When you'd use it:** Frontend dashboard wants to show all devices at once, or you want a complete system overview.

---

#### `GET /api/devices/{location}/{cluster}`
**Purpose:** Get devices for a specific location/cluster

**Returns:**
```json
{
  "heater_1": {"state": 1, "mode": "auto", "channel": 0},
  "exhaust_fan": {"state": 0, "mode": "manual", "channel": 1}
}
```

**When you'd use it:** Frontend shows one room at a time, or you're building a room-specific control panel.

---

#### `GET /api/devices/{location}/{cluster}/{device}`
**Purpose:** Get detailed status of a single device

**Returns:**
```json
{
  "device": "heater_1",
  "location": "Flower Room",
  "cluster": "front",
  "state": 1,
  "mode": "auto",
  "channel": 0,
  "last_updated": "2024-01-15T10:30:00",
  "current_sensor_value": 24.5,
  "setpoint": 25.0,
  "pid_output": 75.0
}
```

**When you'd use it:** Detailed device view, debugging, or showing device-specific information.

---

#### `POST /api/devices/{location}/{cluster}/{device}/control`
**Purpose:** Manually turn a device ON or OFF

**Request:**
```json
{
  "state": 1,  // 0 = OFF, 1 = ON
  "reason": "Manual override"  // optional
}
```

**When you'd use it:** User clicks "Turn Heater On" button in UI, or you want to manually control a device.

---

#### `POST /api/devices/{location}/{cluster}/{device}/mode`
**Purpose:** Change control mode (manual/auto/scheduled)

**Request:**
```json
{
  "mode": "manual"  // or "auto" or "scheduled"
}
```

**When you'd use it:** User wants to switch a device from automatic to manual control, or vice versa.

---

### Setpoint Management Endpoints

#### `GET /api/setpoints`
**Purpose:** Get all setpoints for all locations

**Returns:**
```json
{
  "Flower Room": {
    "front": {
      "temperature": 25.0,
      "humidity": 65.0,
      "co2": 1200.0
    }
  }
}
```

**When you'd use it:** Frontend needs to display or edit setpoints for the entire system.

---

#### `GET /api/setpoints/{location}/{cluster}`
**Purpose:** Get setpoints for a specific location/cluster

**Returns:**
```json
{
  "temperature": 25.0,
  "humidity": 65.0,
  "co2": 1200.0
}
```

**When you'd use it:** User is viewing/editing setpoints for one room.

---

#### `POST /api/setpoints/{location}/{cluster}`
**Purpose:** Update setpoints for a location/cluster

**Request:**
```json
{
  "temperature": 26.0,
  "humidity": 70.0,
  "co2": 1300.0
}
```

**When you'd use it:** User changes temperature setpoint in UI, or you want to programmatically adjust setpoints.

---

### Schedule Management Endpoints

#### `GET /api/schedules`
**Purpose:** List all schedules

**Returns:**
```json
[
  {
    "id": 1,
    "name": "Lights On",
    "location": "Flower Room",
    "cluster": "front",
    "device_name": "light_1",
    "day_of_week": null,  // null = daily
    "start_time": "06:00",
    "end_time": "18:00",
    "enabled": true
  }
]
```

**When you'd use it:** Frontend shows schedule list, or you want to see all active schedules.

---

#### `POST /api/schedules`
**Purpose:** Create a new schedule

**Request:**
```json
{
  "name": "Lights On",
  "location": "Flower Room",
  "cluster": "front",
  "device_name": "light_1",
  "day_of_week": 0,  // 0 = Monday, null = daily
  "start_time": "06:00",
  "end_time": "18:00"
}
```

**When you'd use it:** User creates a new schedule (e.g., "Turn lights on at 6 AM daily").

---

#### `PUT /api/schedules/{id}`
**Purpose:** Update an existing schedule

**When you'd use it:** User edits a schedule (changes time, device, etc.).

---

#### `DELETE /api/schedules/{id}`
**Purpose:** Delete a schedule

**When you'd use it:** User removes a schedule they no longer need.

---

#### `POST /api/schedules/{id}/enable`
**Purpose:** Enable or disable a schedule without deleting it

**Request:**
```json
{
  "enabled": false
}
```

**When you'd use it:** User temporarily disables a schedule (e.g., "Turn off lights schedule for maintenance").

---

### Rules Engine Endpoints

#### `GET /api/rules`
**Purpose:** List all automation rules

**Returns:**
```json
[
  {
    "id": 1,
    "name": "Turn on heater if cold",
    "enabled": true,
    "location": "Flower Room",
    "cluster": "front",
    "condition_sensor": "dry_bulb_f",
    "condition_operator": "<",
    "condition_value": 20.0,
    "action_device": "heater_1",
    "action_state": 1
  }
]
```

**When you'd use it:** Frontend shows all rules, or you want to see what automation rules are active.

---

#### `POST /api/rules`
**Purpose:** Create a new rule

**Request:**
```json
{
  "name": "Turn on heater if cold",
  "location": "Flower Room",
  "cluster": "front",
  "condition_sensor": "dry_bulb_f",
  "condition_operator": "<",
  "condition_value": 20.0,
  "action_device": "heater_1",
  "action_state": 1
}
```

**When you'd use it:** User creates a rule like "If temperature < 20°C, turn heater on".

---

#### `PUT /api/rules/{id}` and `DELETE /api/rules/{id}`
**Purpose:** Update or delete rules

**When you'd use it:** User modifies or removes automation rules.

---

### Control History Endpoint

#### `GET /api/control/history`
**Purpose:** Get log of all device state changes

**Query Parameters:**
- `location` (optional): Filter by location
- `cluster` (optional): Filter by cluster
- `device` (optional): Filter by device
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `limit` (optional): Max results (default 100)

**Returns:**
```json
[
  {
    "timestamp": "2024-01-15T10:30:00",
    "location": "Flower Room",
    "cluster": "front",
    "device_name": "heater_1",
    "old_state": 0,
    "new_state": 1,
    "mode": "auto",
    "reason": "pid_control",
    "sensor_value": 24.5,
    "setpoint": 25.0
  }
]
```

**When you'd use it:** Debugging ("Why did the heater turn on?"), audit logs, or showing device activity history in UI.

---

### WebSocket Updates

**Purpose:** Real-time updates when device states change

**Message Format:**
```json
{
  "type": "device_update",
  "location": "Flower Room",
  "cluster": "front",
  "device": "heater_1",
  "state": 1,
  "mode": "auto",
  "timestamp": "2024-01-15T10:30:00"
}
```

**When you'd use it:** Frontend automatically updates device status without polling, real-time dashboard updates.

---

## Alternative API Designs

### Alternative 1: Simpler RESTful Design

Instead of separate endpoints for each resource, use RESTful conventions:

- `GET /api/devices` - List all
- `GET /api/devices/{location}/{cluster}/{device}` - Get one
- `PUT /api/devices/{location}/{cluster}/{device}` - Update (control + mode in one call)
- `GET /api/setpoints/{location}/{cluster}` - Get
- `PUT /api/setpoints/{location}/{cluster}` - Update
- `GET /api/schedules` - List
- `POST /api/schedules` - Create
- `GET /api/schedules/{id}` - Get one
- `PUT /api/schedules/{id}` - Update
- `DELETE /api/schedules/{id}` - Delete

**Pros:** More standard REST, fewer endpoints
**Cons:** Less granular control (can't set mode without setting state)

---

### Alternative 2: GraphQL API

Single endpoint `/api/graphql` where you query exactly what you need:

```graphql
query {
  devices(location: "Flower Room", cluster: "front") {
    name
    state
    mode
    setpoint {
      temperature
      humidity
    }
  }
}
```

**Pros:** Frontend gets exactly what it needs, flexible queries
**Cons:** More complex to implement, learning curve, overkill for this use case

---

### Alternative 3: Minimal API (Just Control)

Only essential endpoints:
- `GET /api/devices/status` - Get all device states
- `POST /api/devices/control` - Control a device
- `GET /api/setpoints` - Get setpoints
- `POST /api/setpoints` - Update setpoints

Everything else (schedules, rules) configured via YAML file only.

**Pros:** Very simple, less code
**Cons:** Can't manage schedules/rules via API, need to edit files and restart

---

## My Recommendation

**Backend:** Option 1 (Integrated into existing FastAPI backend)
- You already have the infrastructure
- Easier to maintain
- Better integration with existing sensor data

**API Design:** The full set I suggested, but you can start minimal and add endpoints as needed:
- **Phase 1 (Essential):** Device control, setpoints, device status
- **Phase 2 (Automation):** Schedules, rules
- **Phase 3 (Advanced):** Control history, detailed device info

This lets you get basic control working first, then add automation features incrementally.

---

## Questions to Help You Decide

1. **Do you need to manage schedules/rules via API, or is YAML config enough?**
   - If YAML is enough, you can skip schedule/rule CRUD endpoints initially

2. **Do you need detailed device info (PID output, sensor values) in API responses?**
   - Or is just state/mode enough?

3. **How complex will your frontend be?**
   - Simple dashboard → minimal API
   - Full-featured UI → full API set

4. **Do you need control history for debugging/audit?**
   - Or is logging to file enough?

Let me know your preferences and I can adjust the implementation plan accordingly!


