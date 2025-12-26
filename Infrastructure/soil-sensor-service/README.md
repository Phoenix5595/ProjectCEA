# Soil Sensor RS485 Service

FastAPI microservice for monitoring DFRobot RS485 4-in-1 soil sensors (temperature, humidity, EC, pH) via MODBUS-RTU protocol.

## Overview

This service reads data from RS485 soil sensors connected via a MAX13487 transceiver board to the Raspberry Pi GPIO UART. It stores measurements in TimescaleDB and publishes updates to Redis for other services to consume.

## Hardware Requirements

- Raspberry Pi (with GPIO UART)
- MAX13487 RS485 transceiver board
- DFRobot RS485 4-in-1 Soil Sensor (SEN0604) - supports multiple sensors on same bus
- RS485 cable connection between sensors and MAX13487 board
- Proper termination resistors (120Ω at each end of RS485 bus)

## Software Requirements

- Python 3.x (system-wide installation via apt)
- FastAPI web framework
- pyserial library for serial communication
- asyncpg for PostgreSQL async driver
- redis library for Redis client
- pyyaml for YAML configuration parsing
- Modbus RTU protocol implementation

## Installation

### 1. Install Dependencies

Using apt (system-wide installation):
```bash
sudo apt update
sudo apt install python3-serial python3-yaml
pip3 install fastapi uvicorn asyncpg redis
```

Or using pip:
```bash
pip3 install -r requirements.txt
```

### 2. Configure Serial Port Permissions

Add your user to the dialout group:
```bash
sudo usermod -a -G dialout $USER
```
You may need to log out and back in for this to take effect.

### 3. Configure Raspberry Pi UART

If using the built-in UART, enable it in `/boot/firmware/config.txt`:
```
enable_uart=1
```

### 4. Configure Service

Edit `soil_sensor_config.yaml`:
- Set RS485 port (default: `/dev/serial0` for GPIO UART)
- Set baudrate (default: 9600)
- Configure sensors (Modbus IDs, bed assignments)

## Configuration

The service uses `soil_sensor_config.yaml` for configuration.

### Sensor Configuration

Each sensor requires:
- `modbus_id`: Unique Modbus slave ID (1, 2, 3, etc.)
- `name`: Base sensor name (snake_case, e.g., "soil_sensor_front_bed")
- `bed_name`: Bed name ("Front Bed" or "Back Bed")
- `room_name`: Must be "Flower Room"

### Initial Configuration

For the first sensor:
- Modbus ID: 1
- Bed: "Front Bed"
- Room: "Flower Room"
- Serial port: `/dev/serial0` (GPIO UART)
- Polling interval: 5 seconds

## Running the Service

### Manual Start

