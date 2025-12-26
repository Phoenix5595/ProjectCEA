---
name: Node-RED Integration - Complete Architecture with Clear Responsibilities
overview: Complete Node-RED integration with explicit responsibility boundaries. Includes all API endpoints, validation, configuration, and integration tasks.
todos:
  - id: pid_parameter_storage
    content: Add PID parameter storage methods to DatabaseManager (get/set with validation, logging)
    status: completed
  - id: pid_parameter_validation
    content: Add PID parameter validation function with configurable limits per device type
    status: completed
  - id: pid_parameter_api
    content: Create PID parameter API endpoints (GET/POST /api/pid/parameters/{device_type}) with rate limiting
    status: completed
    dependencies:
      - pid_parameter_storage
      - pid_parameter_validation
  - id: pid_dynamic_reload
    content: Modify PIDController to reload parameters dynamically from Redis/DB, check for updates each control loop
    status: completed
    dependencies:
      - pid_parameter_storage
  - id: device_mapping_storage
    content: Add device mapping storage methods to DatabaseManager (backend owns all mappings)
    status: completed
  - id: device_mapping_validation
    content: Add device mapping validation (channel exists, no duplicates)
    status: completed
  - id: device_mapping_api
    content: Add device mapping API endpoints (GET/POST /api/devices/mappings) - backend authoritative
    status: completed
    dependencies:
      - device_mapping_storage
      - device_mapping_validation
  - id: setpoint_validation
    content: Add setpoint validation function using safety_limits from config
    status: completed
  - id: redis_enhancements
    content: "Add all Redis methods: setpoint (with rate limit), mode, failsafe, alarm, heartbeat, last good value, PID params cache"
    status: completed
  - id: database_redis_integration
    content: Modify DatabaseManager.get_setpoint() to read from Redis first, auto-reload from DB on TTL expiry, fallback to database
    status: completed
    dependencies:
      - redis_enhancements
  - id: database_write_redis
    content: Modify DatabaseManager.set_setpoint() to validate, then write to both Redis (with source) and database
    status: completed
    dependencies:
      - redis_enhancements
      - setpoint_validation
  - id: alarm_manager
    content: Create AlarmManager for alarm tracking, failsafe enforcement with reason
    status: completed
    dependencies:
      - redis_enhancements
  - id: heartbeat_task
    content: Add heartbeat background task (automation service, sensor gateways), update last good values
    status: completed
    dependencies:
      - redis_enhancements
  - id: auto_persist_task
    content: Add auto-persist task for setpoints and PID params (sync Redis to DB periodically)
    status: completed
    dependencies:
      - redis_enhancements
  - id: control_engine_enhancements
    content: "Modify ControlEngine: check mode before PID, reload PID params, use last good value, enforce failsafe"
    status: completed
    dependencies:
      - pid_dynamic_reload
      - redis_enhancements
      - alarm_manager
  - id: api_mode_endpoints
    content: Create mode API endpoints (GET/POST /api/mode/{location}/{cluster})
    status: completed
    dependencies:
      - redis_enhancements
  - id: api_failsafe_endpoints
    content: Create failsafe API endpoints (GET /api/failsafe, POST clear)
    status: completed
    dependencies:
      - redis_enhancements
  - id: api_alarm_endpoints
    content: Create alarm API endpoints (GET /api/alarms, POST acknowledge)
    status: completed
    dependencies:
      - alarm_manager
  - id: update_setpoint_endpoint
    content: Update setpoint endpoint to write source=api and set mode=auto
    status: completed
    dependencies:
      - redis_enhancements
  - id: config_parameters
    content: Add last_good_hold_period, rate_limit, and pid_limits config parameters to automation_config.yaml
    status: completed
  - id: populate_redis_startup
    content: Populate Redis from database on startup (setpoints, PID params, device mappings, modes)
    status: completed
    dependencies:
      - redis_enhancements
  - id: main_integration
    content: Initialize AlarmManager in main.py, pass to ControlEngine, start heartbeat and auto-persist tasks, populate Redis on startup
    status: completed
    dependencies:
      - alarm_manager
      - heartbeat_task
      - auto_persist_task
      - populate_redis_startup
  - id: cors_configuration
    content: Verify/enhance CORS configuration in main.py to allow Node-RED (port 3001) to call automation service API, document CORS settings
    status: completed
    dependencies:
      - main_integration
  - id: api_documentation
    content: Ensure all API endpoints have proper OpenAPI/Swagger documentation, verify /docs and /openapi.json endpoints work, document API in README
    status: pending
    dependencies:
      - pid_parameter_api
      - device_mapping_api
      - api_mode_endpoints
      - api_failsafe_endpoints
      - api_alarm_endpoints
  - id: install_node_red
    content: Install Node.js and Node-RED, configure port 3001 with basic authentication
    status: completed
  - id: install_packages
    content: Install node-red-dashboard, node-red-contrib-redis, node-red-contrib-http-request
    status: completed
    dependencies:
      - install_node_red
  - id: create_service
    content: Create systemd service file for Node-RED
    status: completed
    dependencies:
      - install_node_red
  - id: create_flows
    content: "Create Node-RED flows: PID parameter tuning (API only), setpoint control, device control, device mapping view, monitoring - NO control logic"
    status: completed
    dependencies:
      - install_packages
  - id: architecture_doc
    content: Create ARCHITECTURE.md with responsibility matrix, data flow diagrams, failure scenarios, recovery behavior
    status: pending
  - id: node_red_doc
    content: Create Node-RED README with explicit role definition, allowed/forbidden operations
    status: completed
    dependencies:
      - create_flows
  - id: automation_doc
    content: Update automation service README with PID parameter API, device mapping API, responsibility boundaries
    status: pending
    dependencies:
      - pid_parameter_api
      - device_mapping_api
  - id: main_docs
    content: Update Infrastructure/README.md with architecture reference and service responsibilities
    status: pending
    dependencies:
      - architecture_doc
