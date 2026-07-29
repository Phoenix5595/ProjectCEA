# ProjectCEA – System Architecture

**Last updated (deployed):** 2026-07-12 — Schedule architecture redesign: photoperiod from mode_parameters, per-light intensity from light_target_intensity, light_programs for supplemental/override; removed runtime synthesis, per-device SUN/MOON rows, room_schedule rows, and dead SchedulesMixin Redis code.

**Plan-style schematic:** **`ARCHITECTURE_SCHEMATIC.md`** (Mermaid + tables). Update both when the architecture changes and a deploy is done.

---

## Executive Summary

ProjectCEA is a sophisticated Controlled Environment Agriculture (CEA) automation system built on Raspberry Pi 5, designed for precision cultivation of high-value crops in two grow rooms. The system represents a production-grade implementation of IoT principles, combining real-time sensor networks, autonomous control algorithms, and comprehensive data analytics for optimal plant growth conditions.

### Core Design Philosophy

- **Data-First Approach**: Every decision is backed by high-resolution data (1Hz sampling) collected for AI model training and predictive analytics
- **Deterministic Control**: Hard real-time constraints (1-5s control loop) ensure consistent environmental conditions
- **Hardware Abstraction**: Clean separation between control logic and physical actuators enables flexible hardware evolution
- **Fault Tolerance**: Multiple redundancy layers and graceful degradation ensure system reliability
- **Scalable Architecture**: Microservices design allows independent scaling and maintenance of system components

---

## System Scope & Capabilities

### Primary Functions

1. **Environmental Control**: Autonomous regulation of temperature, humidity, VPD (Vapor Pressure Deficit), CO2 levels, and light intensity
2. **Data Acquisition**: High-frequency sensor data collection from multiple modalities (CAN bus, RS485 Modbus, HTTP APIs)
3. **Real-time Monitoring**: Live dashboard with WebSocket updates for immediate system state visibility
4. **Historical Analysis**: Long-term data storage with compression and aggregation for trend analysis
5. **Safety Management**: Interlock systems and fail-safe mechanisms to prevent crop damage
6. **Remote Management**: Web-based configuration and manual override capabilities

### Technical Specifications

- **Control Frequency**: 1-5 second control loop (configurable, max 5s non-negotiable)
- **Data Resolution**: 1Hz sampling rate maintained for 1 year, then compressed
- **Sensor Network**: 3 ESP32 CAN nodes + RS485 soil sensor network + 2× 1-Wire DS18B20 (lab temp, water temp)
- **Actuator Control**: 16-channel relay system + 6-channel PWM dimming
- **Data Storage**: Redis (real-time) + TimescaleDB (historical)
- **User Interface**: React SPA with real-time WebSocket updates
- **Deployment**: Atomic deployment with <30s rollback capability

---

## Detailed System Architecture

### Hardware Layer

#### Sensor Network Infrastructure

**ESP32 CAN Nodes (Primary Sensors)**
- **Flower Back (_b)**: Rear environmental monitoring in flower room
- **Flower Front (_f)**: Front environmental monitoring in flower room
- **Veg Main (_v)**: Primary environmental monitoring in vegetative room
- **Communication**: CAN bus at 250kbps for deterministic, real-time data transmission
- **Sensor Suite per Node**:
  - 2x PT100 temperature sensors (dry bulb, wet bulb)
  - SCD30 CO2 sensor (400-2000ppm range)
  - BME280 environmental sensor (temperature, humidity, pressure)
  - VL53L0X distance sensor (optional canopy height)

**RS485 Modbus Network (Soil & Environmental)**
- **soil-sensor-service**: Dedicated service for soil parameter monitoring
- **Sensor Types**: Soil temperature, moisture, EC, pH
- **Communication**: Modbus RTU over RS485 for noise immunity
- **Topology**: Multi-drop configuration supporting up to 32 devices

**1-Wire DS18B20 (Lab / Water)**
- **onewire-worker**: Reads DS18B20 probes on GPIO 24 (1-Wire)
- **Probes**: Lab ambient temperature (`lab_temp`), water temperature (`water_temperature`)
- **Config**: `onewire_config.yaml` maps device id (e.g. `28-1be2d445e7ac`) to logical name
- **Data**: Published to Redis `sensor:lab_temp`, `sensor:water_temperature` (10s TTL); displayed in Dashboard Lab section

**External Data Sources**
- **weather-service**: HTTP-based integration with external weather APIs (METAR)
- **Primary Source**: Quebec City (CYQB Jean Lesage International Airport)
- **Parameters**: Ambient temperature, humidity, pressure, wind; displayed in automation dashboard header (top-right)
- **Purpose**: External context for energy management and predictive control

#### Actuator Control Hardware

**MCP23017 Relay Control System**
- **Function**: On/off control for HVAC equipment, fans, pumps
- **Interface**: I2C bus 0, address 0x27
- **Channels**: 16 independently controllable relays (0-15)
- **Load Capacity**: 10A @ 250VAC per channel
- **Applications**: Heating elements, exhaust fans, circulation fans, water pumps

