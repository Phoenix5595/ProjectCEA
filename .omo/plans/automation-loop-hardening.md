# Automation Loop Hardening & Optimization

## TL;DR

> **Quick Summary**: Optimize the automation control loop from ~4.5s total cycle to ~1-1.5s by: (1) switching to fixed-rate loop scheduling, (2) parallelizing I2C bus operations, (3) same-tick DB write optimization (multi-row INSERT + asyncio.gather), (4) caching per-tick DB queries, (5) reducing tick interval to 1s, and (6) improving telemetry throttle times.
>
> **Deliverables**:
> - Fixed-rate control loop with deadline enforcement
> - Parallel I2C hardware executor (relay bus 0 ‖ dimmer bus 1)
> - Same-tick multi-row INSERT for `automation_state` (13 queries → 1)
> - Same-tick `asyncio.gather()` for parallel independent DB writes
> - StateManager-cached PID parameters and climate queries
> - Reduced tick interval from 3s to 1s (configurable)
> - Reduced light telemetry throttle from 60s to 10s
> - Documentation update: "No batching of real-time tracking DB writes" rule
>
> **Estimated Effort**: Large (9 tasks, multi-file changes across control layer)
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Task 1 (fixed-rate loop) → Tasks 2-5 (parallel) → Task 7 (reduce interval) → Task 9 (verify)

---

## Context

### Original Request
Audit the automation loop to understand timing, what runs on what, in preparation for hardening and optimizing. User observed that light target changes took more than 3 seconds to take effect.

### Interview Summary
**Key Discussions**:
- Full audit of the 8-background-task architecture and per-tick I/O budget
- User confirmed: light target change latency is the pain point (>3s observed)
- Ramp recalculation code exists and works correctly (scheduler.py lines 401-413)
- I2C buses are physically separate (bus 0 = relays/MCP23017, bus 1 = dimmers/DFR0971) — can be parallelized
- User confirmed: yes, relay and dimmer operations on different devices

**Critical User Constraint**:
- **"Don't batch or delay or buffer the DB writes if it affects real-time data tracking."** — All per-tick DB writes that affect real-time tracking must happen WITHIN the tick cycle. No 10-second flush loops, no background deferral.
- Exception: `effective_setpoints` 10s buffer is ACCEPTABLE — it's historical trending data, not real-time tracking. Redis is the real-time source for control decisions.

**DB Write Analysis** (decided during interview):
| Write | Table | Purpose | Real-time? | Strategy |
|-------|-------|---------|-----------|----------|
| `_log_automation_state()` | `automation_state` (hypertable) | Per-tick device state history | YES | Same-tick multi-row INSERT |
| `log_control_action()` | `control_history` (hypertable) | State change event log | YES | Same-tick asyncio.gather() |
| `device_repo.set_device_state()` | `device_states` (current state) | Current device state for dashboard | YES | Same-tick asyncio.gather() |
| `log_effective_setpoints()` | `effective_setpoints` (hypertable) | Historical trending for Grafana | NO (Redis = real-time) | KEEP 10s buffer |

Tick overrun behavior: **Log warning, skip next sleep** (deadline-based catch-up pattern).

**Three tables are NOT redundant** despite overlapping columns:
- `device_states` = current state snapshot (1 row/device, fast "what's on now?" query)
- `automation_state` = time-series history (13 rows/tick, Grafana trending)
- `control_history` = event log (on state change, audit trail with reason)

### Metis Review Findings
**Identified Gaps** (all addressed):
1. ✅ effective_setpoints 10s buffer — clarified: KEEP (historical trending, Redis is real-time)
2. ✅ DB write deferral violated user rule — revised Tasks 4 & 5 to same-tick optimization only
3. ✅ device_states not redundant — serves distinct purpose (current state vs time-series)
4. ✅ No tick overrun handling specified — added: log warning, skip next sleep
5. ✅ PG connection pool sizing — added to Task 4 acceptance criteria
6. ✅ Guardrails added: NO batching of real-time tracking writes, NO new tables

### Performance Audit Findings

