# ProjectCEA - Comprehensive Agent Guidelines

**Generated:** 2026-02-08 | **Commit: Archiving completed plans** | **Branch:** main

## EXECUTIVE OVERVIEW

ProjectCEA is a production-grade Controlled Environment Agriculture (CEA) automation system representing the intersection of IoT precision agriculture, real-time control systems, and data-driven cultivation optimization. Built on Raspberry Pi 5, this system demonstrates enterprise-grade architecture principles applied to agricultural automation.

### Core Mission

**Primary Objective**: Maximize crop yield and quality through precision environmental control while generating comprehensive datasets for AI model training and predictive analytics.

**Technical Philosophy**: Data-first approach with deterministic real-time control, hardware abstraction, and fault-tolerant design patterns.

### System Scale & Complexity

- **6 Microservices**: Python FastAPI with specialized responsibilities
- **2 Grow Rooms**: Flower and Vegetative with independent control loops
- **3 Sensor Networks**: CAN bus, RS485 Modbus, HTTP APIs
- **Real-time Control**: 1-5 second deterministic control loop
- **Data Resolution**: 1Hz sampling maintained for 1 year, then compressed
- **Hardware Interface**: 22 controllable channels (16 relays + 6 PWM)

---

## COMPREHENSIVE SYSTEM STRUCTURE

```
ProjectCEA/
├── 📁 Infrastructure/           # All production services (see Infrastructure/AGENTS.md)
│   ├── 🚀 automation-service/   # Control loop + frontend (8001)
│   ├── 📊 backend/              # Sensor API + WebSocket (8000)
│   ├── 📡 can-processor-service/# CAN → Redis/DB ingestion
│   ├── 🌱 soil-sensor-service/  # RS485 Modbus → Redis/DB (8002)
│   ├── 🌤️ weather-service/      # External API → Redis/DB (8003)
│   ├── 🎨 frontend/             # React SPA + Grafana dashboards
│   ├── 🗄️ database/             # TimescaleDB schema + migrations
│   ├── 📋 shared/               # Common libraries and utilities
│   ├── 🔧 scripts/             # Deployment and maintenance scripts
│   └── ⚙️ *.service             # Systemd unit files
├── 🔌 Sensor_Nodes/             # ESP32 firmware (see Sensor_Nodes/AGENTS.md)
│   └── 📱 ESP32/
│       ├── 🌸 fullV6/           # Latest stable firmware
│       └── 🔬 experimental/     # Development versions
├── 🧠 .sisyphus/                # AI context and planning
│   ├── 📖 PROJECT_CONTEXT.md    # Technical architecture summary
│   ├── ⚙️ USER_PREFERENCES.md    # Non-negotiable requirements
│   ├── 📋 plans/                # Implementation roadmaps
│   ├── 📂 archive/              # Archived implementation plans
│   └── 📝 drafts/               # Work-in-progress documentation
├── 🚀 deploy.sh                 # Production deployment orchestrator
├── 🔙 rollback.sh               # Fast rollback (<30s guarantee)
├── 📊 ARCHITECTURE.md            # Canonical system documentation
├── 📈 ARCHITECTURE_SCHEMATIC.md # Mermaid diagrams + tables
└── 🤖 AGENTS.md                 # This file - AI assistant guidelines
```

---

## CRITICAL SYSTEM ARCHITECTURE

### Data Flow Architecture (Non-Negotiable Pattern)

```
🌡️  Sensors (CAN/Modbus/HTTP)
    ↓ (250kbps CAN / RS485 / API)
📡 Ingestion Services (can-processor, soil-sensor, weather)
    ↓ (instant Redis + batched DB)
💾 Redis (live state) + TimescaleDB (historical)
    ↓ (<1ms Redis reads)
🎛️  Control Loop (automation-service, 1-5s tick)
    ↓ (I2C commands)
⚡ Actuators (MCP23017 relays + DFR0971 dimming)
    ↓ (state updates)
💾 Redis + TimescaleDB (state persistence)
    ↓ (WebSocket + REST APIs)
🖥️  Frontend (React SPA) + Grafana Analytics
```

### Performance Requirements (Hard SLAs)

