# Plan: Fix Climate Setpoint Ramps

## TL;DR

> **Quick Summary**: Ramps broke when I changed `_calculate_ramp_start_values` to use cached setpoints instead of querying the database with `previous_mode`. The fix restores the database query.
>
> **Deliverables**:
> - Fix `SetpointManager._calculate_ramp_start_values()` to query DB with `previous_mode`
> - Update `SetpointManager.__init__()` to accept `database` parameter
> - Update `control_engine.py` to pass `database` to SetpointManager
>
> **Estimated Effort**: Quick
> **Parallel Execution**: NO - sequential
> **Critical Path**: Task 1 → Task 2 → Task 3 → Deploy

---

## Context

### Original Request
User reported that climate setpoint ramps jump immediately instead of transitioning gradually over N minutes. The system was working last week.

### Root Cause Analysis

**PRIMARY BUG** (introduced in commit `c28d803`):

| Version | Code | Result |
|---------|------|--------|
| **Working** (9ba3288) | `await self.database.setpoint_repo.get_setpoint(location, cluster, previous_mode)` | Queries DB for PREVIOUS mode setpoints |
| **Broken** (c28d803) | `await self._state.get_setpoint(location, cluster)` | Gets CACHED setpoints (already current mode!) |

**Why This Breaks Ramps**:
1. Mode changes DAY → PRE_NIGHT
2. Cache is updated to PRE_NIGHT setpoints (21°C) BEFORE ramp calculation
3. `_calculate_ramp_start_values()` calls `get_setpoint()` → returns 21°C (cached)
4. Ramp starts at 21°C and ends at 21°C
5. **Delta = 0, no ramp occurs!**

**Evidence**:
- Database has correct mode-specific setpoints:
  - DAY: 26°C, NIGHT: 24°C, PRE_DAY: 25°C, PRE_NIGHT: 21°C
- Log shows: `"RAMP: Using PRE_NIGHT setpoints as ramp start"` (wrong - should be DAY)
- Effective setpoints show no gradual transition

---

## Work Objectives

### Core Objective
Restore ramp functionality by querying the database for previous mode setpoints instead of using cached current mode setpoints.

### Concrete Deliverables
- `setpoint_manager.py`: Fixed `_calculate_ramp_start_values()` with 3 DB queries
- `setpoint_manager.py`: Updated `__init__()` to accept `database`
- `control_engine.py`: Updated SetpointManager initialization

### Definition of Done
- [ ] Logs show correct previous mode setpoints (e.g., "Using DAY setpoints as ramp start")
- [ ] Ramps initiate on mode transitions
- [ ] Effective setpoints show gradual transition in database
- [ ] Service starts without errors

### Must Have
- Database query with `previous_mode` parameter
- All three query locations fixed (lines 562, 585, 594)

### Must NOT Have (Guardrails)
- No changes to ramp duration logic
- No changes to threshold logic
- No additional caching

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO specific ramp tests
- **Automated tests**: NO
- **Agent-Executed QA**: YES

### Agent-Executed QA Scenarios

**Scenario: Verify ramp initiation after fix**
```
Tool: Bash (curl + journalctl)
Preconditions: Service running with fix deployed
Steps:
  1. Wait for mode transition time (check schedule)
  2. curl -s http://mothernode:8001/api/debug/ramps/Flower%20Room/main
  3. journalctl -u automation-service --since "5 minutes ago" | grep "RAMP:"
Expected Result: Logs show "Using {previous_mode} setpoints as ramp start"
Evidence: Log output captured
```

**Scenario: Verify effective setpoints show gradual transition**
```
Tool: Bash (psql)
Preconditions: Mode transition just occurred
Steps:
  1. PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
     SELECT timestamp, mode, effective_heating_setpoint, nominal_heating_setpoint 
     FROM effective_setpoints 
     WHERE location='Flower Room' AND mode='PRE_NIGHT' 
     ORDER BY timestamp LIMIT 20;"
Expected Result: effective values gradually change from 26 to 21 over 15 rows
Evidence: Query output captured
```

