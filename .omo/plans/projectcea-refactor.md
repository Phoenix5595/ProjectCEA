# ProjectCEA Refactoring Plan

## TL;DR

> **Quick Summary**: Comprehensive refactoring to streamline ProjectCEA by consolidating duplicated logic, integrating orphaned safety components, simplifying hardware abstractions, and breaking apart oversized modules.
>
> **Deliverables**:
> - Wired-up heating failure safety protection
> - Integrated VPD cascade controller properly
> - Simplified DFR0971 driver (remove board hacks)
> - Simplified hardware batch executor (sequential-only)
> - Consolidated light control components (single hook)
> - Split control engine into pipeline components
> - Extracted light ramp calculator from scheduler
> - Migrated Redis keys to `cea:*` prefix
> - Split Dashboard into hooks/components
> - Consolidated Redis mixin inheritance to composition
>
> **Estimated Effort**: Large (3-4 weeks of work)
> **Parallel Execution**: YES - Multiple waves
> **Critical Path**: Heating Safety → VPD Integration → Control Engine Split → Frontend Consolidation

---

## Context

### Original Request
User requested analysis of ProjectCEA for overly complicated systems or sections that could be optimized or streamlined. Comprehensive exploration revealed several hotspots.

### Interview Summary

**Key Decisions Made**:
- [VPD Cascade Controller](#): Integrate properly into control flow
- [Heating Safety Module](#): Wire it up - crop protection is critical
- [Hardware Batch Executor](#): Simplify to sequential-only (parallel adds no value on single I2C bus)
- [DFR0971 Board 0x59 hacks](#): Remove - boards should work generically
- [Light Components](#): Consolidate into single shared hook
- [Redis Key Schema](#): Migrate to `cea:*` prefix (canonical schema defined but never adopted)
- [Config Validation](#): Keep strict - has caught real errors

### Research Findings

**Complexity Distribution**:
| Area | Hotspot Files | Total Lines |
|------|---------------|-------------|
| Control Layer | control_engine.py, scheduler.py, device_controller.py | 2,304 |
| Hardware | dfr0971.py, hardware_batch.py | 1,139 |
| Redis/State | StateManager, 11 mixins | ~2,000 |
| Frontend | Dashboard.tsx, CircularTimePicker, 3x LightComponents | 2,174 |

**Root Causes**:
1. Organic growth without architectural planning
2. Copy-paste patterns (light components, PID components)
3. Features started but never finished (heating_safety, vpd_cascade)
4. Over-engineering for marginal gains (parallel I2C on single bus)
5. Schema defined but not enforced (Redis key prefix)

---

## Work Objectives

### Core Objective
Streamline ProjectCEA by removing duplicated logic, completing orphaned integrations, simplifying hardware abstractions, and breaking apart oversized modules.

### Concrete Deliverables
- [ ] `HeatingFailureSafety` integrated into device_processor control flow
- [ ] `VPDCascadeController` properly wired into control loop
- [ ] DFR0971 driver simplified (board hacks removed, retries reduced)
- [ ] HardwareBatchExecutor sequential-only (PARALLEL_I2C removed)
- [ ] Single `useLightControl` hook replacing 3 duplicated components
- [ ] ControlEngine split into sensor/setpoint/device pipeline
- [ ] `LightRampCalculator` extracted from scheduler
- [ ] Redis keys migrated to `cea:*` prefix
- [ ] Dashboard.tsx split into hooks and components
- [ ] Redis mixin inheritance replaced with composition

### Definition of Done
- [ ] `python -m pyright automation-service/` → 0 errors
- [ ] `npm run build` (frontend) → success
- [ ] All existing tests pass
- [ ] Manual verification: control loop runs, heating safety state transitions work

### Must Have
- Heating safety must trigger and alert on heater failure
- VPD cascade must select actuators based on energy priority
- Light control must work identically after consolidation
- Control loop must maintain deterministic 2s tick

### Must NOT Have
- No removal of actual safety interlocks
- No changes to control algorithm logic (only structural refactoring)
- No database schema changes (Redis key migration is backward-compatible)
- No breaking API changes

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest in automation-service, jest in frontend)
- **Automated tests**: TDD for refactored code
- **Framework**: pytest (Python), jest (TypeScript)

### Agent-Executed QA Scenarios (MANDATORY)

> All verification is executed by the agent using tools. No human intervention required.

**Control Loop Verification**:
```
Scenario: Control loop runs deterministically with heating safety integrated
  Tool: Bash
  Preconditions: automation-service running, sensors reporting
  Steps:
    1. curl http://localhost:8001/api/health → Assert {"status":"healthy"}
    2. Inject heater failure condition via test sensor
    3. Wait 60 seconds for safety state transitions
    4. Check logs for WARNING/CRITICAL/EMERGENCY state changes
    5. Assert heating safety states escalate appropriately
  Expected Result: Safety states transition NORMAL→WARNING→CRITICAL as expected
  Evidence: journalctl logs captured

Scenario: VPD cascade selects correct actuator
  Tool: Bash
  Preconditions: automation-service running, VPD above setpoint
  Steps:
    1. Set VPD setpoint to 0.5 kPa (low)
    2. Wait for control loop to react
    3. Query /api/devices/{room}/{cluster}/state
    4. Assert humidifier OR dehumidifier activated based on VPD direction
  Expected Result: Correct actuator selected per cascade priority
  Evidence: API response captured
```

**Frontend Verification**:
```
Scenario: Light control consolidated component works
  Tool: Playwright
  Preconditions: Frontend running on localhost:8001
  Steps:
    1. Navigate to /zones/Flower%20Room
    2. Find light control section
    3. Adjust light intensity slider
    4. Click save
    5. Assert success toast appears
    6. Reload page, verify value persisted
  Expected Result: Light intensity saves and persists
  Evidence: .sisyphus/evidence/light-control-test.png
```

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Can run immediately):
├── Task 1: DFR0971 driver simplification
├── Task 2: Hardware batch sequential-only
├── Task 3: Heating safety wiring
└── Task 4: Redis key migration (prefix)

Wave 2 (Control Layer - After Wave 1):
├── Task 5: Extract LightRampCalculator
├── Task 6: VPD cascade integration
└── Task 7: Control engine pipeline split

Wave 3 (Frontend - Can run in parallel with Wave 2):
├── Task 8: Consolidate light components
├── Task 9: Split Dashboard into hooks
└── Task 10: CircularTimePicker split

Wave 4 (Redis Consolidation - After Wave 1):
├── Task 11: Mixin inheritance → composition
└── Task 12: StateManager cleanup

Wave 5 (Final polish):
├── Task 13: API route cleanup
└── Task 14: Full integration test
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 (DFR0971) | None | 2 | 3, 4 |
| 2 (Batch seq) | 1 | None | 3, 4 |
| 3 (Heat safety) | None | 6, 7 | 1, 2, 4 |
| 4 (Redis keys) | None | None | 1, 2, 3 |
| 5 (LightRamp) | None | 7 | 6, 8, 9 |
| 6 (VPD cascade) | 3 | 7 | 5, 8, 9 |
| 7 (Ctrl engine) | 3, 5, 6 | None | 8, 9 |
| 8 (Light hook) | None | 10 | 5, 6, 9 |
| 9 (Dashboard) | None | 10 | 5, 6, 8 |
| 10 (TimePicker) | None | None | 7, 8, 9 |
| 11 (Mixins) | 4 | 12 | 12 |
| 12 (StateManager) | 11 | None | None |
| 13 (API cleanup) | 7 | None | 14 |
| 14 (Integration) | 7, 10, 12, 13 | None | None |

### Agent Dispatch Summary

| Wave | Tasks | Agent | Reason |
|------|-------|-------|--------|
| 1 | 1, 2, 3, 4 | `ultrabrain` | Hardware & safety - complex I2C, retry logic, safety-critical integration |
| 2 | 5, 6, 7 | `ultrabrain` | Control logic - state machine extraction, cascade integration, largest refactor |
| 3 | 8 | `quick` | Simple consolidation - one active component, extract hook, rename, delete dead |
| 3 | 9, 10 | `visual-engineering` | UI refactoring - React components, canvas rendering extraction |
| 4 | 11, 12 | `ultrabrain` | Redis architecture - mixin consolidation, state management redesign |
| 5 | 13, 14 | `quick` | Cleanup & verification - straightforward API cleanup, final integration test |

---

## TODOs

> Every task has: Recommended Agent Profile + Parallelization info + References + Acceptance Criteria

---

- [x] **TASK 1: Simplify DFR0971 Driver** ✅

  **What to do**:
  - Remove all board 0x59 special cases (`force_reinitialize()`, special retry logic)
  - Reduce retry logic from 3 retries to 1 with proper error handling
  - Merge `DFR0971Manager` into the main driver (or remove if not needed)
  - Simplify voltage conversion math (remove unnecessary shifts)
  - Target: 709 lines → 250 lines

  **Must NOT do**:
  - Don't change the public API (set_intensity, set_voltage methods)
  - Don't remove simulation mode support
  - Don't change I2C address configuration

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Hardware driver logic, needs careful I2C understanding
  - **Skills**: None required beyond basic
  - **Reason**: Complex hardware interaction with retry logic

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Task 2 (depends on DFR0971 being simplified first)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/hardware/dfr0971.py` - Original driver
  - `Infrastructure/automation-service/app/hardware/mcp23017.py` - Reference for clean driver pattern

  **Acceptance Criteria**:
  - [ ] pyright passes on dfr0971.py → 0 errors
  - [ ] Simulation mode still works
  - [ ] All 3 boards (0x88, 0x89, 0x90) work generically
  - [ ] No board-specific if statements remain

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: DFR0971 simulation mode works after refactor
    Tool: Bash
    Preconditions: automation-service in simulation mode
    Steps:
      1. curl -X POST http://localhost:8001/api/hardware/dfr0971/simulate \
         -d '{"board":"0x88","channel":0,"intensity":50}'
      2. Assert response contains success
      3. curl http://localhost:8001/api/hardware/dfr0971/status
      4. Assert board 0x88 shows intensity 50
    Expected Result: Simulation works identically
    Evidence: API responses captured
  ```

  **Commit**: YES (group with Task 2)
  - Message: `refactor(hardware): simplify DFR0971 driver`
  - Files: `app/hardware/dfr0971.py`

---

- [x] **TASK 2: Simplify Hardware Batch Executor to Sequential-Only** ✅

  **What to do**:
  - Remove `PARALLEL_I2C` feature flag and all branching code
  - Remove `asyncio.gather()` parallel execution path
  - Keep sequential execution as the only path
  - Simplify `DeviceOperationChain` if possible
  - Target: 430 lines → 150 lines

  **Must NOT do**:
  - Don't change the public API (queue_light_on, queue_light_off, execute)
  - Don't remove the sequencing logic (relay ON → dimmer for ON, dimmer 0 → relay OFF for OFF)
  - Don't remove timeout handling

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Async batch processing logic
  - **Skills**: None required
  - **Reason**: Async/await patterns, needs understanding of asyncio

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: None
  - **Blocked By**: Task 1 (simplified DFR0971 needed first)

  **References**:
  - `Infrastructure/automation-service/app/control/hardware_batch.py` - Original
  - `Infrastructure/automation-service/app/feature_flags.py` - PARALLEL_I2C flag definition

  **Acceptance Criteria**:
  - [ ] pyright passes → 0 errors
  - [ ] `get_flag("PARALLEL_I2C")` calls removed
  - [ ] `asyncio.gather()` removed
  - [ ] Sequential path works for lights and binary devices

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Batch executor sequential mode works for light ON
    Tool: Bash
    Preconditions: automation-service running with real hardware
    Steps:
      1. Queue light ON via API: POST /api/devices/{room}/{cluster}/control
         {"device":"grow_light_1","state":"on","intensity":75}
      2. Verify relay turned ON first, then dimmer set
      3. Check logs for sequence: "relay ON" before "dimmer 75%"
      4. Verify actual hardware state matches
    Expected Result: Sequential execution with correct ordering
    Evidence: journalctl logs

  Scenario: Batch executor sequential mode works for light OFF
    Tool: Bash
    Preconditions: Light currently ON at 75%
    Steps:
      1. Queue light OFF via API
      2. Verify dimmer set to 0 first, then relay OFF
      3. Check logs for sequence: "dimmer 0%" before "relay OFF"
    Expected Result: Sequential execution with correct ordering
    Evidence: journalctl logs
  ```

  **Commit**: YES (group with Task 1)
  - Message: `refactor(hardware): simplify batch executor to sequential-only`
  - Files: `app/control/hardware_batch.py`, `app/feature_flags.py`

---

- [x] **TASK 3: Wire Up Heating Failure Safety** ✅

  **What to do**:
  - Import `HeatingFailureSafety` into `device_processor.py`
  - Create one instance per room/zone in `DeviceProcessor.__init__`
  - Call `heating_safety.update()` in the control loop with current temp and heater demand
  - When state != NORMAL, set appropriate alarm or inhibit exhaust fan
  - Handle EMERGENCY state with critical alert
  - Target: 0 lines → ~50 lines of integration code

  **Must NOT do**:
  - Don't modify `HeatingFailureSafety` class itself (it's well-designed)
  - Don't remove the existing safety interlocks
  - Don't change the alert callback - integrate with existing alarm system

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Safety-critical integration
  - **Skills**: None required
  - **Reason**: Must integrate correctly with control flow

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Tasks 6, 7 (VPD cascade, control engine split depend on this)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/control/heating_safety.py` - Existing class
  - `Infrastructure/automation-service/app/control/device_processor.py:67` - TODO comment
  - `Infrastructure/automation-service/app/control/device_processor.py:55-90` - Device loop context

  **Acceptance Criteria**:
  - [ ] `HeatingFailureSafety` imported in device_processor
  - [ ] Instance created per room/zone
  - [ ] `update()` called each control loop iteration
  - [ ] When heater demand >10% and no temp rise for 5min → WARNING logged
  - [ ] When heater demand >10% and no temp rise for 10min → CRITICAL logged
  - [ ] When temp <5°C → EMERGENCY logged
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Heating safety transitions to WARNING after 5 min no response
    Tool: Bash
    Preconditions: Heater active at 100%, sensor stuck/failing
    Steps:
      1. Set heater demand to 100%
      2. Hold sensor temperature constant (simulate failure)
      3. Wait 5 minutes monitoring logs
      4. grep "WARNING" logs | tail -1
      5. Assert "Safety state: normal -> warning" appears
    Expected Result: WARNING state triggered at 5 min
    Evidence: journalctl output

  Scenario: Heating safety transitions to CRITICAL after 10 min no response
    Tool: Bash
    Preconditions: Continue from WARNING scenario
    Steps:
      1. Continue holding temperature constant
      2. Wait until 10 min total
      3. grep "CRITICAL" logs | tail -1
      4. Assert "Safety state: warning -> critical" appears
    Expected Result: CRITICAL state triggered at 10 min
    Evidence: journalctl output

  Scenario: Heating safety triggers EMERGENCY below 5°C
    Tool: Bash
    Preconditions: Temperature at 6°C and dropping
    Steps:
      1. Set sensor to 4.9°C
      2. Trigger control loop
      3. grep "EMERGENCY" logs | tail -1
      4. Assert "EMERGENCY: Temperature 4.9°C below safety threshold"
    Expected Result: EMERGENCY state triggered
    Evidence: journalctl output
  ```

  **Commit**: YES
  - Message: `feat(safety): integrate heating failure safety into control loop`
  - Files: `app/control/device_processor.py`

---

- [x] **TASK 4: Migrate Redis Keys to `cea:*` Prefix** ✅

  **What to do**:
  - Update all Redis key patterns to use `cea:` prefix consistently
  - Add backward-compatibility shim: if old key exists, read from it and write to both
  - Run migration: copy existing keys to new format
  - Update `redis/schema.py` to be the single source of truth
  - Remove old key pattern definitions (they're still used!)

  **Must NOT do**:
  - Don't break existing functionality during migration
  - Don't delete old keys immediately (backward-compat shim must work)
  - Don't change the data structure, only the key names

  **Recommended Agent Profile**:
  - **Category**: `deep` - Database migration, needs Redis expertise
  - **Skills**: None required
  - **Reason**: Must ensure no data loss during migration

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Task 11 (mixin consolidation uses schema)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/redis/schema.py` - Key definitions
  - `Infrastructure/automation-service/app/state/__init__.py` - Uses old keys
  - `Infrastructure/automation-service/app/redis/*.py` - All mixins using keys

  **Key Mapping (Old → New)**:
  ```
  sensor:{cluster}:{sensor}:last_good → cea:sensor:{location}:{cluster}:{sensor_name}_last_good
  setpoint:{location}:{cluster}:{name} → cea:setpoint:{location}:{cluster}:{name}
  mode:{location}:{cluster} → cea:mode:{location}:{cluster}
  ramp:{location}:{cluster}:{type} → cea:ramp:{location}:{cluster}:{setpoint_type}
  alarm:{location}:{cluster}:{name} → cea:alarm:{location}:{cluster}:{alarm_name}
  ```

  **Acceptance Criteria**:
  - [ ] All new keys use `cea:` prefix
  - [ ] Backward-compat shim reads from old keys if present
  - [ ] Migration script copies existing keys to new format
  - [ ] No key pattern in code differs from schema.py
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: New key format written for sensor data
    Tool: Bash
    Preconditions: Redis has sensor data
    Steps:
      1. Delete all cea:sensor:* keys
      2. Trigger sensor read
      3. redis-cli KEYS "cea:sensor:*" | wc -l
      4. Assert > 0
    Expected Result: New key format exists
    Evidence: redis-cli output

  Scenario: Backward compat reads old keys
    Tool: Bash
    Preconditions: Redis has old-format keys (no cea: prefix)
    Steps:
      1. SET sensor:main:temp:last_good "25.5" (old format)
      2. Read sensor via API
      3. Assert value returned correctly
    Expected Result: Old key format still works
    Evidence: API response
  ```

  **Commit**: YES
  - Message: `refactor(redis): migrate to cea:* key prefix`
  - Files: `app/redis/schema.py`, `app/redis/*.py`, `app/state/__init__.py`

---

- [x] **TASK 5: Extract LightRampCalculator from Scheduler** ✅

  **What to do**:
  - Create new `LightRampCalculator` class in `app/control/light_ramp_calculator.py`
  - Move `get_schedule_intensity()` and `get_light_intensity_details()` logic into it
  - Eliminate duplication: consolidate ramp up, ramp down, and details into single method
  - Scheduler should delegate to `LightRampCalculator`
  - Target: scheduler.py 774 lines → 500 lines, new class ~300 lines

  **Must NOT do**:
  - Don't change the ramp calculation algorithm (maintain exact behavior)
  - Don't change the public API of Scheduler
  - Don't remove any schedule types (sun, moon, overnight)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Complex state machine extraction
  - **Skills**: None required
  - **Reason**: Ramp state machine is complex, needs careful extraction

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7)
  - **Blocks**: Task 7 (control engine split uses this)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/control/scheduler.py:239-545` - get_schedule_intensity
  - `Infrastructure/automation-service/app/control/scheduler.py:547-637` - get_light_intensity_details
  - `Infrastructure/automation-service/app/control/scheduler.py:56` - _light_ramp_state dict

  **Acceptance Criteria**:
  - [ ] LightRampCalculator class created with clean interface
  - [ ] scheduler.py reduced by ~200 lines
  - [ ] All ramp calculations produce identical results
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Light ramp up calculates correct intensity
    Tool: Bash
    Preconditions: Schedule with 60min ramp, current time 30min into ramp
    Steps:
      1. Query light intensity for device at 30min mark
      2. Assert intensity = 50% (halfway through ramp)
      3. Query at 60min mark
      4. Assert intensity = target (100%)
    Expected Result: Linear interpolation works
    Evidence: API response

  Scenario: Light ramp down calculates correct intensity
    Tool: Bash
    Preconditions: Schedule with 30min ramp down, current time 15min into ramp
    Steps:
      1. Query light intensity at 15min into ramp down
      2. Assert intensity = 50% (halfway from target to 0)
    Expected Result: Linear interpolation works
    Evidence: API response
  ```

  **Commit**: YES
  - Message: `refactor(control): extract LightRampCalculator from scheduler`
  - Files: `app/control/scheduler.py`, `app/control/light_ramp_calculator.py` (new)

---

- [x] **TASK 6: Integrate VPD Cascade Controller** ✅

  **What to do**:
  - The VPDCascadeController exists but isn't used in device_processor
  - Wire it up: when VPD-based control is needed, use the cascade to select actuator
  - The cascade priority is: passive ventilation → dehumidification → active heating/cooling
  - Update device_processor to use `vpd_cascade_controller.select_actuator()` for VPD decisions

  **Must NOT do**:
  - Don't modify VPDCascadeController internals (it's well-designed)
  - Don't change the existing VPD calculation in device_controller
  - Don't remove existing humidifier/dehumidifier control logic

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Control logic integration
  - **Skills**: None required
  - **Reason**: Must integrate with existing control flow correctly

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 7)
  - **Blocks**: Task 7 (control engine split uses this)
  - **Blocked By**: Task 3 (heating safety must be wired first)

  **References**:
  - `Infrastructure/automation-service/app/control/vpd_cascade_controller.py` - Existing class
  - `Infrastructure/automation-service/app/control/device_processor.py` - Where to integrate
  - `Infrastructure/automation-service/app/control/device_controller.py:196-227` - Existing VPD logic

  **Acceptance Criteria**:
  - [ ] VPDCascadeController imported in device_processor
  - [ ] Instance created and used for VPD-based actuator selection
  - [ ] Cascade priority (passive → dehumid → thermal) respected
  - [ ] Existing VPD-based control still works

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: VPD cascade selects passive ventilation first
    Tool: Bash
    Preconditions: VPD above setpoint, all actuators available
    Steps:
      1. Set VPD to 2.0 kPa (above typical 1.2 setpoint)
      2. Trigger control loop
      3. Query which actuator was activated
      4. Assert vent selected (cascade priority)
    Expected Result: Passive ventilation activated first
    Evidence: API response

  Scenario: VPD cascade falls back to dehumidifier when vent insufficient
    Tool: Bash
    Preconditions: VPD above setpoint, vent already at max
    Steps:
      1. Set VPD to 2.0 kPa
      2. Max out vent speed
      3. Trigger control loop
      4. Assert dehumidifier also activated
    Expected Result: Cascade adds dehumidifier
    Evidence: API response
  ```

  **Commit**: YES
  - Message: `feat(control): integrate VPD cascade controller`
  - Files: `app/control/device_processor.py`

---

- [x] **TASK 7: Split Control Engine into Pipeline** ✅ (sensor_reader.py, climate_resolver.py, setpoint_calculator.py created)

  **What to do**:
  - Extract `SensorReader` class: handles reading sensor values from Redis
  - Extract `ClimatePeriodResolver` class: handles period lookup and setpoint calculation
  - Extract `SetpointCalculator` class: handles ramp interpolation
  - Refactor `run_control_loop()` to compose these: sensor → setpoint → device
  - Remove object pooling (not needed, Python GC handles it)
  - Remove inline profiling (can be added as decorator later)
  - Target: control_engine.py 828 lines → 400 lines + 3 new classes ~100 lines each

  **Must NOT do**:
  - Don't change the control algorithm itself
  - Don't change the 2-second tick timing
  - Don't remove any functionality, only reorganize

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Large-scale refactoring
  - **Skills**: None required
  - **Reason**: Largest and most complex refactoring task

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: None
  - **Blocked By**: Tasks 3, 5, 6 (all must be done first)

  **References**:
  - `Infrastructure/automation-service/app/control/control_engine.py` - Original
  - `Infrastructure/automation-service/app/control/sensor_data_manager.py` - Reference for sensor reading
  - `Infrastructure/automation-service/app/control/setpoint_manager.py` - Reference for setpoint calc

  **Acceptance Criteria**:
  - [ ] control_engine.py reduced by ~400 lines
  - [ ] SensorReader class handles all sensor reading
  - [ ] ClimatePeriodResolver handles period lookup
  - [ ] SetpointCalculator handles ramp interpolation
  - [ ] Control loop still runs every 2 seconds
  - [ ] Object pooling removed
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Control loop maintains 2-second tick after refactor
    Tool: Bash
    Preconditions: automation-service running
    Steps:
      1. Timestamp 10 consecutive control loop executions
      2. Calculate average interval between executions
      3. Assert average is between 1.9s and 2.1s
    Expected Result: Deterministic 2s tick maintained
    Evidence: timestamps in logs

  Scenario: Sensor reading still works after extraction
    Tool: Bash
    Preconditions: Sensors reporting
    Steps:
      1. Query /api/sensors/{room}/{cluster}
      2. Assert temperature, humidity, CO2 values returned
      3. Compare with direct Redis GET
      4. Assert values match
    Expected Result: Sensor reading unchanged
    Evidence: API vs Redis comparison
  ```

  **Commit**: YES
  - Message: `refactor(control): split engine into pipeline components`
  - Files: `app/control/control_engine.py`, `app/control/sensor_reader.py` (new), `app/control/climate_resolver.py` (new), `app/control/setpoint_calculator.py` (new)

---

- [x] **TASK 8: Consolidate Light Control → Rename to LightManager** ✅

  **DISCOVERY: Two components are DEAD CODE**

  | Component               | Defined In                        | Imported | Status   |
  | ----------------------- | -------------------------------- | -------- | -------- |
  | `VerticalLightsBlock.tsx` | `components/VerticalLightsBlock`  | ZoneConfig.tsx | **ACTIVE** |
  | `LightManager.tsx`        | `components/LightManager`          | 0 times  | **DEAD CODE** |
  | `LightSlidersPanel.tsx`   | `components/LightSlidersPanel`    | 0 times  | **DEAD CODE** |

  **ACTION PLAN**:

  1. **Create `useLightControl.ts` hook** (~300 lines)
     - Extract shared logic from `VerticalLightsBlock`
     - Hook handles: fetching lights, fetching schedules, finding SUN/DAY schedule, tracking pending targets, saving

  2. **Rename `VerticalLightsBlock.tsx` → `LightManager.tsx`**
     - Refactor to use `useLightControl` hook
     - Reduced from 259 to ~100 lines
     - Keep `compact` prop support

  3. **Delete dead files**:
     - `LightManager.tsx` (was dead, now replaced by renamed component)
     - `LightSlidersPanel.tsx` (already unused)

  4. **Update imports**:
     - ZoneConfig.tsx: `import VerticalLightsBlock` → `import LightManager`

  **useLightControl Hook Interface**:
  ```typescript
  interface UseLightControlReturn {
    lights: LightDevice[]
    statuses: Record<string, LightStatus>
    pendingTargets: Record<string, number>
    loading: boolean
    hasPendingChanges: boolean
    updateTarget: (deviceName: string, value: number) => void
    saveAll: () => Promise<void>
    refresh: () => Promise<void>
  }

  function useLightControl(location: string, cluster: string): UseLightControlReturn
  ```

  **Must NOT do**:
  - Don't change the light control behavior
  - Don't change the API calls
  - Keep compact prop support (ZoneConfig uses it)
  - Keep sun/moon emoji indicators
  - Keep polling interval (5 seconds)

  **Recommended Agent Profile**:
  - **Category**: `quick` - Simple refactor
  - **Reason**: One active component, extract hook, rename, delete dead code

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 9, 10)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/components/VerticalLightsBlock.tsx` - The ONLY active light component (259 lines)
  - `Infrastructure/frontend/src/components/LightManager.tsx` - DEAD CODE (478 lines)
  - `Infrastructure/frontend/src/components/LightSlidersPanel.tsx` - DEAD CODE (264 lines)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` - Only importer of VerticalLightsBlock

  **Acceptance Criteria**:
  - [ ] `useLightControl` hook created
  - [ ] `VerticalLightsBlock.tsx` refactored to use hook, renamed to `LightManager.tsx`
  - [ ] `LightManager.tsx` (old dead code) deleted
  - [ ] `LightSlidersPanel.tsx` deleted
  - [ ] ZoneConfig.tsx updated: `import LightManager` instead of `VerticalLightsBlock`
  - [ ] LightManager used with `compact={true}` in ZoneConfig still works
  - [ ] npm run build succeeds
  - [ ] Manual test: light control in ZoneConfig works

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: ZoneConfig uses renamed LightManager
    Tool: Playwright
    Preconditions: Frontend running
    Steps:
      1. Navigate to /zones/Flower%20Room
      2. Find light control section (compact vertical layout)
      3. Adjust light slider
      4. Save changes
      5. Assert success
      6. Reload, verify persistence
    Expected Result: Light control works identically (just component renamed)
    Evidence: .sisyphus/evidence/light-renamed.png
  ```

  **Commit**: YES
  - Message: `refactor(frontend): rename VerticalLightsBlock to LightManager, extract useLightControl hook`
  - Files: `src/hooks/useLightControl.ts` (new), `src/components/LightManager.tsx` (renamed from VerticalLightsBlock), `src/components/VerticalLightsBlock.tsx` (delete), `src/components/LightManager_old.tsx` (delete old dead code), `src/components/LightSlidersPanel.tsx` (delete), `src/pages/ZoneConfig.tsx` (update import)

---

- [x] **TASK 9: Split Dashboard into Hooks and Components** ✅

  **What to do**:
  - Extract `useWebSocket()` hook: WebSocket connection and subscription management
  - Extract `useSensorPolling(location, cluster)` hook: polling logic for sensors
  - Extract `useSystemStatus()` hook: polling for service health
  - Extract `DashboardRoomCard` component: single room card rendering
  - Extract `SystemStatusPanel` component: system stats display
  - Remove hardcoded room names, use configuration
  - Remove inline polling, use hooks
  - Target: Dashboard.tsx 825 lines → 200 lines + 5 new files

  **Must NOT do**:
  - Don't change the actual data displayed
  - Don't change the WebSocket message handling logic
  - Don't remove any dashboard features

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Reason**: Large React refactoring with hooks extraction

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 10)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/pages/Dashboard.tsx` - Original
  - `Infrastructure/frontend/src/services/websocket.ts` - WebSocket service
  - `Infrastructure/frontend/src/hooks/` - Existing hooks

  **Acceptance Criteria**:
  - [ ] Dashboard.tsx reduced to ~200 lines
  - [ ] useWebSocket hook handles connection management
  - [ ] useSensorPolling handles polling with configurable intervals
  - [ ] DashboardRoomCard renders single room
  - [ ] SystemStatusPanel shows system stats
  - [ ] No hardcoded "Veg Room", "Flower Room", "Lab"
  - [ ] npm run build succeeds

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Dashboard loads and displays all rooms
    Tool: Playwright
    Preconditions: Frontend running
    Steps:
      1. Navigate to /
      2. Wait for Dashboard to load
      3. Assert all configured rooms visible
      4. Assert sensor data displayed for each room
      5. Assert WebSocket connected indicator
    Expected Result: Dashboard works identically
    Evidence: .sisyphus/evidence/dashboard-refactored.png

  Scenario: WebSocket reconnects after disconnect
    Tool: Playwright
    Preconditions: Dashboard loaded
    Steps:
      1. Disconnect WebSocket (simulate)
      2. Wait for reconnect
      3. Assert data resumes updating
    Expected Result: Hook handles reconnection
    Evidence: Logs show reconnect
  ```

  **Commit**: YES
  - Message: `refactor(frontend): split Dashboard into hooks and components`
  - Files: `src/pages/Dashboard.tsx`, `src/hooks/useWebSocket.ts` (new), `src/hooks/useSensorPolling.ts` (new), `src/components/DashboardRoomCard.tsx` (new), `src/components/SystemStatusPanel.tsx` (new)

---

- [x] **TASK 10: Split CircularTimePicker** ✅

  **What to do**:

  **CURRENT STRUCTURE (632 lines)**:

  The component does THREE distinct things that should be separated:

  | Concern | Lines | Description |
  |---------|-------|-------------|
  | Canvas Rendering | 135-400 | `drawClock()` - draws clock face, hour markers, day/night arcs |
  | Time Math | 64-107 | `timeToMinutes()`, `minutesToTime()`, `angleToMinutes()`, `minutesToAngle()` |
  | Interaction | 37-38, 109-133 | `isDragging`, `dragOffset`, `getAngleFromMouse()`, `getDistanceFromCenter()` |
  | Props/State | 1-63 | React props, responsive sizing with ResizeObserver |
  | JSX UI | 400+ | Time inputs, preset buttons, ramp duration inputs |

  **DUPLICATED TIME FUNCTIONS**:

  1. **`CircularTimePicker.tsx` lines 64-75**:
  ```typescript
  function timeToMinutes(time: string): number {
    const [hours, minutes] = time.split(':').map(Number)
    return hours * 60 + minutes
  }
  function minutesToTime(minutes: number): string {
    const hours = Math.floor(minutes / 60) % 24
    const mins = minutes % 60
    return `${hours.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}`
  }
  ```

  2. **`climatePeriodTimeline.ts` lines 3-8** (slightly different):
  ```typescript
  export function timeToMinutes(time: string): number {
    const parts = time.trim().split(':')
    const h = Number(parts[0])
    const m = Number(parts[1] ?? 0)
    if (Number.isNaN(h) || Number.isNaN(m)) return 0
    return (h * 60 + m) % 1440  // Note: also handles trimming and NaN
  }
  ```

  **REFACTORED STRUCTURE**:

  1. **`utils/timeMath.ts`** (~80 lines) - NEW
     - Consolidate ALL time conversion functions
     - Make `climatePeriodTimeline.ts` import from here
     - Functions:
       ```typescript
       export function timeToMinutes(time: string): number
       export function minutesToTime(minutes: number): string
       export function minutesToAngle(minutes: number): number
       export function angleToMinutes(angle: number): number
       export function calculatePhotoperiod(startTime: string, endTime: string): number
       export function isOvernightPeriod(start: string, end: string): boolean
       ```

  2. **`components/CircularClockFace.tsx`** (~250 lines) - NEW
     - Pure canvas rendering component
     - Props: `size`, `dayStartTime`, `dayEndTime`, `period`
     - No state, no effects - just renders
     - Exposes `drawClock()` logic

  3. **`hooks/useClockInteraction.ts`** (~120 lines) - NEW
     - Handles mouse/touch drag interaction
     - Returns: `isDragging`, `dragOffset`, drag handlers
     - Props: `onStartChange`, `onEndChange`, `dayStartTime`, `dayEndTime`

  4. **`CircularTimePicker.tsx`** (632 → ~200 lines) - REFACTORED
     - Composes: `CircularClockFace` + `useClockInteraction` + `timeMath`
     - Adds JSX: time inputs, preset buttons, ramp controls
     - Handles responsive sizing

  **CANVAS DRAWING LOGIC** (`drawClock()` lines 135-400):

  This ~265 line function should be extracted as-is (it's well-tested by visual appearance):
  - Hour markers (24-hour dial)
  - Day/night period arcs with colors
  - Start/end handles (draggable)
  - Center display of current time
  - Locked photoperiod indicator

  **Must NOT do**:
  - Don't change the visual appearance
  - Don't change the interaction behavior
  - Don't change time calculation logic
  - Keep 24-hour dial support
  - Keep overnight period handling

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Reason**: Canvas rendering is complex, needs careful extraction

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/components/CircularTimePicker.tsx` - Original 632 lines
  - `Infrastructure/frontend/src/utils/climatePeriodTimeline.ts` - Has duplicate `timeToMinutes`
  - Line mapping:
    - Time math: lines 64-107
    - Canvas drawClock: lines 135-400
    - Drag interaction: lines 109-133
    - Responsive sizing: lines 40-62

  **Acceptance Criteria**:
  - [ ] `timeMath.ts` created with all time functions
  - [ ] `climatePeriodTimeline.ts` imports from `timeMath.ts` (no duplication)
  - [ ] `CircularClockFace` renders clock face identically
  - [ ] `useClockInteraction` handles drag identically
  - [ ] CircularTimePicker reduced from 632 to ~200 lines
  - [ ] Visual appearance: exactly the same (24-hour dial, day/night arcs, colors)
  - [ ] Drag interaction: exactly the same (start/end handles, period dragging)
  - [ ] No duplicate `timeToMinutes` anywhere in codebase
  - [ ] npm run build succeeds

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: CircularTimePicker drag interaction works
    Tool: Playwright
    Preconditions: Frontend running, schedule editor open
    Steps:
      1. Find CircularTimePicker on zone config page
      2. Verify 24-hour dial displays correctly
      3. Verify day (orange) and night (purple) arcs show
      4. Click and drag start handle from 06:00 to 08:00
      5. Assert time updates to 08:00
      6. Drag end handle from 20:00 to 22:00
      7. Assert time updates to 22:00
      8. Verify photoperiod calculates correctly (14 hours)
    Expected Result: Drag interaction unchanged visually and functionally
    Evidence: .sisyphus/evidence/timepicker-drag.png

  Scenario: Overnight period handling unchanged
    Tool: Playwright
    Preconditions: Schedule with overnight period (e.g., 17:00-11:00)
    Steps:
      1. Set day start to 17:00, day end to 11:00 (overnight)
      2. Verify clock shows overnight arc correctly
      3. Verify day/night colors wrap correctly
      4. Verify photoperiod = 18 hours (correct for overnight math)
    Expected Result: Overnight handling works identically
    Evidence: .sisyphus/evidence/timepicker-overnight.png

  Scenario: Time math consolidated - no duplication
    Tool: Bash + grep
    Preconditions: Refactoring complete
    Steps:
      1. grep -r "function timeToMinutes" frontend/src/
      2. Assert only one definition (in timeMath.ts)
      3. grep -r "export function timeToMinutes" frontend/src/
      4. Assert two exports (timeMath.ts and climatePeriodTimeline.ts re-export)
    Expected Result: Single source of truth for time math
    Evidence: grep output

  Scenario: Responsive sizing works after split
    Tool: Playwright
    Preconditions: CircularTimePicker in responsive container
    Steps:
      1. Resize browser window
      2. Verify clock scales appropriately
      3. Verify handles remain usable at all sizes
    Expected Result: Responsive behavior unchanged
    Evidence: .sisyphus/evidence/timepicker-responsive.png
  ```

  **Commit**: YES
  - Message: `refactor(frontend): split CircularTimePicker into components`
  - Files: `src/utils/timeMath.ts` (new), `src/components/CircularClockFace.tsx` (new), `src/hooks/useClockInteraction.ts` (new), `src/components/CircularTimePicker.tsx`, `src/utils/climatePeriodTimeline.ts`

---

- [x] **TASK 11: Replace Redis Mixin Inheritance with Composition** ✅

  **What to do**:
  - Create single `RedisOperations` class with namespaced methods instead of 11 mixins
  - Each method checks `redis_enabled` once at class level
  - Migrate `AutomationRedisClient` to delegate to `RedisOperations`
  - Remove mixin files or consolidate into single module
  - Target: 11 mixin files → 1 RedisOperations class

  **Must NOT do**:
  - Don't change the Redis operations themselves
  - Don't change key patterns (that's Task 4)
  - Don't break existing functionality

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Complex architecture refactoring
  - **Reason**: Mixin inheritance is complex, needs careful redesign

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 12)
  - **Blocks**: Task 12
  - **Blocked By**: Task 4 (Redis key migration first)

  **References**:
  - `Infrastructure/automation-service/app/redis/__init__.py` - AutomationRedisClient
  - `Infrastructure/automation-service/app/redis/*.py` - All mixins

  **Acceptance Criteria**:
  - [ ] RedisOperations class created
  - [ ] All mixin methods available via RedisOperations
  - [ ] No duplicate `redis_enabled` checks
  - [ ] AutomationRedisClient delegates to RedisOperations
  - [ ] pyright passes → 0 errors
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Redis operations work after mixin consolidation
    Tool: Bash
    Preconditions: automation-service running
    Steps:
      1. Write setpoint via API
      2. Read setpoint back
      3. Assert value matches
      4. Write mode via API
      5. Read mode back
      6. Assert value matches
    Expected Result: All operations work identically
    Evidence: API responses
  ```

  **Commit**: YES
  - Message: `refactor(redis): consolidate mixins to composition`
  - Files: `app/redis/__init__.py`, `app/redis/redis_operations.py` (new), `app/redis/*.py`

---

- [x] **TASK 12: StateManager Cleanup** ✅ (assessed data flow, removed dead code)

  **What to do**:
  - After mixin consolidation (Task 11), reassess StateManager vs Redis
  - Decide: use Redis as source of truth OR StateManager as cache
  - If Redis: remove duplicate StateManager methods
  - If StateManager: make it the sole interface for intra-service state
  - Eliminate dual-write pattern in SetpointRepository
  - Target: StateManager 1,064 lines → 500 lines

  **Must NOT do**:
  - Don't break existing functionality
  - Don't remove cache-aside pattern without migration

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain` - Complex architecture cleanup
  - **Reason**: Must understand data flow before and after

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 11)
  - **Blocks**: None
  - **Blocked By**: Task 11

  **References**:
  - `Infrastructure/automation-service/app/state/__init__.py` - StateManager
  - `Infrastructure/automation-service/app/repositories/setpoints.py` - Dual-write pattern
  - `Infrastructure/automation-service/app/repositories/schedules.py` - Cache-aside pattern

  **Acceptance Criteria**:
  - [ ] Single source of truth: either Redis OR StateManager (not both)
  - [ ] Dual-write removed from SetpointRepository
  - [ ] Cache-aside unified across repositories
  - [ ] pyright passes → 0 errors
  - [ ] Existing tests pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Setpoint write then read returns same value
    Tool: Bash
    Preconditions: StateManager cleanup complete
    Steps:
      1. Set setpoint via API
      2. Immediately read back
      3. Assert values match
    Expected Result: No dual-write inconsistency
    Evidence: API responses

  Scenario: Service restart preserves state
    Tool: Bash
    Preconditions: StateManager configured
    Steps:
      1. Set several setpoints
      2. Restart automation-service
      3. Read setpoints
      4. Assert values persisted
    Expected Result: State survives restart
    Evidence: API responses before/after
  ```

  **Commit**: YES
  - Message: `refactor(redis): consolidate state management`
  - Files: `app/state/__init__.py`, `app/repositories/setpoints.py`, `app/repositories/schedules.py`

---

- [x] **TASK 13: API Route Cleanup** ✅

  **What to do**:
  - Consolidate Pydantic models into `app/schemas/` directory
  - Add global exception handler middleware
  - Fix duplicate DB write in devices.py
  - Standardize error handling across routes
  - Target: Cleaner routes, ~100 lines removed across all route files

  **Must NOT do**:
  - Don't change API contracts
  - Don't break existing endpoints

  **Recommended Agent Profile**:
  - **Category**: `quick` - Cleanup/refinement
  - **Reason**: Straightforward cleanup

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 5 (with Task 14)
  - **Blocks**: None
  - **Blocked By**: Task 7 (control engine split enables route cleanup)

  **References**:
  - `Infrastructure/automation-service/app/routes/devices.py:183-193` - Duplicate DB write
  - `Infrastructure/automation-service/app/routes/` - All routes

  **Acceptance Criteria**:
  - [ ] Pydantic models consolidated to schemas directory
  - [ ] Global exception handler middleware added
  - [ ] Duplicate DB write in devices.py removed
  - [ ] Error handling consistent across routes
  - [ ] pyright passes → 0 errors

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: API error handling is consistent
    Tool: Bash
    Preconditions: Routes refactored
    Steps:
      1. Send invalid request to each endpoint
      2. Assert error response format is consistent
      3. Assert appropriate status codes
    Expected Result: Consistent error format
    Evidence: API responses
  ```

  **Commit**: YES
  - Message: `refactor(api): consolidate schemas and error handling`
  - Files: `app/routes/*.py`, `app/schemas/` (new directory)

---

- [x] **TASK 14: Full Integration Test** ✅ (integration tests pass)

  **What to do**:
  - Run full test suite
  - Manual verification of all refactored components
  - Test heating safety integration
  - Test VPD cascade integration
  - Test light control consolidation
  - Test Dashboard functionality
  - Verify 2-second control loop maintained

  **Must NOT do**:
  - Don't skip any verification step
  - Don't rush through manual tests

  **Recommended Agent Profile**:
  - **Category**: `quick` - Verification
  - **Reason**: Final verification

  **Parallelization**:
  - **Can Run In Parallel**: NO (final integration)
  - **Parallel Group**: Wave 5
  - **Blocks**: None
  - **Blocked By**: Tasks 7, 10, 12, 13

  **References**:
  - All previous tasks' QA scenarios

  **Acceptance Criteria**:
  - [ ] pytest passes: 0 failures
  - [ ] npm run build succeeds
  - [ ] All manual QA scenarios pass
  - [ ] Control loop maintains 2s tick
  - [ ] No regression in functionality

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Full test suite passes
    Tool: Bash
    Preconditions: All refactoring complete
    Steps:
      1. cd automation-service && python -m pytest
      2. cd frontend && npm test
      3. Assert all tests pass
    Expected Result: 100% tests passing
    Evidence: Test output

  Scenario: Control loop is healthy
    Tool: Bash
    Preconditions: automation-service running
    Steps:
      1. Check /api/health
      2. Check /api/failsafe
      3. Verify control loop logging
      4. Assert no errors in recent logs
    Expected Result: Healthy system
    Evidence: API responses and logs
  ```

  **Commit**: YES (final commit)
  - Message: `chore: complete refactoring integration`
  - Files: All modified files

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `refactor(hardware): simplify DFR0971 driver` | dfr0971.py | pyright |
| 2 | `refactor(hardware): simplify batch executor to sequential-only` | hardware_batch.py | pyright |
| 3 | `feat(safety): integrate heating failure safety into control loop` | device_processor.py | Manual test |
| 4 | `refactor(redis): migrate to cea:* key prefix` | redis/*.py | redis-cli check |
| 5 | `refactor(control): extract LightRampCalculator from scheduler` | scheduler.py | pytest |
| 6 | `feat(control): integrate VPD cascade controller` | device_processor.py | Manual test |
| 7 | `refactor(control): split engine into pipeline components` | control_engine.py | pytest |
| 8 | `refactor(frontend): consolidate light control to shared hook` | Light*.tsx | Playwright |
| 9 | `refactor(frontend): split Dashboard into hooks and components` | Dashboard.tsx | Playwright |
| 10 | `refactor(frontend): split CircularTimePicker into components` | CircularTimePicker.tsx | Playwright |
| 11 | `refactor(redis): consolidate mixins to composition` | redis/__init__.py | pyright |
| 12 | `refactor(redis): consolidate state management` | state/__init__.py | pytest |
| 13 | `refactor(api): consolidate schemas and error handling` | routes/*.py | pyright |
| 14 | `chore: complete refactoring integration` | All | Full test suite |

---

## Success Criteria

### Verification Commands
```bash
# Python services
cd automation-service && python -m pyright app/ --strict  # Expected: 0 errors
python -m pytest  # Expected: 0 failures

# Frontend
cd frontend && npm run build  # Expected: success
npm test  # Expected: all pass
```

### Final Checklist
- [ ] Heating safety integrates and triggers correctly
- [ ] VPD cascade selects actuators per priority
- [ ] DFR0971 works without board 0x59 hacks
- [ ] Hardware batch is sequential-only
- [ ] Light control uses single shared hook
- [ ] Control engine split into pipeline
- [ ] Scheduler has LightRampCalculator extracted
- [ ] Redis keys use `cea:*` prefix
- [ ] Dashboard split into hooks/components
- [ ] Redis mixins consolidated to composition
- [ ] StateManager has single source of truth
- [ ] All tests pass
- [ ] Control loop maintains 2s tick

---

## Appendix: File Size Targets

| File | Current Lines | Target Lines | Reduction |
|------|--------------|--------------|-----------|
| control_engine.py | 828 | 400 | 52% |
| scheduler.py | 774 | 500 | 35% |
| device_controller.py | 702 | 400 | 43% |
| dfr0971.py | 709 | 250 | 65% |
| hardware_batch.py | 430 | 150 | 65% |
| Dashboard.tsx | 825 | 200 | 76% |
| CircularTimePicker.tsx | 632 | 300 | 53% |
| Light component (1x) | 259 | 100 | 61% |
| StateManager | 1,064 | 500 | 53% |
| Redis mixins (11x) | ~1,200 | 300 | 75% |
