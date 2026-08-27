# ESP32 FullV6 Firmware

Current ESP32 sensor-node firmware. Reads environmental sensors and transmits measurements over CAN bus to the `can-processor-service`.

## Hardware per node

| Sensor | Interface | Address / Pins | Measurements |
|--------|-----------|----------------|--------------|
| MAX31865 + PT100 (Dry) | HSPI | SCK 14, MISO 12, MOSI 13, CS 2 | Temperature |
| MAX31865 + PT100 (Wet) | VSPI | SCK 18, MISO 19, MOSI 23, CS 27 | Temperature |
| BME280 | I2C | 0x76 or 0x77 | Temperature, RH, Pressure |
| SCD30 | I2C | 0x61 | CO2, Temperature, RH |
| VL53L0X | I2C | 0x29 | Distance |

I2C bus: SDA GPIO 21, SCL GPIO 22.
CAN bus: TX GPIO 5, RX GPIO 4.

## CAN protocol

- Bitrate: 250 kbps (`TWAI_TIMING_CONFIG_250KBITS`)
- ID scheme per node:

| Node | PT100 (Dry+Wet) | BME280 | SCD30 | VL53 | Heartbeat |
|------|-----------------|--------|-------|------|-----------|
| 1 | 0x101 | 0x102 | 0x103 | 0x104 | 0x105 |
| 2 | 0x201 | 0x202 | 0x203 | 0x204 | 0x205 |
| 3 | 0x301 | 0x302 | 0x303 | 0x304 | 0x305 |

The CAN processor maps these IDs to:

| Node ID | Location | Cluster |
|---------|----------|---------|
| 1 | Flower Room | back |
| 2 | Flower Room | front |
| 3 | Veg Room | main |

## Decoder and unified measurements

`Infrastructure/can-processor-service/app/decoder.py` parses each frame. `app/processor.py` derives:

- `dry_bulb_*`, `wet_bulb_*` from PT100 frames
- `rh_*` and `vpd_*` from dry/wet bulb temperatures plus pressure
- `co2_*`, `secondary_temp_*`, `secondary_rh_*` from SCD30
- `pressure_*` from BME280
- `water_level_*` from VL53

Values are written to the shared `measurement` hypertable and to Redis state keys. No per-node database tables are used.

## Runtime sensor reconnection

Every 10 seconds (`RECONNECT_CHECK_INTERVAL_MS = 10000`) the firmware checks sensors that failed at startup and reinitializes them if they reappear. This lets you reconnect a sensor without rebooting the ESP32.

## Building and flashing

1. Open `fullV6.ino` in the Arduino IDE or PlatformIO.
2. Select the ESP32 Dev Module.
3. Set the node ID before uploading:

```cpp
#define NODE_ID 1  // Use 1, 2, or 3
```

4. Optional: disable sensors by commenting out the corresponding `USE_*` define.
5. Upload to the correct physical node.

## Data frame layouts

### PT100 (0x1xx / 0x2xx / 0x3xx)

- bytes 0–1: dry temperature × 100 (s16, °C)
- bytes 2–3: wet temperature × 100 (s16, °C)
- bytes 4–5: message counter (u16)
- `0x7FFF` marks an invalid/missing temperature

### BME280 (0x1xx+1)

- bytes 0–1: temperature × 100 (s16, °C)
- bytes 2–3: humidity × 100 (u16, %)
- bytes 4–5: pressure × 10 (u16, hPa)

### SCD30 (0x1xx+2)

- bytes 0–1: CO2 (u16, ppm)
- bytes 2–3: temperature × 100 (s16, °C)
- bytes 4–5: humidity × 100 (u16, %)

### VL53 (0x1xx+3)

- bytes 0–1: distance (u16, mm)
- bytes 2–3: ambient (u16, raw)
- bytes 4–5: signal (u16, raw)

### Heartbeat (0x1xx+4)

- bytes 0–1: signature `0xAA55`
- bytes 2–5: uptime (u32, ms)

## Troubleshooting

- No CAN traffic: check TX/RX wiring, 120 Ω termination, and that `can0` is up at 250 kbps on the Pi.
- Missing sensor values: verify I2C/SPI wiring and sensor addresses; watch the serial monitor for reconnection messages.
- Wrong room/cluster in the dashboard: confirm `NODE_ID` matches the physical node location.
