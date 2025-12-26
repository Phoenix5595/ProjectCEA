# Greenhouse Automation Backend

Autonomous automation backend service for CEA (Controlled Environment Agriculture) system. Provides deterministic, testable climate control with clear separation between control logic, live state, and persistent configuration.

## Overview

The automation backend is a deterministic control system that runs autonomously, independent of any UI or dashboard. It provides:

- **Autonomous operation**: Runs independently, survives UI failures
- **Deterministic control**: Fixed tick rate (1-5 seconds), no event-driven loops
- **Clear separation**: Control Core, State Store (Redis), Config Store (PostgreSQL)
- **Safety-first design**: Safety supervisor overrides all other logic
- **Mode-based scheduling**: Schedules change modes/setpoints, not actuators directly
- **Hardware control**: MCP23017 relay board and DFR0971 light dimming support

**Important**: The automation service does not expose any REST API for control. This prevents UI coupling and ensures autonomous operation.

## Design Goals

The automation backend is designed with the following principles:

1. **Autonomous Operation**: System runs independently of any UI, dashboard, or external automation tools
2. **Deterministic and Testable**: Fixed control loop tick rate, predictable behavior, fully testable
3. **Survive UI Failures**: Control continues even if UI crashes or is unavailable
4. **Separation of Concerns**: Clear boundaries between control logic, live state, and persistent configuration
5. **Future Expansion**: Architecture supports additional zones, sensors, and devices
6. **No External Dependencies**: No Node-RED, no REST API for control operations

## Architecture

The automation backend consists of three core components:

```
┌──────────────────────────────────────────────────────┐
│                  Automation Backend                  │
│                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────┐ │
│  │ Control Core │◄─►│ State Store │◄─►│  Config  │ │
│  │ (Logic)      │   │ (Redis)     │   │ (PostgreSQL)│
│  └──────────────┘   └──────────────┘   └──────────┘ │
│          │                    │                      │
│          ▼                    ▼                      │
│   Actuator Commands     Sensor Inputs                 │
└──────────┬────────────────────┬──────────────────────┘
           │                    │
     Field Devices        Field Devices
```

### Component Responsibilities

- **Control Core**: Owns all automation decisions, implements control theory (PID, hysteresis), implements agronomic logic (modes, seasons), enforces safety limits
- **State Store (Redis)**: Holds real-time values, decouples sensors from logic, provides fast access to current state
- **Config Store (PostgreSQL)**: Holds operator-defined parameters, survives reboots, provides audit trail via versioning

## Data Flow

### Sensor → Control

1. Sensors publish values (via MQTT/CAN/Modbus/HTTP)
2. Ingest service validates data
3. Values written to Redis State Store
4. **No control logic here** - pure data ingestion

### Control Loop Execution

Runs on a fixed tick (configurable: 1-5 seconds, default: 2 seconds):

```
Tick →
  Read state from Redis
  Read config snapshot from PostgreSQL
  Update FSM (mode transitions)
  Run PID controllers
  Apply safety constraints
  Write actuator commands
```

**This loop never blocks and never waits for UI.**

### Actuator Command Path

1. Control Core writes desired outputs (0-100%)
2. Actuator service converts abstract commands to hardware-specific actions
3. Example: Heater demand 37% → PWM / SSR / DAC output

## Core Components

### Control Core

The "brain" of the automation system. Implemented as a systemd-managed Python service.

**Key Submodules:**
- **PID Controllers**: Temperature, humidity, CO₂ control
- **Finite State Machines (FSM)**: Mode management (OFF/AUTO/MANUAL/EMERGENCY, DAY/NIGHT/TRANSITION)
- **Scheduler**: Time-based mode transitions and setpoint changes
- **Safety Supervisor**: Highest priority, overrides all other logic

**Characteristics:**
- Language: Python
- Runtime: systemd service
- Loop rate: 1-5 seconds (configurable, not event-driven)
- Always operates on validated configuration snapshots
- Snapshots are immutable until next reload

### State Store (Redis)

Live, volatile state storage. Redis is the "truth of now."

**Contains:**
- Sensor readings (temperature, humidity, CO₂, etc.)
- Computed values (error, PID output)
- Current mode (per location/cluster)
- Actuator states

**Characteristics:**
- No history guarantees
- Fast access
- Replaceable (can fall back to PostgreSQL)
- TTL-based expiration (default: 10 seconds)

