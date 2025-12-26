---
name: Automation Backend Implementation
overview: Implement a full-featured automation backend service that controls MCP23017 relays, runs PID control, schedules, and rules-based automation. The service will use TimescaleDB for device state storage, read sensor data from the shared database, and provide a complete REST API ready for frontend integration.
todos:
  - id: create_structure
    content: Create directory structure and base files (app/, __init__.py files, requirements.txt, README.md)
    status: pending
  - id: database_schema
    content: Create database.py with TimescaleDB connection and table creation (device_states, control_history, setpoints, schedules, rules, automation_state hypertable)
    status: pending
  - id: hardware_driver
    content: Implement MCP23017 driver (app/hardware/mcp23017.py) with simulation mode support
    status: pending
  - id: config_loader
    content: Implement config loader (app/config.py) for YAML file parsing and database sync
    status: pending
  - id: relay_manager
    content: Implement relay manager (app/control/relay_manager.py) with device mapping, state management, and interlock enforcement
    status: pending
  - id: pid_controller
    content: Implement PID controller (app/control/pid_controller.py) for temperature/CO2 control
    status: pending
  - id: scheduler
    content: Implement scheduler (app/control/scheduler.py) for time-based device control
    status: pending
  - id: rules_engine
    content: Implement rules engine (app/automation/rules_engine.py) for if-then automation rules
    status: pending
  - id: control_engine
    content: Implement control engine (app/control/control_engine.py) that orchestrates rules, schedules, and PID control
    status: pending
  - id: background_tasks
    content: Implement background task (app/background_tasks.py) that runs the control loop every 1 second (configurable, default 1s)
    status: pending
  - id: api_routes
    content: Implement REST API routes (devices, setpoints, schedules, rules, status) in app/routes/
    status: pending
  - id: main_app
    content: Implement FastAPI main app (app/main.py) with startup sequence, route registration, and background task initialization
    status: pending
  - id: config_file
    content: Create automation_config.yaml from template with simulation mode enabled
    status: pending
  - id: documentation
    content: Create README.md with setup instructions, API documentation, and usage examples
    status: pending
---

# Automation Backend Service Implementation

## Overview

Create a FastAPI microservice at `Infrastructure/automation-service/` that provides device control, automation rules, scheduling, and PID control for climate management. The service runs on port 8001, uses TimescaleDB for state storage, and reads sensor data from the shared `can_messages` table.

## Architecture

```
Infrastructure/automation-service/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app with full REST API
│   ├── config.py                  # YAML config loader
│   ├── database.py                # TimescaleDB operations (device states, control history)
│   ├── hardware/
│   │   ├── __init__.py
│   │   └── mcp23017.py            # MCP23017 I2C driver (with simulation mode)
│   ├── control/
│   │   ├── __init__.py
│   │   ├── relay_manager.py       # Device-to-channel mapping, state management
│   │   ├── pid_controller.py      # PID control implementation
│   │   ├── control_engine.py      # Main control logic (rules → schedules → PID)
│   │   └── scheduler.py           # Time-based scheduling
│   ├── automation/
│   │   ├── __init__.py
│   │   ├── rules_engine.py        # If-then rules evaluation
│   │   └── interlock_manager.py   # Safety interlocks
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── devices.py             # Device control endpoints
│   │   ├── setpoints.py           # Setpoint management
│   │   ├── schedules.py           # Schedule CRUD
│   │   ├── rules.py               # Rules CRUD
│   │   └── status.py              # Health/status endpoints
│   └── background_tasks.py        # Automatic control loop
├── automation_config.yaml         # Device configuration (from template)
├── schedules.yaml                  # Initial schedules (optional)
├── rules.yaml                     # Initial rules (optional)
├── requirements.txt
└── README.md
```

## Database Schema (TimescaleDB)

Add new tables to the shared TimescaleDB database:

### `device_states` table

```sql
CREATE TABLE device_states (
    id BIGSERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    channel INTEGER NOT NULL,
    state INTEGER NOT NULL,  -- 0 = OFF, 1 = ON
    mode TEXT NOT NULL,       -- 'manual', 'auto', 'scheduled'
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(location, cluster, device_name)
);

CREATE INDEX idx_device_states_location_cluster ON device_states(location, cluster);
```

### `control_history` table