**DFR0971 PWM Dimming System**
- **Function**: 0-10V analog control for LED lighting systems
- **Interface**: I2C bus 1, addresses 0x88, 0x89, 0x90
- **Channels**: 6 dimming channels (3 boards × 2 channels each)
- **Resolution**: 8-bit PWM (256 levels) converted to 0-10V output
- **Applications**: LED grow light intensity control

### Data Processing Layer

#### Ingestion Services

**can-processor-service**
- **Purpose**: High-throughput CAN message decoding and distribution
- **Processing Pipeline**:
  1. CAN frame reception at 250kbps
  2. Message decoding (sensor type, node ID, data payload)
  3. Value extraction and validation
  4. Multi-path data distribution:
     - Redis state keys (instant, <1ms latency)
     - Redis stream (recent history buffer)
     - TimescaleDB queue (batched, ≤100ms delay)
- **Performance**: 10,000 message queue capacity, 50-message batch threshold
- **Reliability**: Async processing with error recovery and retry logic

**soil-sensor-service**
- **Purpose**: RS485 Modbus sensor polling and data normalization
- **Polling Strategy**: Configurable intervals (typically 30-60 seconds)
- **Data Processing**: Raw Modbus registers → engineering units
- **Error Handling**: Device timeout detection, automatic retry with exponential backoff

**onewire-worker**
- **Purpose**: 1-Wire DS18B20 temperature probes (lab ambient, water temp) on GPIO 24
- **Polling Strategy**: ~1 s interval; reads `/sys/bus/w1/devices/28-*/temperature`
- **Config**: `onewire_config.yaml` maps device id → logical name (`lab_temp`, `water_temperature`)
- **Data**: Redis state keys only (sensor:lab_temp, sensor:water_temperature, 10s TTL); optional stream/DB in future

**weather-service**
- **Purpose**: External weather data acquisition and caching
- **API Integration**: RESTful calls to weather service providers
- **Cache Strategy**: Redis-based caching with 15-minute TTL
- **Data Transformation**: Standardized units and format for internal use

### Data Storage Architecture

#### Redis Real-time Data Store

**Data Organization**:
- **`sensor:{sensor_name}`**: Current sensor values (10s TTL)
  - Example: `sensor:dry_bulb_f_main` → 23.5
  - Purpose: Sub-millisecond access for control loop
- **`sensor:{sensor_name}:ts`**: Timestamp for each sensor value (10s TTL)
  - Example: `sensor:dry_bulb_f_main:ts` → 1706589123456
  - Purpose: Data freshness validation
- **`sensor:raw`**: Redis stream for recent sensor history
  - Configuration: MAXLEN 100,000 entries (~28 hours at 1Hz)
  - Purpose: Recent data queries without DB access
- **`automation:*`**: Device states and control outputs (10s TTL)
  - Example: `automation:flower_room:main:exhaust_fan` → ON
  - Purpose: Current system state for dashboard and control logic
- **`effective_setpoint:*`**: Current target values (no TTL)
  - Example: `effective_setpoint:flower_room:main:temperature` → 24.0
  - Purpose: Persistent setpoint storage
  - **Per-dimmer light** (DFR0971): `effective_setpoint:{loc}:{cluster}:light:{device_name}:effective_intensity` (and `:nominal_intensity`, `:ramp_progress_light`) so multiple fixtures in one cluster do not overwrite each other in Redis.

**Performance Characteristics**:
- **Read Latency**: <1ms for GET operations
- **Write Latency**: <1ms for SET operations
- **Memory Usage**: ~50MB for full system state
- **Persistence**: AOF (Append-Only File) with fsync every second

#### TimescaleDB Historical Data Store

**Schema Design**:
- **`measurement`**: Primary hypertable for all sensor data
  - Time partitioning: 1-day chunks (optimal for 4.3M datapoints/day)
  - Columns: time, sensor_id, value, location, cluster, quality_flag
  - Retention: 2 years full resolution, then compression
- **`measurement_1min`**: 1-minute continuous aggregates
  - Functions: avg, min, max, count, stddev
  - Refresh interval: Every minute
  - Purpose: 1-3 hour time range queries
- **`measurement_5min`**: 5-minute continuous aggregates
  - Functions: avg, min, max, count, stddev
  - Refresh interval: Every 5 minutes
  - Purpose: 3-24 hour time range queries
- **`measurement_hourly`**: Hourly continuous aggregates
  - Functions: avg, min, max, count, stddev
  - Refresh interval: Every hour
  - Purpose: >24 hour time range queries
- **`effective_setpoints`**: Setpoint change history
  - Purpose: Control algorithm analysis and debugging
  - **Per-light Grafana curves** use **`effective_light_intensity`** here (throttled DB logger from the automation loop). ZoneConfig **live CUR** reads **`light:{location}:{cluster}:{device_name}`** in Redis first; those paths are intentionally different.
- **`automation_state`**: Device state change history
  - Purpose: Equipment runtime analysis and maintenance scheduling

**Performance Optimizations**:
- **Compression**: 10-20x reduction for data older than 30 days
- **Chunking**: 1-day time chunks for efficient partition pruning
- **Indexes**: Composite indexes on (time, location, cluster, sensor_id)
- **Query Optimization**: Automatic aggregate selection based on time range

### API & Control Layer

