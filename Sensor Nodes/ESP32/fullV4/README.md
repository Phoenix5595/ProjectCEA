# ESP32 FullV4 - Multi-Node CAN Sensor System

## Overview
ESP32 FullV4 is compatible with the V3 receiver and supports 3 sensor nodes on the same CAN bus. Each node has identical sensor hardware but uses different CAN IDs.

## Hardware
- **2x MAX31865 + PT100** temperature sensors (Dry & Wet)
- **1x BME280** (Temperature, Humidity, Pressure)
- **1x SCD30** (CO2, Temperature, Humidity)  
- **1x VL53L0X** (Time-of-Flight distance sensor)

## CAN ID Mapping
Each node uses a different CAN ID range:

| Node | PT100 (Dry+Wet) | BME280 | SCD30 | VL53 | Status | Heartbeat |
|------|-----------------|--------|-------|------|--------|-----------|
| 1    | 0x101           | 0x102  | 0x103 | 0x104| 0x105  | 0x106     |
| 2    | 0x201           | 0x202  | 0x203 | 0x204| 0x205  | 0x206     |
| 3    | 0x301           | 0x302  | 0x303 | 0x304| 0x305  | 0x306     |

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

### BME280 (0x1xx+2, 0x2xx+2, 0x3xx+2)
```
[0-1] Temperature * 100 (s16, °C)
[2-3] Humidity * 100 (u16, %)
[4-5] Pressure * 10 (u16, hPa)
[6-7] Reserved
```

### SCD30 (0x1xx+3, 0x2xx+3, 0x3xx+3)
```
[0-1] CO2 concentration (u16, ppm)
[2-3] Temperature * 100 (s16, °C)
[4-5] Humidity * 100 (u16, %)
[6-7] Reserved
```

### VL53L0X (0x1xx+4, 0x2xx+4, 0x3xx+4)
```
[0-1] Distance (u16, mm)
[2-3] Ambient (u16, raw)
[4-5] Signal (u16, raw)
[6-7] Reserved
```

### Status (0x1xx+5, 0x2xx+5, 0x3xx+5)
```
[0] MAX1 OK (0x01/0x00)
[1] MAX2 OK (0x01/0x00)
[2] BME280 OK (0x01/0x00)
[3] SCD30 OK (0x01/0x00)
[4] VL53 OK (0x01/0x00)
[5] CAN OK (0x01/0x00)
[6-7] Reserved
```

### Heartbeat (0x1xx+6, 0x2xx+6, 0x3xx+6)
```
[0-1] Signature (0xAA55)
[2-5] Uptime (u32, ms)
[6-7] Reserved
```

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
This code is fully compatible with `can_receiver_v3.py` which will:
- Receive data from all 3 nodes simultaneously
- Store data in separate database tables per node
- Calculate climate data (RH, VPD) from dry/wet bulb temperatures
- Provide real-time statistics per node

## Troubleshooting

### No CAN Communication
- Check CAN bus wiring (TX/RX swapped?)
- Verify 120Ω termination resistors
- Check CAN bus voltage levels

### Missing Sensor Data
- Verify I2C wiring and addresses
- Check sensor power supply
- Enable debug output to see sensor detection

### Temperature Reading Issues
- Check PT100 wiring and connections
- Verify MAX31865 configuration
- Check reference resistor value (430Ω)

## Serial Output
```
=== ESP32 FullV4 Node 1 + TWAI ===
BME280 OK
SCD30 OK  
VL53 OK
TWAI started.
[Node1] T1=23.45°C T2=22.10°C
[Node1] T1=23.47°C T2=22.12°C
```
