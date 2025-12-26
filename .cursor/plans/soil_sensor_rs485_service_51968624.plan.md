---
name: Soil Sensor RS485 Service
overview: Create a new FastAPI microservice to monitor DFRobot RS485 4-in-1 soil sensors (temperature, humidity, EC, pH) via MODBUS-RTU protocol. The service will read from GPIO UART through a MAX13487 RS485 transceiver board, store data in TimescaleDB, and publish to Redis for other services. Initially supports 1 sensor with configurable bed assignment (Front Bed or Back Bed), designed to scale to multiple sensors.
todos: []
---

# Soil Sensor RS485 Service Implementation

## Overview

Create a new standalone FastAPI service (`soil-sensor-service`) that monitors DFRobot RS485 4-in-1 soil sensors via MODBUS-RTU protocol. The service reads sensor data from GPIO UART (through a MAX13487 RS485 transceiver board), stores it in TimescaleDB following the existing schema, and publishes updates to Redis for other services to consume.

## Hardware Requirements

**Required Components:**

- Raspberry Pi (with GPIO UART enabled)
- **MAX13487 RS485 Transceiver Board** (or compatible RS485 transceiver)
                - Converts TTL-level UART signals to RS485 differential signals
                - Required because Raspberry Pi GPIO UART is TTL (3.3V), not RS485
- DFRobot RS485 4-in-1 Soil Sensor (SEN0604)
- RS485 cable (A/B wires) connecting sensor to transceiver board
- 120Ω termination resistors (one at each end of RS485 bus, if multiple sensors)

**Connection Diagram:**

```
Raspberry Pi GPIO UART (TX/RX pins)
    ↓
MAX13487 RS485 Transceiver Board
    ↓ (RS485 A/B differential signals)
RS485 Cable
    ↓
DFRobot Soil Sensor
```

**Note:** The Raspberry Pi GPIO UART alone cannot directly communicate with RS485 devices. The MAX13487 transceiver board is essential hardware that converts between TTL UART and RS485 differential signaling.

## Architecture

```
Infrastructure/
└── soil-sensor-service/
    ├── app/
    │   ├── __init__.py
    │   ├── main.py                 # FastAPI app entry point
    │   ├── config.py                # Configuration loader (YAML)
    │   ├── database.py              # Database manager (TimescaleDB)
    │   ├── redis_client.py          # Redis publisher
    │   ├── modbus_rtu.py            # MODBUS-RTU protocol implementation
    │   ├── soil_sensor_reader.py    # Soil sensor reading logic
    │   ├── background_tasks.py      # Background polling task
    │   └── routes/
    │       ├── __init__.py
    │       ├── status.py            # Health/status endpoints
    │       └── sensors.py           # Sensor data endpoints
    ├── soil_sensor_config.yaml      # Sensor configuration (beds, Modbus IDs)
    ├── requirements.txt
    ├── README.md
    └── soil-sensor-service.service  # systemd service file
```

## Key Components

### 1. Configuration (`soil_sensor_config.yaml`)

YAML configuration file defining:

- RS485 serial port (e.g., `/dev/serial0` for GPIO UART via MAX13487)
- Baudrate (default: 9600)
- Polling interval (5 seconds)
- Sensor definitions:
                - Modbus slave ID
                - Bed assignment (Front Bed or Back Bed - maps to `rack` in database)
                - Sensor name/identifier
                - Room assignment (Flower Room only)

Example structure:

```yaml
rs485:
  port: /dev/serial0  # GPIO UART (connected via MAX13487 transceiver)
  baudrate: 9600
  timeout: 1.0

polling:
  interval_seconds: 5

sensors:
 - modbus_id: 1
    name: "soil_sensor_front_bed"
    bed_name: "Front Bed"  # Maps to rack.name in database
    room_name: "Flower Room"
    zone_name: "Main Zone"  # Default
    facility_name: "CEA Facility"  # Default
```

### 2. MODBUS-RTU Implementation (`app/modbus_rtu.py`)

Reuse and adapt the existing ModbusRTU class from `Test Scripts/RS485/SoilProbes/soil_probe_reader.py`:

- Serial communication handling
- CRC16 calculation and verification
- Read holding registers function
- Error handling and retry logic

### 3. Soil Sensor Reader (`app/soil_sensor_reader.py`)

Class to read all 4 parameters from DFRobot sensor:

- Temperature (°C)
- Humidity (%)
- EC (µS/cm)
- pH

Uses MODBUS function code 0x03 (Read Holding Registers). Register addresses and scaling factors need to be verified from DFRobot wiki documentation.

### 4. Database Integration (`app/database.py`)

Database manager following the existing schema pattern:

- Ensure facility/zone/room/rack exist (create if needed)
- Register device and sensors in database
- Store measurements in `measurement` hypertable
- Map sensor readings to correct `sensor_id` based on configuration

Database structure per sensor:

- **Device**: One device per physical sensor (type: "RS485 Soil Sensor")
- **Sensors**: Four sensors per device:
                - `{sensor_name}_temperature` (unit: "°C", data_type: "temperature")
                - `{sensor_name}_humidity` (unit: "%", data_type: "humidity")
                - `{sensor_name}_ec` (unit: "µS/cm", data_type: "electrical_conductivity")
                - `{sensor_name}_ph` (unit: "pH", data_type: "ph")

**Naming Convention**:

- Sensor names use snake_case: `soil_sensor_front_bed`, `soil_sensor_back_bed`
- Bed names in database: "Front Bed", "Back Bed" (title case, human-readable)
- Device names: "Soil Sensor - Front Bed", "Soil Sensor - Back Bed" (descriptive)

### 5. Redis Publisher (`app/redis_client.py`)

Publish sensor updates to Redis following the existing pattern:

- Publish to `sensor:update` channel (general)
- Publish to `sensor:update:soil` channel (soil-specific)
- Store latest values in Redis state keys: `sensor:{sensor_name}` with TTL
- Include timestamp in messages

Message format:

```json
{
  "sensor_name": "soil_sensor_front_bed_temperature",
  "value": 22.5,
  "unit": "°C",
  "timestamp": "2024-01-15T10:30:00Z",
  "location": "Flower Room",
  "bed": "Front Bed"
}
```

### 6. Background Polling Task (`app/background_tasks.py`)

Async background task that:

- Polls all configured sensors every 5 seconds
- Reads all 4 parameters per sensor
- Stores in database
- Publishes to Redis
- Handles errors gracefully with retry logic
- Logs sensor communication failures

### 7. FastAPI Application (`app/main.py`)

Main application following the pattern from `automation-service`:

- Lifespan manager for startup/shutdown
- Initialize database connection
- Initialize Redis connection
- Register sensors in database on startup
- Start background polling task
- Health/status endpoints
- Graceful shutdown handling

### 8. API Routes

**Status Route** (`app/routes/status.py`):

- `GET /` - Service info
- `GET /health` - Health check
- `GET /status` - Detailed status (sensors, last readings, errors)

**Sensors Route** (`app/routes/sensors.py`):

- `GET /api/sensors` - List all configured sensors
- `GET /api/sensors/{sensor_id}/latest` - Latest reading for a sensor
- `GET /api/sensors/{sensor_id}/readings` - Historical readings

## Database Schema Integration

The service will:

1. Check if facility "CEA Facility" exists, create if not
2. Check if zone "Main Zone" exists, create if not
3. Check if room "Flower Room" exists, create if not
4. For each sensor configuration:

                        - Check if rack (bed) exists in Flower Room ("Front Bed" or "Back Bed"), create if not
                        - Create device entry (type: "RS485 Soil Sensor", name: "Soil Sensor - {Bed Name}")
                        - Create 4 sensor entries (temperature, humidity, ec, ph)

5. Store measurements with proper `sensor_id` references

## Error Handling

- Serial communication errors: Log and retry with exponential backoff
- MODBUS errors: Log error code, skip reading, retry next cycle
- Database errors: Log and continue (don't block other sensors)
- Redis errors: Log warning, continue (database is primary storage)
- Sensor timeout: Mark sensor as unavailable, retry next