#### cea-backend (Port 8000) - Sensor Data Gateway

**Core Responsibilities**:
- **Historical Data API**: RESTful endpoints for time-series data retrieval
- **Real-time Data API**: Live sensor values from Redis state keys
- **WebSocket Service**: Real-time push notifications to connected clients
- **Data Aggregation**: Intelligent query routing between raw and aggregate data
- **Authentication**: JWT-based token validation for API access

**API Endpoints Structure**:
```
GET  /api/sensors/{location}/{cluster}           # Current sensor values
GET  /api/sensors/{location}/{cluster}/history   # Historical data
GET  /api/live/{location}                        # Live dashboard data
POST /api/sensors/batch                          # Batch sensor queries
WS   /ws/{location}                             # WebSocket live updates
```

**Query Optimization Strategy**:
- **< 1 hour**: Raw measurement table (full 1Hz resolution)
- **1-3 hours**: 1-minute aggregates (balanced detail/performance)
- **3-24 hours**: 5-minute aggregates (optimized for dashboard)
- **> 24 hours**: Hourly aggregates (long-term trend analysis)

**WebSocket Implementation**:
- **Connection Management**: Automatic reconnection with exponential backoff
- **Message Types**: sensor_update, device_change, system_alert
- **Broadcast Strategy**: Location-based message routing
- **Performance**: 1-second polling interval, batched message delivery

#### automation-service (Port 8001) - Control System Core

**Control Loop Architecture**:
1. **Sensor Data Acquisition**: Redis `sensor:*` key retrieval (<1ms latency)
2. **Configuration Loading**: Database snapshot of zones, devices, setpoints
3. **Scheduler Processing**: Time-based mode determination and setpoint calculation. Photoperiod comes from `mode_parameters` (room-level, single source of truth). Per-light intensity comes from `light_target_intensity` table (mode-specific). Light programs (`light_programs` table) provide supplemental/override schedules. No runtime synthesis — all data is pre-loaded into Scheduler caches at startup.
4. **Control Algorithm Execution**: PID controllers + VPD cascade logic
5. **Safety Interlock Evaluation**: Equipment protection and failure detection
6. **Device Command Generation**: Relay and PWM output calculations
7. **State Persistence**: Redis and TimescaleDB state updates

**Advanced Control Features**:
- **VPD Cascade Control**: VPD master controller adjusts humidity setpoints
- **Self-Tuning PID**: Relay feedback auto-tuning with Ziegler-Nichols rules
- **Light Schedule Management**: Sun/moon periods with configurable ramping
- **Mode Transitions**: Smooth transitions between DAY/NIGHT (sun/moon photoperiod)
- **Deadband Control**: Prevents equipment hunting with configurable thresholds
- **Anti-Windup Protection**: Integral term clamping during output saturation

**Safety & Interlock Systems**:
- **Heating Failure Detection**: Inhibits exhaust during heating failure
- **Temperature Limits**: Hard limits prevent extreme conditions
- **Equipment Conflict Prevention**: Mutually exclusive device control
- **Sensor Validation**: Plausible range checking and spike detection
- **Fail-Safe Defaults**: Known safe states on system failure

**Configuration Management**:
- **YAML Configuration**: `automation_config.yaml` with Pydantic validation
- **Runtime Validation**: Channel conflicts and hardware compatibility checks
- **Hot Reload**: Configuration changes without service restart (when possible)
- **Version Control**: Git-tracked configuration with change audit trail

**Frontend Hosting**:
- **Static File Service**: React SPA build artifacts (`dist/` directory)
- **API Proxy**: CORS-enabled proxy to backend services
- **Asset Optimization**: Gzip compression and cache headers
- **Development Mode**: Hot module replacement during development

### User Interface Layer

#### React Frontend Application

**Architecture Pattern**: Single Page Application (SPA) with component-based design

**Core Pages**:
- **Dashboard**: Real-time system overview with live sensor displays
- **Zone Configuration**: Room-level settings and device management
- **Setpoint Management**: Target values and schedule configuration
- **Light Control**: Photoperiod and intensity management
- **System Status**: Service health and diagnostic information

**Key Components**:
- **Real-time Charts**: Recharts-based time-series visualization
- **Device Controls**: Manual override interfaces for all equipment
- **Schedule Editor**: Circular time picker for light schedules
- **Alert System**: Toast notifications for system events
- **Data Tables**: Sortable, filterable historical data views

**State Management**:
- **React Query**: Server state synchronization and caching
- **Local State**: Component-level state with useState/useReducer
- **WebSocket Integration**: Real-time updates via custom hooks
- **Persistence**: User preferences in localStorage

**Performance Optimizations**:
- **Code Splitting**: Route-based lazy loading
- **Memoization**: React.memo and useMemo for expensive calculations
- **Virtual Scrolling**: Large dataset rendering optimization
- **Debounced Updates**: Prevents excessive API calls

#### Grafana Analytics Platform

**Dashboard Suite**:
- **flower_sector**: Primary flower room environmental monitoring
- **flower_sector_soil**: Soil parameters and irrigation metrics
- **veg_sector**: Vegetative room comprehensive overview
- **system_performance**: Service health and resource utilization

