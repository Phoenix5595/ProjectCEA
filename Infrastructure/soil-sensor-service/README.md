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

Edit `soil_sensor_config.yaml` for hardware-only RS-485 settings:

- `rs485.port` — default `/dev/ttyUSB0`; use `/dev/serial0` for the GPIO UART
- `rs485.baudrate` — default 9600

The polling intervals are editable in Sensor Settings (`GET /api/settings`, `PUT /api/settings/soil`). The YAML `polling.interval_seconds` and `polling.discovery_interval_seconds` values remain fallbacks for when neither the database nor settings cache has a value. Database changes take effect on the next polling or discovery loop iteration.

The service discovers sensors on the bus and registers them in `sensor_registry`, which owns assignments and permanent probe identities. A ten-minute absence followed by a response creates a pending identity for operator confirmation in Devices → Sensor Settings. Readings use the registry row's linked device; newly created devices use `SENSOR-<sensor_uid>` serials. There is no sensor list in YAML.

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
        -> Redis state keys (`cea:sensor:{location}:{cluster}:{sensor_name}` and `_ts`, 10 s TTL) and channel (`sensor:update:soil`)
```

For each Modbus sample, the service batches its supported readings' current-value and timestamp keys and both update notifications in one existing-client transaction. The raw telemetry stream write and TimescaleDB writes remain separate.

## Troubleshooting

- Permission denied: confirm the user is in `dialout` and re-logged in.
- Communication errors: check wiring, baudrate, Modbus slave ID, and termination.
- Database or Redis unavailable: the service retries and continues storing to whichever destination is reachable.

```bash
journalctl -u soil-sensor-service.service -f
```