| Phase | Current Time | After Optimization |
|-------|-------------|-------------------|
| Config cache lookup | <0.1ms | <0.1ms |
| Sensor reading (Redis) | <1ms | <1ms |
| Climate period resolution (3 DB queries) | 30-90ms | <5ms (cached) |
| Setpoint calculation + VPD | <1ms | <1ms |
| Moon authority check (DB query) | 0-30ms | <1ms (cached) |
| Device processing (PID + I2C + DB) | 300-3500ms | 50-200ms |
| Light effective logging | 0-60ms (throttled) | 0-10ms |
| Automation state logging (13 INSERTs) | 30-390ms | 5-15ms (multi-row) |
| **Total tick execution** | **1.5-5s** | **0.3-0.8s** |
| **Sleep between ticks** | **3.0s (fixed)** | **0.2-0.7s (adaptive)** |
| **Total cycle** | **~4.5s** | **~1.0s** |

---

## Work Objectives

### Core Objective
Reduce the automation control loop cycle time from ~4.5s to ~1s, making light target and setpoint changes take effect within 1-2 seconds.

### Concrete Deliverables
- `background_tasks.py` — Fixed-rate loop with deadline enforcement (log warning + skip sleep on overrun)
- `hardware_batch.py` — Parallel bus execution for I2C operations
- `control_engine.py` — Same-tick multi-row INSERT for automation_state
- `control_engine.py`, `device_processor.py` — Same-tick `asyncio.gather()` for independent DB writes
- `pid_controller_manager.py` — StateManager-cached PID parameters
- `climate_resolver.py` — StateManager-cached period/mode lookups
- `automation_config.yaml` — Reduced `update_interval` from 3 to 1
- `AGENTS.md` — Document "No batching of real-time tracking DB writes" rule

### Definition of Done
- [ ] Control loop cycle time consistently ≤1.5s under normal load (p95 < 1s)
- [ ] Light target change visible in hardware within 2 seconds
- [ ] All existing tests pass
- [ ] Performance monitor shows p95 tick time <1s
- [ ] No regression in sensor data freshness or actuator responsiveness
- [ ] Real-time tracking DB writes (automation_state, control_history, device_states) happen within the tick cycle — no deferred flush

### Must Have
- Fixed-rate loop: sleep = max(0, interval - elapsed), never drift
- I2C relay and dimmer operations run concurrently across buses
- All real-time tracking DB writes happen within the tick (no buffer/defer/10s flush)
- `automation_state` uses multi-row INSERT (13 rows → 1 query) within the tick
- `log_control_action()` and `set_device_state()` run via `asyncio.gather()` within the tick
- PID parameters cached in StateManager with 60s TTL
- Climate period + room mode cached with 30s TTL
- Tick interval configurable down to 1s
- Backward-compatible: 3s interval still works as default if 1s causes issues
- Tick overrun: log warning, skip next sleep (catch-up pattern)

### Must NOT Have (Guardrails)
- No changes to safety interlocks or failsafe logic
- No changes to the PID algorithm itself
- No removal of any logging (only structural optimization)
- No changes to Redis state key writes (those are <1ms and must stay immediate)
- No changes to the Scheduler class internal logic (ramp recalculation is correct)
- No skipping of any control step — every sensor read, setpoint calc, and hardware command must still execute every tick
- No increase in I2C bus contention — bus 0 must be sequential, bus 1 must be sequential; only bus 0 and bus 1 can run in parallel
- **No batching/buffering/deferring of real-time tracking DB writes** — `automation_state`, `control_history`, `device_states` must write within the tick
- No new database tables without explicit user approval
- No reduced sensor sampling rate below 1Hz

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation):
└── Task 1: Fixed-rate control loop

Wave 2 (After Task 1 — parallel):
├── Task 2: Parallel I2C bus execution
├── Task 3: Fixed-rate background tasks
├── Task 4: Same-tick DB write optimization
└── Task 5: Multi-row INSERT for automation_state

Wave 3 (After Wave 2):
├── Task 6: Documentation (no-batching rule)
├── Task 7: Reduce tick interval to 1s
└── Task 8: Cache per-tick DB queries