**Data Source Configuration**:
- **PostgreSQL Connection**: Direct TimescaleDB access
- **Query Optimization**: Time-based aggregate selection
- **Variable Templates**: Dynamic room and device selection
- **Annotation Support**: Manual event marking and automated alerts

**Visualization Features**:
- **Time Series Panels**: Multi-axis sensor correlation
- **Heat Maps**: Spatial temperature distribution
- **Gauge Charts**: Current value displays with thresholds
- **Table Panels**: Detailed data export and analysis

---

## Service Communication Matrix

| Service | Port | Protocol | Data Stores | Dependencies |
|---------|------|----------|-------------|--------------|
| can-processor | — | CAN/Redis | Redis, TimescaleDB | CAN hardware |
| soil-sensor-service | 8002 | Modbus/Redis | Redis, TimescaleDB | RS485 hardware |
| onewire-worker | 8004 | 1-Wire/Redis | Redis (state only) | GPIO 24, 1-Wire probes |
| weather-service | 8003 | HTTP/Redis | Redis, TimescaleDB | External APIs |
| cea-backend | 8000 | HTTP/WebSocket | Redis, TimescaleDB | Redis, TimescaleDB |
| automation-service | 8001 | HTTP | Redis, TimescaleDB | Redis, TimescaleDB, I2C hardware |

### Data Flow Patterns

**Real-time Control Path** (<5ms total latency):
```
CAN Frame → can-processor → Redis SET → automation-service GET → Control Algorithm → I2C Write
```

**Historical Data Path** (≤100ms latency):
```
Sensor Data → can-processor → Queue → Batch Write → TimescaleDB → API Response
```

**UI Update Path** (≤1s latency):
```
Sensor Change → Redis → Backend WebSocket → Frontend React → Re-render
```

### Layer responsibilities & frontend data-fetching

- **Ingestion (`can-processor-service`)**: `DataWriter` must keep Redis/stream writes on the hot path; TimescaleDB writes go through the batch queue. Device and sensor ID resolution uses in-memory caches; batch flush should **prefetch** missing IDs (or bulk-resolve) so the flush loop does not execute repeated per-row `SELECT` lookups on cache misses.
- **Automation (`automation-service`)**: `ControlEngine` coordinates the control tick (sensor read → climate/setpoint resolution → device processing → telemetry). `DeviceProcessor` applies per-cluster device and PID logic. Refactors should **extract named phases** (e.g. hierarchy cache, effective-setpoint logging) behind small modules or protocols without changing loop timing or safety ordering.
- **Frontend (React SPA)**: **Weather** (Quebec City / CYQB) is loaded where the UI consumes it (e.g. `Dashboard` header interval). **`useSensorPolling`** owns initial devices, bulk setpoint keys, per-zone live sensors, and control history — it must **not** issue HTTP calls whose responses are unused (no duplicate weather or system-status fetches unless results update hook state). **`useSystemStatus`** owns automation-service `/api/status` for the system panel. **Manual light flows** should call **`getSchedules` once per zone** and derive per-light day targets from that list, not refetch schedules per light.

---

## Advanced Control Algorithms

### VPD (Vapor Pressure Deficit) Cascade Control

**Physiological Basis**: VPD represents the drying power of air on plant stomata, directly influencing transpiration rates and nutrient uptake. Maintaining optimal VPD is more critical than absolute humidity control.

**Cascade Architecture**:
- **Master Controller**: VPD PID controller computes target humidity
- **Slave Controller**: Humidity PID controller tracks VPD-derived setpoint
- **Temperature Independence**: VPD automatically adjusts for temperature changes

**VPD Calculation**:
```
VPD = SVP_air - (RH/100 × SVP_leaf)
Where:
- SVP_air = Saturation vapor pressure at air temperature
- SVP_leaf = Saturation vapor pressure at leaf temperature
- Leaf temp = Air temp + user-defined delta (typically -1.5°C to -3°C)
```

**Growth Stage Presets**:
- **Propagation**: 0.8-1.0 kPa (high humidity for cuttings)
- **Vegetative**: 1.0-1.2 kPa (balanced growth)
- **Flowering**: 1.2-1.5 kPa (enhanced metabolic activity)

### Self-Tuning PID Implementation

**Relay Feedback Method**:
1. **Oscillation Induction**: Apply on-off control around setpoint
2. **Parameter Extraction**: Measure ultimate gain (Ku) and period (Tu)
3. **Tuning Rule Application**: Ziegler-Nichols or SIMC formulas
4. **Continuous Refinement**: Performance-based parameter adjustment

**Data Logging for Neural-PID**:
- **Control Actions**: Timestamp, setpoint, process variable, error
- **PID Parameters**: Current Kp, Ki, Kd values
- **Performance Metrics**: Settling time, overshoot, steady-state error
- **Equipment State**: Actual device response and latency

**Anti-Windup and Derivative Filtering**:
- **Integral Clamping**: Prevents windup during output saturation
- **Derivative Kick Elimination**: Setpoint change filtering
- **Low-Pass Filtering**: Reduces derivative noise sensitivity

### Light Schedule Architecture (3-Concept Model)

