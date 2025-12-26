# Python Automation Microservice - Hybrid Approach (Config + Full API)

## Overview

Create a separate FastAPI microservice with **full REST API endpoints** (ready for frontend), but **initialize from YAML config files**. This gives you:
- ✅ Full API for future frontend development
- ✅ YAML config files for easy initial setup and management
- ✅ API can override config values (stored in database)
- ✅ Best of both worlds

## Architecture

```
Infrastructure/
└── automation-service/         # Automation microservice (port 8001)
    ├── app/
    │   ├── __init__.py
    │   ├── main.py             # FastAPI app with full API
    │   ├── config.py            # Config loader (YAML → database)
    │   ├── database.py          # Database manager
    │   ├── hardware/
    │   │   └── mcp23017.py
    │   ├── control/
    │   │   ├── relay_manager.py
    │   │   ├── pid_controller.py
    │   │   ├── control_engine.py
    │   │   └── scheduler.py
    │   ├── automation/
    │   │   ├── rules_engine.py
    │   │   └── interlock_manager.py
    │   ├── routes/
    │   │   ├── devices.py       # Full device control API
    │   │   ├── setpoints.py    # Setpoint management API
    │   │   ├── schedules.py    # Schedule CRUD API
    │   │   ├── rules.py         # Rules CRUD API
    │   │   └── status.py        # Status/health
    │   └── background_tasks.py
    ├── automation_config.yaml   # Initial device config (YOU FILL THIS)
    ├── schedules.yaml            # Initial schedules (optional)
    ├── rules.yaml               # Initial rules (optional)
    ├── requirements.txt
    └── README.md
```

## How It Works

### Initialization (Startup)

1. **Service starts**
2. **Loads YAML config files**:
   - `automation_config.yaml` → Device mappings, setpoints, PID params
   - `schedules.yaml` → Initial schedules (if exists)
   - `rules.yaml` → Initial rules (if exists)
3. **Syncs to database**:
   - Devices → `device_states` table
   - Setpoints → `setpoints` table
   - Schedules → `schedules` table
   - Rules → `rules` table
4. **Background task starts** running automation

### Runtime

- **Config files**: Initial/default values
- **Database**: Current runtime state (can be modified via API)
- **API**: Can read/write to database (overrides config)
- **Background task**: Uses database values (not config files)

### Workflow Options

**Option A: Use YAML (Now)**
1. Edit `automation_config.yaml`
2. Restart service (or call reload endpoint)
3. Config syncs to database
4. Automation runs

**Option B: Use API (Later with Frontend)**
1. Frontend calls API endpoints
2. API updates database directly
3. Automation uses new values immediately
4. No restart needed

## Full API Endpoints (Frontend-Ready)

### Device Control (`/api/devices`)

```python
GET /api/devices
  → List all devices with current state

GET /api/devices/{location}/{cluster}
  → Get devices for location/cluster

GET /api/devices/{location}/{cluster}/{device}
  → Get detailed device status

POST /api/devices/{location}/{cluster}/{device}/control
  → Manual control (turn ON/OFF)
  Body: {"state": 1, "reason": "manual"}

POST /api/devices/{location}/{cluster}/{device}/mode
  → Set control mode
  Body: {"mode": "manual" | "auto" | "scheduled"}

GET /api/control/history
  → Get control history (with filters)
```

### Setpoints (`/api/setpoints`)

```python
GET /api/setpoints
  → Get all setpoints

GET /api/setpoints/{location}/{cluster}
  → Get setpoints for location/cluster

POST /api/setpoints/{location}/{cluster}
  → Update setpoints
  Body: {"temperature": 26.0, "humidity": 70.0, "co2": 1300.0}
```

### Schedules (`/api/schedules`)

```python
GET /api/schedules
  → List all schedules

POST /api/schedules
  → Create schedule
  Body: {
    "name": "Lights On",
    "location": "Flower Room",
    "cluster": "front",
    "device_name": "light_1",
    "day_of_week": null,  # null = daily
    "start_time": "06:00",
    "end_time": "18:00"
  }

PUT /api/schedules/{id}
  → Update schedule

DELETE /api/schedules/{id}
  → Delete schedule

POST /api/schedules/{id}/enable
  → Enable/disable schedule
  Body: {"enabled": true}
```

### Rules (`/api/rules`)

```python
GET /api/rules
  → List all rules

POST /api/rules
  → Create rule
  Body: {
    "name": "Turn on heater if cold",
    "location": "Flower Room",
    "cluster": "front",
    "condition_sensor": "dry_bulb_f",
    "condition_operator": "<",
    "condition_value": 20.0,
    "action_device": "heater_1",
    "action_state": 1
  }

PUT /api/rules/{id}
  → Update rule

DELETE /api/rules/{id}
  → Delete rule

POST /api/rules/{id}/enable
  → Enable/disable rule
  Body: {"enabled": true}
```