Final:
└── Task 9: End-to-end verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 4, 5 | None (foundation) |
| 2 | 1 | 9 | 3, 4, 5 |
| 3 | 1 | 9 | 2, 4, 5 |
| 4 | 1 | 9 | 2, 3, 5 |
| 5 | 1 | 9 | 2, 3, 4 |
| 6 | None | None | Any |
| 7 | 2, 3, 4, 5 | 9 | 6, 8 |
| 8 | 1 | 9 | 6, 7 |
| 9 | 7, 8 | None | None (final) |

---

## TODOs

- [x] 1. Fixed-Rate Control Loop

  **What to do**:
  - Change `background_tasks.py` `_control_loop()` from post-execution sleep to deadline-based scheduling
  - Current: `await run_control_loop(); await asyncio.sleep(self.update_interval)` — total cycle = tick_time + 3s
  - New: `start = time.monotonic(); await run_control_loop(); elapsed = time.monotonic() - start; sleep_time = max(0, self.update_interval - elapsed); await asyncio.sleep(sleep_time)`
  - Or equivalently with deadline: `deadline = time.monotonic() + self.update_interval; await run_control_loop(); sleep_time = max(0, deadline - time.monotonic()); await asyncio.sleep(sleep_time)`
  - Add `time.monotonic()` import
  - Preserve the `_control_failure_count` / degraded mode logic
  - Add performance logging: log actual sleep time and tick execution time each cycle
  - Calendar mode scheduler stays inline (after control loop, before sleep)
  - **Tick overrun handling**: If tick exceeds deadline (sleep_time = 0), log warning and skip sleep — next tick starts immediately for catch-up

  **Must NOT do**:
  - Do NOT change the 1-5s validation range
  - Do NOT remove calendar mode scheduler from control loop
  - Do NOT change degraded mode detection threshold (3 failures)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (foundation)
  - **Blocks**: Tasks 2, 3, 4, 5
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/background_tasks.py:126-168` — Current loop implementation
  - `Infrastructure/automation-service/app/control/timing.py` — TimingCollector (reusable)
  - `Infrastructure/automation-service/app/control/performance_monitor.py` — PerformanceMonitor (existing metrics)

  **Acceptance Criteria**:

  - [ ] `_control_loop()` uses `time.monotonic()` deadline calculation with the simpler `start/elapsed` or `deadline/remaining` form (NOT the confusing `elapsed = time.monotonic() - deadline + self.update_interval` formula)
  - [ ] Tick overrun: log warning, skip sleep on overrun
  - [ ] Degraded mode still works (3 consecutive failures triggers, 10 successes clears)
  - [ ] Calendar mode scheduler still runs inline
  - [ ] Performance stats still recorded

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Loop timing drift is eliminated
    Tool: Bash
    Preconditions: automation-service running with current update_interval (default: 3s)
    Steps:
      1. Collect 30 seconds of loop timing: curl -s http://localhost:8001/api/timing/breakdown
      2. Assert: avg_loop_ms is consistent — no systematic drift over time (all readings within 20% of each other)
      3. Assert: No interval exceeds update_interval * 1.5 (e.g., no cycle >4.5s with 3s interval)
    Expected Result: Loop timing is consistent and predictable regardless of tick execution time
    Note: With 1s interval (after Task 7), verify avg_loop_ms < 1200, max_loop_ms < 2000
    Evidence: .sisyphus/evidence/task-1-timing.txt

  Scenario: Tick overrun handling
    Tool: Bash
    Preconditions: automation-service running
    Steps:
      1. Check logs for tick overrun warnings
      2. Verify that after overrun, next tick starts immediately
    Expected Result: Overruns logged, loop catches up by skipping sleep
    Evidence: .sisyphus/evidence/task-1-overrun.txt
  ```

  **Commit**: YES
  - Message: `perf(control): fixed-rate loop with deadline enforcement and overrun handling`
  - Files: `Infrastructure/automation-service/app/background_tasks.py`

---

