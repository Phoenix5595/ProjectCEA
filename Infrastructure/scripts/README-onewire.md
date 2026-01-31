# 1-Wire Temperature Probes (e.g. DS18B20) on Raspberry Pi

Two DS18B20 (or compatible) temperature probes can be read on **GPIO 24** (physical pin 18) via the Linux 1-Wire (w1) subsystem.

## Enable 1-Wire on GPIO 24

On the Raspberry Pi:

1. Edit `/boot/firmware/config.txt` (Pi 4/5) or `/boot/config.txt`:
   ```ini
   dtoverlay=w1-gpio,gpiopin=24
   ```
2. Reboot: `sudo reboot`

## Wiring

- **Data**: GPIO 24 (BCM 24) = physical pin **18**
- **Pull-up**: ~4.7 kΩ between data and 3.3 V
- **Power**: 3.3 V and GND from the Pi (or use parasitic power if supported)

## Check that the Pi sees the probes

Run on the Pi:

```bash
# List 1-Wire devices (DS18B20 show as 28-xxxxxxxxxxxx)
ls /sys/bus/w1/devices/

# Read temperatures (script in this repo)
./Infrastructure/scripts/read-onewire-temp.sh
```

Or one-liners:

```bash
# List devices
ls /sys/bus/w1/devices/28-*

# Read one device (replace 28-xxxx with your device id)
cat /sys/bus/w1/devices/28-*/temperature
# Value is millidegrees Celsius (e.g. 23500 = 23.5 °C)
```

If you see two `28-*` directories and can read their `temperature` file, the Pi sees both probes.

## Integration with CEA services

**onewire-reader-service** (Infrastructure/onewire-reader-service/) runs as a systemd service and:

- Reads `/sys/bus/w1/devices/28-*/temperature` at ~1 Hz
- Maps device ids to logical names via `onewire_config.yaml` (e.g. `28-1be2d445e7ac` → `lab_temp`, `28-7227d445d907` → `water_temperature`)
- Writes Redis keys `sensor:lab_temp`, `sensor:water_temperature` (and `:ts`) with 10s TTL
- Dashboard Lab section shows "Lab temp" and "Water Temp" from these keys

Install and start: see Infrastructure README; service file `onewire-reader.service`. After Redis, start `onewire-reader.service`.