**Redis Keys:**
- `sensor:*` - Sensor readings (including VPD sensors: vpd_f, vpd_b, vpd_v)
- `setpoint:{location}:{cluster}:{type}` - Current setpoints (legacy mode=NULL only, mode-based setpoints stored in PostgreSQL)
- `mode:{location}:{cluster}` - Current mode
- `device:{location}:{cluster}:{device}` - Device states

### Config Store (PostgreSQL)

Persistent configuration storage. Survives reboots and provides audit trail.

**Contains:**
- Setpoints (temperature, humidity, CO₂, VPD per location/cluster, per mode)
- Schedules (time-based mode transitions)
- PID constants (Kp, Ki, Kd per device type)
- Safety limits
- Zone definitions

**VPD Setpoints**: VPD (Vapor Pressure Deficit) is calculated from temperature and relative humidity, but controlled via dehumidifying devices (fans, extraction fans, dehumidifiers). When VPD is below setpoint, dehumidifying devices turn ON.

**Tables:**
- `setpoints` - Setpoints per location/cluster/mode (mode can be NULL for legacy setpoints). Includes temperature, humidity, CO₂, and VPD setpoints.
- `schedules` - Time-based schedules with mode field (DAY/NIGHT/TRANSITION)
- `pid_parameters` - PID constants per device type
- `config_versions` - Audit trail of all config changes (version_id, timestamp, author, comment, changes)

**VPD Control**: VPD setpoints control dehumidifying devices (fans, dehumidifiers). When current VPD (calculated from temp/RH) is below the setpoint, dehumidifying devices turn ON to increase VPD.

**Characteristics:**
- Configuration changes are explicit actions, not side effects
- All changes logged to `config_versions` table
- Changes applied atomically on next control loop tick after validation

## Control Logic Layers

The control system operates in a four-layer hierarchy:

### Layer 1: Safety (Highest Priority)

- Sensor failure detection
- Hard limits (temperature, humidity, CO₂)
- Emergency shutdown
- **Overrides everything**

### Layer 2: Modes (FSM)

- **OFF**: System disabled
- **AUTO**: Automatic control active
- **MANUAL**: Manual override active
- **EMERGENCY**: Emergency state
- **DAY / NIGHT / TRANSITION**: Time-based modes (for lighting and climate)

**Only one mode active at a time per zone.**

Mode controls device behavior:
- DAY mode → lights ON (or configured intensity)
- NIGHT mode → lights OFF
- TRANSITION mode → intermediate intensity (optional)

### Layer 3: Schedules

Defines when modes change and when setpoints change.

**Important**: Schedules do not control hardware directly. They:
- Change modes (e.g., 06:00 → DAY mode, 20:00 → NIGHT mode)
- Change setpoints (e.g., 06:00 → target 24°C, 20:00 → target 18°C)

This keeps schedules simple and safe.

### Layer 4: Controllers

- PID controllers for temperature, humidity, CO₂
- Feed-forward when applicable
- Output limits enforced

## Configuration Model

### YAML (Hardware/Static Configuration)

File: `automation_config.yaml`

**Purpose**: Hardware-specific, static configuration that rarely changes.

**Contains:**
- Hardware settings (I2C bus, addresses, simulation mode)
- Device mappings (channel assignments)
- Default PID parameters (can be overridden in PostgreSQL)
- Default setpoints (can be overridden in PostgreSQL)
- Safety limits
- Sensor mappings
- Interlock definitions

**Example:**
```yaml
hardware:
  i2c_bus: 1
  i2c_address: 0x20
  simulation: false
  
devices:
  "Flower Room":
    main:
      heater_1:
        channel: 0
        device_type: "heater"
        pid_enabled: true
        pwm_period: 100
```

### PostgreSQL (Runtime Configuration)

**Purpose**: Operator-defined parameters that change during operation.

**Tables:**

#### `setpoints`
- `location`, `cluster`, `mode` (unique together, mode can be NULL for legacy setpoints)
- `temperature`, `humidity`, `co2`, `vpd` (VPD setpoint for dehumidifying device control)
- `updated_at`

#### `schedules`
- `id`, `name`, `location`, `cluster`, `device_name`
- `start_time`, `end_time`, `day_of_week` (0-6, NULL for daily)
- `enabled`, `mode` (DAY/NIGHT/TRANSITION for mode-based scheduling)
- `created_at`