- [x] 2. Parallel I2C Bus Execution

  **What to do**:
  - Modify `HardwareBatchExecutor.execute()` to group operations by I2C bus and run bus 0 (relay) and bus 1 (dimmer) concurrently via `asyncio.gather()`
  - Within each bus group, operations remain sequential (hardware requirement)
  - Reduce `CHAIN_TIMEOUT_SECONDS` from 0.5s to 0.15s per chain
  - Add performance instrumentation: relay phase time, dimmer phase time, total hardware time

  **Must NOT do**:
  - Do NOT parallelize operations on the same I2C bus
  - Do NOT change relay-before-dimmer sequencing for light ON
  - Do NOT remove per-chain timeout

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Reason**: Requires careful I2C bus sequencing analysis

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 3, 4, 5)
  - **Parallel Group**: Wave 2
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/control/hardware_batch.py:1-391` — Current sequential executor
  - `Infrastructure/automation-service/app/hardware/mcp23017.py` — Bus 0 driver
  - `Infrastructure/automation-service/app/hardware/dfr0971.py` — Bus 1 driver

  **Acceptance Criteria**:

  - [ ] Bus 0 and bus 1 execute in parallel via `asyncio.gather()`
  - [ ] Within each bus, operations remain sequential
  - [ ] `CHAIN_TIMEOUT_SECONDS` reduced to 0.15s
  - [ ] Total hardware time <300ms
  - [ ] Existing tests pass

  **Commit**: YES
  - Message: `perf(hardware): parallel I2C bus execution with reduced timeout`
  - Files: `Infrastructure/automation-service/app/control/hardware_batch.py`

---

- [x] 3. Fixed-Rate Background Tasks

  **What to do**:
  - Apply deadline-based scheduling to all background tasks (heartbeat, auto-persist, setpoint_history, **batch_flush** (effective_setpoints 10s buffer — KEEP THIS LOOP, only change its scheduling to deadline-based), calendar_sync)
  - Note: `_calendar_sync_loop` already uses `time.monotonic()` — only needs verification, not conversion
  - Tick overrun: log warning, skip sleep
  - Maintain existing intervals (30s/60s/300s/10s/300s)

  **Must NOT do**:
  - Do NOT change functional behavior or intervals
  - Do NOT remove error handling

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 2, 4, 5)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/background_tasks.py:231-474` — All background task loops

  **Acceptance Criteria**:

  - [ ] All background task loops use `time.monotonic()` deadline-based scheduling
  - [ ] Each interval unchanged (30s/60s/300s/10s)
  - [ ] Error handling preserved
  - [ ] Calendar mode scheduler still at 60s intervals

  **Commit**: YES
  - Message: `perf(background): deadline-based scheduling for all background tasks`
  - Files: `Infrastructure/automation-service/app/background_tasks.py`

---

