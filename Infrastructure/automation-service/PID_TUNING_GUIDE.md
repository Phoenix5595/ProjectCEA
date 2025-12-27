# PID Tuning Guide

This guide explains how to select and tune PID (Proportional-Integral-Derivative) controller parameters for your CEA automation system.

## Understanding PID Parameters

### Kp (Proportional Gain)
- **What it does**: Responds to the current error (difference between setpoint and current value)
- **Effect**: Higher Kp = faster response, but may cause overshoot and oscillation
- **Too high**: System oscillates, overshoots setpoint
- **Too low**: Slow response, takes long time to reach setpoint

### Ki (Integral Gain)
- **What it does**: Eliminates steady-state error (accumulates past errors)
- **Effect**: Higher Ki = eliminates offset faster, but can cause overshoot
- **Too high**: System becomes unstable, oscillates
- **Too low**: System may never reach exact setpoint (steady-state error)

### Kd (Derivative Gain)
- **What it does**: Predicts future error based on rate of change
- **Effect**: Reduces overshoot and oscillation
- **Too high**: System becomes sensitive to noise, may become unstable
- **Too low or zero**: More overshoot, slower settling

## Default Starting Values

Based on your system configuration, here are recommended starting values:

### Heater (Temperature Control)
- **Kp**: 25.0 (range: 0.0 - 100.0)
- **Ki**: 0.02 (range: 0.0 - 1.0)
- **Kd**: 0.0 (range: 0.0 - 10.0) - Usually 0 for heaters

**Why these values?**
- Temperature systems are slow-responding, so moderate Kp
- Small Ki to eliminate steady-state error without overshoot
- Kd = 0 because heaters don't need derivative control (thermal mass provides natural damping)

### Extraction Fan (Temperature Cooling Control)
- **Kp**: 25.0 (range: 0.0 - 100.0)
- **Ki**: 0.02 (range: 0.0 - 1.0)
- **Kd**: 0.0 (range: 0.0 - 10.0) - Usually 0 for fans

**Why these values?**
- Same PID parameters as heaters because fans respond similarly to temperature changes
- Temperature systems are slow-responding, so moderate Kp
- Small Ki to eliminate steady-state error without overshoot
- Kd = 0 because thermal mass provides natural damping

**Configuration**:
To enable PID control for extraction fans, set in `automation_config.yaml`:
```yaml
devices:
  "Flower Room":
    main:
      exhaust_fan:
        channel: 1
        device_type: fan
        pid_enabled: true
        pid_setpoints:
          cooling_setpoint: 1      # Priority 1: use cooling_setpoint
          # vpd_setpoint: 2         # Priority 2: optional VPD control
        pwm_period: 100
```

