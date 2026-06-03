# Config Event Bus System - Comprehensive Refactor

## TL;DR

> **Quick Summary**: Implement an event-driven architecture for real-time configuration changes. When ramp times or other config changes are saved, immediately notify the control loop via an event bus instead of waiting up to 60 seconds. Also reduce batch interval from 10s to 5s for faster Grafana updates.
> 
> **Deliverables**:
> - Event bus module with asyncio.Queue for config change notifications
> - Immediate scheduler refresh on ramp config changes
> - Removal of legacy 60-second schedule refresh loop
> - Reduced batch interval (10s → 5s) for effective_setpoints
> - Comprehensive test coverage
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Event Bus → Scheduler Integration → Remove Legacy

---

## Context

### Original Request

User changed ramp up/down times for flower mode during an active ramp period. After saving, expected immediate recalculation (within 1-2 seconds) but the system didn't recalculate until the 60-second background refresh.

Additionally, adjusting target intensity during a ramp showed correct values in frontend immediately, but Grafana showed stale data for 15-30 seconds.

### Interview Summary

**Key Discussions**:
- Ramp time changes should trigger immediate control loop notification
- Frontend shows correct values (reads from Redis) but Grafana is delayed
- User wants comprehensive fix, not band-aid
- Production can halt temporarily for proper implementation
- Tests should be added after implementation
- Batch interval should be reduced to 5 seconds

**Research Findings**:
- Bug location: `room_modes.py:289-299` saves to DB but doesn't notify control loop
- Schedule refresh loop runs every 60 seconds in `background_tasks.py`
- Recalculation logic EXISTS in `scheduler.py:352-364` but uses stale in-memory schedules
- Batch interval is 10 seconds in `setpoints.py:20` causing Grafana delay
- Light intensity written to Redis immediately but Grafana queries TimescaleDB

### Metis Review

**Identified Gaps** (addressed):
- Edge case: What if new ramp duration < elapsed time? → Jump to target gracefully
- Event queue overflow handling → Bounded queue with maxsize=100
- Thread safety between event worker and control loop → asyncio.Lock protection
- Rapid successive config changes → Process all, final state wins

---

## Work Objectives

### Core Objective

Implement event-driven configuration change propagation to ensure real-time control system responsiveness.

### Concrete Deliverables

- `app/events/` module with ConfigEventBus class
- Integration in `room_modes.py` for ramp config changes
- Integration in `control_engine.py` for event consumption
- Removal of `_schedule_refresh_loop` from `background_tasks.py`
- Reduced batch interval in `setpoints.py`
- Unit and integration tests

### Definition of Done

- [x] Changing ramp times during active ramp triggers immediate recalculation (<2s)
- [x] Grafana shows updated light intensity within 7 seconds
- [x] All tests pass
- [x] No regression in control loop timing (<5s per tick)
- [x] Legacy 60-second refresh loop removed

### Must Have

- Event bus with bounded queue (maxsize=100)
- Immediate scheduler.update_schedules() on config change
- Thread-safe event processing
- Edge case handling (shorter ramp than elapsed time)

### Must NOT Have (Guardrails)

- NO event persistence (in-memory only)
- NO generic pub/sub infrastructure (scope to config changes)
- NO blocking of control loop during event processing
- NO changes to Grafana dashboards
- NO event sourcing/replay complexity

---

## Verification Strategy (MANDATORY)

### Test Decision

- **Infrastructure exists**: YES (pytest in tests/)
- **Automated tests**: YES (tests after)
- **Framework**: pytest

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

**Scenario 1: Ramp Config Change During Active Ramp**

```
Scenario: Ramp time change triggers immediate recalculation
  Tool: interactive_bash (tmux)
  Preconditions: 
    - Automation service running
    - Light ramp in progress (start 30min ramp, wait 5 minutes)
  Steps:
    1. curl -X PATCH http://localhost:8001/api/room-modes/Flower%20Room/main/params \
         -H "Content-Type: application/json" \
         -d '{"light_ramp_up_minutes": 10}'
    2. sleep 2
    3. curl -s http://localhost:8001/api/debug/ramp-states | jq '.light_ramps'
  Expected Result: Ramp duration reflects new value (10min), remaining time recalculated
  Evidence: .sisyphus/evidence/task-ramp-recalc.json
```

**Scenario 2: Control Loop Timing During Event Processing**