```bash
cd Infrastructure/soil-sensor-service
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

### Systemd Service

Install the systemd service:
```bash
sudo cp soil-sensor-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable soil-sensor-service
sudo systemctl start soil-sensor-service
```

## API Endpoints

### Status
- `GET /` - Service info
- `GET /health` - Health check
- `GET /status` - Detailed status

### Sensors
- `GET /api/sensors` - List all configured sensors
- `GET /api/sensors/{sensor_id}/latest` - Latest reading for a sensor
- `GET /api/sensors/{sensor_id}/readings` - Historical readings

## Functional Requirements

### RS485 Communication
- Establish serial communication with MAX13487 board via GPIO UART
- Support configurable serial port (e.g., `/dev/serial0` for GPIO UART)
- Support configurable baudrate (default: 9600)
- Implement Modbus RTU protocol for communication
- Handle CRC16 checksum calculation and verification
- Support multiple sensors on same RS485 bus (different Modbus slave IDs)

### Data Reading
- Read soil temperature from sensor (units: °C)
- Read soil humidity/moisture from sensor (units: %)
- Read EC (Electrical Conductivity) from sensor (units: µS/cm)
- Read pH from sensor (units: pH)
- Read all parameters in a single request when possible
- Support reading individual parameters
- Poll sensors at configurable interval (default: 5 seconds)

### Data Processing
- Convert raw register values to physical measurements using scaling factors
- Handle measurement units correctly (temperature: °C, humidity: %, EC: µS/cm, pH: pH)
- Validate received data (CRC verification, response format)
- Handle sensor communication errors gracefully

### Database Integration
- Store measurements in TimescaleDB `measurement` hypertable
- Register sensors in database following room/rack/device/sensor hierarchy
- Create room/bed hierarchy if it doesn't exist
- Register device and 4 sensors (temperature, humidity, ec, ph) per physical sensor
- Support configurable bed assignment per sensor (Front Bed, Back Bed)
- Only register sensors in Flower Room
- Use proper sensor naming convention (snake_case for code, human-readable for database)

### Redis Integration
- Publish sensor updates to Redis `sensor:update` channel
- Publish sensor updates to Redis `sensor:update:soil` channel
- Store latest sensor values in Redis state keys with TTL
- Include timestamp in Redis messages
- Continue operation if Redis is unavailable (database is primary storage)

### Error Handling
- Handle serial communication errors with retry logic
- Handle CRC verification failures
- Handle timeout errors
- Handle Modbus error responses
- Handle database connection errors
- Handle Redis connection errors (non-blocking)
- Log all errors appropriately
- Attempt to reconnect sensors on communication failure

## Technical Specifications

### Communication Protocol
- Protocol: Modbus RTU over RS485
- Function Code: 0x03 (Read Holding Registers)
- Default Baudrate: 9600 bps
- Data Bits: 8
- Parity: None
- Stop Bits: 1
- CRC: CRC16-Modbus

### Register Configuration
- Register addresses must be verified from DFRobot SEN0604 documentation
- Default addresses (may need adjustment):
  - Temperature: 0x0000
  - Humidity: 0x0001
  - EC: 0x0002
  - pH: 0x0003
- Scaling factors must be verified from sensor documentation
- Default scaling (may need adjustment):
  - Temperature: 0.1
  - Humidity: 0.1
  - EC: 1.0
  - pH: 0.01

### Database Schema
- Follows existing CEA database schema
- Hierarchy: Room → Rack (Bed) → Device → Sensor → Measurement
- Beds map to `rack` table in database
- Each physical sensor creates one device with 4 sensors

### Naming Conventions
- Sensor names in code: snake_case (e.g., `soil_sensor_front_bed`)
- Bed names in database: Title Case (e.g., "Front Bed", "Back Bed")
- Device names: Human-readable (e.g., "Soil Sensor - Front Bed")
- Sensor full names: `{base_name}_{type}` (e.g., `soil_sensor_front_bed_temperature`)

## Database Integration

The service automatically:
- Creates room/bed hierarchy if needed
- Registers devices and sensors in TimescaleDB
- Stores measurements in the `measurement` hypertable
- Creates 4 sensors per device: temperature, humidity, ec, ph

## Redis Integration

The service publishes sensor updates to Redis:
- Channel: `sensor:update` (general)
- Channel: `sensor:update:soil` (soil-specific)
- State keys: `sensor:{sensor_name}` with TTL

## Troubleshooting

### Permission Denied
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

### Port Not Found
Check available serial ports:
```bash
ls /dev/tty*
```

### Communication Errors
1. Verify wiring connections
2. Check baudrate matches sensor settings
3. Verify Modbus slave ID
4. Check RS485 termination resistors
5. Verify register addresses in code (may need adjustment)

### CRC Errors
- Check wiring (loose connections)
- Verify baudrate is correct
- Check for electrical interference
- Verify RS485 termination

### Database Connection Issues
- Check PostgreSQL is running: `sudo systemctl status postgresql`
- Verify connection settings in environment variables
- Service will retry connection with exponential backoff

### Redis Connection Issues
- Service automatically falls back to TimescaleDB if Redis unavailable
- Check Redis is running: `sudo systemctl status redis-server`

## Adding More Sensors

1. Edit `soil_sensor_config.yaml`
2. Add new sensor entry with unique Modbus ID
3. Restart service

## Testing Requirements

- Test with single sensor initially
- Verify MODBUS register addresses from DFRobot documentation
- Test database registration and measurement storage
- Test Redis publishing
- Test error handling (disconnect sensor, serial errors)
- Verify Grafana can query the data
- Test adding additional sensors via configuration

## Future Enhancement Considerations

- Support for additional sensor types
- Configurable register addresses and scaling factors per sensor
- Sensor calibration support
- Data validation and outlier detection
- Alerting on sensor communication failures
- WebSocket support for real-time updates
- Historical data aggregation and statistics

## Files

- `app/main.py` - FastAPI application
- `app/config.py` - Configuration loader
- `app/database.py` - Database manager
- `app/redis_client.py` - Redis publisher
- `app/modbus_rtu.py` - MODBUS-RTU protocol
- `app/soil_sensor_reader.py` - Sensor reader
- `app/background_tasks.py` - Polling task
- `soil_sensor_config.yaml` - Configuration file

## Installation Requirements

- Install dependencies via system package manager (apt) or pip
- No virtual environment required (system-wide installation preferred)
- Service should be executable and runnable as systemd service