#### `pid_parameters`
- `device_type` (primary key: heater, co2, etc.)
- `kp`, `ki`, `kd`
- `updated_at`, `updated_by`, `source`

#### `config_versions`
- `version_id`, `timestamp`, `author`, `comment`
- `config_type` (setpoint, schedule, pid, safety)
- `location`, `cluster` (if applicable)
- `changes` (JSONB with actual changes)

### Redis (Live State)

**Purpose**: Fast access to current operational state.

**Keys:**
- `sensor:{sensor_name}` - Latest sensor reading
- `setpoint:{location}:{cluster}:{type}` - Current setpoint
- `mode:{location}:{cluster}` - Current mode
- `device:{location}:{cluster}:{device}` - Device state

**TTL**: 10 seconds (values expire if not refreshed)

### Config Snapshots & Versioning

**Critical Design Decision**: Configuration changes are applied atomically on the next control loop tick after validation. Mid-tick changes are never applied.

**How it works:**
1. Control Core loads a validated configuration snapshot at the start of each control tick
2. Snapshot is consistent and immutable during tick execution
3. Configuration changes do not affect control behavior until the next snapshot reload
4. All changes are logged to `config_versions` table with author, timestamp, and change details

**Benefits:**
- Deterministic behavior
- No race conditions
- Full audit trail
- Rollback capability (via config_versions history)

## Scheduling Model

### Mode-Based Scheduling

Schedules work by changing system modes and setpoints, not by directly controlling actuators.

#### Lighting Schedules

**Approach**: Schedules change to DAY/NIGHT/TRANSITION mode → mode controls lights

- **DAY mode**: Lights turn ON (or to configured intensity)
- **NIGHT mode**: Lights turn OFF
- **TRANSITION mode**: Intermediate intensity (optional, e.g., 50%)

**Example Schedule:**
- 06:00 → Change to DAY mode
- 20:00 → Change to NIGHT mode

The Control Core's FSM then applies the mode:
- DAY mode → Relay ON + DFR0971 intensity set to configured value
- NIGHT mode → Relay OFF

#### Climate Schedules

**Approach**: Schedules change setpoints and modes

**Example:**
- 06:00 → DAY mode, target temperature 24°C
- 20:00 → NIGHT mode, target temperature 18°C

The Control Core's PID controllers then work toward the new setpoints.

### Schedule Conflict Detection

The CLI tool and Control Core detect overlapping schedules for the same zone/mode:
- Same location/cluster
- Overlapping time ranges
- Same or conflicting modes
- Same day of week (or both daily)

Conflicts are rejected to prevent ambiguous behavior.

## CLI Tool

The `config_cli.py` tool provides a safe, validated interface for editing PostgreSQL configuration.

### Installation

```bash
cd Infrastructure/automation-service
chmod +x config_cli.py
```

### Usage

#### Setpoints

```bash
# Get setpoints (legacy/default mode)
config_cli.py setpoint get "Flower Room" main

# Get setpoints for specific mode
config_cli.py setpoint get "Flower Room" main --mode DAY

# Set setpoints (legacy mode, mode=NULL)
config_cli.py setpoint set "Flower Room" main --temp 24.0 --humidity 65.0 --co2 1200

# Set mode-based setpoints
config_cli.py setpoint set "Flower Room" main --mode DAY --temp 24.0 --vpd 1.2
config_cli.py setpoint set "Flower Room" main --mode NIGHT --temp 18.0 --vpd 1.5

# Set VPD setpoint (controls dehumidifying devices)
config_cli.py setpoint set "Flower Room" main --vpd 1.2

# Dry-run (validate without applying)
config_cli.py --dry-run setpoint set "Flower Room" main --temp 24.0 --vpd 1.2
```

**Note**: 
- VPD setpoint controls dehumidifying devices (fans, dehumidifiers). When VPD is below setpoint, devices turn ON.
- Mode-based setpoints allow different setpoints for DAY/NIGHT/TRANSITION modes per zone.
- Legacy setpoints (without --mode) use mode=NULL and work with existing configurations.

#### PID Parameters