**Concept 1: Photoperiod (from `mode_parameters`)**
- **Source**: `mode_parameters.day_start_time` and `mode_parameters.night_start_time` (per room, per mode)
- **Overnight-capable**: `day_start_time` can be > `night_start_time` (e.g., veg mode day_start=16:00, night_start=10:00 → 18h overnight photoperiod from 16:00 to 10:00 next day)
- **Scheduler**: `is_in_photoperiod()` reads from cached mode_parameters and handles overnight wrap
- **Failsafe**: Missing mode_parameters → returns True (lights ON at 10%, never darkness) + CRITICAL alarm

**Concept 2: Per-Light Intensity (from `light_target_intensity`)**
- **Source**: `light_target_intensity` table — `(device_id, mode_id) → target_intensity`
- **Mode-specific**: Different intensities for veg vs flower modes
- **Default**: 10% hardcoded failsafe if no row exists (visible low light, not darkness) + WARNING alarm
- **Deprecated**: `mode_parameters.main_light_intensity` / `supplemental_light_intensity` columns still exist but are no longer read by the Scheduler

**Concept 3: Light Programs (from `light_programs`)**
- **Purpose**: Supplemental (adds light during dark) and override (replaces intensity during sun) programs
- **Modes**: Time-slot mode (start_time + end_time, overnight wrap supported) or cycle mode (on/off pulses within window)
- **Resolution**: Priority-based (highest wins, ties broken by created_at ASC)
- **Scope**: Device-level or room-level; mode-specific or all modes

**Ramp Calculation Algorithm**:
```
intensity = min(100, (elapsed_time / ramp_duration) × 100)
Where:
- elapsed_time = time_since_schedule_start
- ramp_duration = mode_parameters.light_ramp_up_minutes / light_ramp_down_minutes
- Intensity never undefined: 10% default if config missing, 0% during dark
```

**Time-Based State Recovery**:
- **Service Restart**: Ramps resume based on elapsed time, not stored intensity
- **Startup Gate**: `asyncio.Event` prevents control loop from ticking until all Scheduler caches are loaded
- **Device Independence**: Each light device maintains separate ramp state; program ramps use separate state keys from photoperiod ramps

**Non-Light Device Schedules**:
- **DAY/NIGHT rows** in `schedules` table control ON/OFF enable for heaters, fans, dehumidifiers
- **Climate periods** (`climate_periods` table) drive temperature, humidity, CO2 setpoints independently of light schedule

## Safety Systems & Interlocks

### Heating Failure Protection

**Problem Scenario**: Heater failure causes temperature drop, humidity rises, VPD decreases. Traditional humidity control would activate exhaust, worsening the heating failure.

**Safety Logic**:
```
IF heating_active AND temperature < (setpoint - 2°C):
    inhibit_exhaust_fan()
    trigger_heating_failure_alarm()
    maintain_safe_minimum_temperature()
```

### Equipment Conflict Prevention

**Mutually Exclusive Operations**:
- **Heating/Cooling**: Prevents simultaneous operation
- **Humidification/Dehumidification**: Avoids wasteful opposition
- **CO2 Enrichment/Exhaust**: Maintains CO2 efficiency

**Priority Hierarchy**:
1. **Safety Critical**: Heating failure, extreme temperatures
2. **Production Critical**: CO2 enrichment during light periods
3. **Efficiency**: Equipment runtime optimization
4. **Comfort**: Minor environmental fluctuations

### Sensor Validation and Data Quality

**Plausible Range Checking**:
- **Temperature**: -10°C to 50°C operational range
- **Humidity**: 10% to 100% relative humidity
- **CO2**: 0 to 5000 ppm (extended range for safety)
- **VPD**: 0 to 5 kPa (physiological limits)

**Spike Detection**:
- **Rate of Change**: Maximum 5°C/minute, 10% RH/minute
- **Outlier Detection**: 3-sigma deviation from recent values
- **Sensor Cross-Validation**: Correlation between redundant sensors

**Data Quality Flags**:
- **GOOD**: Validated and within range
- **UNCERTAIN**: Minor anomalies, use with caution
- **BAD**: Failed validation, exclude from control
- **MISSING**: No recent data, use backup sensors

## Hardware Configuration & Validation

### I2C Bus Architecture

**Bus Separation Strategy**:
- **Bus 0**: MCP23017 relay control (address 0x27)
  - 16 digital output channels (0-15)
  - 10A load capacity per channel
  - Opto-isolated inputs for safety monitoring
- **Bus 1**: DFR0971 dimming control (addresses 0x88, 0x89, 0x90)
  - 6 PWM channels (3 boards × 2 channels)
  - 0-10V analog output for LED control
  - 8-bit resolution (256 levels)

**Configuration Validation**:
```yaml
hardware:
  mcp_i2c_bus: 0        # Must be 0-7
  dfr0971_i2c_bus: 1     # Must be 0-7, different from MCP
  dfr0971_boards:
    - board_id: "DFR0971_1"
      reference: "DFR0971"
      address: 0x88
      channels: [0, 1]
```

**Startup Validation Process**:
1. **I2C Bus Scan**: Verify device presence with `i2cdetect`
2. **Channel Uniqueness**: Ensure no duplicate channel assignments
3. **Board Compatibility**: Validate dimming board references
4. **Load Capacity**: Verify power requirements within limits

