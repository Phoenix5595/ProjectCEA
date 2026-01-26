# Draft: Light Intensity Investigation

## Context
- User request: Investigate why light intensity isn't working properly in the greenhouse automation system.
- Scope: Focus on Grow Room lighting controlled by Infrastructure/automation-service, interfacing with CAN-bus LED drivers and any light-sensing instruments.
- Timeframe: Immediate investigation with iterative findings; plan to validate with tests or measurements.

## Requirements (confirmed)
- [1] Identify root cause(s) of intensity discrepancy between commanded setpoint and actual light output.
- [2] Determine whether the issue is systemic (all rooms) or isolated to a single channel/driver.
- [3] Propose concrete remediation steps and a test plan to verify resolution.
- [4] Maintain safety and avoid hardware damage; document any configuration changes.
- [5] Provide evidence (logs, CAN traces, sensor readings) to support findings.

## Technical Decisions
- [D1] Approach: perform end-to-end tracing from light setpoint generation in control loop to DAC/PWM output and LED driver response; validate sensor feedback if present.
- [D2] Measurement: rely on existing sensors (lux/ PAR) or add lightweight instrumentation if necessary to quantify light output.
- [D3] Risk tolerance: prefer non-destructive tests; avoid altering automation behavior beyond necessary diagnostics.
- [D4] Abstraction: treat the light control path as a pipeline: (setpoint) -> control loop -> CAN/DAC -> LED driver -> light output -> sensor (if any) -> feedback

## Research Findings

### End-to-End Brightness Control Path (COMPLETE)
- **Setpoints & Ramping**: 
  - SetpointManager/RampManager (setpoint_manager.py) computes effective brightness including ramp persistence across restarts
  - Redis keys: `setpoint:*`, `effective_setpoint:*`, `ramp:*` for brightness ramp state
  - DB tables: `setpoints`, `effective_setpoints` with `nominal_light_intensity`, `effective_light_intensity`, `ramp_progress_light`
- **Scheduling**: 
  - Scheduler (scheduler.py) calculates target intensity via `get_light_intensity_details()`, including MINIMUM_LIGHT_INTENSITY (10%)
  - Time-based schedules drive DAY/NIGHT mode transitions affecting brightness
- **Control Loop**: 
  - ControlEngine orchestrates; DeviceProcessor adds `light_intensity` context for dimmable lights
  - Uses dfr0971_manager to apply intensity to hardware
- **Hardware Interface**:
  - DFR0971 DAC boards (0x58/0x59/0x5A) provide 0-10V dimming signals
  - MCP23017 relay expander (0x20) provides ON/OFF control
  - **No CAN path for lights** - Lights are controlled via I2C DACs, not CAN messages

### Hardware Mapping (COMPLETE)
- **DFR0971 Boards**:
  - Board 0: 0x58 (Veg Top and Bottom Right Light Board)
  - Board 1: 0x59 (Apache° and Veg Bottom Left Light Board)  
  - Board 2: 0x5A (Chilled Light Board)
  - Each board has 2 channels (0, 1)
- **Light-to-Board Mapping**:
  - **Flower Room**:
    - light_1 (Chilled Front): Board 2 (0x5A), Channel 0, MCP23017 Channel 3, safety_level 0
    - light_2 (Apache): Board 1 (0x59), Channel 1, MCP23017 Channel 4, safety_level 0
    - light_3 (Chilled Back): Board 2 (0x5A), Channel 1, MCP23017 Channel 5, safety_level 0
  - **Veg Room**:
    - light_1 (Eyefinity Top): Board 0 (0x58), Channel 0, MCP23017 Channel 3, safety_level 0
    - light_2 (Ridgetop Bottom Right): Board 0 (0x58), Channel 1, MCP23017 Channel 4, safety_level 40
    - light_3 (Ridgetop Bottom Left): Board 1 (0x59), Channel 0, MCP23017 Channel 5, safety_level 40

### Recent Changes Impact
- **Ramp Persistence**: Recent commit added ramp state persistence across restarts via Redis (RampManager.persist_ramp/restore_ramps_from_redis)
- **Board Initialization**: Lighting initialization restores intensities from Redis/DB on startup

### Common Failure Patterns (from external patterns)
- **DAC Scaling Issues**: Incorrect 0-100% → 0-10V mapping; 12-bit DAC encoding errors
- **Calibration Drift**: DAC/LED driver non-linearity over temperature/time
- **I2C Communication Failures**: errno 121 Remote I/O errors, especially on board 0x59
- **Safety Level Override**: Configured safety_level (e.g., Veg Room lights capped at 40%) restricting brightness
- **Range Setting Issues**: Boards losing 10V range setting, defaulting to 5V

## Hypotheses (PRIORITIZED)

### H1 (HIGH): Veg Room Safety Level Capping (40%)
- **Evidence**: Veg Room light_2 and light_3 have `safety_level: 40` in automation_config.yaml
- **Impact**: Maximum brightness limited to 40% regardless of setpoint or schedule
- **Detection**: Check if actual output correlates with 40% cap
- **Verification**: Remove safety level temporarily to test full brightness