```bash
# Get PID parameters
config_cli.py pid get heater

# Set PID parameters
config_cli.py pid set heater --kp 25.0 --ki 0.02 --kd 0.0

# Dry-run
config_cli.py pid set heater --kp 25.0 --ki 0.02 --kd 0.0 --dry-run
```

#### Schedules

```bash
# List schedules
config_cli.py schedule list
config_cli.py schedule list --location "Flower Room"

# Create schedule (mode-based for lighting)
config_cli.py schedule create "Day Mode" "Flower Room" main light_1 06:00 20:00 --mode DAY

# Create schedule (setpoint change for climate)
config_cli.py schedule create "Day Temp" "Flower Room" main heater_1 06:00 20:00

# Update schedule
config_cli.py schedule update 1 --mode NIGHT --start 20:00

# Delete schedule
config_cli.py schedule delete 1
```

#### Show Effective Config

```bash
# Show all configuration for a zone
config_cli.py config show "Flower Room" main
```

### Safety Features

1. **Validation**: All values validated against documented ranges
   - Setpoints: temperature (10-35°C), humidity (30-90%), CO2 (400-2000 ppm)
   - PID gains: per device type (see validation ranges in code)
   - Modes: DAY, NIGHT, TRANSITION only
   - Times: HH:MM format, valid hour/minute ranges

2. **Rejection (not clamping)**: Invalid values are rejected, not clamped to valid range

3. **Schedule Conflict Detection**: Overlapping schedules for same zone/mode are detected and rejected

4. **Dry-Run Mode**: All write operations support `--dry-run` to validate without applying

5. **Safety Boundary**: CLI never writes Redis, never interacts with actuators

6. **Audit Trail**: All changes logged to `config_versions` table with author, timestamp, and change details

### Validation Ranges

**Setpoints:**
- Temperature: 10.0 - 35.0 °C
- Humidity: 30.0 - 90.0 %
- CO2: 400.0 - 2000.0 ppm

**PID Gains (per device type):**

Heater:
- Kp: 0.0 - 100.0
- Ki: 0.0 - 1.0
- Kd: 0.0 - 10.0

CO2:
- Kp: 0.0 - 50.0
- Ki: 0.0 - 0.5
- Kd: 0.0 - 5.0

**Modes:**
- DAY, NIGHT, TRANSITION

### CLI Safety Model

- **Administrative use only**: Not for automated control or fast-changing values
- **PostgreSQL only**: Never writes Redis, never touches actuators
- **Validation first**: All inputs validated before any database operations
- **Explicit changes**: Shows diff before applying (unless dry-run)
- **Audit trail**: All changes logged with author and timestamp

## Deployment

### Systemd Service

The automation service runs as a systemd-managed Python service.

**Service File**: `automation-service.service`

```ini
[Unit]
Description=CEA Automation Service
After=postgresql.service redis-server.service
Requires=postgresql.service

[Service]
Type=simple
User=cea
WorkingDirectory=/home/antoine/Project CEA/Infrastructure/automation-service
ExecStart=/usr/bin/python3 -m app.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Installation:**
```bash
cd "/home/antoine/Project CEA/Infrastructure"
sudo cp automation-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable automation-service.service
sudo systemctl start automation-service.service
```

**Control:**
```bash
sudo systemctl status automation-service.service
sudo journalctl -u automation-service.service -f
sudo systemctl restart automation-service.service
```

### Fixed Tick Rate

The control loop runs at a fixed interval (configurable: 1-5 seconds, default: 2 seconds).

**Configuration**: Set in `automation_config.yaml`:
```yaml
control:
  update_interval: 2  # seconds