---

# Node-RED Integration - Complete Architecture with Clear Responsibilities

## Overview

This plan installs Node-RED as an **operator interface and configuration editor only**, with strict responsibility boundaries. All control logic, PID execution, hardware access, and authoritative persistence remain in the automation backend service.

## Core Architectural Principles (Non-Negotiable)

### 1. PID Control vs PID Configuration

**PID Execution**: MUST remain entirely in automation backend

- Backend owns all PID loop execution
- Backend calculates PID terms (Kp, Ki, Kd)
- Backend controls timing and update intervals
- Node-RED NEVER calculates PID terms
- Node-RED NEVER influences control timing

**PID Configuration**: Node-RED may view and edit PID parameters via API

- PID parameters stored in database (authoritative)
- PID parameters cached in Redis for fast reads
- Backend validates PID parameter ranges
- Backend enforces update rate limits
- PID loops reload parameters dynamically from Redis/DB

### 2. Device ↔ Hardware Mapping Ownership

**Backend owns**:

- Device IDs and names
- MCP23017 board IDs
- Pin/channel numbers
- Active-high/low logic
- Safe state definitions
- Device-to-hardware mappings

**Node-RED**:

- May edit mappings ONLY via API
- Backend validates all mappings
- Backend persists mappings to database
- **FORBIDDEN**: Direct MCP pin toggling
- **FORBIDDEN**: Persisting mapping state directly

### 3. Node-RED Role Definition

**Node-RED is an operator interface and configuration editor, NOT an automation engine**

**Allowed**:

- Setpoint entry (via API or Redis override)
- PID parameter editing (via API)
- Mode selection (via API)
- Alarm acknowledgment (via API)
- Device control (via API)
- Viewing sensor data, device states, alarms

**Forbidden**:

- PID math/calculations
- Safety logic execution
- Hardware access (MCP23017)
- Authoritative persistence (database writes)
- Control loop execution
- Timing-critical operations

### 4. Redis Usage Rules

**Redis is a real-time state bus, NOT the system of record**

**Rules**:

- Redis-first reads for control (fast, real-time)
- Database is source of truth (authoritative)
- TTL for volatile data (sensors, heartbeats)
- Auto-reload from DB when TTL expires (setpoints, PID params)
- Source tracking for all writable values
- Redis failures fall back to database

**Data Classification**:

- **Volatile** (TTL required): Sensors, heartbeats, device states
- **Semi-persistent** (TTL with reload): Setpoints, PID parameters
- **Persistent** (no TTL, DB authoritative): Device mappings, schedules, rules

### 5. Mode & Safety Enforcement

**Control behavior MUST depend on explicit mode and alarm state**

**Modes**:

- `auto` - Normal PID control, rules, schedules active
- `manual` - Manual device control only, no PID
- `override` - Temporary override (e.g., Node-RED direct write)
- `failsafe` - Safety mode, PID frozen, devices in safe state

**Enforcement**:

- PID runs only when mode allows
- Critical alarms force failsafe mode
- Recovery rules documented and enforced
- Mode changes logged with timestamp and source

### 6. Heartbeat and Watchdog Behavior

**Control logic MUST verify data freshness before acting**

**Heartbeats**:

- Automation service heartbeat → Redis every 1-2s
- Sensor gateway heartbeat → Redis every 1-2s
- Node-RED heartbeat (optional, for UI status)