| Metric | Requirement | Measurement Point |
|--------|-------------|-------------------|
| **Control Loop Latency** | ≤5 seconds max | Sensor read → Actuator response |
| **Target Control Latency** | 1-2 seconds | Normal operation |
| **Sensor Update Rate** | 1Hz (1-second) | All sensor types |
| **Redis Operations** | <1ms | GET/SET operations |
| **Database Batch Delay** | ≤100ms | 50-message threshold |
| **WebSocket Updates** | ≤1 second | Data change → UI update |
| **API Response (95th)** | <200ms | REST endpoint responses |
| **System Uptime** | 99.9% | Monthly availability |
| **Recovery Time** | <30 seconds | Automated rollback |

---

## WHERE TO LOOK - COMPREHENSIVE GUIDE

### Primary Documentation (Read First)

| Priority | Document | Purpose | Location |
|----------|----------|---------|----------|
| **🔴 CRITICAL** | **`ARCHITECTURE.md`** | **Complete system narrative + ASCII schematic** | Project root |
| **🔴 CRITICAL** | **`ARCHITECTURE_SCHEMATIC.md`** | **Mermaid diagrams + structured tables** | Project root |
| **🟡 HIGH** | **This `AGENTS.md`** | **AI assistant instructions + context** | Project root |
| **🟡 HIGH** | **`.sisyphus/PROJECT_CONTEXT.md`** | **Technical summary for quick reference** | .sisyphus/ |

### Service-Specific Documentation

| Service Area | Location | Focus |
|---------------|----------|-------|
| **🎛️ Control Logic** | `Infrastructure/automation-service/app/control/` | PID, scheduling, device control |
| **📊 Sensor API** | `Infrastructure/backend/app/routes/` | Data retrieval + WebSocket |
| **📡 CAN Processing** | `Infrastructure/can-processor-service/app/` | Message decoding + distribution |
| **🗄️ Database Schema** | `Infrastructure/database/` | TimescaleDB structure + queries |
| **🎨 Frontend UI** | `Infrastructure/frontend/src/` | React components + state management |
| **📈 Grafana Dashboards** | `Infrastructure/frontend/grafana/dashboards/` | Analytics + visualization |
| **🔧 ESP32 Firmware** | `Sensor_Nodes/ESP32/fullV6/` | Sensor node implementation |
| **⚙️ Deployment** | `deploy.sh`, `rollback.sh` | Production deployment automation |

### Configuration & Validation

| Component | Location | Validation Method |
|------------|----------|-------------------|
| **Hardware Config** | `automation_config.yaml` | Pydantic schema validation |
| **Service Config** | Individual `app/config.py` | Environment validation |
| **Database Schema** | `Infrastructure/database/cea_schema.sql` | TimescaleDB validation |
| **Frontend Build** | `Infrastructure/frontend/package.json` | npm/yarn validation |
| **Systemd Services** | `*.service` files | systemd syntax checking |

---

## NON-NEGOTIABLE SYSTEM RULES

### Real-Time Constraints (Critical)

| Rule | Reason | Violation Impact |
|------|--------|-----------------|
| **1/sec sampling minimum** | AI training requires full resolution data | Model accuracy degradation |
| **100ms max DB batch delay** | Live Redis instant, DB can buffer | Control loop starvation |
| **1-5s control tick max** | Deterministic environmental control | Crop stress, yield loss |
| **<1ms Redis operations** | Control loop performance requirement | Actuator latency |

### Control Algorithm Requirements (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **VPD is master controller** | VPD → humidity setpoint cascade | Physiological plant response |
| **Humidity is slave to VPD** | Tracks VPD-derived targets | Prevents over/under-humidification |
| **Safety interlocks mandatory** | Heating failure → exhaust inhibition | Crop protection |
| **No hardcoded setpoints** | Database-driven configuration | Flexibility, traceability |

### Data Management Rules (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **Query DB with time filters** | Always include time range | Prevent full table scans |
| **Use aggregates for long ranges** | <1h: raw, 1-3h: 1min, 3-24h: 5min, >24h: hourly | Query performance |
| **Never hourly aggregates for <7d** | Hides critical dynamics | Analysis accuracy |
| **Full TDD required** | All new code needs tests | System reliability |

### Repository Pattern Architecture (Critical)

**Database Access Layer**:
- **Repository Pattern**: All data operations go through specialized repository classes
- **ControlAction Repository**: Handles control action logging and retrieval
- **Device Repository**: Manages device states and hardware configurations
- **PID Repository**: Stores and retrieves PID controller parameters and tuning data
- **RoomMode Repository**: Controls room operational modes and transitions
- **Schedule Repository**: Manages light schedules and photoperiod controls
- **Sensor Repository**: Handles sensor data validation and storage
- **Setpoint Repository**: Manages environmental setpoints and targets
- **Config Repository**: System configuration and parameter storage