### Config Management (`/api/config`)

```python
GET /api/config/reload
  → Reload YAML config files and sync to database
  → Useful when you edit config files

GET /api/config/export
  → Export current database state to YAML (optional)
```

## Configuration Files

### `automation_config.yaml`

**Purpose**: Initial device configuration, setpoints, PID params

**Structure**: Same as template I created earlier

**Usage**:
- Fill in your device mappings
- Service loads on startup
- Syncs to database
- Can be overridden via API

### `schedules.yaml` (Optional)

**Purpose**: Initial schedules (can also be created via API)

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
  # More schedules...
```

### `rules.yaml` (Optional)

**Purpose**: Initial automation rules (can also be created via API)

```yaml
rules:
  - name: "Turn on heater if cold - Flower Room Front"
    enabled: true
    location: "Flower Room"
    cluster: "front"
    condition_sensor: "dry_bulb_f"
    condition_operator: "<"
    condition_value: 20.0
    action_device: "heater_1"
    action_state: 1
    priority: 0
  # More rules...
```

## Database Schema

Same tables as before:
- `device_states` - Current device states (from config or API)
- `control_history` - Log of all control actions
- `setpoints` - Current setpoints (from config or API)
- `schedules` - Schedules (from config or API)
- `rules` - Rules (from config or API)

## Implementation Details

### Config Loader (`app/config.py`)

**Responsibilities**:
1. Load YAML files on startup
2. Sync to database (if database is empty or on reload)
3. Provide config values to other components
4. Support hot-reload (optional)

**Key Methods**:
```python
class ConfigLoader:
    def load_config() -> Dict
    def sync_to_database() -> None  # Sync YAML → database
    def reload() -> None  # Reload from files and sync
```

### Database Manager (`app/database.py`)

**Responsibilities**:
1. Read sensor data from `can_messages.db`
2. Read/write device states
3. Read/write setpoints
4. Read/write schedules
5. Read/write rules
6. Log control history

**Key Methods**:
```python
class DatabaseManager:
    # Sensor data (read-only from can_messages table)
    async def get_latest_sensor_value(location, cluster, sensor_type) -> float
    
    # Device states
    async def get_device_state(location, cluster, device) -> Dict
    async def set_device_state(location, cluster, device, state, mode) -> bool
    
    # Setpoints
    async def get_setpoint(location, cluster, parameter) -> float
    async def set_setpoint(location, cluster, parameter, value) -> bool
    
    # Schedules
    async def get_schedules() -> List[Dict]
    async def create_schedule(schedule_data) -> int
    async def update_schedule(id, schedule_data) -> bool
    async def delete_schedule(id) -> bool
    
    # Rules
    async def get_rules() -> List[Dict]
    async def create_rule(rule_data) -> int
    async def update_rule(id, rule_data) -> bool
    async def delete_rule(id) -> bool
    
    # History
    async def log_control_action(...) -> bool
    async def get_control_history(filters) -> List[Dict]
```

### API Routes

All routes read/write to **database** (not config files directly):
- Config files are source of truth for initial values
- Database is source of truth for runtime values
- API modifies database
- Background task reads from database

## Benefits

1. **Full API Ready**: Frontend can be built immediately
2. **YAML for Now**: Easy to configure without frontend
3. **Flexible**: Use YAML or API, or both
4. **No Lock-in**: Can switch between config and API anytime
5. **Version Control**: Config files can be git-tracked
6. **Backup**: Database state can be exported to YAML

## Workflow Examples

### Example 1: Initial Setup (YAML)

1. Fill in `automation_config.yaml` with device mappings
2. Add `schedules.yaml` with light schedules
3. Add `rules.yaml` with automation rules
4. Start service → Config loads → Syncs to database → Automation runs
5. Check status via `GET /api/devices`

### Example 2: Change Setpoint (YAML)

1. Edit `automation_config.yaml` → change temperature setpoint
2. Call `GET /api/config/reload` (or restart service)
3. New setpoint syncs to database
4. Automation uses new setpoint immediately

### Example 3: Change Setpoint (API - Future Frontend)

1. Frontend calls `POST /api/setpoints/Flower Room/front`
2. Body: `{"temperature": 26.0}`
3. Database updated immediately
4. Automation uses new setpoint immediately
5. No restart needed

### Example 4: Add Schedule (API - Future Frontend)

1. Frontend calls `POST /api/schedules`
2. Body: `{"name": "New Schedule", ...}`
3. Schedule saved to database
4. Scheduler picks it up immediately
5. No restart needed

## Implementation Priority

1. **Config loader** - Load YAML and sync to database
2. **Database manager** - All CRUD operations
3. **Hardware/Control layers** - Device control logic
4. **Background task** - Automation loop
5. **API routes** - Full REST API endpoints
6. **Config reload endpoint** - For YAML updates

This gives you the best of both worlds: easy YAML config now, full API ready for frontend later!


