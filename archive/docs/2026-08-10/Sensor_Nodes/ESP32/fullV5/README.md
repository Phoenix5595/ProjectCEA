# ESP32 FullV5 - Multi-Node CAN Sensor System (No Status Frame)

## Overview
ESP32 FullV5 is optimized for efficiency by removing the status frame. It supports 3 sensor nodes on the same CAN bus with missing sensor detection handled by the receiver.

## Hardware
- **2x MAX31865 + PT100** temperature sensors (Dry & Wet)
- **1x BME280** (Temperature, Humidity, Pressure)
- **1x SCD30** (CO2, Temperature, Humidity)  
- **1x VL53L0X** (Time-of-Flight distance sensor)

## CAN ID Mapping
Each node uses a different CAN ID range (no status frame):

| Node | PT100 (Dry+Wet) | BME280 | SCD30 | VL53 | Heartbeat |
|------|-----------------|--------|-------|------|-----------|
| 1    | 0x101           | 0x102  | 0x103 | 0x104| 0x105     |
| 2    | 0x201           | 0x202  | 0x203 | 0x204| 0x205     |
| 3    | 0x301           | 0x302  | 0x303 | 0x304| 0x305     |

## Configuration

### Set Node ID
Before uploading, set the node ID by editing this line in the code:
```cpp
#define NODE_ID 1  // Change this to 1, 2, or 3
```

### Compile-time Options
You can disable sensors by commenting out these lines:
```cpp
#define USE_BME280 1  // Comment out to disable BME280
#define USE_SCD30 1   // Comment out to disable SCD30  
#define USE_VL53 1    // Comment out to disable VL53
```

## Wiring

### MAX31865 PT100 Sensors
```
MAX1 (Dry) - HSPI:
- SCK  → GPIO 14
- MISO → GPIO 12  
- MOSI → GPIO 13
- CS   → GPIO 2

MAX2 (Wet) - VSPI:
- SCK  → GPIO 18
- MISO → GPIO 19
- MOSI → GPIO 23
- CS   → GPIO 27
```

### I2C Sensors
```
SDA → GPIO 21
SCL → GPIO 22

BME280: 0x76 or 0x77
SCD30: 0x61
VL53L0X: 0x29
```

### CAN Bus
```
CAN TX → GPIO 5
CAN RX → GPIO 4
```

## Data Format

### PT100 Temperature (0x1xx, 0x2xx, 0x3xx) - Fused Dry+Wet
```
[0-1] Dry temperature * 100 (s16, °C)
[2-3] Wet temperature * 100 (s16, °C)
[4-5] Message counter (u16)
[6-7] Reserved (2 bytes FREE)
```

### BME280 (0x1xx+1, 0x2xx+1, 0x3xx+1)
```
[0-1] Temperature * 100 (s16, °C)
[2-3] Humidity * 100 (u16, %)
[4-5] Pressure * 10 (u16, hPa)
[6-7] Reserved (2 bytes FREE)
```

### SCD30 (0x1xx+2, 0x2xx+2, 0x3xx+2)
```
[0-1] CO2 concentration (u16, ppm)
[2-3] Temperature * 100 (s16, °C)
[4-5] Humidity * 100 (u16, %)
[6-7] Reserved (2 bytes FREE)
```

### VL53L0X (0x1xx+3, 0x2xx+3, 0x3xx+3)
```
[0-1] Distance (u16, mm)
[2-3] Ambient (u16, raw)
[4-5] Signal (u16, raw)
[6-7] Reserved (2 bytes FREE)
```

### Heartbeat (0x1xx+4, 0x2xx+4, 0x3xx+4)
```
[0-1] Signature (0xAA55)
[2-5] Uptime (u32, ms)
[6-7] Reserved (2 bytes FREE)
```

## Missing Sensor Detection

The V5 receiver automatically detects missing sensors by:
1. **Data presence**: If no data received = sensor missing
2. **Invalid values**: 0x7FFF in PT100 frames = sensor failed
3. **SQL logging**: Missing sensors logged in `missing_sensors5` table
4. **VPD calculation**: Uses sea level pressure (1013.25 hPa) when BME280 missing

## Usage

### For Node 1:
1. Set `#define NODE_ID 1`
2. Upload to ESP32
3. Connect to CAN bus

### For Node 2:
1. Set `#define NODE_ID 2`  
2. Upload to ESP32
3. Connect to CAN bus

### For Node 3:
1. Set `#define NODE_ID 3`
2. Upload to ESP32  
3. Connect to CAN bus

## Receiver Compatibility
This code is fully compatible with `can_receiver_v5.py` which will:
- Receive data from all 3 nodes simultaneously
- Store data in separate database tables per node
- Calculate climate data (RH, VPD) from dry/wet bulb temperatures
- Detect and log missing sensors in SQL
- Use sea level pressure for VPD when BME280 missing
- Provide real-time statistics per node

## Database Schema

### Main Tables:
- `tempdry1`: Dry temperature data
- `tempwet1`: Wet temperature data  
- `climate5`: Calculated RH and VPD
- `bme2805`: BME280 sensor data
- `scd305`: SCD30 sensor data
- `vl535`: VL53L0X sensor data
- `heartbeat5`: Heartbeat data
- `missing_sensors5`: Missing sensor tracking
- `stats5`: Cluster statistics

### Missing Sensor Tracking:
```sql
SELECT cluster_id, sensor_type, status, 
       datetime(first_missing_ts, 'unixepoch') as first_missing,
       datetime(last_checked_ts, 'unixepoch') as last_checked
FROM missing_sensors5 
WHERE status = 'MISSING';
```

## Troubleshooting

### No CAN Communication
- Check CAN bus wiring (TX/RX swapped?)
- Verify 120Ω termination resistors
- Check CAN bus voltage levels

### Missing Sensor Data
- Check `missing_sensors5` table for sensor status
- Verify I2C wiring and addresses
- Check sensor power supply
- Enable debug output to see sensor detection

### Temperature Reading Issues
- Check PT100 wiring and connections
- Verify MAX31865 configuration
- Check reference resistor value (430Ω)

## Serial Output
```
=== ESP32 FullV5 Node 1 + TWAI ===
BME280 OK
SCD30 OK  
VL53 OK
TWAI started.
[Node1] T1=23.45°C T2=22.10°C
[Node1] T1=23.47°C T2=22.12°C
```

## Efficiency Improvements
- **No status frame**: 17% fewer messages per node
- **Fused PT100**: 50% fewer temperature messages
- **Missing sensor detection**: Handled by receiver, not transmitter
- **Sea level pressure default**: VPD calculation works without BME280
