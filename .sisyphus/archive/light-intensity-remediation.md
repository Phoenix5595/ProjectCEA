# Light Intensity Remediation Plan

## Summary
Investigation reveals Veg Room lights are capped at 40% brightness by safety_level configuration, while Flower Room lights have no such restriction. Board 0x59 (Apache/Veg Room light_3) shows known I2C communication issues in code.

## Priority Actions

### 1. SAFETY LEVEL VERIFICATION (HIGH)
- **Objective**: Confirm whether safety level capping is the root cause
- **Action**: 
  1. Temporarily set Veg Room safety_level to 0 for testing
  2. Test full brightness range (10%-100%) on Veg Room lights
  3. Measure actual voltage at LED drivers vs commanded values
  4. Restore original safety levels after testing
- **Success Criteria**: Veg Room lights achieve >40% brightness with linear voltage response

### 2. I2C STABILITY ASSESSMENT (HIGH) 
- **Objective**: Verify board 0x59 communication reliability
- **Target**: Apache light (Board 1, Channel 1) and Veg Room light_3 (Board 1, Channel 0)
- **Action**:
  1. Monitor automation-service logs for errno 121 errors during intensity changes
  2. Perform 10-minute intensity change test on affected lights
  3. Use `i2cdetect -y 1` to verify board presence
  4. Check signal integrity with oscilloscope if available
- **Success Criteria**: No I2C errors; intensity changes complete successfully

### 3. DAC CALIBRATION VERIFICATION (MEDIUM)
- **Objective**: Ensure 0-100% maps correctly to 0-10V output
- **Action**:
  1. Generate intensity sweep: 25%, 50%, 75%, 100%
  2. Measure actual voltage at LED driver inputs with multimeter
  3. Compare measured vs calculated: `voltage = (intensity/100) * 10.0`
  4. Verify 12-bit DAC encoding: `dac_value = int(voltage * 1000) << 4`
- **Success Criteria**: Measured voltage within ±5% of calculated value

### 4. CONFIGURATION OPTIMIZATION (LOW)
- **Objective**: Standardize safety levels across rooms
- **Action**:
  1. Review business requirements for Veg Room 40% cap
  2. If cap is intentional, document clearly in automation_config.yaml comments
  3. If cap is unintentional, set all rooms to safety_level: 0
  4. Consider per-room safety_level parameters instead of hardcoded values
- **Success Criteria**: Consistent safety level policy across all rooms

## Testing Strategy

### Non-Destructive First
1. Use API endpoints to test brightness changes
2. Monitor Redis state changes in real-time
3. Review TimescaleDB `effective_light_intensity` history
4. Use simulation mode for initial validation

### Hardware Validation
1. Multimeter measurements at DFR0971 output terminals
2. I2C bus analysis with logic analyzer
3. Temperature response testing at different ambient conditions
4. EEPROM persistence verification (power cycle test)

### Success Metrics
- Veg Room lights achieve >40% brightness when safety_level = 0
- Board 0x59 shows no I2C errors during 10-minute test
- DAC linearity within ±5% across full intensity range
- No unexpected safety level reapplications after restart

## Rollback Plan
If any test worsens light performance:
1. Immediate safety_level restoration to previous values
2. Use `./rollback.sh` for full system rollback
3. Document failure and restore last known good configuration

## Implementation Notes
- All changes via `./config_cli.py` for audit trail
- Test in isolation before applying to production
- Monitor system logs during each test phase
- Maintain existing safety interlocks during testing

## Timeline
- **Day 1**: Safety level verification and I2C testing
- **Day 2**: DAC calibration and configuration review  
- **Day 3**: Full system integration testing
- **Day 4**: Production deployment with monitoring

---
*Plan ready for execution. Use `/start-work` to begin implementation.*