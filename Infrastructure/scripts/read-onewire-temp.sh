#!/usr/bin/env bash
# Read 1-Wire temperature probes (e.g. DS18B20) on Raspberry Pi.
# Expects 1-Wire on GPIO 24: add to /boot/firmware/config.txt:
#   dtoverlay=w1-gpio,gpiopin=24
# Then reboot. Devices appear under /sys/bus/w1/devices/ (28-* = DS18B20).

set -e

W1_DEVICES="/sys/bus/w1/devices"

if [[ ! -d "$W1_DEVICES" ]]; then
  echo "1-Wire bus not found. Enable it on the Pi:"
  echo "  1. Add to /boot/firmware/config.txt:  dtoverlay=w1-gpio,gpiopin=24"
  echo "  2. Reboot:  sudo reboot"
  exit 1
fi

# List DS18B20-family devices (address starts with 28-)
count=0
for dev in "$W1_DEVICES"/28-*; do
  [[ -d "$dev" ]] || continue
  name=$(basename "$dev")
  temp_file="$dev/temperature"
  if [[ -r "$temp_file" ]]; then
    raw=$(cat "$temp_file")
    # Value is millidegrees Celsius
    deg=$(awk "BEGIN { printf \"%.2f\", $raw/1000 }" 2>/dev/null || echo "$((raw / 1000)).$(( (raw % 1000 + 500) / 100 ))")
    echo "$name: ${deg} °C"
    count=$((count + 1))
  else
    echo "$name: (read failed)"
  fi
done

if [[ $count -eq 0 ]]; then
  echo "No 1-Wire temperature devices (28-*) found under $W1_DEVICES"
  echo "Check wiring and pull-up (~4.7k to 3.3V). GPIO 24 = physical pin 18."
  exit 1
fi

echo "--- Found $count probe(s) ---"