- [x] 4. Same-Tick DB Write Optimization (asyncio.gather for Parallel Writes)

  **What to do**:
  - Move `log_control_action()`, `set_device_state()`, and `_log_automation_state()` calls out of the per-device sequential loop
  - Collect them during the tick (in `device_processor.py` or `control_engine.py`), then fire all independent DB writes with `await asyncio.gather(...)` after hardware execution completes
  - `device_processor.py` changes: collect DB write callables during device loop, return them to caller for `asyncio.gather()` dispatch
  - All writes complete WITHIN the tick — `asyncio.gather` blocks until done, but runs concurrently instead of sequentially
  - **CRITICAL**: No deferral to background flush loops. Real-time tracking writes happen within the tick.
  - Optimize `device_states`: only write when state actually changes (add check before upsert)
  - **Important behavioral note**: Currently `device_states.updated_at` is refreshed every tick (acts as a heartbeat). Always write `updated_at` even if state unchanged (lightweight UPSERT that only updates timestamp), OR rely on Redis state keys as the service heartbeat.
  - Verify PG connection pool size supports concurrent writes (≥5 connections)

  **Must NOT do**:
  - Do NOT add ANY deferred/buffered write patterns for real-time tracking tables
  - Do NOT change Redis state writes (immediate, stay immediate)
  - Do NOT change `effective_setpoints` 10s buffer (historical trending, not real-time)
  - Do NOT skip any data

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Reason**: Careful analysis of which writes can be parallelized vs sequential

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 2, 3, 5)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/control/control_engine.py:402-465` — `_set_device_state()` hot path
  - `Infrastructure/automation-service/app/control/control_engine.py:501-535` — `_log_automation_state()` per-tick
  - `Infrastructure/automation-service/app/control/device_controller.py:700-734` — `log_control_action`
  - `Infrastructure/automation-service/app/repositories/devices.py:37-64` — `set_device_state()` UPSERT

  **Acceptance Criteria**:

  - [ ] `log_control_action()` and `set_device_state()` collected during tick, fired via `asyncio.gather()` after hardware
  - [ ] `_log_automation_state()` included in the `asyncio.gather()` call
  - [ ] All DB writes complete within the tick — `await asyncio.gather(...)` blocks until done
  - [ ] `device_processor.py` collects DB write callables and returns them to `control_engine.py` for `asyncio.gather()` dispatch (not fire-and-forget)
  - [ ] Redis state writes remain immediate (no change)
  - [ ] `effective_setpoints` 10s buffer untouched
  - [ ] `device_states` upsert only on actual state change (always update `updated_at` even if state unchanged — see behavioral note above)
  - [ ] All DB writes (automation_state multi-row INSERT + parallel log_control_action + set_device_state) complete within <100ms total (p95) as measured by `/api/timing/breakdown` device_processing_time
  - [ ] PG connection pool ≥5 connections

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Real-time DB writes are same-tick (not deferred)
    Tool: Bash
    Preconditions: automation-service running
    Steps:
      1. Record time: START=$(date +%s)
      2. Sleep 5 seconds
      3. psql -U cea -d projectcea -c "SELECT MAX(timestamp) FROM automation_state WHERE timestamp > NOW() - INTERVAL '5 seconds'"
      4. Assert: most recent timestamp within 3 seconds (not 10-15s delayed)
      5. Repeat 3 times over 15 seconds
      6. Assert: all samples show data within 2 seconds of now
    Expected Result: automation_state data appears within 1-2s, not 10s batches
    Evidence: .sisyphus/evidence/task-4-realtime-db.txt
  ```

  **Commit**: YES
  - Message: `perf(control): same-tick parallel DB writes via asyncio.gather`
  - Files: `Infrastructure/automation-service/app/control/control_engine.py`, `Infrastructure/automation-service/app/control/device_processor.py`, `Infrastructure/automation-service/app/control/device_controller.py`

---

