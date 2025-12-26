# Automation Service Startup Sequence

## What Happens When You Launch the Service

### Command
```bash
cd Infrastructure/automation-service
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### Startup Sequence

#### 1. **FastAPI Application Initializes**
- FastAPI app starts
- CORS middleware configured
- Routes registered
- Logging configured

**What you see:**
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
```

#### 2. **Lifespan Startup (FastAPI lifespan events)**

**2a. Load Configuration Files**
- Reads `automation_config.yaml`
- Reads `schedules.yaml` (if exists)
- Reads `rules.yaml` (if exists)
- Validates configuration

**What you see:**
```
INFO:     Loading configuration from automation_config.yaml
INFO:     Found 2 locations: Flower Room, Veg Room
INFO:     Found 12 devices configured
INFO:     Loading schedules from schedules.yaml (optional)
INFO:     Loading rules from rules.yaml (optional)
```

**2b. Initialize Hardware (MCP23017)**
- Connects to I2C bus
- Initializes MCP23017 chip
- Sets all channels to outputs
- Turns all relays OFF initially

**What you see:**
```
INFO:     Initializing MCP23017 on I2C bus 1, address 0x20
INFO:     MCP23017 initialized successfully
INFO:     All channels set to outputs
INFO:     All relays turned OFF (initial state)
```

**OR if simulation mode:**
```
WARNING:  Running in simulation mode (hardware not connected)
INFO:     MCP23017 simulation mode active
```

**2c. Initialize Database Connection**
- Connects to `can_messages.db` (shared with main backend)
- Creates automation tables if they don't exist:
  - `device_states`
  - `control_history`
  - `setpoints`
  - `schedules`
  - `rules`

**What you see:**
```
INFO:     Connecting to database: /home/antoine/Project CEA/Database/CAN_Bus/can_messages.db
INFO:     Creating automation tables if needed
INFO:     Database connection established
```

**2d. Sync Configuration to Database**
- Checks if database has existing data
- If empty or on first run: syncs YAML config → database
- Devices → `device_states` table
- Setpoints → `setpoints` table
- Schedules → `schedules` table
- Rules → `rules` table

**What you see:**
```
INFO:     Syncing configuration to database...
INFO:     Synced 12 devices to device_states table
INFO:     Synced setpoints for 3 location/cluster pairs
INFO:     Synced 4 schedules to schedules table
INFO:     Synced 6 rules to rules table
INFO:     Configuration sync complete
```

**2e. Restore Device States from Database**
- Reads last known device states from database
- Restores relay states to match database
- This ensures devices return to last state after restart

**What you see:**
```
INFO:     Restoring device states from database...
INFO:     Restored heater_1 (Flower Room/front) to OFF
INFO:     Restored light_1 (Flower Room/front) to ON
INFO:     Restored 12 device states
```

**2f. Initialize Control Components**
- Relay Manager initialized
- PID Controllers initialized (for heaters, CO2)
- Control Engine initialized
- Scheduler initialized
- Rules Engine initialized

**What you see:**
```
INFO:     Initializing relay manager...
INFO:     Initializing PID controllers for 2 devices
INFO:     Initializing control engine...
INFO:     Initializing scheduler...
INFO:     Initializing rules engine...
INFO:     All control components initialized
```

**2g. Start Background Task**
- Starts automatic control loop
- Runs every 5 seconds (configurable)
- Executes: rules → schedules → PID control → threshold control

**What you see:**
```
INFO:     Starting background control task...
INFO:     Control loop running every 5 seconds
INFO:     Background task started
```

#### 3. **Application Startup Complete**

**What you see:**
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

#### 4. **Service is Now Running**

**What's happening:**
- ✅ API is accepting requests on port 8001
- ✅ Background task is running automation loop
- ✅ Devices are controlled based on:
  - Rules (if sensor < threshold, turn device ON)
  - Schedules (time-based control)
  - PID control (for heaters, CO2)
  - Threshold control (for other devices)

**What you can do:**
- Check status: `GET http://localhost:8001/api/status`
- List devices: `GET http://localhost:8001/api/devices`
- Manual control: `POST http://localhost:8001/api/devices/Flower Room/front/heater_1/control`
- View history: `GET http://localhost:8001/api/control/history`

#### 5. **Background Task Activity (Every 5 seconds)**

**What happens:**
1. Read latest sensor values from database
2. Evaluate rules engine
3. Check schedules
4. Apply PID control (heaters, CO2)
5. Apply threshold control (other devices)
6. Update device states
7. Log control actions

**What you see (in logs, if verbose):**
```
DEBUG:    Control loop iteration
DEBUG:    Reading sensor values for Flower Room/front
DEBUG:    Temperature: 24.5°C, Humidity: 65.0%, CO2: 1200 ppm
DEBUG:    Evaluating 6 rules...
DEBUG:    Rule "Turn on heater if cold" evaluated: False (temp 24.5 > 20.0)
DEBUG:    Checking schedules...
DEBUG:    Schedule "Lights On" active: True (06:00 - 18:00)
DEBUG:    Applying PID control for heater_1...
DEBUG:    PID output: 45% (setpoint: 25.0, current: 24.5)
DEBUG:    Heater state: ON (PID > 50%)
DEBUG:    Control loop complete
```

## Example Full Startup Log

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Loading configuration from automation_config.yaml
INFO:     Found 2 locations: Flower Room, Veg Room
INFO:     Found 12 devices configured
INFO:     Initializing MCP23017 on I2C bus 1, address 0x20
INFO:     MCP23017 initialized successfully
INFO:     Connecting to database: /home/antoine/Project CEA/Database/CAN_Bus/can_messages.db
INFO:     Creating automation tables if needed
INFO:     Syncing configuration to database...
INFO:     Synced 12 devices to device_states table
INFO:     Synced setpoints for 3 location/cluster pairs
INFO:     Restoring device states from database...
INFO:     Restored 12 device states
INFO:     Initializing control components...
INFO:     Starting background control task...
INFO:     Control loop running every 5 seconds
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

## What If Something Goes Wrong?

### Hardware Not Connected
```
WARNING:  Failed to initialize MCP23017 hardware: [Errno 2] No such file or directory
WARNING:  Falling back to simulation mode
INFO:     MCP23017 simulation mode active
```
→ Service continues in simulation mode (safe for testing)

### Config File Missing
```
ERROR:    Config file not found: automation_config.yaml
```
→ Service fails to start (needs config file)

### Database Connection Failed
```
ERROR:    Failed to connect to database: [Errno 2] No such file or directory
```
→ Service fails to start (needs database)

### Invalid Config
```
ERROR:    Invalid configuration: Device 'heater_1' missing channel number
```
→ Service fails to start (fix config file)

## Shutdown Sequence

When you press CTRL+C:

```
INFO:     Shutting down
INFO:     Stopping background control task...
INFO:     Saving device states to database...
INFO:     Closing MCP23017 connection...
INFO:     Application shutdown complete.
INFO:     Finished server process [12345]
```

## Health Check

You can check if service is running:
```bash
curl http://localhost:8001/health
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00",
  "background_task": "running",
  "hardware": "connected"  // or "simulation"
}
```

## Summary

When you launch the service:
1. ✅ Loads your YAML config
2. ✅ Connects to hardware (or simulation mode)
3. ✅ Syncs config to database
4. ✅ Restores device states
5. ✅ Starts automation loop
6. ✅ API is ready for requests
7. ✅ Automation is running automatically

The service is **fully automated** - it will control devices based on your config without any manual intervention!


