# Post-Mortem: ProjectCEA Refactor (2026-03-24)

## Incident Summary

**Time:** 2026-03-24 14:21 EDT (deployment)
**Duration:** ~3 hours until rollback to working release
**Impact:** Lights non-functional, heating safety false alarms

## What Was Changed

### Backend (automation-service)

| File | Change | Impact |
|------|--------|--------|
| `app/control/scheduler.py` | Extracted LightRampCalculator, changed ramp logic | **BREAKS LIGHTS** |
| `app/control/hardware_batch.py` | Removed PARALLEL_I2C flag | OK |
| `app/control/device_processor.py` | Added heating safety wiring | OK |
| `app/hardware/dfr0971.py` | Removed 0x59 board hacks | OK |
| `app/redis/*.py` | Added cea:* prefix backward compat | OK |
| `app/control/sensor_reader.py` | NEW (pipeline split) | Not used |
| `app/control/climate_resolver.py` | NEW (pipeline split) | Not used |
| `app/control/setpoint_calculator.py` | NEW (pipeline split) | Not used |

### Frontend

| File | Change | Impact |
|------|--------|--------|
| `src/components/LightManager.tsx` | Renamed from VerticalLightsBlock | BUILD ERROR |
| `src/hooks/useLightControl.ts` | NEW hook | Not used |
| `src/pages/Dashboard.tsx` | Split into hooks | BUILD ERROR |
| `src/components/CircularTimePicker.tsx` | Split into components | BUILD ERROR |

## Root Cause Analysis

### Why Lights Stopped Working

The scheduler refactor introduced a `LightRampCalculator` class that was supposed to be a drop-in replacement for the ramp calculation logic. However:

1. **The delegation pattern was broken** - `get_schedule_intensity()` called `self._light_ramp_calculator.get_schedule_intensity()` but the state (`_light_ramp_state`) was split between the Scheduler and the Calculator.

2. **State inconsistency** - When the service restarted, the ramp state was lost because the Calculator's state wasn't being persisted/restored properly.

3. **The original code had MINIMUM_LIGHT_INTENSITY as a class constant** - The refactored code redefined it inside the function, causing inconsistent behavior.

### Why Heating Safety Tripped

- No sensors connected to Veg Room/Flower Room
- Temperature reading = 0.0°C
- 0.0°C < 5.0°C emergency threshold
- False emergency triggered

This is a **pre-existing design issue** - the heating safety doesn't distinguish between "no sensor" (invalid reading) and "actually 0°C" (real emergency).

## What Went Right

1. **Parallel I2C removal** - Clean simplification, no issues
2. **Redis key migration** - Backward compat worked, no data loss
3. **Heating safety wiring** - Code was correct, just triggered on false data
4. **DFR0971 simplification** - Clean removal of 0x59 hacks

## What Went Wrong

1. **Scheduler refactor** - Delegated to new class without proper testing
2. **LightManager changes** - TypeScript types mismatched after restore
3. **CircularTimePicker changes** - Could have corrupted schedule writing (not confirmed)
4. **Testing was insufficient** - No integration test before deploy

## Key Lesson

**Never refactor working code without a proper test harness.**

The scheduler's ramp logic is complex and time-sensitive. Extracting it to a new class without:
- Unit tests covering overnight schedules (17:00-05:00)
- Integration tests with real hardware
- Comparison tests showing identical output

...guaranteed subtle bugs.

## Recommendations for Future Refactors

### 1. Test-First Refactoring

Before ANY refactor:
```python
# Write a test that verifies current behavior
def test_overnight_schedule_17_to_5():
    """At 17:30, should be in SUN period for 17:00-05:00 schedule."""
    schedule = {
        'mode': 'SUN',
        'start_time': '17:00',
        'end_time': '05:00',
        'target_intensity': 100
    }
    result = get_schedule_intensity(schedule, '17:30')
    assert result == expected  # Whatever the CURRENT behavior is
```

Then refactor, and ensure all tests still pass.

### 2. Incremental Changes

Instead of:
- Extract LightRampCalculator
- Split ControlEngine pipeline
- Refactor Redis mixins

Do:
- Week 1: Add tests for current behavior
- Week 2: Refactor ONE small piece, test thoroughly
- Week 3: Repeat

### 3. Never Deploy Without

- [ ] All existing tests passing
- [ ] Manual testing in staging
- [ ] Rollback plan documented
- [ ] Health check monitoring

## Files That Need Attention

### Working (No Action Needed)
- `hardware_batch.py` - Simplified, works
- `dfc0971.py` - Simplified, works  
- `redis/` - Migration complete

### Needs Testing Before Next Deploy
- `scheduler.py` - Has inline ramp logic restored (WORKING)
- `device_processor.py` - Heating safety wired

### Needs Fix Before Deploy
- `frontend/` - TypeScript build errors

## Timeline

| Time | Event |
|------|-------|
| 09:33 | Last known good deployment |
| 14:21 | Broken deployment (refactor) |
| 17:23 | User reports issue |
| 17:29 | Rollback to 09:33 release |
| 17:34 | System operational |

## Status

**Working Release:** `20260324-093316-8471d6e`
**Current Status:** Lights functional, frontend needs rebuild
