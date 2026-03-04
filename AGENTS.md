# ProjectCEA - Comprehensive Agent Guidelines

**Generated:** 2026-02-08 | **Branch:** main

## ⚠️ CRITICAL URLS - READ THIS FIRST

| Service | URL | Notes |
|---------|-----|-------|
| **Dashboard (Frontend)** | `http://mothernode:8001` | Main CEA dashboard |
| **Grafana** | `http://iskradocker:3000` | **NOT localhost:3000!** |
| **Backend API** | `http://mothernode:8000` | Sensor data |
| **Automation API** | `http://mothernode:8001` | Control & config |
| **Weather** | `http://mothernode:8003` | Weather service |

**Grafana is on iskradocker, NOT localhost!**

---

## COMPREHENSIVE SYSTEM STRUCTURE

```
ProjectCEA/
├── 📁 Infrastructure/           # All production services (see Infrastructure/AGENTS.md)
│   ├── 🚀 automation-service/   # Control loop + frontend (8001)
│   ├── 📊 backend/              # Sensor API + WebSocket (8000)
│   ├── 📡 can-processor-service/# CAN ingestion
│   ├── 🌱 soil-sensor-service/  # RS485 Modbus ingestion
│   ├── 🌤️ weather-service/      # External API ingestion
│   ├── 🎨 frontend/             # React SPA + Grafana
│   ├── 🗄️ database/             # TimescaleDB schema
│   ├── 📋 shared/               # Common libraries
│   └── 🔧 scripts/             # Maintenance scripts
├── 🔌 Sensor_Nodes/             # ESP32 firmware (see Sensor_Nodes/AGENTS.md)
├── 🧠 .sisyphus/                # AI context and planning
├── 📊 ARCHITECTURE.md            # Canonical system documentation
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
