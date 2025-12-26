# Python Automation Microservice - Config-Based Implementation

## Overview

Create a separate FastAPI microservice that runs automation based on YAML configuration files. No frontend initially - all configuration is done via YAML files. API endpoints are minimal (status/health checks) for now.

## Architecture

```
Infrastructure/
└── automation-service/         # Automation microservice (port 8001)
    ├── app/
    │   ├── __init__.py
    │   ├── main.py             # FastAPI app (minimal API, mainly status)
    │   ├── config.py            # Config loader for automation_config.yaml
    │   ├── database.py          # Database manager (shared can_messages.db)
    │   ├── hardware/
    │   │   ├── __init__.py
    │   │   └── mcp23017.py      # MCP23017 driver
    │   ├── control/
    │   │   ├── __init__.py
    │   │   ├── relay_manager.py # Device management
    │   │   ├── pid_controller.py # PID control
    │   │   ├── control_engine.py # Automatic control logic
    │   │   └── scheduler.py     # Time-based scheduling
    │   ├── automation/
    │   │   ├── __init__.py
    │   │   ├── rules_engine.py  # Rules engine
    │   │   └── interlock_manager.py # Safety interlocks
    │   ├── routes/
    │   │   ├── __init__.py
    │   │   └── status.py        # Status/health endpoints (minimal)
    │   └── background_tasks.py # Automatic control loop
    ├── automation_config.yaml   # Main device configuration
    ├── schedules.yaml           # Schedule definitions (optional, can be in main config)
    ├── rules.yaml               # Rules definitions (optional, can be in main config)
    ├── requirements.txt
    └── README.md
```

## Configuration Files

### 1. `automation_config.yaml` (Main Config)

**Purpose**: Device-to-channel mapping, control parameters, setpoints

**Structure**:
```yaml
hardware:
  i2c_bus: 1
  i2c_address: 0x20
  simulation: false

devices:
  "Flower Room":
    front:
      heater_1:
        channel: 0
        device_type: "heater"
        pid_enabled: true
        interlock_with: ["exhaust_fan"]
      # ... more devices

control:
  pid:
    heater_kp: 25.0
    heater_ki: 0.02
    heater_kd: 0.0
  
  safety_limits:
    max_temperature: 35.0
    min_temperature: 10.0
    # ...
  
  default_setpoints:
    "Flower Room":
      front:
        temperature: 25.0
        humidity: 65.0
        co2: 1200.0
      # ...

  update_interval: 5
```

### 2. `schedules.yaml` (Optional - can be in main config)

**Purpose**: Time-based device control schedules

**Structure**:
```yaml
schedules:
  - name: "Lights On - Flower Room Front"
    location: "Flower Room"
    cluster: "front"
    device_name: "light_1"
    day_of_week: null  # null = daily, 0-6 = Monday-Sunday
    start_time: "06:00"
    end_time: "18:00"
    enabled: true
  
  - name: "Lights On - Flower Room Back"
    location: "Flower Room"
    cluster: "back"
    device_name: "light_1"
    day_of_week: null
    start_time: "06:00"
    end_time: "18:00"
    enabled: true
  
  # More schedules...
```

### 3. `rules.yaml` (Optional - can be in main config)

**Purpose**: Simple automation rules (if sensor > threshold then device ON/OFF)

**Structure**:
```yaml
rules:
  - name: "Turn on heater if cold - Flower Room Front"
    enabled: true
    location: "Flower Room"
    cluster: "front"
    condition:
      sensor: "dry_bulb_f"
      operator: "<"  # <, >, <=, >=, ==
      value: 20.0
    action:
      device: "heater_1"
      state: 1  # 1 = ON, 0 = OFF
    priority: 0  # Higher priority rules execute first
  
  - name: "Turn off heater if too hot - Flower Room Front"
    enabled: true
    location: "Flower Room"
    cluster: "front"
    condition:
      sensor: "dry_bulb_f"
      operator: ">"
      value: 28.0
    action:
      device: "heater_1"
      state: 0
    priority: 0
  
  # More rules...
```

## Implementation Approach

### Phase 1: Core Functionality (Config-Based)

1. **Hardware Driver** (`app/hardware/mcp23017.py`)
   - MCP23017 I2C driver
   - Support simulation mode
   - Channel control (set/get)

2. **Config Loader** (`app/config.py`)
   - Load `automation_config.yaml`
   - Load `schedules.yaml` (if exists, or from main config)
   - Load `rules.yaml` (if exists, or from main config)
   - Hot-reload support (optional - reload on file change)

3. **Database Manager** (`app/database.py`)
   - Read sensor data from `can_messages.db`
   - Write device state to `can_messages.db` (device_states table)
   - Log control actions (control_history table)
   - Store setpoints (setpoints table)