```

**Why fixed tick rate?**
- Deterministic behavior
- Predictable timing
- Easier to test and debug
- No event-driven complexity

### Startup Ordering

The service depends on:
- `postgresql.service` (Config Store)
- `redis-server.service` (State Store)

systemd enforces startup ordering automatically.

### Isolation

- Control service runs independently
- UI can crash without impact
- No REST API for control (prevents accidental UI coupling)

## Safety & Resilience

### Sensor Timeout Detection

- Sensors have TTL in Redis (default: 10 seconds)
- Missing sensor values trigger safety fallback
- Control skips affected devices if sensor unavailable

### Default Fallback Values

- Last-known-good sensor value held for configurable period (default: 30 seconds)
- After timeout, system enters SAFE mode

### Last-Known-Good Config

- Config snapshots validated before use
- Invalid config rejected, previous snapshot retained
- System continues with last valid configuration

### Watchdog Timer

- Control loop must complete within tick interval
- Stuck loops trigger watchdog
- Watchdog failure → enter EMERGENCY mode

### Graceful Degradation

**On failure:**
1. Enter SAFE mode
2. Disable aggressive actuation
3. Preserve plant safety
4. Log errors for operator review

**SAFE mode behavior:**
- All devices set to safe states
- No PID control
- Only manual overrides allowed
- Requires operator intervention to exit

## Observability

### Structured Logging

All logs are structured and include:
- Timestamp
- Component (Control Core, Safety Supervisor, etc.)
- Location/Cluster/Device context
- Action taken
- Reason/Error

**Log locations:**
- systemd journal: `journalctl -u automation-service.service -f`
- Application logs: Standard output (captured by systemd)

### Metrics

The system tracks (but does not expose via API):
- PID error (setpoint - current value)
- Output saturation (PID output at limits)
- Mode transitions (count and timing)
- Sensor freshness (time since last reading)

**History:**
- Written asynchronously to TimescaleDB
- Never blocks control loop
- Tables: `automation_state`, `control_history`

### No REST API

**Important**: The automation service does not expose any REST API for control. This is by design to:
- Prevent UI coupling
- Ensure autonomous operation
- Maintain safety boundaries
- Keep control loop deterministic

**Future**: A separate monitoring/observability service may provide read-only APIs for dashboards, but the control service itself remains API-free.

## Hardware Support

### MCP23017 Relay Control

- Controls 16 relay channels for device ON/OFF control
- I2C address: 0x20 (default, configurable)
- Supports multiple boards (if needed)

**Configuration:**
```yaml
hardware:
  i2c_bus: 1
  i2c_address: 0x20
  simulation: false  # Set to true for testing without hardware
```

**Verification:**
```bash
sudo raspi-config  # Enable I2C
i2cdetect -y 1     # Should show 0x20
```

### DFR0971 Light Dimming

- Supports multiple DFR0971 2-Channel I2C 0-10V DAC modules
- Each board has 2 channels (0 and 1)
- Up to 3 boards supported (6 channels total)
- I2C addresses: 0x58 (Veg Top and Bottom Right), 0x59 (Apache° and Veg Bottom Left), 0x5A (Chilled) (configurable via jumpers)

**Configuration:**
```yaml
hardware:
  dfr0971_boards:
    - board_id: 0
      i2c_address: 0x58
      name: "Veg Top and Bottom Right Light Board"
    - board_id: 1
      i2c_address: 0x59
      name: "Apache° and Veg Bottom Left Light Board"
    - board_id: 2
      i2c_address: 0x5A
      name: "Chilled Light Board"
```

**Device Configuration:**
```yaml
devices:
  "Flower Room":
    main:
      light_1:
        channel: 3                    # MCP23017 relay channel (ON/OFF)
        device_type: "light"
        dimming_enabled: true
        dimming_type: "dfr0971"
        dimming_board_id: 0           # DFR0971 board ID
        dimming_channel: 0            # DFR0971 channel (0 or 1)
```

**Mode-Based Control:**
- DAY mode → Relay ON + DFR0971 intensity set to configured value (e.g., 100%)
- NIGHT mode → Relay OFF
- TRANSITION mode → Relay ON + DFR0971 intensity at intermediate value (e.g., 50%)

**Implementation:**
- Based on official DFRobot GP8403 library for Raspberry Pi
- Output range: Automatically set to 0-10V on initialization (stored to EEPROM)
- Conversion: Backend automatically converts intensity percentage (0-100%) to voltage (0-10V)

### Simulation Mode

For testing without hardware:
```yaml
hardware:
  simulation: true