- [x] 5. Multi-Row INSERT for `automation_state`

  **What to do**:
  - Create `ControlActionRepository.log_automation_state_batch(records: list)` method with single multi-row INSERT
  - Change `_log_automation_state()` in `control_engine.py` to collect all device records, then call batch method once
  - This is SAME-TICK: batch accumulated during tick, written within `asyncio.gather()` (from Task 4)
  - **NOT a 10-second buffer.** No background flush loop.

  **CRITICAL WARNING — DO NOT COPY from setpoints.py**:
  The `setpoints.py` file contains a `_batch_buffer` + `flush_batch_buffer()` pattern (10-second flush). **DO NOT copy this pattern.** Use only the multi-row INSERT syntax shown below:

  ```python
  # CORRECT: same-tick multi-row INSERT (no buffer, no flush loop)
  async def log_automation_state_batch(self, records: list[dict]) -> bool:
      if not records:
          return True
      values_clause = ", ".join([f"(${i*17+1}), (${i*17+2}), ..." for i in range(len(records))])
      # Instead, use asyncpg's execute with inline VALUES:
      rows = [(r["location"], r["cluster"], r["device_name"], ...) for r in records]
      query = f"""
          INSERT INTO automation_state
          (timestamp, location, cluster, device_name, device_state, device_mode, ...)
          VALUES %s
      """
      # Use conn.copy_records_to_table() for bulk insert performance
      await conn.copy_records_to_table('automation_state', records=rows, columns=(...))
      return True
  ```
  See `control_actions.py:100-173` for the current per-row INSERT to replace.

  **Must NOT do**:
  - Do NOT create a `_batch_buffer` list in the repository
  - Do NOT create a `flush_batch_buffer()` method that runs on a timer
  - Do NOT call this method from `_batch_flush_loop`
  - Do NOT lose any data
  - Do NOT change table schema

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 2, 3, 4)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/repositories/control_actions.py:100-173` — Current per-row INSERT
  - `Infrastructure/automation-service/app/repositories/setpoints.py:179-263` — Reference for multi-row INSERT pattern (use INSERT pattern ONLY, NOT buffer/flush)

  **Acceptance Criteria**:

  - [ ] `ControlActionRepository` has `log_automation_state_batch(records)` method
  - [ ] `_log_automation_state()` collects records and calls batch method once per tick
  - [ ] Single INSERT with all configured device rows (N rows, where N = number of configured devices): `INSERT INTO automation_state (...) VALUES (...), (...), ...`
  - [ ] **No 10-second flush loop** — written within the tick
  - [ ] All device rows still appear in `automation_state`
  - [ ] Performance: single INSERT completes in <30ms

  **Commit**: YES
  - Message: `perf(db): same-tick multi-row INSERT for automation_state`
  - Files: `Infrastructure/automation-service/app/repositories/control_actions.py`, `Infrastructure/automation-service/app/control/control_engine.py`

---

- [x] 6. Documentation: No-Batching Rule for Real-Time Tracking Writes

  **What to do**:
  - Add "Data Management Rules" section to `AGENTS.md` documenting: **No batching, buffering, or deferring of DB writes that affect real-time data tracking**
  - List real-time tracking tables: `automation_state`, `control_history`, `device_states`
  - Document `effective_setpoints` 10s buffer as EXCEPTION (historical trending, Redis = real-time)
  - Document three table purposes (time-series, event log, current state)
  - Update "NON-NEGOTIABLE SYSTEM RULES" section

  **Must NOT do**:
  - Do NOT change any code
  - Do NOT remove existing documentation

  **Recommended Agent Profile**:
  - **Category**: `writing`

  **Parallelization**:
  - **Can Run In Parallel**: YES (any time)
  - **Blocked By**: None

  **Commit**: YES
  - Message: `docs: add no-batching rule for real-time tracking DB writes`
  - Files: `AGENTS.md`

---

- [x] 7. Reduce Tick Interval to 1s

  **What to do**:
  - Change `update_interval` from 3 to 1 in `automation_config.yaml`
  - Update config validation docs (1s is recommended default)
  - Change `_light_effective_log_interval_sec` from 60 to 10

  **Must NOT do**:
  - Do NOT change 1-5s validation bounds
  - Do NOT remove 3s option
  - Do NOT change any control logic

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 6, 8)
  - **Blocked By**: Tasks 2, 3, 4, 5

  **References**:
  - `Infrastructure/automation-service/automation_config.yaml:147` — `update_interval: 3`
  - `Infrastructure/automation-service/app/control/control_engine.py:137-139` — `_light_effective_log_interval_sec = 60`

  **Acceptance Criteria**:

  - [ ] `automation_config.yaml` has `update_interval: 1`
  - [ ] `_light_effective_log_interval_sec` reduced to 10
  - [ ] Total cycle time ~1.0-1.5s with 1s interval

  **Commit**: YES
  - Message: `perf(control): reduce tick interval to 1s and Grafana throttle to 10s`
  - Files: `Infrastructure/automation-service/automation_config.yaml`, `Infrastructure/automation-service/app/control/control_engine.py`

---

- [x] 8. Cache Per-Tick DB Queries (PID, Climate, Mode)

  **What to do**:
  - Add StateManager caching for:
    1. PID parameters (60s TTL): key `cache:pid:{device_type}`, invalidate on `PID_PARAMS_CHANGED` event
    2. Room mode (30s TTL): key `cache:mode:{location}:{cluster}`, invalidate on `MODE_CHANGED` event
    3. Light schedule (30s TTL): key `cache:light_schedule:{location}:{cluster}`, invalidate on `SCHEDULE_CHANGED` event
    4. Climate period (30s TTL): key `cache:climate_period:{location}:{cluster}:{time_str}`, invalidate on `CLIMATE_PERIOD_CHANGED` or `SCHEDULE_CHANGED` event
  - Invalidate caches via `_config_event_consumer_loop` when relevant events fire

  **Must NOT do**:
  - Do NOT cache sensor values (must be real-time from Redis)
  - Do NOT cache effective setpoints (computed per-tick from ramps)
  - Do NOT exceed 60s TTL

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 6, 7)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/control/climate_resolver.py:27-107` — Uncached DB queries
  - `Infrastructure/automation-service/app/control/pid_controller_manager.py:240,455` — PID lookups
  - `Infrastructure/automation-service/app/state/__init__.py` — StateManager

  **Acceptance Criteria**:

  - [ ] Climate resolver uses StateManager cache (30s TTL)
  - [ ] PID manager uses StateManager cache (60s TTL)
  - [ ] Cache invalidation on config events
  - [ ] Climate resolution time <5ms (was 30-90ms)

  **Commit**: YES
  - Message: `perf(control): cache PID, climate, and mode queries with StateManager TTL`
  - Files: `Infrastructure/automation-service/app/control/climate_resolver.py`, `Infrastructure/automation-service/app/control/pid_controller_manager.py`, `Infrastructure/automation-service/app/control/control_engine.py`, `Infrastructure/automation-service/app/background_tasks.py`

