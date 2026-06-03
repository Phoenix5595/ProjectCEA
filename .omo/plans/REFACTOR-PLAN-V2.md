# ProjectCEA Refactor - Improved Plan (v2)

## Principles

1. **NEVER refactor without tests** - Write tests first, verify current behavior, then refactor
2. **Incremental changes** - One small piece at a time, test thoroughly between
3. **Keep it working** - If it ain't broke, don't fix it
4. **Test on target hardware** - Simulated hardware may not catch timing issues

---

## Phase 1: Safety & Monitoring (DO FIRST)

### Task 1.1: Fix Heating Safety False Alarms

**Problem:** 0.0°C sensor reading triggers emergency (no sensor = 0.0°C)

**Solution:** Add sensor validity check before emergency trigger

```python
# In heating_safety.py
def update(self, current_temp, heater_demand, current_time):
    # Skip if sensor reading is invalid (0.0°C likely means no sensor)
    if current_temp <= 0.0 and heater_demand == 0:
        logger.debug("Skipping safety check - no valid sensor data")
        return SafetyState.NORMAL
    
    # Existing emergency logic...
```

**Files:** `app/control/heating_safety.py`
**Test:** Deploy, verify no false alarms with disconnected sensors

---

## Phase 2: Backend Simplification (LOW RISK)

These changes are safe and don't affect core logic.

### Task 2.1: Hardware Batch Sequential Only ✅ (DONE)

**Status:** Completed, working

### Task 2.2: DFR0971 Board Hacks Removal ✅ (DONE)

**Status:** Completed, working

### Task 2.3: Redis Key Migration ✅ (DONE)

**Status:** Completed with backward compat

---

## Phase 3: Frontend Cleanup (MEDIUM RISK)

### Task 3.1: Restore Working Frontend

**Problem:** Current frontend has TypeScript errors

**Solution:**
```bash
git checkout HEAD~10 -- Infrastructure/frontend/src/pages/ZoneConfig.tsx
git checkout HEAD~10 -- Infrastructure/frontend/src/components/LightManager.tsx
npm run build  # Verify it works
```

**Files:** `ZoneConfig.tsx`, `LightManager.tsx`
**Test:** `npm run build && deploy`

---

## Phase 4: Scheduler Refactor (HIGH RISK - SKIP FOR NOW)

**Problem:** The ramp logic is complex and time-sensitive

**Current Status:** Working code, no need to refactor

**If非要 refactor:**
1. Write integration tests FIRST
2. Test overnight schedules (17:00-05:00)
3. Test ramp resume after restart
4. Compare output BEFORE and AFTER refactor

**Don't extract LightRampCalculator** - the inline code works fine

---

## Phase 5: Control Engine Pipeline (HIGH RISK - SKIP FOR NOW)

**Problem:** control_engine.py is 800+ lines

**Current Status:** Working code, no need to refactor

**If非要 refactor:**
1. Only extract when there's a proven need
2. Extract ONE class at a time
3. Test each extraction before moving on

---

## Phase 6: Redis Mixin Composition (LOW RISK)

**Status:** Completed, working

**Note:** The composition facade was created but the mixins still exist. This is fine - it provides a cleaner interface without breaking anything.

---

## Recommended Actions

### Immediate (Deploy Now)
1. ✅ Keep current working backend
2. ✅ Restore frontend to HEAD~10
3. ✅ Rebuild and deploy

### This Week
1. Fix heating safety false alarms (Task 1.1)
2. Verify all systems operational
3. Write integration tests for scheduler

### Later (If Needed)
1. Consider scheduler refactor ONLY with tests
2. Consider control engine split ONLY with compelling reason
3. Frontend hook extraction only if it solves a real problem

---

## What NOT To Do

| Don't Do | Why |
|----------|-----|
| Don't extract LightRampCalculator | Works fine, risk is high |
| Don't split control_engine | Works fine, no proven need |
| Don't change scheduler ramp logic | Time-sensitive, tested code |
| Don't deploy without testing | Learned the hard way |

---

## Success Criteria

- [x] System is operational (lights work)
- [x] No false heating emergencies
- [ ] Frontend builds and deploys cleanly
- [ ] All integration tests pass
- [ ] Heating safety handles missing sensors gracefully