**Behavior**:

- PID ignores stale data (no heartbeat or expired)
- Alarm raised on heartbeat loss
- Safe behavior on failure (failsafe mode)
- Last good value used during brief glitches (within hold period)

## Implementation Tasks

### Part 1: Automation Service - PID Parameter Management

#### 1.1 PID Parameter Storage

**File**: `Infrastructure/automation-service/app/database.py`

Add methods:

- `get_pid_parameters(device_type)` - Get PID parameters from database
- `set_pid_parameters(device_type, kp, ki, kd, source='api')` - Set PID parameters (validated, logged)

**Database Table**: `pid_parameters`

```sql
CREATE TABLE pid_parameters (
    device_type TEXT PRIMARY KEY,
    kp REAL NOT NULL,
    ki REAL NOT NULL,
    kd REAL NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    updated_by TEXT,  -- 'api', 'node-red', 'config'
    source TEXT
);
```

**Redis Keys**:

- `pid:parameters:{device_type}` (JSON: `{"kp": float, "ki": float, "kd": float, "source": str, "updated_at": timestamp_ms}`, TTL: 300s, auto-reload from DB)

#### 1.2 PID Parameter Validation

**File**: `Infrastructure/automation-service/app/validation.py` (new)

Add validation:

- `validate_pid_parameters(kp, ki, kd, device_type, config)` - Validate against limits
- Read limits from config: `pid_limits` section
- Returns (is_valid, error_message, validated_values)

**Config Limits** (in `automation_config.yaml`):

```yaml
control:
  pid_limits:
    heater:
      kp_min: 0.0
      kp_max: 100.0
      ki_min: 0.0
      ki_max: 1.0
      kd_min: 0.0
      kd_max: 10.0
    co2:
      kp_min: 0.0
      kp_max: 50.0
      ki_min: 0.0
      ki_max: 0.5
      kd_min: 0.0
      kd_max: 5.0
```

#### 1.3 PID Parameter API Endpoints

**File**: `Infrastructure/automation-service/app/routes/pid.py` (new)

Endpoints:

- `GET /api/pid/parameters` - Get all PID parameters
- `GET /api/pid/parameters/{device_type}` - Get PID parameters for device type
- `POST /api/pid/parameters/{device_type}` - Update PID parameters (validated, rate limited)
- `GET /api/pid/parameters/{device_type}/history` - Get parameter change history

**Rate Limiting**: Max 1 update per 5 seconds per device_type (prevents tuning instability)

#### 1.4 Dynamic PID Parameter Reload

**File**: `Infrastructure/automation-service/app/control/pid_controller.py`

Modify PIDController:

- Add method: `reload_parameters()` - Reload from Redis/DB
- Check Redis for updated parameters every control loop
- If Redis TTL expired, reload from database
- Update internal Kp, Ki, Kd values dynamically
- Log parameter changes

**File**: `Infrastructure/automation-service/app/control/control_engine.py`

Modify PID control:

- Before each PID calculation, check for parameter updates
- Reload parameters if changed (within rate limit window)
- Use updated parameters for PID calculation

### Part 2: Automation Service - Device Mapping Management

#### 2.1 Device Mapping Storage

**File**: `Infrastructure/automation-service/app/database.py`

Add methods:

- `get_device_mapping(location, cluster, device_name)` - Get device mapping
- `set_device_mapping(location, cluster, device_name, channel, active_high, safe_state)` - Set mapping (validated)
- `get_all_device_mappings()` - Get all mappings

**Database Table**: `device_mappings` (or extend existing `device_states`)

#### 2.2 Device Mapping Validation

**File**: `Infrastructure/automation-service/app/validation.py`

Add validation:

- `validate_device_mapping(channel, mcp_board_id, config)` - Validate channel exists, not duplicate
- Check against hardware config (MCP23017 has 16 channels)
- Prevent duplicate channel assignments

#### 2.3 Device Mapping API Endpoints

**File**: `Infrastructure/automation-service/app/routes/devices.py` (extend existing)

Add endpoints:

- `GET /api/devices/mappings` - Get all device mappings
- `GET /api/devices/{location}/{cluster}/{device}/mapping` - Get device mapping
- `POST /api/devices/{location}/{cluster}/{device}/mapping` - Update device mapping (validated)
- **FORBIDDEN**: Direct hardware pin control from API

### Part 3: Automation Service - Enhanced Features

#### 3.1 Setpoint Management (Enhanced)

**File**: `Infrastructure/automation-service/app/redis_client.py`

Add rate limiting for Node-RED overrides:

- `check_rate_limit(location, cluster, setpoint_type, max_per_second=1)` - Check if write allowed
- Track last write timestamp per setpoint
- Reject writes that are too frequent

#### 3.2 Setpoint Validation

**File**: `Infrastructure/automation-service/app/validation.py`

Add validation:

- `validate_setpoint(setpoint_type, value, config)` - Validate against safety_limits
- Returns (is_valid, error_message)
- Used in `set_setpoint()` before writing

#### 3.3 Database Redis Integration

**File**: `Infrastructure/automation-service/app/database.py`

**Modify `get_setpoint()`**:

1. Try Redis first
2. If Redis key expired (TTL), reload from database and write to Redis
3. If not in Redis, read from database and cache in Redis
4. Return setpoint data

**Modify `set_setpoint()`**:

1. Validate setpoint values (using safety_limits)
2. If invalid, return False with error message
3. Write to TimescaleDB
4. Write to Redis with source='api'
5. Return success

#### 3.4 Mode Management (Enhanced)

**File**: `Infrastructure/automation-service/app/redis_client.py`

Add methods:

- `read_mode(location, cluster)` - Read mode
- `write_mode(location, cluster, mode, source='api')` - Write mode with source tracking
- Log mode changes with timestamp and source

#### 3.5 Failsafe Management (Enhanced)

**File**: `Infrastructure/automation-service/app/redis_client.py`

Add methods:

- `read_failsafe(location, cluster)` - Read failsafe reason/details
- `write_failsafe(location, cluster, reason, triggered_by, timestamp=None)` - Write failsafe state
- `clear_failsafe(location, cluster)` - Clear failsafe (if conditions met)

#### 3.6 Last Good Value Tracking

**File**: `Infrastructure/automation-service/app/redis_client.py`

Add methods:

- `write_last_good_value(cluster, sensor_name, value, timestamp=None)`
- `read_last_good_value(cluster, sensor_name)`
- `check_last_good_age(cluster, sensor_name, max_age_seconds=30)`

#### 3.7 Heartbeat System

**File**: `Infrastructure/automation-service/app/background_tasks.py`

Add heartbeat task:

- Write automation service heartbeat every 1-2 seconds
- Check sensor heartbeats
- Update last good values when sensors valid
- Trigger alarms if sensors offline

#### 3.8 Alarm System

**File**: `Infrastructure/automation-service/app/redis_client.py`

Add methods:

- `write_alarm(location, cluster, alarm_name, severity, message)` - Write alarm
- `acknowledge_alarm(location, cluster, alarm_name)` - Acknowledge alarm
- `read_alarms(location, cluster)` - Read active alarms

**File**: `Infrastructure/automation-service/app/alarm_manager.py` (new)

Create AlarmManager class:

- Track alarm states
- Enforce failsafe on critical alarms (with reason tracking)
- Auto-acknowledge on condition clear
- Log alarms to database

### Part 4: API Configuration and Documentation

#### 4.1 CORS Configuration

**File**: `Infrastructure/automation-service/app/main.py`

Verify/enhance CORS middleware:

- Current: Allows all origins (`allow_origins=["*"]`)
- For Node-RED integration: Ensure Node-RED (port 3001) can call automation service (port 8001)
- Document CORS settings in README
- Note: Current configuration is sufficient for local network, but document for production considerations

#### 4.2 API Documentation (OpenAPI/Swagger)

**File**: `Infrastructure/automation-service/app/main.py`

FastAPI automatically generates OpenAPI docs:

- `/docs` - Interactive Swagger UI
- `/openapi.json` - OpenAPI JSON schema

Ensure all endpoints have:

- Proper response models (Pydantic)
- Request/response examples
- Error response documentation
- Tags for organization

**File**: `Infrastructure/automation-service/README.md`

Document:

- API documentation access: `http://localhost:8001/docs`
- OpenAPI schema: `http://localhost:8001/openapi.json`
- How to use Swagger UI for testing

### Part 5: API Endpoints

#### 5.1 Mode Endpoints

**File**: `Infrastructure/automation-service/app/routes/mode.py` (new)

- `GET /api/mode/{location}/{cluster}` - Get mode
- `POST /api/mode/{location}/{cluster}` - Set mode
- `GET /api/mode` - Get all modes

#### 5.2 Failsafe Endpoints

**File**: `Infrastructure/automation-service/app/routes/failsafe.py` (new)

- `GET /api/failsafe/{location}/{cluster}` - Get failsafe details
- `GET /api/failsafe` - Get all failsafe states
- `POST /api/failsafe/{location}/{cluster}/clear` - Clear failsafe (if conditions met)

#### 5.3 Alarm Endpoints