4. **Relay Manager** (`app/control/relay_manager.py`)
   - Device-to-channel mapping from config
   - State management
   - Interlock enforcement
   - Safety limit checking

5. **PID Controller** (`app/control/pid_controller.py`)
   - PID implementation
   - Per-device configuration from config

6. **Control Engine** (`app/control/control_engine.py`)
   - Automatic control loop
   - Reads sensor data
   - Applies PID control
   - Applies threshold control
   - Respects manual overrides (stored in database)

7. **Scheduler** (`app/control/scheduler.py`)
   - Evaluates schedules from config
   - Applies scheduled states

8. **Rules Engine** (`app/automation/rules_engine.py`)
   - Evaluates rules from config
   - Simple if-then logic

9. **Background Task** (`app/background_tasks.py`)
   - Runs control loop every N seconds (from config)
   - Executes: rules → schedules → PID control → threshold control

10. **Minimal API** (`app/routes/status.py`)
    - `GET /health` - Health check
    - `GET /api/status` - Current device states
    - `GET /api/devices` - List all devices with current state
    - `GET /api/config/reload` - Reload config files (optional)

### Phase 2: API Endpoints (Later - when you build frontend)

Can add later:
- Device control endpoints
- Setpoint management endpoints
- Schedule CRUD endpoints
- Rules CRUD endpoints

## Configuration Management

### Config File Locations

1. **Primary**: `Infrastructure/automation-service/automation_config.yaml`
2. **Schedules** (optional): `Infrastructure/automation-service/schedules.yaml` OR in main config
3. **Rules** (optional): `Infrastructure/automation-service/rules.yaml` OR in main config

### Config Reload Strategy

**Option 1: Manual Reload**
- Service reads config on startup
- To change config: edit file, call `GET /api/config/reload` endpoint
- Or restart service

**Option 2: Auto-Reload** (Optional)
- Watch config files for changes
- Automatically reload when files change
- More complex, but convenient

**Recommendation**: Start with Option 1 (manual reload), add Option 2 later if needed.

## Database Schema

Same as before - add tables to `can_messages.db`:
- `device_states` - Current device states
- `control_history` - Log of all control actions
- `setpoints` - Current setpoints (can be overridden via config or API later)

## Workflow

1. **User edits YAML files**:
   - `automation_config.yaml` - Device mappings, setpoints, PID params
   - `schedules.yaml` - Time-based schedules
   - `rules.yaml` - Automation rules

2. **Service reloads config** (manual or auto)

3. **Background task runs**:
   - Every 5 seconds (configurable)
   - Reads latest sensor values
   - Evaluates rules
   - Checks schedules
   - Applies PID/threshold control
   - Updates device states

4. **All actions logged** to `control_history` table

## API Endpoints (Minimal for Now)

```python
# app/routes/status.py

GET /health
  → {"status": "ok", "timestamp": "..."}

GET /api/status
  → {
      "devices": {
        "Flower Room": {
          "front": {
            "heater_1": {"state": 1, "mode": "auto", "channel": 0}
          }
        }
      },
      "sensors": {
        "Flower Room": {
          "front": {
            "temperature": 24.5,
            "humidity": 65.0,
            "co2": 1200.0
          }
        }
      }
    }

GET /api/devices
  → List all devices with current state

GET /api/config/reload  # Optional
  → Reload config files
```

## Benefits of Config-Based Approach

1. **Simple**: No frontend needed initially
2. **Version Control**: Config files can be git-tracked
3. **Easy Backup**: Just backup YAML files
4. **No Database for Config**: Config is in files, state is in database
5. **Easy to Understand**: YAML is human-readable
6. **Can Add API Later**: When you build frontend, add CRUD endpoints

## File Structure

```
Infrastructure/automation-service/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── hardware/mcp23017.py
│   ├── control/
│   │   ├── relay_manager.py
│   │   ├── pid_controller.py
│   │   ├── control_engine.py
│   │   └── scheduler.py
│   ├── automation/
│   │   ├── rules_engine.py
│   │   └── interlock_manager.py
│   ├── routes/status.py
│   └── background_tasks.py
├── automation_config.yaml      # Main config (YOU FILL THIS IN)
├── schedules.yaml               # Optional schedules
├── rules.yaml                   # Optional rules
├── requirements.txt
└── README.md
```

## Next Steps

1. You fill in `automation_config.yaml` with your device mappings
2. I implement the service based on that config
3. You can add `schedules.yaml` and `rules.yaml` later
4. When ready for frontend, we add API endpoints for config management

This approach lets you get automation working quickly with just config files, then add a frontend later when needed!


