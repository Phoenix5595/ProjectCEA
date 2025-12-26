---
name: DFR0971 Test Script
overview: Create a comprehensive test script for the DFR0971 2-Channel I2C 0-10V DAC Module with YAML configuration. The script will include basic voltage/intensity tests, ramp testing, and interactive mode for manual control.
todos: []
---

# DFR0971 Test Script with YAML Configuration

Create a standalone test script for the DFR0971 DAC module in `Test Scripts/Light/` with a YAML configuration file.

## Files to Create

1. **`Test Scripts/Light/test_dfr0971.py`** - Main test script
2. **`Test Scripts/Light/dfr0971_config.yaml`** - Configuration file

## Implementation Details

### Configuration File (`dfr0971_config.yaml`)

- I2C bus number (default: 1 for Raspberry Pi)
- Board configuration:
- I2C address (default: 0x58)
- Board name (optional)
- Simulation mode flag (for testing without hardware)
- Test parameters:
- Default test voltage/intensity values
- Ramp test parameters (step size, delay)

### Test Script (`test_dfr0971.py`)

**Features:**

1. **Basic Tests**

- Initialize board and verify connection
- Test voltage setting (0-10V) on both channels
- Test intensity setting (0-100%) on both channels
- Verify channel independence

2. **Ramp Test**

- Gradually increase intensity from 0% to 100%
- Gradually decrease intensity from 100% to 0%
- Configurable step size and delay

3. **Interactive Mode**

- Command-line interface for manual control
- Commands:
    - Set voltage: `voltage <channel> <voltage>`
    - Set intensity: `intensity <channel> <percentage>`
    - Get status: `status [channel]`
    - Ramp: `ramp <channel> <start> <end> <steps>`
    - Help: `help`
    - Quit: `quit` or `exit`

**Implementation Approach:**

- Import the existing `DFR0971Driver` from `Infrastructure/automation-service/app/hardware/dfr0971.py`
- Use `pyyaml` for configuration loading
- Add proper error handling and logging
- Support both simulation and hardware modes
- Include safety checks (voltage/intensity limits)

## Dependencies

- `pyyaml` (for YAML config parsing)
- `smbus2` (already used by DFR0971 driver, for I2C communication)
- Existing DFR0971 driver module

## Usage

```bash
# Run with default config
python3 test_dfr0971.py

# Run with custom config
python3 test_dfr0971.py --config custom_config.yaml

# Run in simulation mode (from config)
python3 test_dfr0971.py --simulation
```

The script will:

1. Load configuration from YAML
2. Initialize the DFR0971 board