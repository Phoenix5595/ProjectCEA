# Raspberry Pi 5 Power Consumption Tracking

This document tracks the power consumption of all hardware modules/cards connected to the Raspberry Pi 5.

**Raspberry Pi 5 Specifications:**
- **Model**: Raspberry Pi 5 (8GB RAM, 512GB SSD)
- **GPIO 3.3V Rail Capacity**: ~50-100 mA (typical safe limit)
- **GPIO 5V Rail Capacity**: 
  - **Directly connected to power supply** (not regulated by Pi)
  - With 5V/5A power supply: ~4.2A available (Pi uses ~800mA, leaving ~4.2A for peripherals)
  - **Conservative safe limit for GPIO header**: ~2A (to ensure system stability)
  - Total power supply capacity: 5A (shared between Pi and all peripherals)
- **Power Supply**: 5V/5A (25W) recommended

---

## Connected Hardware Modules

### 5V Rail Modules

| Module | Quantity | Voltage | Operating Current | Standby Current | Total Operating | Total Standby | Notes |
|--------|----------|---------|-------------------|-----------------|-----------------|---------------|-------|
| **MCP23017 Relay Board** | 1 | 5V | ~1-2 mA | ~0.5-1 mA | 1-2 mA | 0.5-1 mA | 16-channel relay expander (I2C) |
| **DFR0971 DAC Module** | 3 | 5V | ~4 mA | ~1-2 mA | 12 mA | 3-6 mA | 2-channel 0-10V DAC per board (I2C) |
| **RS485 Transceiver (MAX13487)** | 1 | 5V | ~10-15 mA | ~2-3 mA | 10-15 mA | 2-3 mA | RS485 transceiver for soil sensors (UART) |
| **Total 5V Modules** | 5 | - | - | - | **23-29 mA** | **5.5-10 mA** | - |

### 3.3V Rail Modules

| Module | Quantity | Voltage | Operating Current | Standby Current | Total Operating | Total Standby | Notes |
|--------|----------|---------|-------------------|-----------------|-----------------|---------------|-------|
| **CAN Bus Interface (MCP2515)** | 1 | 3.3V | ~5-10 mA | ~1-2 mA | 5-10 mA | 1-2 mA | SPI-based CAN controller |
| **Total 3.3V Modules** | 1 | - | - | - | **5-10 mA** | **1-2 mA** | - |

---

## Power Consumption Summary

### Total Current Draw by Rail

| State | 3.3V Rail | 5V Rail | Total System |
|-------|-----------|---------|--------------|
| **Operating** | 5-10 mA | 23-29 mA | **28-39 mA** |
| **Standby** | 1-2 mA | 5.5-10 mA | **6.5-12 mA** |

### Power Consumption (Watts)

| State | 3.3V Power | 5V Power | Total Power |
|-------|------------|----------|-------------|
| **Operating** | ~0.016-0.033 W | ~0.115-0.145 W | **~0.131-0.178 W** |
| **Standby** | ~0.003-0.007 W | ~0.027-0.05 W | **~0.030-0.057 W** |

---

## Raspberry Pi Capacity Analysis

### 3.3V Rail Usage

| Metric | Value | Percentage |
|--------|-------|------------|
| **Total Operating Current** | 5-10 mA | **5-20%** of 50-100 mA capacity |
| **Total Standby Current** | 1-2 mA | **1-4%** of 50-100 mA capacity |
| **Status** | ✅ **Well within limits** | Safe margin available (40-95 mA remaining) |

### 5V Rail Usage

| Metric | Value | Percentage |
|--------|-------|------------|
| **Total Operating Current** | 23-29 mA | **1.15-1.45%** of 2A safe limit, **0.55-0.69%** of 4.2A available |
| **Total Standby Current** | 5.5-10 mA | **0.275-0.5%** of 2A safe limit, **0.13-0.24%** of 4.2A available |
| **Status** | ✅ **Very low** | Plenty of headroom (~1971-4177 mA remaining) |
| **Note** | GPIO 5V pins are directly connected to power supply | Conservative 2A limit recommended for stability |

---

## Detailed Module Specifications

### MCP23017 Relay Board (5V Rail)

- **Module**: MCP23017 16-Channel I2C GPIO Expander
- **I2C Address**: 0x20 (default, configurable 0x20-0x27)
- **Voltage**: **5V** (can run on 3.3V or 5V, using 5V to reduce 3.3V load)
- **Operating Current**: ~1-2 mA
- **Standby Current**: ~0.5-1 mA
- **Power**: ~0.005-0.01 W at 5V
- **Function**: Controls relay ON/OFF for all devices (heaters, fans, lights, pumps, etc.)

### DFR0971 DAC Module (x3) (5V Rail)

