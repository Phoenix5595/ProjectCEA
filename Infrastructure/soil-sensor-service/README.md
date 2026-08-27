# Soil Sensor Service

FastAPI service on port 8002 that polls DFRobot RS485 4-in-1 soil sensors (temperature, humidity, EC, pH) over Modbus RTU and stores the readings in TimescaleDB and Redis.

## Hardware

- DFRobot SEN0604 soil sensors on an RS485 bus
- RS485 to TTL converter (e.g., MAX13487-based) connected to the Pi UART
- 120 Ω termination resistors at each end of the bus

## Setup

Run inside the service virtual environment:

```bash
cd Infrastructure/soil-sensor-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Add the running user to the `dialout` group for UART access:

```bash
sudo usermod -a -G dialout $USER
```

If you use the built-in UART, enable it:

```ini
enable_uart=1
```

in `/boot/firmware/config.txt`, then reboot.

## Configuration

Edit `soil_sensor_config.yaml`:

- `rs485.port` — default `/dev/ttyUSB0`; use `/dev/serial0` for the GPIO UART
- `rs485.baudrate` — default 9600
- `polling.interval_seconds` — default 5
- `polling.discovery_interval_seconds` — default 30

The service auto-discovers sensors on the bus and registers them in the database. Manual entries in the `sensors:` list are optional.

## Running

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

Production runs under `soil-sensor-service.service`.

## Health and data flow

- `GET /health` — liveness
- `GET /status` — service and sensor status
- `GET /api/sensors` — configured sensors
- `GET /api/sensors/{sensor_id}/latest` — latest reading
- `GET /api/sensors/{sensor_id}/readings` — historical readings

Data path:

```
RS485 Modbus RTU
    -> Soil Sensor Service
        -> TimescaleDB measurement hypertable
        -> Redis state keys (sensor:*) and channels (sensor:update:soil)
```

## Troubleshooting

- Permission denied: confirm the user is in `dialout` and re-logged in.
- Communication errors: check wiring, baudrate, Modbus slave ID, and termination.
- Database or Redis unavailable: the service retries and continues storing to whichever destination is reachable.

```bash
journalctl -u soil-sensor-service.service -f
```