```
Scenario: Control loop doesn't block during config change
  Tool: Bash
  Preconditions: Control loop running normally
  Steps:
    1. Start timing: START=$(date +%s.%N)
    2. Send 10 rapid config changes in loop
    3. End timing: END=$(date +%s.%N)
    4. Calculate: echo "$END - $START" | bc
  Expected Result: All changes processed, no tick exceeds 5 seconds
  Evidence: .sisyphus/evidence/task-timing.log
```

**Scenario 3: Grafana Data Freshness**

```
Scenario: Light intensity appears in Grafana within 7 seconds
  Tool: Bash (curl + psql)
  Preconditions: Light intensity just changed
  Steps:
    1. Change light intensity via API
    2. sleep 7
    3. psql -c "SELECT effective_light_intensity FROM effective_setpoints 
         WHERE location='Flower Room' ORDER BY timestamp DESC LIMIT 1"
  Expected Result: Database shows new intensity value
  Evidence: .sisyphus/evidence/task-grafana-freshness.txt
```

**Scenario 4: Edge Case - Shorter Ramp Than Elapsed**

```
Scenario: Ramp shorter than elapsed time handled gracefully
  Tool: Bash
  Preconditions: 30-minute ramp at minute 25
  Steps:
    1. curl -X PATCH .../params -d '{"light_ramp_up_minutes": 10}'
    2. Check logs for "jumping to target" or graceful completion
    3. Verify no NaN/intensity errors
  Expected Result: System jumps to target intensity, no crash
  Evidence: .sisyphus/evidence/task-edge-case.log
```

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 1: Create Event Bus Module (no dependencies)
└── Task 5: Reduce Batch Interval (no dependencies)

Wave 2 (After Wave 1):
├── Task 2: Integrate Event Bus in room_modes.py (depends: 1)
├── Task 3: Integrate Event Bus in control_engine.py (depends: 1)
└── Task 6: Update Grafana Queries (depends: 5) - SKIPPED per user request

Wave 3 (After Wave 2):
└── Task 4: Remove Legacy Schedule Refresh Loop (depends: 2, 3)

Critical Path: Task 1 → Task 2/3 → Task 4
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3 | 5 |
| 2 | 1 | 4 | 3, 6 |
| 3 | 1 | 4 | 2, 6 |
| 4 | 2, 3 | None | None (final) |
| 5 | None | None | 1 |
| 6 | 5 | None | 2, 3 |

---

## TODOs