- **Module**: DFRobot DFR0971 2-Channel I2C 0-10V DAC Module
- **I2C Addresses**: 0x58, 0x59, 0x5A (configurable via jumpers)
- **Voltage**: **5V** (can run on 3.3V or 5V, using 5V to reduce 3.3V load)
- **Operating Current**: ~4 mA per board
- **Standby Current**: ~1-2 mA per board
- **Power**: ~0.02 W (20 mW) per board at 5V
- **Function**: Controls light dimming intensity (0-10V output per channel)
- **Channels**: 2 channels per board = 6 total channels (3 boards × 2 channels)

**Per Board:**
- Operating: 4 mA
- Standby: 1-2 mA
- Power: ~0.02 W

**Total (3 boards):**
- Operating: 12 mA
- Standby: 3-6 mA
- Power: ~0.06 W

### RS485 Transceiver (MAX13487) (5V Rail)

- **Module**: MAX13487 RS485 Transceiver Board
- **Voltage**: **5V** (can run on 3.3V or 5V, using 5V to reduce 3.3V load)
- **Operating Current**: ~10-15 mA
- **Standby Current**: ~2-3 mA
- **Power**: ~0.05-0.075 W at 5V
- **Function**: RS485 communication with DFRobot soil sensors (MODBUS-RTU)
- **Interface**: UART (GPIO serial port)
- **Note**: Power consumption varies by board model (typical range: 10-15 mA)

### CAN Bus Interface (MCP2515) (3.3V Rail)

- **Module**: MCP2515 CAN Bus Controller (SPI-based)
- **Voltage**: **3.3V** (requires 3.3V, cannot run on 5V)
- **Operating Current**: ~5-10 mA
- **Standby Current**: ~1-2 mA
- **Power**: ~0.016-0.033 W at 3.3V
- **Function**: CAN bus communication with ESP32 sensor nodes
- **Interface**: SPI (not USB adapter)
- **Status**: ✅ **Confirmed installed**
- **Note**: Only module that must use 3.3V rail

---

## Power Supply Recommendations

### Current Setup

- **Total Operating Current**: 28-39 mA
- **Total Standby Current**: 6.5-12 mA
- **Recommended Power Supply**: 5V/5A (25W) for Raspberry Pi 5
- **Status**: ✅ **More than sufficient** - modules use < 2% of available capacity

### Adding More Modules

**Safe Limits:**
- **3.3V Rail**: Can add up to ~40-95 mA more (50-100 mA total capacity)
- **5V Rail**: Can add up to ~1971-4177 mA more (2A safe limit, up to 4.2A available with 5A power supply)

**Example Additions:**
- Additional DFR0971 boards: +4 mA each
- Additional I2C sensors: +1-5 mA each
- Display modules: +20-100 mA each (depends on type)

---

## Notes

1. **Power Distribution Strategy**:
   - **5V Rail**: All modules that can run on 5V (MCP23017, DFR0971 x3) = 13-14 mA
   - **3.3V Rail**: Only modules that require 3.3V (MCP2515 CAN) = 5-10 mA
   - This optimizes power distribution by using the higher-capacity 5V rail for compatible modules

2. **All modules are powered from GPIO header** (no USB adapters used)
3. **Power consumption is very low** - total is < 40 mA, well within Pi capacity
4. **DFR0971 boards are the largest consumers** (12 mA total on 5V rail), followed by RS485 (10-15 mA)
5. **Standby power is minimal** - most modules draw < 2 mA when idle
6. **No external power supplies needed** for these modules
7. **Plenty of headroom** for adding more modules:
   - 5V rail: ~1971-4177 mA remaining (using 2A safe limit, up to 4.2A available)
   - 3.3V rail: ~40-95 mA remaining
8. **5V rail is directly connected to power supply** - not regulated by Pi, so current draw reduces total available for Pi and other peripherals
9. **RS485 transceiver** is on 5V rail to reduce 3.3V load (MAX13487 can run on either voltage)

---

## Update Log

| Date | Change | Updated By |
|------|--------|------------|
| 2025-12-22 | Initial power tracking document created | System |
| 2025-12-22 | Added 3x DFR0971 boards (5V rail) | System |
| 2025-12-22 | Added MCP23017 relay board (5V rail) | System |
| 2025-12-22 | Confirmed MCP2515 CAN bus card installed (3.3V rail) | System |
| 2025-12-22 | Updated power distribution: 5V vs 3.3V rails | System |
| 2025-12-22 | Updated 5V rail capacity to 2A safe limit | System |
| 2025-12-22 | Added RS485 transceiver (MAX13487) on 5V rail | System |

---

## References

- DFR0971 Power Consumption: ~4 mA operating, ~1-2 mA standby
- MCP23017 Power Consumption: ~1-2 mA operating, ~0.5-1 mA standby
- Raspberry Pi 5 GPIO Specifications: [Official Documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)

