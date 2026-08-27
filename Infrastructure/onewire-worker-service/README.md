# 1-Wire Temperature Reader Service

FastAPI service on port 8004 that reads DS18B20 temperature probes through the Linux 1-Wire (`w1`) subsystem and publishes the values to Redis.

## Hardware wiring

- Data: GPIO 24 (BCM) = physical pin 18
- Pull-up: ~4.7 kΩ between data and 3.3 V
- Power: 3.3 V and GND from the Pi

## Enable 1-Wire on GPIO 24

Add to `/boot/firmware/config.txt` (Pi 4/5) or `/boot/config.txt`:

```ini
dtoverlay=w1-gpio,gpiopin=24
```

Then reboot.

## Verify the Pi sees the probes

```bash
ls /sys/bus/w1/devices/28-*
cat /sys/bus/w1/devices/28-*/temperature
```

The value is millidegrees Celsius (for example, `23500` = 23.5 °C).

## Configuration

Edit `onewire_config.yaml`. Map each `28-*` device ID shown by `ls /sys/bus/w1/devices/28-*` to a logical sensor name such as `lab_temp` or `water_temperature`. Logical names must match the backend/frontend expectations. The file also sets the polling interval.

## Running

```bash
cd Infrastructure/onewire-worker-service
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8004
```

Production runs under `onewire-worker.service`.

## Data flow

```
/sys/bus/w1/devices/28-*
    -> onewire-worker-service
        -> Redis state keys (sensor:lab_temp, sensor:water_temperature, 10 s TTL)
        -> Backend /api/sensors/Lab/main/live
```

## Read-only diagnostics

Check the service is running:

```bash
systemctl status onewire-worker.service
journalctl -u onewire-worker.service -n 50
```

Read current values locally without touching production data:

```bash
# Service debug endpoint
curl -s http://127.0.0.1:8004/readings

# Redis keys
redis-cli MGET sensor:lab_temp sensor:water_temperature

# Backend live endpoint
curl -s http://127.0.0.1:8000/api/sensors/Lab/main/live
```

If values are missing, confirm the `28-*` IDs match `onewire_config.yaml` and that the service is active.