- [x] 1. Create ConfigEventBus Module ✅

  **What to do**:
  - Create `app/events/__init__.py` with event bus implementation
  - Define ConfigChangeEvent dataclass with: event_type, location, cluster, config_type, timestamp
  - Implement bounded asyncio.Queue with maxsize=100
  - Implement publish() and subscribe() methods
  - Add thread-safe access via asyncio.Lock

  **Must NOT do**:
  - Do NOT add event persistence
  - Do NOT create generic pub/sub for all events
  - Do NOT add event versioning

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
    - Reason: Core architecture component requiring careful design
  - **Skills**: []
    - No special skills needed - pure Python/asyncio

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 5)
  - **Blocks**: Tasks 2, 3
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/control/scheduler.py:634-637` - update_schedules pattern to follow

  **API/Type References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py:51-58` - ModeParameters with ramp times

  **External References**:
  - asyncio.Queue docs: https://docs.python.org/3/library/asyncio-queue.html
  - FastAPI background tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/

  **Acceptance Criteria**:
  - [ ] File created: `app/events/__init__.py`
  - [ ] ConfigEventBus class with bounded queue (maxsize=100)
  - [ ] publish() method non-blocking
  - [ ] subscribe() returns async iterator
  - [ ] Thread-safe via asyncio.Lock

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Event bus handles rapid events without blocking
    Tool: Bash (python test)
    Steps:
      1. Create test that publishes 50 events rapidly
      2. Measure time to publish all
      3. Verify queue doesn't overflow
    Expected Result: All events queued, no blocking
  ```

  **Commit**: YES
  - Message: `feat(events): add ConfigEventBus for real-time config propagation`
  - Files: `app/events/__init__.py`

---

- [x] 2. Integrate Event Bus in room_modes.py ✅

  **What to do**:
  - Import ConfigEventBus in `app/routes/room_modes.py`
  - After successful `update_light_schedule_ramp_times()` call, publish ConfigChangeEvent
  - Event should include: location, cluster, config_type="ramp_times"
  - Add logging for event publication

  **Must NOT do**:
  - Do NOT block API response waiting for event processing
  - Do NOT add event publishing for all config changes (only ramp times for now)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple integration, few lines of code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 3)
  - **Blocks**: Task 4
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py:288-299` - Current save location (add event publish after)

  **API/Type References**:
  - `Infrastructure/automation-service/app/repositories/schedules.py:361-388` - update_light_schedule_ramp_times

  **Acceptance Criteria**:
  - [ ] ConfigChangeEvent published after ramp time save
  - [ ] Event includes location, cluster, timestamp
  - [ ] API response not delayed by event publishing
  - [ ] Log message confirms event published

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Ramp config change publishes event immediately
    Tool: Bash (curl + log check)
    Steps:
      1. curl -X PATCH .../params -d '{"light_ramp_up_minutes": 15}'
      2. journalctl -u automation-service -n 20 | grep "ConfigChangeEvent published"
    Expected Result: Log shows event published
  ```

  **Commit**: YES
  - Message: `feat(routes): publish ConfigChangeEvent on ramp time save`
  - Files: `app/routes/room_modes.py`

---

- [x] 3. Integrate Event Bus in Control Engine ✅

  **What to do**:
  - Add event consumer in `ControlEngine` or `BackgroundTasks`
  - Subscribe to ConfigChangeEvent stream
  - On event: call `scheduler.update_schedules()` immediately
  - Handle edge case: if ramp duration < elapsed time, jump to target

  **Must NOT do**:
  - Do NOT block control loop during event processing
  - Do NOT process events synchronously in control loop tick

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
    - Reason: Core integration with control loop timing constraints
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 2)
  - **Blocks**: Task 4
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/control/scheduler.py:312-364` - Ramp recalculation logic
  - `Infrastructure/automation-service/app/control/scheduler.py:634-637` - update_schedules method

  **API/Type References**:
  - `Infrastructure/automation-service/app/background_tasks.py:271-293` - Current 60s refresh loop (will be removed)

  **Acceptance Criteria**:
  - [ ] Event consumer running as background task
  - [ ] scheduler.update_schedules() called on config change event
  - [ ] Edge case handled: ramp < elapsed → jump to target
  - [ ] No control loop blocking

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Config change triggers immediate scheduler update
    Tool: Bash
    Steps:
      1. Start 30min ramp, wait 5min
      2. Change ramp to 10min via API
      3. Check scheduler has new schedules within 2 seconds
    Expected Result: Scheduler updated, ramp recalculated
  ```

  **Commit**: YES
  - Message: `feat(control): consume ConfigChangeEvent for immediate scheduler update`
  - Files: `app/control/control_engine.py`, `app/background_tasks.py`

---

- [x] 4. Remove Legacy Schedule Refresh Loop ✅

  **What to do**:
  - Remove `_schedule_refresh_loop()` from `background_tasks.py`
  - Remove `self._schedule_refresh_task` initialization
  - Remove task from start/stop methods
  - Update logger.info message to reflect event-driven approach

  **Must NOT do**:
  - Do NOT remove other background tasks (heartbeat, batch_flush, etc.)
  - Do NOT break the background task initialization pattern

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple removal of deprecated code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final)
  - **Blocks**: None
  - **Blocked By**: Tasks 2, 3

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/background_tasks.py:271-293` - Code to remove
  - `Infrastructure/automation-service/app/background_tasks.py:50-51` - Task variable to remove
  - `Infrastructure/automation-service/app/background_tasks.py:64-65` - Task start to remove

  **Acceptance Criteria**:
  - [ ] `_schedule_refresh_loop` method removed
  - [ ] `_schedule_refresh_task` variable removed
  - [ ] Task no longer started in start()
  - [ ] Task no longer cancelled in stop()
  - [ ] Tests pass without legacy loop

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: No 60-second refresh running
    Tool: Bash
    Steps:
      1. journalctl -u automation-service -f &
      2. Wait 70 seconds
      3. Check for "Refreshed X schedules" message
    Expected Result: No automatic refresh log, only event-driven updates
  ```

  **Commit**: YES
  - Message: `refactor: remove legacy 60-second schedule refresh loop`
  - Files: `app/background_tasks.py`

---

- [x] 5. Reduce Batch Interval for Effective Setpoints ✅

  **What to do**:
  - Change `self._batch_interval = 10.0` to `self._batch_interval = 5.0` in `setpoints.py`
  - Update `_batch_flush_loop` interval to 5.0 seconds in `background_tasks.py`
  - Add comment explaining the trade-off (more DB writes for faster Grafana)

  **Must NOT do**:
  - Do NOT remove batching entirely (would cause DB load issues)
  - Do NOT change flush logic, just interval

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single-line change
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/repositories/setpoints.py:20` - Current 10s interval

  **Acceptance Criteria**:
  - [ ] Batch interval changed to 5.0 seconds
  - [ ] Comment added explaining rationale
  - [ ] Grafana shows data within 7 seconds of change

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Light intensity appears in DB within 7 seconds
    Tool: Bash
    Steps:
      1. Change light intensity via control loop
      2. sleep 7
      3. Query effective_setpoints for latest light value
    Expected Result: New intensity value present
  ```

  **Commit**: YES
  - Message: `perf: reduce batch interval to 5s for faster Grafana updates`
  - Files: `app/repositories/setpoints.py`, `app/background_tasks.py`

---

- [x] 6. Add Comprehensive Tests ✅

  **What to do**:
  - Create `tests/test_event_bus.py` for ConfigEventBus unit tests
  - Create `tests/test_ramp_recalc_integration.py` for end-to-end test
  - Test edge cases: rapid events, queue full, shorter ramp than elapsed
  - Test timing: ensure control loop doesn't block

  **Must NOT do**:
  - Do NOT add tests that require actual hardware
  - Do NOT slow down test suite significantly

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard pytest test writing
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (after implementation)
  - **Blocks**: None
  - **Blocked By**: Tasks 1-5

  **References**:

  **Test References**:
  - `Infrastructure/automation-service/tests/test_scheduler_light.py` - Existing scheduler tests
  - `Infrastructure/automation-service/tests/test_light_schedule_rules.py` - Ramp test patterns

  **Acceptance Criteria**:
  - [ ] test_event_bus.py created with queue tests
  - [ ] test_ramp_recalc_integration.py created with E2E tests
  - [ ] Edge cases covered: queue full, rapid events, short ramp
  - [ ] All tests pass: `pytest tests/`

  **Commit**: YES
  - Message: `test: add event bus and ramp recalculation tests`
  - Files: `tests/test_event_bus.py`, `tests/test_ramp_recalc_integration.py`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(events): add ConfigEventBus for real-time config propagation` | `app/events/__init__.py` | Unit tests pass |
