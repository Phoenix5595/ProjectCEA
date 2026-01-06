# SENSOR NODES

**Generated:** 2025-01-05

## OVERVIEW
ESP32 Arduino firmware for CAN bus sensor nodes. Multiple versions for different hardware configurations.

## STRUCTURE

```
Sensor Nodes/
└── ESP32/
    ├── fullV1/           # Version 1 complete firmware
    ├── fullV2/           # Version 2 complete firmware
    ├── fullV3/           # Version 3 complete firmware
    ├── fullV4/           # Version 4 complete firmware
    ├── fullV5/           # Version 5 complete firmware
    ├── fullV6/           # Version 6 complete firmware
    ├── can_twai_test/     # TWAI (CAN) test firmware
    ├── can_spi_test_can_bus/  # SPI CAN test firmware
    └── can_test_alternative/  # Alternative CAN test firmware
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Production firmware | `fullV1/` - `fullV6/` | Use latest stable version |
| CAN communication | All versions | CAN bus message handling |
| Hardware tests | `can_*_test/` | TWAI, SPI, alternative implementations |
| Sensor reading | All versions | Sensor data collection logic |

## CONVENTIONS

### Arduino IDE
- **Board**: ESP32 (various modules)
- **Framework**: Arduino ESP32
- **Upload**: USB/Serial interface
- **Monitor**: Serial monitor for debugging

### CAN Bus Protocol
- **Interface**: TWAI (Two-Wire Automotive Interface)
- **Message ID**: Standard CAN identifiers
- **Data**: 8-byte payload per message
- **Baud rate**: Configured in firmware

### Sensor Types
- Temperature sensors
- Humidity sensors
- CO₂ sensors
- VPD (calculated)
- Light sensors

### Version Evolution
- `fullV1-V6`: Incremental improvements and bug fixes
- Test versions: Experimental features, hardware validation

## COMMANDS

```bash
# Arduino IDE
# 1. Open project folder (e.g., fullV6/)
# 2. Select board: ESP32 Dev Module
# 3. Select port: /dev/ttyUSB0
# 4. Upload

# PlatformIO (if configured)
cd Sensor\ Nodes/ESP32/fullV6
pio run --target upload
pio device monitor

# Serial monitor
# Arduino IDE: Tools → Serial Monitor
# baud rate: 115200 (typically)
```

## ANTI-PATTERNS (THIS PROJECT)

- **Never**: Upload firmware to wrong node (nodes have unique IDs)
- **Never**: Change CAN message format without updating backend parser
- **Never**: Skip sensor calibration before deployment
- **Never**: Use test firmware in production (use fullV*)
- **Never**: Modify CAN baud rate without updating all nodes

## NOTES

- **Hardware**: ESP32 microcontrollers with CAN controllers
- **Communication**: CAN bus to Raspberry Pi (via MCP2515 or built-in TWAI)
- **Deployment**: Upload via Arduino IDE or PlatformIO
- **Testing**: Test versions for validating hardware changes
- **Backend integration**: CAN processor service parses node messages