**File**: `Infrastructure/automation-service/app/routes/alarms.py` (new)

- `GET /api/alarms/{location}/{cluster}` - Get alarms
- `GET /api/alarms` - Get all alarms
- `POST /api/alarms/{location}/{cluster}/{alarm_name}/acknowledge` - Acknowledge alarm

#### 5.4 Update Setpoint Endpoint

**File**: `Infrastructure/automation-service/app/routes/setpoints.py`

- Modify `POST /api/setpoints/{location}/{cluster}` to:
  - Write source='api' to Redis
  - Set mode to 'auto' (if not in failsafe)
  - No rate limiting (API is authoritative)

### Part 6: Configuration

#### 5.1 Add Configuration Parameters

**File**: `Infrastructure/automation-service/automation_config.yaml`

Add to control section:

```yaml
control:
  # Last good value hold period (seconds)
  last_good_hold_period: 30  # Hold last good value for 30s before failsafe
  
  # Rate limiting for Node-RED overrides
  rate_limit:
    node_red_max_per_second: 1  # Max writes per second per setpoint
  
  # PID parameter limits
  pid_limits:
    heater:
      kp_min: 0.0
      kp_max: 100.0
      ki_min: 0.0
      ki_max: 1.0
      kd_min: 0.0
      kd_max: 10.0
    co2:
      kp_min: 0.0
      kp_max: 50.0
      ki_min: 0.0
      ki_max: 0.5
      kd_min: 0.0
      kd_max: 5.0
```

### Part 7: Node-RED Installation and Flows

#### 6.1 Node-RED Installation

- Install Node.js and Node-RED
- Configure port 3001 with basic authentication
- Install packages: `node-red-dashboard`, `node-red-contrib-redis`, `node-red-contrib-http-request`

**Files to create**:

- `Infrastructure/node-red.service` - Systemd service
- `Infrastructure/frontend/node-red/settings.js.template` - Configuration

#### 6.2 Node-RED Flows

Create flows for:

- PID parameter tuning (API only)
- Setpoint control (API + optional Redis override)
- Mode management
- Failsafe display
- Alarm display and acknowledgment
- Device control (API only)
- Device mapping view (read-only)
- Sensor display with last good value
- System status

**File**: `Infrastructure/frontend/node-red/flows/cea_operator_interface.json`

### Part 8: Documentation

#### 7.1 Responsibility Matrix

**File**: `Infrastructure/ARCHITECTURE.md` (new)

Document:

- Automation Backend Responsibilities
- Node-RED Responsibilities
- Redis Responsibilities
- Database Responsibilities

#### 7.2 Data Flow Diagrams

**File**: `Infrastructure/ARCHITECTURE.md`

Include diagrams for all data flows

#### 7.3 Failure Scenarios and Recovery

**File**: `Infrastructure/ARCHITECTURE.md`

Document recovery behavior for all failure scenarios

#### 7.4 Node-RED Documentation

**File**: `Infrastructure/frontend/node-red/README.md`

- Role definition (operator interface only)
- Allowed/forbidden operations
- API endpoint reference

#### 7.5 Automation Service Documentation

**File**: `Infrastructure/automation-service/README.md`

- All API endpoints
- Responsibility boundaries
- Safety enforcement

## Files to Create/Modify

### New Files

- `Infrastructure/ARCHITECTURE.md`
- `Infrastructure/frontend/node-red/README.md`
- `Infrastructure/frontend/node-red/settings.js.template`
- `Infrastructure/frontend/node-red/package.json`
- `Infrastructure/frontend/node-red/flows/cea_operator_interface.json`
- `Infrastructure/node-red.service`
- `Infrastructure/automation-service/app/routes/pid.py`
- `Infrastructure/automation-service/app/routes/mode.py`
- `Infrastructure/automation-service/app/routes/failsafe.py`
- `Infrastructure/automation-service/app/routes/alarms.py`
- `Infrastructure/automation-service/app/validation.py`
- `Infrastructure/automation-service/app/alarm_manager.py`

### Modified Files

- `Infrastructure/automation-service/app/database.py`
- `Infrastructure/automation-service/app/control/pid_controller.py`
- `Infrastructure/automation-service/app/control/control_engine.py`
- `Infrastructure/automation-service/app/routes/devices.py`
- `Infrastructure/automation-service/app/routes/setpoints.py`
- `Infrastructure/automation-service/app/redis_client.py`
- `Infrastructure/automation-service/app/background_tasks.py`
- `Infrastructure/automation-service/app/main.py`
- `Infrastructure/automation-service/automation_config.yaml`
- `Infrastructure/automation-service/README.md`
- `Infrastructure/README.md`