---

- [x] 9. End-to-End Verification and Performance Validation

  **What to do**:
  - Run `scripts/validate_loop_performance.py` and verify all thresholds
  - Benchmark control loop with all optimizations (update_interval=1)
  - Verify light target change latency (<2s)
  - Verify no data loss in PostgreSQL
  - Monitor CPU usage on Raspberry Pi
  - Verify all existing tests pass

  **Status**: ✅ DEPLOYED AND VERIFIED (2026-05-29)

  **Verification Results** (post-deploy):
  - automation_state: 13 entries per tick at **1s interval** (was 3s before deploy) ✅
  - control_history: active and recording state changes every ~1s ✅
  - hardware_batch: **~165ms total** per batch execution (parallel relay‖dimmer) ✅
  - Service: running stable, no errors in logs ✅
  - Tick rate: 1s confirmed via DB timestamps (15 consecutive ticks at 1s spacing) ✅

---

## Commit Strategy

| After Task | Message | Files | Verification |
|-----------|---------|-------|--------------|
| 1 | `perf(control): fixed-rate loop with deadline enforcement and overrun handling` | `background_tasks.py` | pytest |
| 2 | `perf(hardware): parallel I2C bus execution with reduced timeout` | `hardware_batch.py` | validate_loop_performance |
| 3 | `perf(background): deadline-based scheduling for all background tasks` | `background_tasks.py` | Verify task intervals |
| 4 | `perf(control): same-tick parallel DB writes via asyncio.gather` | `control_engine.py`, `device_processor.py`, `device_controller.py` | pytest + data completeness |
| 5 | `perf(db): same-tick multi-row INSERT for automation_state` | `control_actions.py`, `control_engine.py` | Verify data + real-time timestamps |
| 6 | `docs: add no-batching rule for real-time tracking DB writes` | `AGENTS.md` | Verify documentation |
| 7 | `perf(control): reduce tick interval to 1s and Grafana throttle to 10s` | `automation_config.yaml`, `control_engine.py` | Verify loop timing |
| 8 | `perf(control): cache PID, climate, and mode queries with StateManager TTL` | `climate_resolver.py`, `pid_controller_manager.py`, `control_engine.py`, `background_tasks.py` | Verify cache hit rates |
| 9 | `test(control): validate end-to-end performance with 1s loop interval` | `validate_loop_performance.py` | Full benchmark |

---

## Success Criteria

### Verification Commands
```bash
# Loop timing
curl -s http://localhost:8001/api/timing/breakdown | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'avg={d[\"total_loop_time\"][\"avg\"]:.0f}ms p95={d[\"total_loop_time\"][\"p95\"]:.0f}ms')"

# Real-time data (should show ~1s intervals, NOT 10s batches)
psql -U cea -d projectcea -c "SELECT timestamp FROM automation_state WHERE timestamp > NOW() - INTERVAL '15 seconds' ORDER BY timestamp"

# Historical trending (10s buffer is OK)
psql -U cea -d projectcea -c "SELECT COUNT(*) FROM effective_setpoints WHERE timestamp > NOW() - INTERVAL '5 minutes'"

# CPU usage
curl -s http://localhost:8001/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'CPU: {d[\"system\"][\"cpu_percent\"]}%')"
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] No real-time tracking DB writes are deferred/buffered beyond tick boundary
- [ ] `effective_setpoints` 10s buffer remains intact (historical trending)
- [ ] Documentation includes "No batching of real-time tracking DB writes" rule