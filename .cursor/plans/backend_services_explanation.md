# Backend Services Explanation

## Two Separate Services

You have **two separate backend services** that run independently:

### 1. Main Backend (Sensor Backend)
- **Location**: `Infrastructure/backend/`
- **Port**: 8000
- **Purpose**: Sensor data, alarms, statistics
- **What it does**: Reads sensor data, sends alarms, provides API for frontend

### 2. Automation Service (New)
- **Location**: `Infrastructure/automation-service/`
- **Port**: 8001
- **Purpose**: Device control, automation
- **What it does**: Controls relays, runs automation, manages devices

## What Happens When You Launch Main Backend (Port 8000)

### Command
```bash
cd Infrastructure/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Startup Sequence

1. **FastAPI app starts**
   - CORS configured
   - Routes registered (sensors, statistics, config)

2. **Lifespan startup events**:
   - ✅ Starts background task: `broadcast_latest_sensor_data()`
     - Reads sensor data from database
     - Broadcasts via WebSocket every few seconds
   
   - ✅ Starts alarm monitoring task: `monitor_alarms()`
     - Checks sensor values against thresholds
     - Sends email alerts when thresholds exceeded
     - Runs every 30 seconds (configurable)
   
   - ✅ Starts daily recap task: `daily_recap_task()`
     - Sends daily email summary at 8 AM

3. **Service is ready**
   - API endpoints available on port 8000
   - WebSocket available on `/ws/{location}`
   - Background tasks running

### What Main Backend Does

- ✅ Reads sensor data from `can_messages.db`
- ✅ Provides REST API: `/api/sensors/*`, `/api/statistics/*`
- ✅ WebSocket broadcasts for real-time sensor updates
- ✅ Monitors sensors and sends email alarms
- ✅ Sends daily recap emails

### What Main Backend Does NOT Do

- ❌ Does NOT control devices/relays
- ❌ Does NOT run automation
- ❌ Does NOT manage schedules/rules
- ❌ Does NOT interact with MCP23017 hardware

## What Happens When You Launch Automation Service (Port 8001)

### Command
```bash
cd Infrastructure/automation-service
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### Startup Sequence

1. **FastAPI app starts**
   - CORS configured
   - Routes registered (devices, setpoints, schedules, rules)

2. **Lifespan startup events**:
   - ✅ Loads YAML config files
   - ✅ Initializes MCP23017 hardware
   - ✅ Connects to database
   - ✅ Syncs config to database
   - ✅ Restores device states
   - ✅ Starts background control task
     - Runs automation loop every 5 seconds
     - Evaluates rules, schedules, PID control

3. **Service is ready**
   - API endpoints available on port 8001
   - Automation running automatically

### What Automation Service Does

- ✅ Controls MCP23017 relays (devices)
- ✅ Runs automation (rules, schedules, PID)
- ✅ Provides REST API: `/api/devices/*`, `/api/setpoints/*`, etc.
- ✅ Reads sensor data from database (to make control decisions)
- ✅ Writes device state to database

### What Automation Service Does NOT Do

- ❌ Does NOT provide sensor data API (that's main backend)
- ❌ Does NOT send email alarms (that's main backend)
- ❌ Does NOT broadcast sensor data via WebSocket (that's main backend)

## Do You Need Both?

### Option 1: Just Sensor Monitoring (No Automation)

**Launch only main backend:**
```bash
cd Infrastructure/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**What you get:**
- ✅ Sensor data API
- ✅ WebSocket sensor updates
- ✅ Email alarms
- ✅ Statistics
- ❌ No device control
- ❌ No automation

**Use case**: You just want to monitor sensors, no automation needed.

---

### Option 2: Full System (Sensors + Automation)

**Launch both services:**

**Terminal 1:**
```bash
cd Infrastructure/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Terminal 2:**
```bash
cd Infrastructure/automation-service
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

**What you get:**
- ✅ Sensor data API (port 8000)
- ✅ Device control API (port 8001)
- ✅ Email alarms (port 8000)
- ✅ Automation running (port 8001)
- ✅ Full system functionality

**Use case**: You want monitoring AND automation.

---

## How They Work Together

### Shared Resources

1. **Database**: Both services use `can_messages.db`
   - Main backend: Reads sensor data (writes alarms)
   - Automation service: Reads sensor data, writes device state

2. **Sensor Data Flow**:
   ```
   CAN Bus → Database (can_messages table)
              ↓
        ┌─────┴─────┐
        ↓           ↓
   Main Backend  Automation Service
   (monitors)    (controls based on sensors)
   ```

3. **No Direct Communication**:
   - Services don't talk to each other
   - They both read from/write to database
   - Database is the communication layer

### Example Workflow

1. **Sensor reading arrives** → Stored in `can_messages.db`
2. **Main backend** reads it → Broadcasts via WebSocket, checks alarms
3. **Automation service** reads it → Evaluates rules, controls devices
4. **Device state changes** → Stored in `device_states` table
5. **Frontend** can read from both APIs

## Startup Script

You can create a startup script to launch both:

**`start_infrastructure.sh`:**
```bash
#!/bin/bash

# Start main backend (sensors, alarms)
cd Infrastructure/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start automation service
cd ../automation-service
uvicorn app.main:app --host 0.0.0.0 --port 8001 &
AUTOMATION_PID=$!

echo "Main backend started (PID: $BACKEND_PID)"
echo "Automation service started (PID: $AUTOMATION_PID)"
echo "Press CTRL+C to stop both services"

# Wait for interrupt
trap "kill $BACKEND_PID $AUTOMATION_PID; exit" INT
wait
```

## Summary

### Main Backend (Port 8000)
- **Always needed** if you want sensor monitoring
- Provides sensor data API, alarms, WebSocket
- Does NOT control devices

### Automation Service (Port 8001)
- **Only needed** if you want device control/automation
- Controls relays, runs automation
- Reads sensor data to make control decisions

### Answer to Your Question

**Q: What happens when I launch the sensor backend?**
- Main backend starts (port 8000)
- Sensor monitoring, alarms, API work
- NO device control happens

**Q: Do I also need to launch the automation backend?**
- **Yes, if you want automation**
- **No, if you only want sensor monitoring**

They are **independent services** - you can run one or both depending on your needs!


