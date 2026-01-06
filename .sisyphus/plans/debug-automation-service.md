# Debugging Work Plan: Automation Service Effective Setpoints & Ramp Logic

## Executive Summary

**Objective**: Debug and fix issues with effective setpoint calculation, mode transitions, and ramp logic in the automation service.

**Scope**: Focus on `control_engine.py` ramp calculation logic and mode transition handling.

**Estimated Time**: 2-3 hours

---

## Success Criteria

### Functional Requirements
1. ✅ Effective setpoints ramp smoothly during mode transitions (no jumps)
2. ✅ Mode transitions (PRE_DAY → DAY → PRE_NIGHT → NIGHT) work correctly
3. ✅ Setpoint changes apply correctly (instant or ramped based on context)
4. ✅ Ramp state persists across service restarts (no jumps on restart)
5. ✅ VPD control uses effective setpoints like PID control does

### Observable Outcomes
1. ✅ No "jump" in setpoint values during mode transitions
2. ✅ Setpoint values follow linear interpolation during ramp duration
3. ✅ Ramp completes exactly at configured `ramp_in_duration`
4. ✅ Service restart maintains ramp progress (doesn't reset to nominal)
5. ✅ Consistent behavior between PID and VPD control paths

### Verification Methods
1. Monitor `RAMP DEBUG` logs to see linear progression
2. Check effective setpoint values in Redis (`automation:{location}:{cluster}:setpoints`)
3. Simulate mode transitions via `config_cli.py` or direct DB updates
4. Restart service mid-ramp and verify smooth continuation
5. Compare PID and VPD effective setpoint calculations

---

## Identified Issues (From Code Analysis)

### 🔴 Critical Issues

#### 1. Missing Ramp State Restoration (`container.py:198`)
**Location**: `Infrastructure/automation-service/app/container.py`
**TODO Comment**: `# TODO: Implement restore_ramp_state_from_database method if needed`

**Problem**: When service restarts, any active climate ramps (e.g., temperature ramping from DAY to NIGHT) are lost. The system will "jump" to the target value instead of resuming a smooth transition.

**Impact**:
- Thermal shock to plants
- Abrupt lighting changes
- Loss of smooth climate transitions

#### 2. Complex Ramp Logic with Edge Cases (`control_engine.py` lines 750-900)
**Location**: `Infrastructure/automation-service/app/control/control_engine.py`
**Issue**: Ramp calculation logic is complex with multiple branches and potential edge cases:
- Mode transition detection (`is_real_mode_transition`)
- PRE_DAY/PRE_NIGHT special handling
- Ramp restart logic with potential race conditions
- Target change mid-ramp handling

**Symptoms**:
- Setpoints jump during mode transitions
- Ramps don't complete at expected time
- Inconsistent behavior on service restart

#### 3. VPD Bypasses Ramp Logic
**Location**: `control_engine.py`
**Issue**:
- Line 753: VPD effective setpoints calculated in `_calculate_effective_setpoint`
- Line 1187: `_process_vpd_control` fetches nominal setpoint directly from database, bypassing ramp logic

**Impact**: VPD control doesn't follow smooth transitions like PID control does

### 🟡 Medium Issues

#### 4. Verbose "RAMP DEBUG" Logs
**Location**: `control_engine.py` lines 766-802, 887-895
**Issue**: Development debug statements still in production code

**Impact**: Log noise, difficult to diagnose real issues

#### 5. Hardcoded DEBUG Log Level
**Location**: `control_engine.py` lines 17, 21
**Issue**: Logger hardcoded to `logging.DEBUG`, overrides system config

**Impact**: Flooding production logs

---

## Test Plan

### Objective
Verify ramp calculation logic works correctly for all mode transitions and setpoint changes.

### Prerequisites
1. Automation service running in simulation mode (`simulation: true` in config)
2. Database with test setpoints for DAY/NIGHT/PRE_DAY/PRE_NIGHT modes
3. Redis connection for monitoring live state
4. Python pytest environment set up

### Test Cases

#### Test 1: Mode Transition Ramp (PRE_DAY → DAY)
**Input**: Transition from NIGHT to PRE_DAY, then to DAY
**Expected Output**: Smooth ramp from NIGHT setpoint to DAY setpoint over `ramp_in_duration`
**Verification**:
```bash
# Set up test setpoints
config_cli.py setpoint set "Test Room" main --mode NIGHT --temp 18.0
config_cli.py setpoint set "Test Room" main --mode DAY --temp 24.0 --ramp-in-duration 30

# Monitor logs
journalctl -u automation-service -f | grep "RAMP DEBUG"

# Verify linear progression in Redis
redis-cli GET "automation:Test Room:main:temperature"
```

#### Test 2: Setpoint Change Mid-Ramp
**Input**: Change target setpoint while ramp is in progress
**Expected Output**: Instant jump to new target (no ramp per line 870-875)
**Verification**:
```bash
# Start ramp
config_cli.py setpoint set "Test Room" main --mode DAY --temp 24.0 --ramp-in-duration 60

# Wait 30 seconds (half ramp)
# Change target mid-ramp
config_cli.py setpoint set "Test Room" main --mode DAY --temp 22.0

# Verify instant change (no continued ramp)
```

#### Test 3: Service Restart Mid-Ramp
**Input**: Restart service while ramp is active
**Expected Output**: **CURRENTLY FAILS** - After fix, should resume ramp smoothly
**Verification**:
```bash
# Start ramp
config_cli.py setpoint set "Test Room" main --mode DAY --temp 24.0 --ramp-in-duration 60

# Wait 30 seconds
sudo systemctl restart automation-service

# Check Redis - should continue from ~21.0°C, not jump to 24.0°C
redis-cli GET "automation:Test Room:main:temperature"
```

#### Test 4: PRE_DAY → DAY → PRE_NIGHT → NIGHT Sequence
**Input**: Complete day cycle with all mode transitions
**Expected Output**: Each transition uses correct base setpoint and ramps smoothly
**Verification**:
- PRE_DAY: Ramps from NIGHT → DAY
- DAY: Maintains DAY setpoint
- PRE_NIGHT: Ramps from DAY → NIGHT
- NIGHT: Maintains NIGHT setpoint

#### Test 5: VPD vs PID Consistency
**Input**: Mode change affects both VPD and temperature setpoints
**Expected Output**: Both PID and VPD should use effective (ramped) setpoints consistently
**Verification**:
```python
# In control_engine.py, add logging to compare:
logger.info(f"PID effective: {effective_temp}, VPD effective: {effective_vpd}")
# Both should follow same ramp curve
```

### Success Criteria
- All test cases pass
- No setpoint jumps observed
- Ramps complete exactly at configured duration
- Service restart doesn't break ramp progress

### How to Execute
1. Set up simulation environment
2. Configure test setpoints for all modes
3. Run test cases sequentially
4. Monitor logs and Redis state
5. Compare actual vs expected behavior
6. Document failures and edge cases

---

## Implementation Plan

### Phase 1: Investigation & Diagnosis (30 minutes)

#### Step 1.1: Enable Detailed Ramp Logging
**Goal**: Understand current behavior by analyzing existing debug logs

**Actions**:
1. Ensure `RAMP DEBUG` logs are enabled
2. Start automation service in simulation mode
3. Trigger mode transitions via `config_cli.py`
4. Capture logs for each transition type
5. Analyze log output to identify where logic diverges from expected

**Files**:
- `Infrastructure/automation-service/app/control/control_engine.py` (read-only)

**Verification**:
- Logs captured for all transition types
- Identified where ramps jump or behave unexpectedly

---

#### Step 1.2: Reproduce Issues
**Goal**: Confirm bug exists and document symptoms

**Actions**:
1. Reproduce mode transition jump
2. Reproduce setpoint change behavior
3. Reproduce service restart issue
4. Document each issue with:
   - Steps to reproduce
   - Expected vs actual behavior
   - Log excerpts showing the issue

**Verification**:
- All 3 core issues reproduced
- Clear documentation of symptoms

---

### Phase 2: Fix Critical Issues (90 minutes)

#### Step 2.1: Implement Ramp State Persistence
**Goal**: Service restarts maintain ramp progress

**Approach**:
1. Add `restore_ramp_state_from_database()` method to `ControlEngine`
2. Store ramp state in TimescaleDB (new table: `ramp_state`)
3. On service startup, load ramp state and resume from last position
4. If ramp expired (completed), use target setpoint

**Files to Modify**:
- `Infrastructure/automation-service/app/database.py` - Add ramp state CRUD operations
- `Infrastructure/automation-service/app/control/control_engine.py` - Add restore logic
- `Infrastructure/automation-service/app/bootstrap.py` - Call restore on startup
- Database migration script for `ramp_state` table

**Database Schema** (`ramp_state` table):
```sql
CREATE TABLE IF NOT EXISTS ramp_state (
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    setpoint_type TEXT NOT NULL,
    current_effective_setpoint FLOAT NOT NULL,
    target_setpoint FLOAT NOT NULL,
    ramp_start_timestamp TIMESTAMP NOT NULL,
    ramp_duration_minutes FLOAT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (location, cluster, setpoint_type)
);
```

**Implementation Details**:
```python
# In control_engine.py
async def restore_ramp_state_from_database(self):
    """Restore ramp state from database on service startup."""
    ramp_states = await self.database.get_all_ramp_states()

    for ramp_state in ramp_states:
        ramp_key = (ramp_state['location'], ramp_state['cluster'], ramp_state['setpoint_type'])

        # Check if ramp is still active
        ramp_start = ramp_state['ramp_start_timestamp']
        ramp_duration = timedelta(minutes=ramp_state['ramp_duration_minutes'])
        ramp_end = ramp_start + ramp_duration

        if datetime.now() < ramp_end:
            # Ramp is still active - restore state
            self._ramp_state[ramp_key] = {
                'current_effective_setpoint': ramp_state['current_effective_setpoint'],
                'ramp_start_timestamp': ramp_state['ramp_start_timestamp'],
                'ramp_duration': ramp_state['ramp_duration_minutes'],
                'target_setpoint': ramp_state['target_setpoint']
            }
            logger.info(f"Restored ramp state for {ramp_key}: {ramp_state}")
        else:
            # Ramp completed - use target setpoint
            self._ramp_state[ramp_key] = {
                'current_effective_setpoint': ramp_state['target_setpoint'],
                'ramp_start_timestamp': datetime.now(),
                'ramp_duration': 0,
                'target_setpoint': ramp_state['target_setpoint']
            }
            logger.info(f"Ramp completed for {ramp_key}, using target: {ramp_state['target_setpoint']}")
```

**Verification**:
- Service restart mid-ramp continues smoothly
- Ramp completes at correct time
- No setpoint jumps on restart

---

#### Step 2.2: Fix PRE_DAY/PRE_NIGHT Transition Logic
**Goal**: Ensure mode transitions use correct base setpoints

**Current Problem** (lines 781-861):
- PRE_DAY should ramp from NIGHT setpoint
- PRE_NIGHT should ramp from DAY setpoint
- Logic exists but may have race conditions or edge cases

**Approach**:
1. Review transition logic for consistency
2. Add safeguards for edge cases (e.g., missing setpoint data)
3. Ensure ramp only restarts on actual mode change
4. Add unit tests for transition logic

**Files to Modify**:
- `Infrastructure/automation-service/app/control/control_engine.py` (lines 781-861)

**Verification**:
- PRE_DAY transitions start from NIGHT value
- PRE_NIGHT transitions start from DAY value
- No double-restart of ramp on same transition

---

#### Step 2.3: Fix VPD to Use Effective Setpoints
**Goal**: VPD control follows same ramp logic as PID control

**Current Problem**:
- `_process_vpd_control` fetches nominal setpoint directly (line ~1187)
- Should use pre-calculated `effective_vpd_setpoint` from `_calculate_effective_setpoint`

**Approach**:
1. Identify where VPD gets its setpoint
2. Change to use effective setpoint from control loop context
3. Verify behavior matches PID control

**Files to Modify**:
- `Infrastructure/automation-service/app/control/control_engine.py` - `_process_vpd_control()` method

**Implementation**:
```python
# Change from:
nominal_vpd_data = await self.database.get_setpoint(location, cluster, current_mode)
vpd_setpoint = nominal_vpd_data.get('vpd') if nominal_vpd_data else None

# To:
vpd_setpoint = effective_vpd_setpoint  # Use pre-calculated effective setpoint
```

**Verification**:
- VPD follows smooth ramps like PID
- Consistent behavior across control types

---

### Phase 3: Clean Up & Polish (30 minutes)

#### Step 3.1: Remove Hardcoded DEBUG Logs
**Goal**: Production-ready logging

**Actions**:
1. Remove `logger.setLevel(logging.DEBUG)` from line 17, 21
2. Convert `RAMP DEBUG` statements to proper log levels
   - Keep some as `logger.debug()` for troubleshooting
   - Remove verbose ones
3. Add `LOG_LEVEL` configuration to `automation_config.yaml`

**Files to Modify**:
- `Infrastructure/automation-service/app/control/control_engine.py`
- `Infrastructure/automation-service/automation_config.yaml`

**Verification**:
- Log level controlled by config
- No excessive DEBUG logs in production

---

#### Step 3.2: Add Unit Tests
**Goal**: Ensure ramp logic is testable and tested

**Test Files**:
- `Infrastructure/automation-service/tests/test_ramp_logic.py`
- `Infrastructure/automation-service/tests/test_mode_transitions.py`

**Test Cases**:
1. Test ramp calculation with various durations
2. Test mode transition logic (PRE_DAY, PRE_NIGHT)
3. Test setpoint change mid-ramp (instant change)
4. Test ramp state persistence and restoration

**Verification**:
- All tests pass
- Coverage >80% on ramp-related code

---

### Phase 4: Integration & Verification (30 minutes)

#### Step 4.1: End-to-End Testing
**Goal**: Verify all fixes work together

**Actions**:
1. Run full test suite
2. Perform manual testing with simulation mode
3. Test complete day cycle (NIGHT → PRE_DAY → DAY → PRE_NIGHT → NIGHT)
4. Test service restart mid-ramp
5. Compare PID and VPD behavior

**Verification**:
- All tests pass
- Manual testing shows smooth transitions
- No setpoint jumps
- Service restart maintains ramp progress

---

#### Step 4.2: Production Deployment Checklist
**Goal**: Safe deployment to production

**Pre-Deployment**:
- [ ] Database migration script created and tested
- [ ] Configuration backup taken
- [ ] Rollback plan documented
- [ ] Staging environment tested

**Deployment**:
1. Stop automation service
2. Run database migration
3. Deploy updated code
4. Start automation service
5. Monitor logs for errors

**Post-Deployment**:
- [ ] Monitor logs for 24 hours
- [ ] Verify smooth mode transitions
- [ ] Check for any setpoint jumps
- [ ] Confirm no regression in other features

---

## Rollback Plan

If issues occur after deployment:

1. **Immediate Rollback**:
   ```bash
   sudo systemctl stop automation-service
   # Revert database migration
   psql -U cea_user -d cea_sensors -f rollback_ramp_state.sql
   # Restore previous code
   git checkout <previous-commit>
   sudo systemctl start automation-service
   ```

2. **Database Rollback Script** (`rollback_ramp_state.sql`):
   ```sql
   DROP TABLE IF EXISTS ramp_state;
   ```

3. **Monitor**: Watch logs for stability after rollback

---

## Dependencies

### Required Tools
- Python 3.8+
- pytest and pytest-asyncio
- TimescaleDB client (asyncpg)
- Redis client (redis-py)

### External Dependencies
- None (all services internal)

---

## Notes & Considerations

### Architecture Implications
- Ramp state persistence adds new database table
- Slight performance overhead on each tick (saving ramp state)
- Trade-off: complexity vs. robustness

### Future Improvements
1. Add ramp state monitoring dashboard
2. Alert on ramp completion or unexpected jumps
3. Configurable ramp curves (linear, sigmoid, etc.)
4. Per-device ramp parameters

### Known Limitations
- Ramp duration must be >0 to ramp (instant on first setpoint)
- PRE_DAY/PRE_NIGHT modes are optional but recommended
- VPD control historically bypassed ramp logic (now fixed)

---

## Completion Checklist

- [ ] All critical issues fixed
- [ ] Ramp state persistence implemented
- [ ] PRE_DAY/PRE_NIGHT transitions work correctly
- [ ] VPD uses effective setpoints
- [ ] Debug logs cleaned up
- [ ] Unit tests added and passing
- [ ] Manual testing completed
- [ ] Production deployment checklist completed
- [ ] Rollback plan documented
- [ ] All success criteria met

---

## Estimated Timeline

| Phase | Time |
|-------|------|
| Phase 1: Investigation | 30 min |
| Phase 2: Fix Critical Issues | 90 min |
| Phase 3: Clean Up | 30 min |
| Phase 4: Integration | 30 min |
| **Total** | **3 hours** |

---

## References

**Key Files**:
- `Infrastructure/automation-service/app/control/control_engine.py` - Main ramp logic
- `Infrastructure/automation-service/app/container.py` - Dependency injection
- `Infrastructure/automation-service/app/database.py` - Database operations
- `Infrastructure/automation-service/automation_config.yaml` - Configuration

**Documentation**:
- `Infrastructure/README.md` - Overall system architecture
- `Infrastructure/automation-service/README.md` - Service-specific docs

**Test Infrastructure**:
- `Infrastructure/automation-service/tests/` - Existing tests
- `Infrastructure/automation-service/config_cli.py` - Configuration tool