### H2 (HIGH): DFR0971 Board 0x59 Communication Issues  
- **Evidence**: Code shows special handling for board 0x59 due to known I2C issues (multiple retries, force re-initialization)
- **Impact**: Apache light (Board 1, Channel 1) and Veg Room light_3 (Board 1, Channel 0) may experience intermittent failures
- **Detection**: Look for I2C error logs or missed intensity updates
- **Verification**: Test I2C stability, monitor logs for errno 121

### H3 (MEDIUM): DAC Scaling/Range Issues
- **Evidence**: Code converts intensity% → voltage → 12-bit DAC value with left-shift; potential off-by-one or range confusion
- **Impact**: 100% intensity may not reach 10V, resulting in dimmer output
- **Detection**: Measure actual voltage at LED driver input vs commanded
- **Verification**: Validate conversion math and range settings

### H4 (LOW): Schedule/Mode Conflicts
- **Evidence**: DAY/NIGHT mode transitions drive brightness changes; ramp logic may conflict with manual intensity changes
- **Impact**: Unexpected brightness behavior during mode transitions
- **Detection**: Check mode changes vs brightness timeline
- **Verification**: Test schedule changes in isolation

## Diagnostic Plan (EVIDENCE-DRIVEN)

### Step 1: Verify Current State (NON-DESTRUCTIVE)
1. **Read Current Configuration**:
   - Extract current brightness setpoints and safety levels from automation_config.yaml
   - Check Redis for current `effective_setpoint` values
   - Query TimescaleDB for recent `effective_light_intensity` history
2. **Hardware Baseline**:
   - Use multimeter to measure voltage at LED driver inputs for each channel
   - Verify MCP23017 relay operation (ON/OFF status)
   - Document baseline: commanded vs measured voltage

### Step 2: Test Controlled Intensity Changes
1. **API Testing**:
   - Use `POST /api/lights/{location}/{cluster}/{device}/intensity` to set known values (25%, 50%, 75%, 100%)
   - Record commanded intensity and actual hardware response
2. **Direct Hardware Testing**:
   - Use config_cli or direct DFR0971 calls to bypass automation
   - Verify linear response across intensity range
3. **Safety Override Test**:
   - Temporarily set safety_level to 0 for Veg Room lights
   - Verify if brightness increases above previous 40% limit

### Step 3: Monitor for Common Issues
1. **I2C Communication**: 
   - Monitor logs for errno 121, retry patterns, especially for board 0x59
2. **Range Setting**:
   - Verify boards maintain 10V output range after restart
   - Check for EEPROM storage vs runtime state divergence
3. **Temperature Compensation**:
   - Test DAC output at different ambient temperatures
   - Check for non-linear brightness response

### Step 4: Validation Criteria
- **Success**: Measured voltage matches commanded within ±5% across full range
- **Consistency**: No I2C errors over 10-minute test period  
- **Linearity**: Brightness increase is proportional to voltage change
- **Persistence**: Brightness survives restart and matches Redis/DB state

## Next Steps & Evidence Collection

### Immediate Data Collection:
- [ ] Capture current `effective_light_intensity` values from TimescaleDB (last 24 hours)
- [ ] Extract all I2C error logs from automation-service journal
- [ ] Document current safety_level settings per light
- [ ] Measure baseline voltages at LED drivers with multimeter

### Targeted Tests:
- [ ] Veg Room lights: Test with safety_level temporarily set to 0
- [ ] Board 0x59 lights: Extended I2C monitoring during intensity changes
- [ ] Full brightness sweep: 10% → 100% with voltage measurements at each step
- [ ] Schedule transition test: DAY → NIGHT → DAY with brightness tracking

### Decision Matrix:
| Test | Success Criteria | Failure Indicators |
|-------|------------------|-------------------|
| Safety Level Override | Veg Room lights reach >40% | Still capped at 40% |
| Board 0x59 Stability | No I2C errors over 10 minutes | errno 121 errors, retries |
| DAC Linearity | Measured voltage matches calculated within ±5% | >10% deviation, non-linear response |
| Range Persistence | Boards maintain 10V range after restart | Range resets to 5V |

## Questions for User

- [Q1] Which specific lights/rooms are experiencing intensity issues? (all lights or specific devices?)
- [Q2] What is the observed behavior? (lights not responding, wrong intensity, flickering, intermittent?)
- [Q3] When did this issue start? (recent changes, after restart, gradual degradation?)
- [Q4] Are there any visible error messages in logs or I2C communication issues?
- [Q5] Do you have access to measure actual voltage at the LED drivers?

## Scope Boundaries
- INCLUDE: End-to-end light control path; hardware interface to LED driver; light sensors; relevant software modules; CAN messages
- EXCLUDE: Other environmental sensors (temperature, humidity) unless they influence lighting; non-light hardware faults not related to LED control

---
*Investigation complete. Ready for diagnostic execution and work plan creation.*