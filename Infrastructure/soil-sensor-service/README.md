# Soil Sensor Service

FastAPI service on port 8002 that polls DFRobot RS485 4-in-1 soil sensors (temperature, humidity, EC, pH) over Modbus RTU and stores the readings in TimescaleDB and Redis.

## Hardware

- DFRobot SEN0604 4-in-1 soil probes (Modbus RTU, 9600 8N1, factory address 1) on one RS485 bus
- DFRobot DFR0845 RS485-to-UART module on the Pi GPIO UART (`/dev/ttyAMA0`, GPIO14/15); direction switching is on-board, there is no DE/RE pin
- 120 Ω termination at each end of the bus

Wiring:

|DFR0845 pin|Connects to|
|---|---|
|T (TX)|Pi pin 10 (GPIO15 RX)|
|R (RX)|Pi pin 8 (GPIO14 TX)|
|+ (VCC)|Pi pin 1 (3.3 V)|
|− (GND)|Pi pin 6 (GND)|
|RS485 12V / GND / A / B|Probe power and bus; the 12V output is isolated and limited to 2 W (~160 mA). Feed 12V-IN from an external supply when the probes need more.|

Measure the module T pin idle voltage (must be ≤ 3.4 V) before connecting it to Pi pin 10.

## Setup

Run inside the service virtual environment:

```bash
cd Infrastructure/soil-sensor-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The production unit runs as `root`. When running as another user, add it to the `dialout` group for UART access:

```bash
sudo usermod -a -G dialout $USER
```

Enable UART0 on GPIO14/15 (`/dev/ttyAMA0`) in `/boot/firmware/config.txt`, then reboot:

```ini
dtparam=uart0=on
```

## Configuration

Edit `soil_sensor_config.yaml` for hardware-only RS-485 settings (8N1 is fixed in code):

- `rs485.port` — required, `/dev/ttyAMA0` (GPIO UART through the DFR0845). `/dev/serial0` is the Pi 5 debug UART and must not be used.
- `rs485.baudrate` — default 9600
- `rs485.timeout` — poll read timeout in seconds, default 0.5
- `rs485.scan_timeout` — discovery/commissioning read timeout in seconds, default 0.2
- `rs485.inter_frame_delay` — minimum gap between frames in seconds, default 0.005 (≥ 4 byte-times at 9600 baud)

The polling intervals are editable in Sensor Settings (`GET /api/settings`, `PUT /api/settings/soil`). The YAML `polling.interval_seconds` and `polling.discovery_interval_seconds` values remain fallbacks for when neither the database nor settings cache has a value. Database changes take effect on the next polling or discovery loop iteration.

## Probe identity and commissioning (R9)

One `SoilBus` owns the serial port; polls and discovery share it transaction by transaction. The probe's Modbus address is its device ID, stored in `sensor_registry.hardware_address` with a `sensor_address_history` row. Address 1 is the factory address and is never a device ID.

- At start the service wires every registry probe, then scans addresses 2–254 once and adopts any unknown probe that answers. A probe already set to an address other than 1 is adopted at the next service start.
- Every discovery cycle reads address 1. A new probe connected alone at factory address 1 is written (`0x06` on register `0x07D0`) to the lowest address in 2–254 that never appeared in `sensor_registry` or `sensor_address_history`, verified at the new address and registered unassigned.
- New and reconnecting probes are never pending; identity confirmation in Sensor Settings is CAN-only.
- A probe absent for 10 minutes loses its placement (closed as `disconnected`). When it answers again it returns to its last spot only if that placement was closed by the disconnect and no placed probe in the same bed lies within 1 ft; a Veg probe returns to Veg Room. Otherwise it stays Unassigned.

The bus state is published to Redis `cea:soil:bus_status` (TTL 300 s) and shown as a banner in Sensor Settings → RS485:

|State|Banner|
|---|---|
|`power_cycle_required`|The probe accepted the address write but does not answer at the new address yet: power-cycle it. The reservation lives in memory until the probe answers or the service restarts.|
|`collision`|Three consecutive corrupt or exception replies at address 1: several new probes are connected. Connect one at a time.|
|`no_free_address`|Every address 2–254 has been used.|

Readings use the registry row's linked device. There is no sensor list in YAML.

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
SEN0604 probes -> RS485 -> DFR0845 -> /dev/ttyAMA0 (Modbus RTU)
    -> Soil Sensor Service (SoilBus, ProbeDiscovery, poller)
        -> sensor_registry / sensor_address_history / soil_probe_placement (identity, placement)
        -> TimescaleDB measurement hypertable
        -> Redis state keys (`cea:sensor:{location}:{cluster}:{sensor_name}` and `_ts`, 10 s TTL), channel (`sensor:update:soil`) and `cea:soil:bus_status`
```

For each Modbus sample, the service batches its supported readings' current-value and timestamp keys and both update notifications in one existing-client transaction. The raw telemetry stream write and TimescaleDB writes remain separate.

## Troubleshooting

- Permission denied: confirm the user is in `dialout` and re-logged in.
- Communication errors: check wiring, baudrate, Modbus slave ID, and termination.
- Database or Redis unavailable: the service retries and continues storing to whichever destination is reachable.

```bash
journalctl -u soil-sensor-service.service -f
```