```

In simulation mode:
- All I2C operations are simulated
- Device states tracked in memory only
- Useful for development and testing

## Installation

1. **Install dependencies:**
```bash
cd Infrastructure/automation-service
pip install -r requirements.txt
```

2. **Configure environment variables:**
```bash
export POSTGRES_HOST="localhost"
export POSTGRES_DB="cea_sensors"
export POSTGRES_USER="cea_user"
export POSTGRES_PASSWORD="your_password"
export REDIS_URL="redis://localhost:6379"
```

3. **Configure hardware:**
```bash
cp automation_config.yaml.template automation_config.yaml
# Edit automation_config.yaml with your device mappings
```

4. **Install systemd service:**
```bash
cd "/home/antoine/Project CEA/Infrastructure"
sudo cp automation-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable automation-service.service
sudo systemctl start automation-service.service
```

## Troubleshooting

### Hardware Issues

- Verify I2C is enabled: `sudo raspi-config`
- Check I2C devices: `i2cdetect -y 1`
- Verify MCP23017 is at address 0x20
- Use simulation mode for testing: `simulation: true` in config

### Database Connection Issues

- Check TimescaleDB is running: `sudo systemctl status postgresql`
- Verify connection settings in environment variables
- Service will retry connection with exponential backoff

### Redis Connection Issues

- Service automatically falls back to TimescaleDB if Redis unavailable
- Check Redis is running: `sudo systemctl status redis-server`
- Redis is optional but recommended for performance

### Control Loop Issues

- Check logs: `journalctl -u automation-service.service -f`
- Verify tick rate is appropriate (1-5 seconds)
- Check for sensor timeouts (missing sensor data)
- Verify config snapshots are loading correctly

## Development

### Testing

Start with simulation mode:
```yaml
hardware:
  simulation: true
```

Test CLI tool:
```bash
./config_cli.py setpoint get "Flower Room" main
./config_cli.py setpoint set "Flower Room" main --temp 24.0 --dry-run
```

### Adding New Devices

1. Add device to `automation_config.yaml`:
```yaml
devices:
  "Location":
    cluster:
      device_name:
        channel: X
        device_type: "heater" | "fan" | "co2" | etc.
        pid_enabled: true | false
        pwm_period: 100  # Optional
```

2. Restart service:
```bash
sudo systemctl restart automation-service.service
```

## REST API & WebSocket

The automation service provides REST API endpoints for configuration management and a WebSocket endpoint for real-time updates.

### REST API Endpoints

**Setpoints:**
- `GET /api/setpoints/{location}/{cluster}?mode={mode}` - Get setpoints (optional mode parameter)
- `GET /api/setpoints/{location}/{cluster}/all-modes` - Get all setpoints for all modes
- `POST /api/setpoints/{location}/{cluster}` - Update setpoints (supports mode and vpd fields)

**PID Parameters:**
- `GET /api/pid/parameters/{device_type}` - Get PID parameters
- `POST /api/pid/parameters/{device_type}` - Update PID parameters

**Schedules:**
- `GET /api/schedules` - List all schedules
- `POST /api/schedules` - Create schedule
- `PUT /api/schedules/{id}` - Update schedule
- `DELETE /api/schedules/{id}` - Delete schedule

**Devices:**
- `GET /api/devices` - Get all devices
- `GET /api/devices/{location}/{cluster}` - Get devices for location/cluster

**Modes:**
- `GET /api/mode/{location}/{cluster}` - Get current mode
- `POST /api/mode/{location}/{cluster}` - Set mode

### WebSocket Endpoint

- `WebSocket /ws` - Real-time updates for sensor data, device states, and mode changes

**Message Types:**
- `sensor_update` - Sensor value changed
- `device_update` - Device state changed
- `mode_update` - Mode changed
- `initial_state` - Initial state on connection

## Integration

The automation service integrates with:
- **TimescaleDB**: Shared database for sensor data and automation state
- **Redis**: Real-time sensor values and state
- **CAN Worker**: Publishes sensor data to Redis
- **Soil Sensor Service**: Publishes sensor data to Redis
- **Frontend**: React frontend for configuration and monitoring (served from `Infrastructure/frontend/dist/`)

**Note**: The automation service does not integrate with Node-RED or any external automation tools. It operates autonomously.

## Summary

The greenhouse automation backend is a deterministic control system with clear separation between live state (Redis), persistent configuration (PostgreSQL), and control logic (Control Core). It runs independently of any UI and uses scheduled mode transitions, PID-based controllers, and safety supervision to manage environmental conditions.

**Key Principles:**
- Deterministic control with fixed tick rate
- Separation of concerns (control, state, config)
- Safety first (safety supervisor overrides all)
- Mode-based operation (schedules change modes, not actuators)
- Autonomous operation (no UI dependency)
- Config snapshots (immutable during tick execution)
- Atomic config updates (applied on next tick after validation)
