# SENSOR NODES

## OVERVIEW

ESP32-based CAN bus sensor nodes. Read environmental sensors, transmit via CAN to mothernode. Arduino/PlatformIO firmware.

## STRUCTURE

```
Sensor_Nodes/
└── ESP32/
    ├── fullV6/              # LATEST STABLE - use this
    │   ├── fullV6.ino       # Main firmware
    │   └── README.md
    ├── fullV5/              # Previous version
    ├── fullV4/              # Legacy
    ├── fullV1-V3/           # Deprecated
    └── can_*/               # CAN bus test sketches
```

## HARDWARE PER NODE

| Sensor | Interface | Address | Measurements |
|--------|-----------|---------|--------------|
| BME280 | I2C | 0x76/0x77 | Temp, RH, Pressure |
| SCD30 | I2C | 0x61 | CO2, Temp, RH |
| MAX31865 | SPI | — | PT100 temperature |

## CAN BUS PROTOCOL

- **Bitrate**: 250 kbps
- **ID scheme**: `0x1XX` (Node 1), `0x2XX` (Node 2), etc.
- **Message format**: Defined in `can-processor-service/app/decoder.py`

## NODE ID MAPPING

| Node ID | Location | Cluster |
|---------|----------|---------|
| 1 | Flower Room | back |
| 2 | Flower Room | front |
| 3 | Veg Room | main |
| 4 | Lab | main |
| 5 | Outside | main |

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Upload to wrong node | Nodes have unique IDs |
| Change CAN format without updating parser | Protocol mismatch |
| Skip sensor calibration | Accuracy |
| Use test firmware in production | Use fullV* |
| Change CAN baud rate alone | Must update all nodes + Pi |

## DEVELOPMENT

```bash
# Arduino IDE or PlatformIO
# Select: ESP32 Dev Module
# Upload to correct node (verify node_id in code)
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| V6 firmware | `ESP32/fullV6/README.md` |
| V5 firmware | `ESP32/fullV5/README.md` |