**Priority-Based Multi-Setpoint Control**:
Fans can use multiple setpoints with priorities. Lower priority number = higher priority. Only the highest-priority active PID drives the fan. Example:
- Priority 1 (cooling_setpoint): Temperature control (highest priority)
- Priority 2 (vpd_setpoint): VPD optimization (lower priority, only used if cooling doesn't need action)

### CO2 System
- **Kp**: 10.0 (range: 0.0 - 50.0)
- **Ki**: 0.01 (range: 0.0 - 0.5)
- **Kd**: 0.0 (range: 0.0 - 5.0)

**Why these values?**
- CO2 systems respond faster than temperature, so lower Kp
- Very small Ki to prevent overshoot
- Kd = 0 typically sufficient

## Tuning Strategy

### Step 1: Start with Defaults
Begin with the default values from your configuration. These are proven starting points.

### Step 2: Tune Kp First (Proportional)
1. Set Ki = 0 and Kd = 0
2. Gradually increase Kp until the system responds quickly
3. Stop when you see oscillation or overshoot
4. Reduce Kp by 20-30% for safety margin

**What to watch:**
- ✅ Good: System reaches setpoint within reasonable time (5-10 minutes for temperature)
- ❌ Bad: System oscillates around setpoint
- ❌ Bad: System overshoots setpoint significantly

### Step 3: Add Ki (Integral)
1. Keep Kp at the value from Step 2
2. Gradually increase Ki from 0
3. Watch for steady-state error elimination
4. Stop if system becomes unstable or oscillates

**What to watch:**
- ✅ Good: System reaches exact setpoint (no steady-state error)
- ❌ Bad: System oscillates or becomes unstable
- ❌ Bad: System overshoots and takes long to settle

### Step 4: Add Kd (Derivative) - Optional
1. Only add Kd if you have overshoot or oscillation
2. Start with small values (0.1 - 1.0)
3. Gradually increase until overshoot is reduced
4. Be careful - too much Kd can make system sensitive to noise

**When to use Kd:**
- System overshoots setpoint
- System oscillates around setpoint
- You need faster settling time

**When NOT to use Kd:**
- System is stable without it (most heaters)
- Sensor readings are noisy (Kd amplifies noise)

## Device-Specific Guidelines

### Heaters
- **Typical Kp**: 20-30
- **Typical Ki**: 0.01-0.05
- **Typical Kd**: 0 (thermal mass provides damping)
- **Response time**: 5-15 minutes to reach setpoint
- **Overshoot tolerance**: ±1°C is acceptable

### CO2 Systems
- **Typical Kp**: 5-15
- **Typical Ki**: 0.005-0.02
- **Typical Kd**: 0-1.0
- **Response time**: 2-5 minutes
- **Overshoot tolerance**: ±50 ppm is acceptable

### Extraction Fans (Temperature Control)
- **Typical Kp**: 20-30 (same as heaters)
- **Typical Ki**: 0.01-0.05 (same as heaters)
- **Typical Kd**: 0 (same as heaters - thermal mass provides damping)
- **Response time**: 5-15 minutes (same as heaters)
- **Overshoot tolerance**: ±1°C is acceptable

**Why these values?**
- Same PID parameters as heaters because fans respond similarly to temperature changes
- Temperature systems are slow-responding, so moderate Kp
- Small Ki to eliminate steady-state error without overshoot
- Kd = 0 because thermal mass provides natural damping

**Configuration**:
To enable PID control for extraction fans, set in `automation_config.yaml`:
```yaml
devices:
  "Flower Room":
    main:
      exhaust_fan:
        channel: X
        device_type: fan
        pid_enabled: true  # Enable PID control (same as heaters)
        pwm_period: 100    # Optional: PWM period in seconds
```

**How Extraction Fan PID Works**:
Extraction fans use **the same PID control mechanism as heaters**:
1. **Same PID Formula**: error = setpoint - current_value
2. **Same PWM Control**: PID output 0-100% controls fan duty cycle
3. **Same Tuning Approach**: Use the same tuning strategy as heaters

**Configuration in automation_config.yaml**:
Add PID parameters for fans (can use same values as heaters):
```yaml
control:
  pid:
    # Heater PID parameters
    heater_kp: 25.0
    heater_ki: 0.02
    heater_kd: 0.0
    
    # Extraction fan PID parameters (can use same as heaters)
    fan_kp: 25.0      # Same as heater_kp
    fan_ki: 0.02      # Same as heater_ki
    fan_kd: 0.0       # Same as heater_kd
    
    # Or use default_kp, default_ki, default_kd if fan-specific not set
    default_kp: 10.0
    default_ki: 0.01
    default_kd: 0.0
```

**Important Notes**: 
- Extraction fans use **the same PID control mechanism as heaters**
- Same tuning strategy: start with heater values, adjust as needed
- Fan PID uses the same temperature sensor and setpoint as heater PID
- Ensure interlock rules prevent heater and exhaust fan from running simultaneously
- Enable PID by setting `pid_enabled: true` in device configuration
- **Note**: The control system may need code updates to support PID for 'fan' device type (currently supports 'heater' and 'co2')

### Fans (ON/OFF Only - Default)
- **Default behavior**: Fans are binary ON/OFF devices
- Use hysteresis control for VPD (dehumidification) control
- To use PID for temperature cooling, enable `pid_enabled: true` in config

### Dehumidifiers (ON/OFF Only)
- **No PID control**: Dehumidifiers are binary ON/OFF devices
- Use hysteresis control instead

## Common Problems and Solutions

### Problem: System Oscillates
**Symptoms**: Value goes above setpoint, then below, then above again
**Solutions**:
- Reduce Kp by 20-30%
- Reduce Ki by 50%
- Add small Kd (0.5-2.0) if not already present

### Problem: Slow Response
**Symptoms**: Takes very long time to reach setpoint
**Solutions**:
- Increase Kp by 20-30%
- Check if device has enough capacity (heater too small?)

### Problem: Steady-State Error
**Symptoms**: System stabilizes but not at exact setpoint
**Solutions**:
- Increase Ki gradually (start with 0.01 increments)
- Don't increase too much or system will oscillate

### Problem: Overshoot
**Symptoms**: System goes well past setpoint before settling
**Solutions**:
- Reduce Kp by 10-20%
- Add Kd (start with 0.5-1.0)
- Reduce Ki if it's high

### Problem: System Unstable
**Symptoms**: Wild oscillations, system never settles
**Solutions**:
- Reduce all parameters by 50%
- Start over with Step 2 (tune Kp first)
- Check for sensor issues or device malfunctions

### Problem: Extraction Fan Issues
**Symptoms**: Fan doesn't respond, oscillates, or cycles rapidly
**Solutions**:
- Use same troubleshooting as heaters (same PID mechanism)
- Reduce Kp by 20-30% if oscillating
- Add Kd (0.5-2.0) if overshooting
- Increase PWM period (e.g., 120-150 seconds) to slow cycling
- Check if heater and fan are fighting - ensure interlock rules are configured
- Verify PID is enabled: `pid_enabled: true` in device config

## Validation Ranges

The system enforces these limits to prevent unsafe values:

**Heater:**
- Kp: 0.0 - 100.0
- Ki: 0.0 - 1.0
- Kd: 0.0 - 10.0

**CO2:**
- Kp: 0.0 - 50.0
- Ki: 0.0 - 0.5
- Kd: 0.0 - 5.0

**Extraction Fan (same as Heater):**
- Kp: 0.0 - 100.0
- Ki: 0.0 - 1.0
- Kd: 0.0 - 10.0

## Testing Your Tuning

1. **Set a test setpoint** (e.g., 2°C above current temperature)
2. **Observe the response**:
   - How long to reach setpoint?
   - Any overshoot?
   - Does it settle at exact setpoint?
   - Any oscillation?
3. **Make small adjustments** (10-20% changes)
4. **Test again** with different setpoints
5. **Document your final values** for future reference

## Best Practices

1. **Make small changes**: Adjust one parameter at a time by 10-20%
2. **Test thoroughly**: Wait 15-30 minutes between changes to see full response
3. **Document changes**: Keep notes on what values you tried and results
4. **Start conservative**: Better to be slightly slow than unstable
5. **Consider plant safety**: Avoid aggressive tuning that could stress plants
6. **Monitor during different conditions**: Test during day/night, different seasons

## Temperature Above Setpoint (Lights Heating the Room)

### What Happens Currently

When lights heat the room and temperature rises **above** the setpoint:

1. **PID Controller Behavior**:
   - Error = setpoint - current_value becomes **negative**
   - PID output is clamped to 0-100%, so it becomes **0%**
   - **Heater turns OFF** (correct - no need to heat)

2. **The Problem**:
   - Heater is off, but temperature continues to rise from lights
   - **No automatic cooling** is activated
   - Temperature may exceed setpoint significantly

### Solutions

#### Option 1: Configure Exhaust Fans (Recommended)

Configure exhaust fans to turn ON when temperature exceeds setpoint:

**Manual Configuration** (via frontend or API):
- Set exhaust fan to turn ON when temperature > setpoint + threshold
- This requires a control rule or manual intervention

**Current Limitation**: 
- Fans are ON/OFF devices (not PID controlled)
- No automatic temperature-based fan control exists yet
- You may need to manually control fans during light periods

#### Option 2: Use Interlock Rules

Configure interlock rules in `automation_config.yaml`:
```yaml
interlocks:
  - when_device: exhaust_fan
    then_device: heater_1
    action: force_off
```

This ensures heater is OFF when exhaust fan is ON (prevents fighting).

#### Option 3: Adjust Setpoints for Day Period

**Practical Solution**: Set higher temperature setpoints during DAY mode when lights are on:

- **DAY mode setpoint**: 27-28°C (accounts for light heat)
- **NIGHT mode setpoint**: 21-22°C (no light heat)

This way, the system expects higher temperatures during light periods.

#### Option 4: Reduce Light Intensity

If temperature consistently exceeds setpoint:
- Reduce light intensity during hottest parts of day
- Use dimming to control heat output
- Balance light needs with temperature control

### Future Enhancement

A future enhancement could add:
- **Cooling control**: Exhaust fans turn ON when temperature > setpoint + threshold
- **Dual-mode PID**: Separate heating and cooling PID controllers
- **Temperature-based fan control**: Fans controlled by temperature error (inverse of heater control)

### Best Practice

1. **Monitor temperature during light periods**
2. **Set DAY mode setpoints higher** to account for light heat
3. **Manually control exhaust fans** if temperature exceeds setpoint
4. **Consider light intensity** - dimming reduces heat output
5. **Use interlock rules** to prevent heater and exhaust fan from fighting

### Example Scenario

**Problem**: Lights on, temperature rises to 28°C, setpoint is 25°C

**Current behavior**:
- Heater PID: error = 25 - 28 = -3°C → output = 0% → heater OFF ✅
- Exhaust fan: No automatic control → stays OFF ❌
- Result: Temperature stays high

**Solution**:
- Set DAY mode setpoint to 27°C (accounts for light heat)
- Or manually turn on exhaust fan
- Or reduce light intensity during hottest period

## When to Re-tune

You may need to re-tune PID parameters when:
- Adding/removing equipment (changes system response)
- Changing growing conditions (different plant stage)
- Seasonal changes (outdoor temperature affects system)
- System behavior changes (equipment aging, sensor drift)

## Getting Help

If you're having trouble tuning:
1. Start with default values from config
2. Make one small change at a time
3. Document what you observe
4. Check system logs for errors or warnings
5. Verify sensors are working correctly
6. Ensure devices have adequate capacity

## Example Tuning Sessions

### Example 1: Tuning Heater for Flower Room

1. **Start**: Kp=25.0, Ki=0.02, Kd=0.0 (defaults)
2. **Observe**: System reaches setpoint in 8 minutes, slight overshoot of 0.5°C
3. **Adjust**: Reduce Kp to 22.0 (reduce overshoot)
4. **Observe**: System reaches setpoint in 10 minutes, no overshoot, settles at setpoint
5. **Result**: Kp=22.0, Ki=0.02, Kd=0.0 works well - keep these values

**Final values**: Kp=22.0, Ki=0.02, Kd=0.0

### Example 2: Tuning Extraction Fan (Same as Heater)

**Scenario**: Tuning extraction fan for Flower Room (same approach as heater)

1. **Start**: Kp=25.0, Ki=0.02, Kd=0.0 (same defaults as heater)
2. **Observe**: System responds to temperature changes, slight overshoot of 0.5°C
3. **Adjust**: Reduce Kp to 22.0 (reduce overshoot, same as heater tuning)
4. **Observe**: System reaches setpoint in 10 minutes, no overshoot, settles at setpoint
5. **Result**: Kp=22.0, Ki=0.02, Kd=0.0 works well - same values as heater

**Final values**: Kp=22.0, Ki=0.02, Kd=0.0

**Key points**:
- Use **same tuning approach** as heaters
- Start with **same default values** as heaters
- Same response characteristics (5-15 minutes)
- Same overshoot tolerance (±1°C)