### Device Configuration Schema

**Device Types and Capabilities**:
- **relay**: On/off control (heaters, fans, pumps)
- **dimming**: Variable control (LED lights, variable speed fans)
- **sensor**: Input devices (temperature, humidity, CO2)
- **input**: Digital inputs (limit switches, status indicators)
- **output**: Analog outputs (control signals)

**Channel Assignment Rules**:
- **Relay Channels**: 0-15, unique per room/cluster
- **Dimming Channels**: 0-5 per board, globally unique
- **Sensor Channels**: Virtual, based on sensor ID
- **Validation**: Pydantic models enforce constraints at startup

## Deployment & Operations

### Production Deployment Architecture

**Directory Structure**:
```
/opt/projectcea/
├── current/                    # Active release (symlink)
├── releases/
│   ├── 20240130-123456-abc123/  # Timestamped releases
│   ├── 20240129-154321-def456/
│   └── ...
├── shared/                      # Common configuration
└── logs/                        # Service logs
```

**Atomic Deployment Process**:
1. **Build Phase**: Compile frontend, create virtual environments
2. **Sync Phase**: Rsync Infrastructure/ to release directory
3. **Test Phase**: Configuration validation, service health checks
4. **Switch Phase**: Atomic symlink update to new release
5. **Restart Phase**: Service restart in dependency order
6. **Verify Phase**: Health checks and rollback if needed

**Rollback Strategy**:
- **Target**: <30 seconds from detection to recovery
- **Process**: Symlink switch + service restart
- **Automation**: `./rollback-deploy.sh` handles symlink + service restart
- **Safety**: Previous release always available for instant rollback

### Service Management

**Systemd Service Dependencies**:
```
postgresql.service
redis-server.service
    ↓
can-setup.service (oneshot)
    ↓
can-processor.service
soil-sensor-service.service
onewire-worker.service
weather-service.service
    ↓
cea-backend.service (8000)
automation-service.service (8001)
```

**Service Configuration**:
- **User**: Dedicated `cea` system user (no root privileges)
- **Resource Limits**: Memory caps and CPU affinity
- **Restart Policy**: Automatic restart on failure with backoff
- **Logging**: Structured JSON logs with rotation
- **Security**: NoNewPrivileges, PrivateTmp, ProtectSystem

### Monitoring & Observability

**Health Check Endpoints**:
- **Backend**: `GET /health` - Database and Redis connectivity
- **Automation**: `GET /api/health` - Control loop status and hardware
- **Frontend**: Built asset verification and API connectivity

**Performance Metrics**:
- **Control Loop Latency**: Time from sensor read to actuator command
- **Database Query Performance**: Query times and optimization status
- **Memory Usage**: Service memory consumption and leaks
- **Hardware Response**: I2C communication latency and errors

**Alert Conditions**:
- **Control Loop Missed**: >5 second interval violation
- **Sensor Stale**: >30 seconds without updates
- **Hardware Failure**: I2C communication errors
- **Database Connection**: Service unable to reach data stores

## Development & Maintenance

### Code Architecture Patterns

**Microservice Design Principles**:
- **Single Responsibility**: Each service has a focused purpose
- **Loose Coupling**: Services communicate via well-defined APIs
- **High Cohesion**: Related functionality grouped within services
- **Interface Segregation**: Minimal, specific API contracts

**Database Access Patterns**:
- **Connection Pooling**: Asyncpg with configured pool size
- **Query Optimization**: Prepared statements and proper indexing
- **Transaction Management**: Atomic operations for data consistency
- **Error Handling**: Graceful degradation and retry logic

**Configuration Management**:
- **Environment Separation**: Dev/staging/production configurations
- **Secret Management**: Environment variables for sensitive data
- **Validation**: Pydantic models for configuration schema
- **Documentation**: Inline comments and external specification

### Testing Strategy

**Unit Testing**:
- **Control Algorithms**: PID controller behavior and edge cases
- **Data Processing**: Sensor validation and transformation
- **Configuration**: Schema validation and error handling
- **Hardware Abstraction**: I2C driver mocking and simulation

**Integration Testing**:
- **Service Communication**: API contract validation
- **Database Operations**: Data persistence and retrieval
- **Hardware Simulation**: End-to-end control loop testing
- **Error Scenarios**: Failure recovery and system resilience

**Performance Testing**:
- **Load Testing**: High-frequency sensor data processing
- **Stress Testing**: Resource exhaustion and recovery
- **Latency Testing**: Control loop timing requirements
- **Longevity Testing**: Extended operation stability

### Troubleshooting Guide

**Common Issues and Solutions**:

**Control Loop Not Responding**:
1. Check service status: `systemctl status automation-service`
2. Verify Redis connectivity: `redis-cli ping`
3. Review configuration validation: `journalctl -u automation-service`
4. Check I2C hardware: `i2cdetect -y 0 && i2cdetect -y 1`

**Sensor Data Missing**:
1. Verify CAN processor: `journalctl -u can-processor`
2. Check Redis keys: `redis-cli keys sensor:*`
3. Test sensor nodes: Physical inspection and CAN bus analysis
4. Review data quality flags in database