```sql
CREATE TABLE control_history (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    channel INTEGER NOT NULL,
    old_state INTEGER,
    new_state INTEGER,
    mode TEXT,
    reason TEXT,
    sensor_value REAL,
    setpoint REAL
);

SELECT create_hypertable('control_history', 'timestamp');
CREATE INDEX idx_control_history_location ON control_history(location, cluster);
CREATE INDEX idx_control_history_timestamp ON control_history(timestamp DESC);
```

### `setpoints` table

```sql
CREATE TABLE setpoints (
    id BIGSERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    temperature REAL,
    humidity REAL,
    co2 REAL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(location, cluster)
);
```

### `schedules` table

```sql
CREATE TABLE schedules (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    day_of_week INTEGER,  -- NULL = daily, 0-6 = Monday-Sunday
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `rules` table

```sql
CREATE TABLE rules (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    condition_sensor TEXT NOT NULL,
    condition_operator TEXT NOT NULL,  -- '<', '>', '<=', '>=', '=='
    condition_value REAL NOT NULL,
    action_device TEXT NOT NULL,
    action_state INTEGER NOT NULL,  -- 0 = OFF, 1 = ON
    priority INTEGER DEFAULT 0,
    schedule_id INTEGER,  -- NULL = always active, or reference to schedules table
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
);
```

**Note**: Rules are constrained by schedules. A rule only evaluates when:

- `schedule_id` is NULL (rule always active), OR
- The associated schedule is currently active (within start_time/end_time)

### `automation_state` table

```sql
CREATE TABLE automation_state (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    device_state INTEGER NOT NULL,  -- 0 = OFF, 1 = ON
    device_mode TEXT NOT NULL,     -- 'manual', 'auto', 'scheduled'
    pid_output REAL,               -- PID output percentage (0-100%), NULL if not PID-controlled
    duty_cycle_percent REAL,        -- Current duty cycle percentage (0-100%), NULL if not PWM
    active_rule_ids INTEGER[],     -- Array of rule IDs that are currently active/matched
    active_schedule_ids INTEGER[],  -- Array of schedule IDs that are currently active
    control_reason TEXT,            -- 'rule', 'schedule', 'pid', 'manual', 'interlock'
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('automation_state', 'timestamp');
CREATE INDEX idx_automation_state_location ON automation_state(location, cluster);
CREATE INDEX idx_automation_state_timestamp ON automation_state(timestamp DESC);
CREATE INDEX idx_automation_state_device ON automation_state(location, cluster, device_name);
```

**Purpose**: Logs full automation state every control loop (1 second) for historical analysis and debugging.

- Tracks device states, modes, PID outputs, duty cycles
- Records which rules and schedules are active
- Stores control reason for each state
- Time-series hypertable for efficient storage and querying
- **Note**: PID K values (Kp, Ki, Kd) are configured in `automation_config.yaml`, not logged here

## Implementation Details

### 1. Hardware Driver (`app/hardware/mcp23017.py`)

- Port MCP23017 driver from `Test Scripts/climate_control/relay_control/mcp23017_driver.py`
- Support simulation mode (default for initial testing)
- I2C bus configuration from config file
- Channel control (0-15) with state tracking

### 2. Database Manager (`app/database.py`)

- Use `asyncpg` for async TimescaleDB operations (same as main backend)
- Connection pool management with **retry with exponential backoff** on connection failure
- **On DB connection loss**: Pause control loop, retry connection, resume when reconnected
- Device state read/write operations
- Control history logging
- **Automation state logging**: Write full automation state to `automation_state` table every control loop
  - Device states, modes, PID outputs, duty cycles
  - Active rule IDs and schedule IDs
  - Control reason
- Setpoint management
- Sensor data reading: **Redis first** (read from `sensor:*` keys), **fallback to TimescaleDB** if Redis unavailable
- Redis client with connection error handling and automatic fallback

### 3. Config Loader (`app/config.py`)

- Load `automation_config.yaml` on startup
- Load optional `schedules.yaml` and `rules.yaml`
- Sync config to database tables on startup
- Support config reload endpoint
- **Incremental config reload**: Apply changes as they are loaded (not atomic)

### 4. Relay Manager (`app/control/relay_manager.py`)

- Device-to-channel mapping from config
- **Single MCP23017 board support** (one I2C address)
- State management with database persistence
- Interlock enforcement (prevent conflicting states)
  - **Interlock actions logged** to control_history with reason "interlock"
- Safety limit checking
- Manual override support

### 5. PID Controller (`app/control/pid_controller.py`)

- PID implementation for temperature/CO2 control
- **PID K values (Kp, Ki, Kd) configurable in `automation_config.yaml`**:
  - Per device type (e.g., `control.pid.heater_kp`, `control.pid.co2_kp`)
  - Or per-device override in device configuration
  - Default values if not specified
- **Time-based PWM output**: PID output (0-100%) converted to time-based duty cycle
  - Example: 60% output with 100s period = ON for 60 seconds, OFF for 40 seconds
  - **Control period configurable per device** in config file (default: 100 seconds)
  - Tracks ON/OFF state within current period
- PID updates every control loop (1 second)
- Anti-windup protection
- Output range limiting (0-100%)

### 6. Control Engine (`app/control/control_engine.py`)

- Main automation loop logic
- **Execution order**: Rules (if schedule active) → Schedules → PID Control → Threshold Control
- **Rules are constrained by schedules**: Rules only evaluate when their associated schedule is active (or if schedule_id is NULL)
- **Priority-based rule resolution**: When multiple rules match, highest priority wins
- Respects manual overrides (persist in database, restored on restart)
- Reads latest sensor values from Redis (every control loop), fallback to TimescaleDB
- **Missing sensor handling**: If sensor value is None/missing, skip control for that device (no action taken)
- Applies control decisions via relay manager
- **Tracks automation context** for logging:
  - Which rules are active/matched
  - Which schedules are active
  - PID output percentages
  - Duty cycle percentages (for PWM devices)
  - Control reason for each device
- **PID K values (Kp, Ki, Kd) read from config**, not logged (configurable in automation_config.yaml)

### 7. Scheduler (`app/control/scheduler.py`)

- Time-based device control
- Daily/weekly schedule support
- **Uses local system time** for schedule evaluation
- Active schedule evaluation
- Schedule state application

### 8. Rules Engine (`app/automation/rules_engine.py`)

- If-then rule evaluation
- Sensor value comparison (>, <, <=, >=, ==)
- **Schedule constraint checking**: Only evaluate rules when their associated schedule is active
- **Priority-based execution**: Priority range 0-100 (higher number = higher priority)
  - When multiple rules match, highest priority rule wins
  - Default priority: 0 if not specified
- Action application (device ON/OFF)
- Rules execute first in control loop (before schedules)

### 9. Background Task (`app/background_tasks.py`)

- Runs control loop every **1 second** (configurable in `automation_config.yaml`, default: 1 second)
- Executes control engine
- **Pauses control loop if database connection lost**, retries with exponential backoff, resumes when reconnected
- Logs all actions to control_history
- **Logs full automation state** to automation_state table every control loop
  - Device states, modes, PID outputs, duty cycle percentages
  - Active rule IDs and schedule IDs
  - Control reason for each device
- **PID K values (Kp, Ki, Kd) are configurable in automation_config.yaml**, not logged
- Handles errors gracefully (hardware failures fall back to simulation mode)
- **Standard logging level**: Info, errors, warnings (not verbose debug)

### 10. REST API Endpoints

#### Device Control (`/api/devices`)

- `GET /api/devices` - List all devices with current state
- `GET /api/devices/{location}/{cluster}` - Get devices for location/cluster
- `GET /api/devices/{location}/{cluster}/{device}` - Get device details
- `POST /api/devices/{location}/{cluster}/{device}/control` - Manual control (state: 0/1)
- `POST /api/devices/{location}/{cluster}/{device}/mode` - Set mode (manual/auto/scheduled)
- `GET /api/control/history` - Get control history with filters

#### Setpoints (`/api/setpoints`)

- `GET /api/setpoints` - Get all setpoints
- `GET /api/setpoints/{location}/{cluster}` - Get setpoints for location/cluster
- `POST /api/setpoints/{location}/{cluster}` - Update setpoints

#### Schedules (`/api/schedules`)

- `GET /api/schedules` - List all schedules
- `POST /api/schedules` - Create schedule
- `PUT /api/schedules/{id}` - Update schedule
- `DELETE /api/schedules/{id}` - Delete schedule

#### Rules (`/api/rules`)

- `GET /api/rules` - List all rules
- `POST /api/rules` - Create rule
- `PUT /api/rules/{id}` - Update rule
- `DELETE /api/rules/{id}` - Delete rule
- `POST /api/rules/{id}/toggle` - Enable/disable rule

#### Status (`/api/status`)

- `GET /health` - Health check
- `GET /api/status` - Full system status (devices, sensors, automation state)

**Note**: API authentication uses same method as main backend (port 8000)

## Configuration Files

### `automation_config.yaml`

- Copy from `automation_config.yaml.template`
- Fill in device mappings (channels, device types)
- **Configure PID parameters**:
  - **PID K values (Kp, Ki, Kd)**: Configurable per device type or per device
    - Example: `control.pid.heater_kp: 25.0`, `control.pid.heater_ki: 0.02`, `control.pid.heater_kd: 0.0`
    - Or per-device override in device configuration
  - **PWM control period**: Configurable per device (default: 100 seconds)
- Set default setpoints
- Configure safety limits
- Map sensors to locations/clusters
- **Control loop interval**: `control.update_interval` (default: 1 second)

### `schedules.yaml` (optional)

- Initial schedule definitions
- Can be managed via API later

### `rules.yaml` (optional)

- Initial automation rules
- Can be managed via API later

## Startup Sequence

1. Load YAML config files
2. Initialize TimescaleDB connection
3. Create database tables if not exist
4. Sync config to database (devices, setpoints, schedules, rules)
5. Initialize Redis connection (for sensor data)
6. Initialize MCP23017 hardware (or simulation mode)

   - If hardware fails, automatically fall back to simulation mode

7. **Restore device states from database** (if exists), otherwise use config defaults
8. Start background control task (runs every 1 second by default)
9. Start FastAPI server on port 8001

## Sensor Data Integration

- **Primary**: Read from Redis state keys (`sensor:dry_bulb_f`, `sensor:rh_b`, `sensor:co2_f`, etc.)
  - Read every control loop (1 second) for real-time values
  - Fast and efficient for control decisions
- **Fallback**: If Redis unavailable, query TimescaleDB `can_messages` table
  - Query by location/cluster → node_id mapping
  - Extract sensor values from `decoded_data` JSONB column
  - Use sensor names from config (e.g., `dry_bulb_f`, `rh_b`, `co2_f`)
- Redis connection errors trigger automatic fallback to TimescaleDB

## Dependencies

- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `asyncpg` - Async PostgreSQL/TimescaleDB driver
- `redis` - Redis client for sensor data (with fallback to TimescaleDB)
- `pyyaml` - YAML config parsing
- `smbus2` - I2C communication (optional, for hardware)
- `pydantic` - Data validation

## Testing

- Start with simulation mode (`simulation: true` in config)
- Test API endpoints
- Verify database operations
- Test control logic without hardware
- Add hardware when ready

## Integration Points

- **Database**: Shared TimescaleDB instance (same as main backend)
- **Sensor Data**: **Redis state keys** (primary), TimescaleDB `can_messages` table (fallback)
- **State Storage**: Writes to `device_states`, `control_history` tables in TimescaleDB
- **Automation Logging**: Writes to `automation_state` hypertable every control loop (1 second)
  - Full automation state: device states, modes, PID outputs, duty cycles
  - Active rules and schedules
  - Control reasons
- **PID K values (Kp, Ki, Kd)**: Configurable in `automation_config.yaml`, not logged to database
- **API**: Separate service on port 8001 (main backend on 8000)
- **Hardware**: MCP23017 I2C relay board (16 channels), with automatic simulation mode fallback

## Control Flow Details

### Rules and Schedules Relationship

**Key Principle**: Schedules constrain rules - rules only evaluate when their associated schedule is active.

**Execution Flow**:

1. **Manual Override Check**: If device is in manual mode, skip automatic control
2. **Rules Evaluation** (if schedule active):

   - Check if rule's associated schedule is active (or schedule_id is NULL)
   - Evaluate rule conditions against sensor values
   - Apply highest priority matching rule
   - If rule matches, apply action and skip to next device

3. **Schedule Evaluation** (only if no rule matched):

   - Check if any schedule is active for the device
   - Apply schedule state (ON/OFF)
   - If schedule applies, skip to next device

4. **PID Control** (only if no rule/schedule applied):

   - Compute PID output (0-100%)
   - Convert to time-based PWM (ON for X seconds, OFF for Y seconds)
   - Apply PWM state

**Example**:

- Schedule: "heater_1 ON from 06:00-18:00"
- Rule: "If temp < 20°C, heater_1 ON" (schedule_id references the above schedule)
- During 06:00-18:00: Rule evaluates (schedule is active), applies if condition matches
- Outside 06:00-18:00: Rule does NOT evaluate (schedule not active), schedule applies instead

## Next Steps After Implementation

1. Test in simulation mode
2. Configure actual device mappings
3. Set up schedules and rules
4. Integrate with frontend
5. Deploy as systemd service