---

## Execution Strategy

### Dependency Matrix

| Task | Depends On | Blocks |
|------|------------|--------|
| 1 | None | 2 |
| 2 | 1 | 3 |
| 3 | 2 | Deploy |
| Deploy | 3 | None |

### Agent Dispatch Summary

All tasks are quick code changes - single agent sequential execution.

---

## TODOs

- [ ] 1. Fix SetpointManager._calculate_ramp_start_values() database queries

  **What to do**:
  - Line 562: Change `await self._state.get_setpoint(location, cluster)` to `await self.database.setpoint_repo.get_setpoint(location, cluster, previous_mode)`
  - Line 585: Change `await self._state.get_setpoint(location, cluster)` to `await self.database.setpoint_repo.get_setpoint(location, cluster, primary)`
  - Line 594: Change `await self._state.get_setpoint(location, cluster)` to `await self.database.setpoint_repo.get_setpoint(location, cluster, secondary)`
  
  **Must NOT do**:
  - Do not change any threshold logic
  - Do not add additional caching

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  
  **Parallelization**: NO - sequential first task

  **References**:
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:531-610` - Full `_calculate_ramp_start_values` method
  - Working version (commit 9ba3288): `git show 9ba3288:Infrastructure/automation-service/app/control/setpoint_manager.py`
  - `Infrastructure/automation-service/app/repositories/setpoint.py` - `get_setpoint()` method signature

  **Acceptance Criteria**:
  - [ ] All 3 `get_setpoint()` calls pass the mode parameter
  - [ ] LSP/pyright shows no type errors
  - [ ] ruff check passes

- [ ] 2. Update SetpointManager.__init__() to accept database parameter

  **What to do**:
  - Add `database: DatabaseManager` as first parameter to `__init__()`
  - Store as `self.database = database`
  
  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: NO - depends on Task 1

  **References**:
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:345-357` - Current `__init__` method
  - `Infrastructure/automation-service/app/database.py` - DatabaseManager type

  **Acceptance Criteria**:
  - [ ] `__init__` accepts `database` parameter
  - [ ] `self.database` is stored
  - [ ] Type hints are correct

- [ ] 3. Update control_engine.py SetpointManager initialization

  **What to do**:
  - Add `database=database` to SetpointManager constructor call
  
  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: NO - depends on Task 2

  **References**:
  - `Infrastructure/automation-service/app/control/control_engine.py:79-85` - Current initialization

  **Acceptance Criteria**:
  - [ ] `database=database` passed to SetpointManager
  - [ ] Service starts without AttributeError

- [ ] 4. Deploy and verify

  **What to do**:
  - Run `./deploy.sh`
  - Check service status
  - Monitor logs for next mode transition
  
  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: NO - depends on Task 3

  **Acceptance Criteria**:
  - [ ] Deploy completes without errors
  - [ ] Service status shows active (running)
  - [ ] No errors in journalctl

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1, 2, 3 | `fix(control): restore database query for previous mode setpoints in ramp calculation` | `setpoint_manager.py`, `control_engine.py` |

---

## Success Criteria

### Verification Commands
```bash
# Check service status
sudo systemctl status automation-service --no-pager -l | head -10

# Check for errors
sudo journalctl -u automation-service --since "1 minute ago" --no-pager | grep -i error

# Check ramp debug
curl -s http://mothernode:8001/api/debug/ramps/Flower%20Room/main

# Check effective setpoints (after mode transition)
PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
SELECT timestamp, mode, effective_heating_setpoint, nominal_heating_setpoint 
FROM effective_setpoints 
WHERE location='Flower Room' AND timestamp > now() - interval '30 minutes'
ORDER BY timestamp DESC LIMIT 30;"
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] No "Must NOT Have" present
- [ ] Logs show correct previous mode setpoints
- [ ] Effective setpoints show gradual transition