**Lights Not Responding**:
1. Check DFR0971 status: `curl http://localhost:8001/api/hardware/dfr0971/status`
2. Verify dimming board configuration in YAML
3. Test I2C communication: `i2cdetect -y 1`
4. Review light schedule and ramp calculations

**Database Performance Issues**:
1. Check query performance: TimescaleDB query analyzer
2. Verify compression status: Hypertable compression policies
3. Review index usage: PostgreSQL EXPLAIN ANALYZE
4. Monitor disk space: Database storage and retention policies

**Frontend Not Loading**:
1. Check automation-service: `curl http://localhost:8001/`
2. Verify build assets: `ls -la Infrastructure/frontend/dist/`
3. Review API connectivity: Browser developer tools
4. Check WebSocket connection: Network tab inspection

---

## Future Architecture Evolution

### Scalability Roadmap

**Multi-Room Expansion**:
- **Horizontal Scaling**: Additional automation instances per room
- **Centralized Management**: Cross-room coordination and load balancing
- **Resource Isolation**: Independent control loops per room
- **Data Federation**: Unified analytics across multiple facilities

**AI/ML Integration**:
- **Predictive Control**: Machine learning models for environmental optimization
- **Anomaly Detection**: Unsupervised learning for system health monitoring
- **Neural-PID**: Deep learning replacement for traditional PID control
- **Computer Vision**: Canopy analysis and growth stage detection

**Advanced Sensor Networks**:
- **Wireless Sensors**: LoRaWAN integration for remote monitoring
- **Imaging Systems**: Hyperspectral and thermal camera integration
- **Chemical Analysis**: Nutrient solution monitoring and automation
- **Environmental Mapping**: Spatial sensor distribution and interpolation

### Technology Migration Path

**Container Orchestration**:
- **Dockerization**: Service containerization for deployment consistency
- **Kubernetes**: Container orchestration for scaling and management
- **Service Mesh**: Advanced inter-service communication and observability
- **GitOps**: Automated deployment and configuration management

**Edge Computing**:
- **Distributed Processing**: Local processing at sensor nodes
- **Edge Gateways**: Intermediate processing and data filtering
- **Offline Operation**: Graceful degradation without internet connectivity
- **Data Synchronization**: Conflict resolution and state consistency

**Cloud Integration**:
- **Data Analytics**: Cloud-based processing for large-scale analysis
- **Model Training**: GPU-accelerated machine learning pipelines
- **Remote Monitoring**: Centralized multi-site management
- **Backup and Recovery**: Cloud-based disaster recovery solutions
6. Device commands → RelayManager (MCP23017) and DFR0971 (dimming); state → Redis `automation:*` and TimescaleDB.

**Tick interval**: 1–5 s (configurable; non-negotiable max 5 s).

---

## Hardware Configuration Summary

### Raspberry Pi 5 Specifications
- **Processor**: Broadcom BCM2712, Quad-core ARM Cortex-A76 @ 2.4GHz
- **Memory**: 8GB LPDDR4X-4267
- **Storage**: 512GB NVMe SSD (not SD card for reliability)
- **Interfaces**: 2× USB 3.0, 2× HDMI, Gigabit Ethernet, Wi-Fi 4, Bluetooth 5.0
- **GPIO**: 40-pin header with I2C, SPI, UART, PWM capabilities

### I2C Hardware Mapping
- **MCP23017 (Relays)**: I2C bus 0, address 0x27, 16 channels (0–15)
- **DFR0971 (Dimming)**: I2C bus 1, addresses 0x88/0x89/0x90, 6 channels total
- **Bus Separation**: Prevents interference between digital and analog control

### CAN Bus Configuration
- **Speed**: 250kbps (deterministic, noise-immune)
- **Topology**: Multi-drop with 120Ω termination resistors
- **Nodes**: 3 ESP32 microcontrollers with integrated CAN controllers
- **Protocol**: Custom 11-bit identifier format for sensor type and node ID

---

## Performance Benchmarks & SLAs

### Control Loop Performance
- **Maximum Latency**: 5 seconds from sensor read to actuator response
- **Target Latency**: 1-2 seconds under normal operation
- **Sensor Update Rate**: 1Hz (1-second intervals)
- **Control Algorithm Execution**: <10ms per tick
- **Hardware Response Time**: <100ms for I2C operations

### Data Processing Performance
- **CAN Message Processing**: <1ms per message
- **Redis Operations**: <1ms for GET/SET operations
- **Database Batch Writes**: ≤100ms delay, 50-message batches
- **WebSocket Updates**: ≤1 second from data change to UI update
- **API Response Times**: <200ms for 95th percentile

### System Reliability Targets
- **Uptime**: 99.9% availability (8.76 hours downtime/month max)
- **MTBF**: Mean time between failures >30 days
- **MTTR**: Mean time to repair <30 minutes (automated rollback)
- **Data Loss**: Zero data loss with redundant storage
- **Recovery Time**: <30 seconds for automated rollback

---

## Security Considerations

### Network Security
- **Service Isolation**: Services communicate within internal network only
- **API Authentication**: JWT-based token validation for sensitive operations
- **CORS Configuration**: Restricted to trusted origins only
- **Rate Limiting**: API endpoints protected against abuse