**DatabaseManager Facade**:
- **Pure Facade Pattern**: DatabaseManager now only provides connection management
- **No Direct Data Operations**: All queries handled by dedicated repositories
- **Connection Pool Management**: Centralized database connection handling
- **Transaction Coordination**: Manages cross-repository transactions when needed

**Type Checking Requirements**:
- **pyright Strict Mode**: All services must pass strict type checking (0 errors)
- **Type Coverage**: 100% type annotation coverage required for new code
- **Interface Compliance**: Repository interfaces must be fully typed
- **Runtime Type Safety**: Pydantic models for all data structures

**File Organization**:
```
Infrastructure/automation-service/app/repositories/
├── control_action.py      # ControlAction repository
├── device.py             # Device repository  
├── pid.py                # PID repository
├── room_mode.py          # RoomMode repository
├── schedule/             # Schedule repository (new directory)
│   ├── __init__.py
│   ├── models.py         # Schedule data models
│   ├── repository.py     # Schedule repository implementation
│   └── routes.py         # Schedule API routes
├── sensor.py             # Sensor repository
├── setpoint.py           # Setpoint repository
└── config.py             # Config repository
```

### Hardware Rules (Critical)

| Rule | Hardware Mapping | Reason |
|------|------------------|--------|
| **MCP23017 = relays only** | I2C bus 0, address 0x27, channels 0-15 | Digital on/off control |
| **DFR0971 = dimming only** | I2C bus 1, addresses 0x88/0x89/0x90 | Analog 0-10V control |
| **Never swap roles** | MCP for dimming or DFR for relays | Hardware capability limits |
| **Bus separation mandatory** | Different I2C buses for relay/dimming | Prevent interference |

### Operational Rules (Critical)

| Rule | Implementation | Reason |
|------|----------------|--------|
| **Rollback <30s** | `./rollback.sh` automation | Minimize crop stress |
| **No bare excepts** | Proper exception handling | System reliability |
| **Config validation required** | Service startup fails on invalid config | Prevent runtime errors |
| **Never touch working systems** | Unless explicitly requested | Production stability |

---

## ADVANCED TOPICS - DEEP DIVE

### VPD Cascade Control Architecture

**Physiological Foundation**: VPD represents the evaporative demand on plant stomata, directly controlling transpiration rates, nutrient uptake, and photosynthetic efficiency.

**Implementation Details**:
- **Master Controller**: VPD PID computes target humidity based on temperature and leaf temperature delta
- **Slave Controller**: Humidity PID tracks VPD-derived setpoint
- **Temperature Compensation**: Automatic adjustment for temperature changes
- **Growth Stage Optimization**: Different VPD targets for propagation (0.8-1.0 kPa), vegetative (1.0-1.2 kPa), flowering (1.2-1.5 kPa)

**Mathematical Model**:
```
VPD = SVP_air - (RH/100 × SVP_leaf)
SVP = 0.6108 × exp((17.27 × T)/(T + 237.3))  # Tetens formula
Leaf_temp = Air_temp + user_delta (-1.5°C to -3°C typical)
```

### Self-Tuning PID Implementation

**Relay Feedback Auto-Tuning**:
1. **Oscillation Induction**: Apply relay control around setpoint
2. **Parameter Extraction**: Measure ultimate gain (Ku) and period (Tu)
3. **Tuning Rules**: Apply Ziegler-Nichols or SIMC formulas
4. **Continuous Refinement**: Performance-based parameter adjustment

**Neural-PID Data Collection**:
- Every control action logs: timestamp, setpoint, PV, error, PID output, Kp/Ki/Kd values
- Performance metrics: settling time, overshoot, steady-state error
- Equipment response: actual device state and latency
- Future ML model training dataset

**Anti-Windup and Filtering**:
- Integral clamping during output saturation
- Derivative kick elimination on setpoint changes
- Low-pass filtering for derivative noise reduction
- Deadband implementation to prevent hunting

### Light Schedule and Ramp Management

**Photoperiod Architecture**:
- **Sun Period**: Lights ON with configurable intensity ramping (0-240 minutes)
- **Moon Period**: Lights OFF (0% intensity)
- **Transition Periods**: Optional PRE_DAY/PRE_NIGHT for gradual setpoint changes

