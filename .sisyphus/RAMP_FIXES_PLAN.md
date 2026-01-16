# Ramp Transition Fixes - Comprehensive Plan

## Summary
Fixed missing ramp transitions and service stability issues in automation-service.

---

## Completed Fixes

### 1. Added Missing Ramp Transitions (PRE_DAY->DAY, PRE_NIGHT->NIGHT)

**File**: `app/control/setpoint_manager.py`

**Problem**: Only 2 of 4 mode transitions had ramps:
- NIGHT -> PRE_DAY (worked)
- PRE_DAY -> DAY (MISSING)
- DAY -> PRE_NIGHT (worked)
- PRE_NIGHT -> NIGHT (MISSING)

**Solution**: Refactored _calculate_ramp_start_values() to use previous_mode parameter generically.

### 2. Fixed Service Restart Loop (WatchdogSec)

**File**: `/etc/systemd/system/automation-service.service`

**Problem**: Service restarted every 30s causing sawtooth setpoints.
- Type=notify expected sd_notify calls uvicorn doesnt send
- WatchdogSec=30 killed service after 30s without ping

**Solution**:
- Changed Type=notify to Type=simple
- Removed WatchdogSec=30 line
- Changed User=cea to User=root

### 3. Fixed Spurious Ramp on Service Startup

**File**: `app/control/setpoint_manager.py`

**Problem**: On startup, previous_mode=None treated as mode change, starting ramp mid-period.

**Solution**: Modified _detect_mode_change() to return False when previous_mode is None.

### 4. Fixed Room Name Mismatch (2026-01-15)

**Tables**: `room_light_schedule`, `setpoints`, `schedules`

**Problem**: `room_light_schedule` had "Vegetation Room" but all other tables used "Veg Room".
- Scheduler couldn't find light schedule for Veg Room
- Result: `mode=NULL` in effective_setpoints → no ramping

**Solution**:
```sql
UPDATE room_light_schedule SET room_name = 'Veg Room' WHERE room_name = 'Vegetation Room';
```

**Verification**: After fix, Veg Room now correctly shows `mode=DAY` in effective_setpoints.

---

## Mode Transition Table

| Transition | Ramp FROM | Ramp TO |
|------------|-----------|---------|
| NIGHT -> PRE_DAY | NIGHT setpoints | PRE_DAY setpoints |
| NIGHT -> DAY | NIGHT setpoints | DAY setpoints |
| PRE_DAY -> DAY | PRE_DAY setpoints | DAY setpoints |
| DAY -> PRE_NIGHT | DAY setpoints | PRE_NIGHT setpoints |
| DAY -> NIGHT | DAY setpoints | NIGHT setpoints |
| PRE_NIGHT -> NIGHT | PRE_NIGHT setpoints | NIGHT setpoints |

---

## Current Database Configuration (2026-01-15)

### Setpoints Table Status

| Location | Mode | Heat | Cool | VPD | ramp_in_duration |
|----------|------|------|------|-----|------------------|
| Flower Room | DAY | 24°C | 26°C | 1.20 | 15 min ✅ |
| Flower Room | NIGHT | 20°C | 24°C | 1.00 | 15 min ✅ |
| Flower Room | PRE_DAY | 25°C | 28°C | 1.20 | 15 min ✅ |
| Flower Room | PRE_NIGHT | 21°C | 24°C | 1.20 | 15 min ✅ |
| Veg Room | DAY | 24°C | 28°C | 1.10 | 15 min ✅ |
| Veg Room | NIGHT | 22°C | 24°C | 1.10 | 15 min ✅ |
| Veg Room | PRE_DAY | NULL | NULL | NULL | 0 ❌ |
| Veg Room | PRE_NIGHT | NULL | NULL | NULL | 0 ❌ |

### Schedules Table Status (Period Lengths)

| Location | pre_day_duration | pre_night_duration |
|----------|------------------|-------------------|
| Flower Room | 120 min ✅ | 60 min ✅ |
| Veg Room | 0 ❌ | 0 ❌ |

---

## Debug Findings (2026-01-15)

### ✅ Flower Room - Configuration Complete
- All 4 modes have valid setpoints with `ramp_in_duration = 15`
- Schedule has `pre_day_duration = 120` and `pre_night_duration = 60`
- PRE_DAY/PRE_NIGHT modes WILL be triggered by scheduler
- Ramping confirmed working (ramp_progress visible in effective_setpoints)

### ✅ Veg Room - Fixed (2026-01-15)
- **Root cause found**: `room_light_schedule` had "Vegetation Room" but other tables had "Veg Room"
- **Fix applied**: Updated `room_light_schedule.room_name` to "Veg Room"
- Mode detection now working (`mode=DAY` visible in effective_setpoints)
- DAY and NIGHT have `ramp_in_duration = 15` → direct DAY↔NIGHT ramping will work
- No PRE_DAY/PRE_NIGHT periods configured (pre_day_duration=0, pre_night_duration=0)
- **Behavior**: System will ramp directly between DAY and NIGHT setpoints

### Direct DAY↔NIGHT Ramping (No PRE periods)

When `pre_day_duration = 0` and `pre_night_duration = 0`:
- Scheduler returns only DAY or NIGHT modes (never PRE_DAY/PRE_NIGHT)
- On DAY→NIGHT transition: ramps from DAY setpoints → NIGHT setpoints
- On NIGHT→DAY transition: ramps from NIGHT setpoints → DAY setpoints
- Ramp duration: uses `ramp_in_duration` from the TARGET mode's setpoint

This is supported by fallback logic in `_calculate_ramp_start_values()`:
```python
'DAY': ('PRE_DAY', 'NIGHT'),      # PRE_DAY if exists, else NIGHT
'NIGHT': ('PRE_NIGHT', 'DAY')     # PRE_NIGHT if exists, else DAY
```

---

## Remaining Investigation (If Flower Room Still Not Ramping)

1. Check logs: `journalctl -u automation-service | grep -E "RAMP|mode_changed"`
2. Verify `_current_climate_mode` tracking in control_engine.py
3. Check if ramp thresholds are skipping small deltas
4. Verify service hasn't restarted mid-ramp

---

## Key Files

- setpoint_manager.py - RampManager, ramp calculations
- scheduler.py - Determines current climate mode
- control_engine.py - Orchestrates control loop
- /etc/systemd/system/automation-service.service - Systemd config

---

## Pending (Nice-to-have)

1. sd_notify integration for proper Type=notify support
2. Watchdog integration for auto-restart on hang
3. Persist ramp state across restarts

---

## Rollback

cd /home/antoine/ProjectCEA && ./rollback.sh

---

Created: 2026-01-15
Updated: 2026-01-15 19:50 - Fixed room name mismatch (Vegetation Room → Veg Room)