### System Security
- **User Privileges**: Dedicated `cea` user with minimal permissions
- **File Permissions**: Restrictive file system permissions (600/644)
- **Service Hardening**: Systemd security profiles (NoNewPrivileges, PrivateTmp)
- **Secret Management**: Environment variables for sensitive configuration

### Data Security
- **Database Access**: Restricted to service accounts only
- **Redis Security**: Password authentication and network restrictions
- **Log Security**: No sensitive data in log files
- **Backup Security**: Encrypted backups with secure storage

---

## Compliance & Standards

### Agricultural Standards
- **GAP (Good Agricultural Practices)**: Environmental control compliance
- **Organic Certification**: Chemical-free growing environment support
- **Food Safety**: HACCP principles for environmental monitoring

### Technical Standards
- **ISO 9001**: Quality management system adherence
- **IEC 61131**: Industrial control system standards
- **IEEE 802.3**: Ethernet networking compliance
- **CAN Bus**: ISO 11898 standard compliance

### Data Standards
- **GDPR Compliance**: Personal data protection (if applicable)
- **Data Retention**: Configurable retention policies
- **Data Portability**: Standardized export formats
- **Audit Trail**: Complete change logging and accountability

---

## Reference Documentation

### Key File Locations
| Document | Purpose | Location |
|----------|---------|----------|
| **System Architecture** | Complete technical overview | `ARCHITECTURE.md` (project root) |
| **Plan-Style Schematic** | Mermaid diagrams + tables | `ARCHITECTURE_SCHEMATIC.md` (project root) |
| **Agent Guidelines** | AI assistant instructions | `AGENTS.md` (project root) |
| **Infrastructure Details** | Service-specific documentation | `Infrastructure/AGENTS.md` |
| **Control System** | PID, scheduling, device control | `Infrastructure/automation-service/AGENTS.md` |
| **Database Schema** | TimescaleDB structure and queries | `Infrastructure/database/AGENTS.md` |
| **Frontend Architecture** | React components and state management | `Infrastructure/frontend/AGENTS.md` |

### Configuration Files
| File | Purpose | Validation |
|------|---------|------------|
| `automation_config.yaml` | Hardware and zone configuration | Pydantic schema validation |
| `pyproject.toml` | Python dependencies and build config | Poetry/toml validation |
| `package.json` | Frontend dependencies and build scripts | npm/yarn validation |
| `*.service` | Systemd service definitions | systemd syntax validation |

### Command Reference
```bash
# System Operations
./deploy.sh                    # Deploy new version
./rollback-deploy.sh            # Rollback to previous release (see deploy_state.json)
./restart_all_services.sh       # Restart all services in order

# Service Management
systemctl status automation-service
systemctl restart cea-backend
journalctl -u can-processor -f

# Database Operations
psql -U cea -d projectcea       # Connect to TimescaleDB
redis-cli                       # Connect to Redis
./Infrastructure/automation-service/config_cli.py setpoint get "Flower Room" main

# Hardware Testing
i2cdetect -y 0                  # Scan I2C bus 0 (relays)
i2cdetect -y 1                  # Scan I2C bus 1 (dimming)
candump can0                    # Monitor CAN bus traffic
```

---

## Archive & Version History

Previous versions of this documentation are stored in **`archive/`** at project root with dated filenames:
- `ARCHITECTURE_2026-01-30.md` - Enhanced comprehensive documentation
- `ARCHITECTURE_2026-01-15.md` - Initial production documentation
- `ARCHITECTURE_SCHEMATIC_2026-01-30.md` - Updated Mermaid diagrams

When updating architecture documentation after a deploy:
1. Copy current files to `archive/` with timestamp
2. Update both `ARCHITECTURE.md` and `ARCHITECTURE_SCHEMATIC.md`
3. Bump **Last updated (deployed)** timestamp
4. Commit changes with descriptive message

---

## Quick Reference for Developers

### Getting Started
1. **Read First**: `ARCHITECTURE.md` for complete system understanding
2. **Setup**: Follow `Infrastructure/README.md` for development environment
3. **Services**: Use `./restart_all_services.sh` for proper startup order
4. **Testing**: Run `pytest` in individual service directories

### Common Development Tasks
- **Add New Sensor**: Update can-processor decoder + database schema
- **Modify Control Logic**: Edit `Infrastructure/automation-service/app/control/`
- **Update Frontend**: Modify React components in `Infrastructure/frontend/src/`
- **Change Database**: Create migration in `Infrastructure/database/migrations/`
- **Add API Endpoint**: Update routes in respective service `app/routes/`

### Debugging Checklist
- [ ] Check service logs: `journalctl -u <service> -f`
- [ ] Verify configuration: `automation_config.yaml` validation
- [ ] Test hardware: `i2cdetect` for I2C devices
- [ ] Check data flow: Redis keys + database entries
- [ ] Monitor performance: Control loop timing requirements

---

*This comprehensive documentation serves as the definitive reference for ProjectCEA system architecture, operation, and maintenance. For specific implementation details, refer to the individual service documentation and code comments.*