**Time-Based Ramp Recovery**:
- Intensity = min(100, (elapsed_time / ramp_duration) × 100)
- Service restarts resume based on elapsed time, not stored intensity
- Each light device maintains separate ramp state
- Intensity never undefined: either computed or 0%

### Safety Systems and Interlocks

**Heating Failure Protection**:
```
IF heating_active AND temperature < (setpoint - 2°C):
    inhibit_exhaust_fan()
    trigger_heating_failure_alarm()
    maintain_safe_minimum_temperature()
```

**Equipment Conflict Prevention**:
- Mutual exclusion: heating/cooling, humidification/dehumidification
- Priority hierarchy: safety > production > efficiency > comfort
- CO2 enrichment protection during exhaust operation

**Sensor Validation**:
- Plausible range checking: temp -10°C to 50°C, RH 10-100%, CO2 0-5000ppm
- Spike detection: max 5°C/minute, 10% RH/minute rate of change
- Cross-validation between redundant sensors
- Data quality flags: GOOD, UNCERTAIN, BAD, MISSING

---

## DEVELOPMENT WORKFLOW & BEST PRACTICES

### Code Development Standards

**Python Services**:
- **Type Hints**: Full type annotation using Python 3.9+ syntax
- **Type Checking**: pyright strict mode enforced for all services (achieved 0 LSP errors)
- **Repository Pattern**: All data access uses dedicated repository classes (ControlAction, Device, PID, RoomMode, Schedule, Sensor, Setpoint, Config)
- **Database Architecture**: DatabaseManager is now a pure facade, repositories handle data operations
- **Async/Await**: All I/O operations must be asynchronous
- **Error Handling**: Structured exception handling with specific error types
- **Logging**: Structured JSON logging with correlation IDs
- **Testing**: pytest with >80% coverage requirement

**React Frontend**:
- **TypeScript**: Strict mode with no implicit any
- **Component Structure**: Functional components with hooks
- **State Management**: React Query for server state, useState for local state
- **Styling**: Tailwind CSS with component-scoped classes
- **Testing**: Jest + React Testing Library

### Git Workflow