| 2 | `feat(routes): publish ConfigChangeEvent on ramp time save` | `app/routes/room_modes.py` | Manual API test |
| 3 | `feat(control): consume ConfigChangeEvent for immediate scheduler update` | `app/control/control_engine.py`, `app/background_tasks.py` | Integration test |
| 4 | `refactor: remove legacy 60-second schedule refresh loop` | `app/background_tasks.py` | No refresh logs |
| 5 | `perf: reduce batch interval to 5s for faster Grafana updates` | `app/repositories/setpoints.py` | Grafana shows <7s |
| 6 | `test: add event bus and ramp recalculation tests` | `tests/test_*.py` | pytest passes |

---

## Success Criteria

### Verification Commands

```bash
# Test ramp recalculation
curl -X PATCH http://localhost:8001/api/room-modes/Flower%20Room/main/params \
  -H "Content-Type: application/json" \
  -d '{"light_ramp_up_minutes": 15}'
sleep 2
curl -s http://localhost:8001/api/debug/ramp-states | jq '.light_ramps'

# Verify no 60-second refresh
journalctl -u automation-service -f  # Watch for 70 seconds, should not see "Refreshed X schedules"

# Check Grafana data freshness
psql -U cea -d projectcea -c "SELECT effective_light_intensity, timestamp FROM effective_setpoints WHERE location='Flower Room' ORDER BY timestamp DESC LIMIT 1;"

# Run tests
pytest Infrastructure/automation-service/tests/ -v
```

### Final Checklist

- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Ramp changes trigger immediate recalculation (<2s)
- [ ] Grafana shows updated data within 7 seconds
- [ ] Control loop timing maintained (<5s per tick)
- [ ] Legacy 60-second refresh removed