**Branch Strategy**:
- **main**: Production-ready code, always deployable
- **develop**: Integration branch for feature completion
- **feature/***: Individual feature development
- **hotfix/***: Critical production fixes

**Commit Standards**:
- **Format**: Conventional Commits (feat:, fix:, docs:, etc.)
- **Scope**: Include service/component affected
- **Description**: Clear, concise change description
- **Testing**: Include test coverage indicator

### Deployment Pipeline

**Development Environment**:
1. **Local Development**: `/home/antoine/ProjectCEA/`
2. **Service Testing**: Individual service unit and integration tests
3. **System Testing**: Full stack integration tests
4. **Code Review**: Peer review with checklist verification

**Production Deployment**:
1. **Build Phase**: `./deploy.sh` handles compilation and packaging
2. **Atomic Switch**: Symlink update ensures instant rollback
3. **Service Restart**: Ordered service restart with health checks
4. **Verification**: Automated health checks and manual verification
5. **Monitoring**: Enhanced monitoring during deployment window

### Troubleshooting Methodology

**Systematic Approach**:
1. **Check Logs**: `journalctl -u <service> -f` for recent errors
2. **Verify Dependencies**: Redis, PostgreSQL, I2C hardware status
3. **Test Configuration**: `automation_config.yaml` validation
4. **Check Data Flow**: Redis keys, database entries, API responses
5. **Hardware Verification**: `i2cdetect`, CAN bus status, sensor connectivity

**Common Failure Patterns**:
- **Control Loop Stall**: Check Redis connectivity and I2C hardware
- **Sensor Data Missing**: Verify CAN processor and ESP32 nodes
- **Database Performance**: Check query optimization and indexes
- **Frontend Issues**: Verify build assets and API connectivity

---

## COMMAND REFERENCE & QUICK START

### Essential Commands

```bash
# System Operations
./deploy.sh                    # Deploy new version with verification
./rollback.sh                   # Emergency rollback (<30s guarantee)
./restart_all_services.sh       # Restart all services in correct order

# Service Management
systemctl status automation-service  # Check control service status
systemctl restart cea-backend       # Restart sensor API service
journalctl -u can-processor -f       # Monitor CAN processor logs

# Database Operations
psql -U cea -d projectcea             # Connect to TimescaleDB
redis-cli                             # Connect to Redis for debugging
./Infrastructure/automation-service/config_cli.py setpoint get "Flower Room" main

# Hardware Testing
i2cdetect -y 0                        # Scan I2C bus 0 (relays)
i2cdetect -y 1                        # Scan I2C bus 1 (dimming)
candump can0                          # Monitor CAN bus traffic
```

### Configuration Management

**YAML Configuration Structure**:
```yaml
hardware:
  mcp_i2c_bus: 0
  dfr0971_i2c_bus: 1
  dfr0971_boards:
    - board_id: "DFR0971_1"
      reference: "DFR0971"
      address: 0x88
      channels: [0, 1]

control:
  update_interval: 2  # 1-5 seconds only
  
zones:
  - location: "Flower Room"
    clusters:
      - cluster: "main"
        devices:
          - device_name: "exhaust_fan"
            device_type: "relay"
            channel: 0
```

### API Testing Examples

```bash
# Test backend health
curl http://localhost:8000/health

# Get current sensor values
curl http://localhost:8000/api/sensors/Flower\ Room/main

# Test automation service
curl http://localhost:8001/api/health

# Get device states
curl http://localhost:8001/api/devices/Flower\ Room/main

# Manual device control
curl -X POST http://localhost:8001/api/devices/Flower\ Room/main/exhaust_fan \
  -H "Content-Type: application/json" \
  -d '{"state": true, "mode": "auto"}'
```

---

## PERFORMANCE MONITORING & OPTIMIZATION

### Key Performance Indicators

**Control System Metrics**:
- **Loop Execution Time**: Must be <100ms per tick
- **Sensor Freshness**: All sensors <5 seconds old
- **Actuator Response**: <500ms from command to hardware
- **Setpoint Tracking**: <0.5°C temperature, <2% humidity error

**Data Processing Metrics**:
- **CAN Message Latency**: <10ms from reception to Redis
- **Database Query Time**: <200ms for 95th percentile
- **WebSocket Update Rate**: 1Hz maximum frequency
- **API Response Time**: <100ms average, <500ms maximum

**System Health Metrics**:
- **Memory Usage**: <80% of available RAM per service
- **CPU Usage**: <50% average, <80% peak
- **Disk Usage**: <90% with automatic cleanup
- **Network Latency**: <1ms internal, <100ms external

### Optimization Strategies

**Database Optimization**:
- **Query Patterns**: Always use time range filters
- **Index Usage**: Composite indexes on (time, location, cluster)
- **Compression**: Enable for data older than 30 days
- **Connection Pooling**: Optimize pool size for concurrency

**Application Optimization**:
- **Async Operations**: Never block the event loop
- **Caching**: Redis for frequently accessed data
- **Batch Processing**: Group database writes efficiently
- **Memory Management**: Monitor for leaks and optimize usage

---

## SECURITY & COMPLIANCE

### Security Hardening

**Network Security**:
- **Service Isolation**: Internal network only for inter-service communication
- **Firewall Rules**: Restrict access to necessary ports only
- **API Authentication**: JWT tokens for sensitive operations
- **Rate Limiting**: Prevent abuse and DoS attacks

**System Security**:
- **User Privileges**: Dedicated `cea` user with minimal permissions
- **File Permissions**: Restrictive permissions (600/644/755)
- **Service Hardening**: systemd security profiles enabled
- **Secret Management**: Environment variables for sensitive data

**Data Security**:
- **Database Access**: Service accounts only, no direct user access
- **Redis Security**: Password authentication and network restrictions
- **Log Security**: No sensitive data in application logs
- **Backup Security**: Encrypted backups with secure storage

### Compliance Requirements

**Agricultural Standards**:
- **GAP Compliance**: Good Agricultural Practices adherence
- **Food Safety**: HACCP principles for environmental monitoring
- **Organic Standards**: Support for organic certification requirements
- **Traceability**: Complete audit trail for all control decisions

**Technical Standards**:
- **ISO 9001**: Quality management system compliance
- **IEC 61131**: Industrial control system standards
- **IEEE 802.3**: Ethernet networking compliance
- **CAN Bus**: ISO 11898 standard compliance

---

## FUTURE ROADMAP & EVOLUTION

### Scalability Planning

**Multi-Room Expansion**:
- **Horizontal Scaling**: Independent automation instances per room
- **Centralized Management**: Cross-room coordination and load balancing
- **Resource Isolation**: Separate control loops and data storage
- **Unified Analytics**: Cross-facility data aggregation and analysis

**Technology Evolution**:
- **Container Orchestration**: Docker + Kubernetes migration path
- **Edge Computing**: Distributed processing at sensor nodes
- **Cloud Integration**: Hybrid cloud for analytics and backup
- **AI/ML Integration**: Advanced predictive control algorithms

### Advanced Features

**Predictive Analytics**:
- **Machine Learning Models**: Environmental optimization algorithms
- **Anomaly Detection**: Unsupervised learning for system health
- **Neural-PID Control**: Deep learning replacement for traditional PID
- **Computer Vision**: Canopy analysis and growth stage detection

**Enhanced Sensor Networks**:
- **Wireless Integration**: LoRaWAN for remote monitoring
- **Imaging Systems**: Hyperspectral and thermal cameras
- **Chemical Analysis**: Nutrient solution monitoring
- **Environmental Mapping**: Spatial sensor distribution

---

## EMERGENCY PROCEDURES

### Critical Incident Response

**System Failure Response**:
1. **Assessment**: Determine scope and impact of failure
2. **Isolation**: Isolate affected components to prevent cascade
3. **Recovery**: Execute rollback or recovery procedures
4. **Verification**: Confirm system stability and data integrity
5. **Documentation**: Record incident and lessons learned

**Emergency Rollback**:
```bash
# Immediate rollback (<30 seconds)
./rollback.sh

# Verify system status
systemctl status automation-service cea-backend
./Infrastructure/automation-service/config_cli.py health
```

**Data Recovery Procedures**:
- **Redis Recovery**: Restart service, data repopulates from sensors
- **Database Recovery**: TimescaleDB point-in-time recovery if needed
- **Configuration Recovery**: Git history for configuration files
- **Hardware Recovery**: I2C device reset and reinitialization

### Communication Protocols

**Alert Escalation**:
1. **Level 1**: Automated system alerts and notifications
2. **Level 2**: Technical team notification and investigation
3. **Level 3**: Management notification for extended outages
4. **Level 4**: External communication for customer impact

**Documentation Requirements**:
- **Incident Reports**: Detailed analysis of root causes
- **Change Logs**: Complete record of all system modifications
- **Performance Metrics**: Baseline and post-incident comparisons
- **Improvement Actions**: Preventive measures and process updates

---

## REFERENCE MATERIALS

### External Documentation

**Technology Documentation**:
- **FastAPI**: https://fastapi.tiangolo.com
- **React**: https://react.dev
- **TimescaleDB**: https://docs.timescale.com
- **Redis**: https://redis.io/docs
- **Raspberry Pi**: https://www.raspberrypi.org/documentation

**Hardware Specifications**:
- **MCP23017**: Microchip I2C I/O expander datasheet
- **DFR0971**: DFRobot PWM dimming board documentation
- **ESP32**: Espressif ESP32 technical reference
- **CAN Bus**: ISO 11898 standard documentation

### Internal References

**Service Documentation**:
- `Infrastructure/AGENTS.md` - Service-specific details
- `Infrastructure/automation-service/AGENTS.md` - Control system
- `Infrastructure/backend/AGENTS.md` - Sensor API
- `Infrastructure/database/AGENTS.md` - Database schema
- `Infrastructure/frontend/AGENTS.md` - React application

**Configuration Files**:
- `automation_config.yaml` - Hardware and zone configuration
- `pyproject.toml` - Python dependencies and build config
- `package.json` - Frontend dependencies and scripts
- `*.service` - Systemd service definitions

---

## CONCLUSION

This comprehensive documentation serves as the definitive reference for ProjectCEA system architecture, operation, maintenance, and evolution. The system represents a sophisticated implementation of modern IoT principles applied to precision agriculture, with enterprise-grade reliability, performance, and scalability.

**Key Success Factors**:
- **Rigorous adherence to non-negotiable system rules**
- **Comprehensive monitoring and observability**
- **Systematic approach to troubleshooting and maintenance**
- **Continuous improvement through data-driven optimization**
- **Preparedness for emergency situations and rapid recovery**

For specific implementation details, refer to the individual service documentation and code comments. Always prioritize system stability and crop safety when making operational decisions.

---

*Last updated: 2026-02-08 - Updated documentation to reflect repository pattern refactoring, pyright type checking enforcement, and archived completed plans.*
