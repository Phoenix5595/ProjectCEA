# 1-Second Control Loop Optimization

## TL;DR

> **Quick Summary**: Optimize the automation-service control loop to guarantee <1 second execution time while scaling to 5 clusters and 10 soil probes. Current worst case (~570ms) is already under 1s, but we need safety margins and determinism guarantees.
> 
> **Deliverables**:
> - Control loop consistently <500ms with 1s hard ceiling
> - Feature-flagged optimizations with individual rollback capability
> - Performance monitoring and alerting for >900ms loops
> - Support for 5 clusters (Flower×2, Veg×2, Lab×1) + 10 soil probes
> 
> **Estimated Effort**: Medium (3-5 days)
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Task 1 → Task 3 → Task 5 → Task 8

---

## Context

### Original Request
Optimize software and architecture to run all control loops at max 1 second. User willing to drop local Grafana if beneficial. Adding 3 new sensor clusters and 5 soil probes.

### Interview Summary
**Key Discussions**:
- **Current state**: 3s tick (configurable 1-5s), worst case ~570ms execution
- **Primary bottleneck**: DFR0971 I2C dimming operations (50-290ms per op)
- **Scaling plan**: +3 clusters (Flower secondary, Veg secondary, Lab new), +5 soil probes
- **Dimmers**: Already at peak config, no new dimmers being added
- **Soil probes**: Zero control loop impact (separate service, async Redis writes)
- **Grafana**: Low impact on control loop (control uses Redis, not PostgreSQL)
- **Test approach**: TDD for all changes

**Research Findings**:
- Control loop best case: ~40ms, worst case: ~570ms
- DFR0971 bottleneck: 50-290ms per operation with conservative retry delays
- MCP23017 relays: 5-20ms per operation (different I2C bus, parallelizable)
- Redis operations: <1ms with 30s sensor cache
- Sensor batching: MGET already implemented via `get_sensor_values_batch()`
- Iskra already handles analytics offloading (DB replica + Redis + Grafana)

### Metis Review
**Identified Gaps** (addressed in plan):
- Feature flags needed for all optimizations (enables individual rollback)
- Timing tests required as acceptance criteria
- Load testing with full sensor complement
- Edge case handling: I2C failures, sensor timeouts, startup rush
- Monitoring alerts for loops approaching 1s threshold

---

## Work Objectives

### Core Objective
Guarantee control loop execution <1 second under all conditions, with target <500ms for safety margin.

### Concrete Deliverables
- `automation_config.yaml`: `update_interval: 1`
- `app/control/dfr0971.py`: Optimized retry timing with feature flag
- `app/control/hardware_manager.py`: Parallel I2C operations
- `app/control/control_engine.py`: Loop timing instrumentation
- Performance monitoring endpoint enhancements
- Alert configuration for >900ms loops
- Load tests covering 5 clusters + 10 soil probes

### Definition of Done
- [ ] `bun test` / `pytest` passes for all changes
- [ ] Control loop <500ms in load tests with 5 clusters
- [ ] Control loop <1s under stress conditions (simulated I2C failures)
- [ ] Feature flags allow individual optimization rollback
- [ ] Monitoring dashboard shows loop timing histogram
- [ ] Alerts fire when loop exceeds 900ms

### Must Have
- 1-second maximum control loop guarantee
- Feature flags for each optimization tier
- Automated timing tests
- Rollback capability without full redeploy

### Must NOT Have (Guardrails)
- **No hardware changes** - software/config only
- **No removal of safety interlocks** - all existing safety checks preserved
- **No changes to soil-sensor-service** - it's independent and working
- **No ESP32 firmware changes** - CAN ingestion is fine
- **No database schema changes** - Redis/PostgreSQL structure unchanged
- **No reduction of sensor sampling rate** - 1Hz minimum preserved
- **No hardcoded timing values** - all configurable via YAML or feature flags

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> ALL verification is agent-executable via tools.

### Test Decision
- **Infrastructure exists**: YES (pytest in automation-service)
- **Automated tests**: TDD (write tests first)
- **Framework**: pytest with pytest-asyncio

### Agent-Executed QA Scenarios (MANDATORY)

All tasks include Playwright/Bash/curl verification scenarios. Each scenario specifies exact commands, expected outputs, and evidence capture.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 1: Performance baseline & instrumentation (no deps)
├── Task 2: Feature flag infrastructure (no deps)
└── Task 6: Grafana assessment & optional removal (no deps)

Wave 2 (After Wave 1):
├── Task 3: DFR0971 retry optimization (depends: 1, 2)
├── Task 4: Parallel I2C operations (depends: 1, 2)
└── Task 5: Redis batching optimization (depends: 1, 2)

Wave 3 (After Wave 2):
├── Task 7: Load testing with 5 clusters (depends: 3, 4, 5)
└── Task 8: Monitoring & alerting (depends: 7)

Critical Path: Task 1 → Task 3 → Task 7 → Task 8
Parallel Speedup: ~50% faster than sequential
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 3, 4, 5, 7 | 2, 6 |
| 2 | None | 3, 4, 5 | 1, 6 |
| 3 | 1, 2 | 7 | 4, 5 |
| 4 | 1, 2 | 7 | 3, 5 |
| 5 | 1, 2 | 7 | 3, 4 |
| 6 | None | None | 1, 2 |
| 7 | 3, 4, 5 | 8 | None |
| 8 | 7 | None | None |

---

## TODOs

### Wave 1: Foundation

- [x] 1. Performance Baseline & Timing Instrumentation

  **What to do**:
  - Add detailed timing instrumentation to control loop
  - Measure each phase: sensor read, PID calc, hardware ops, state updates
  - Create `/api/timing` endpoint for detailed breakdown
  - Establish baseline metrics before any optimizations
  - Write tests that assert timing thresholds

  **Must NOT do**:
  - Change any control logic yet
  - Modify hardware communication patterns

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused instrumentation task, single service
  - **Skills**: []
    - No special skills needed for Python instrumentation

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 6)
  - **Blocks**: Tasks 3, 4, 5, 7
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/control/background_tasks.py:run_control_loop()` - Main loop to instrument
  - `Infrastructure/automation-service/app/control/control_engine.py:process_cluster()` - Per-cluster processing
  - `Infrastructure/automation-service/app/routes/status.py` - Existing /api/status endpoint pattern
  - `Infrastructure/automation-service/app/control/dfr0971.py` - DFR0971 timing to measure

  **Acceptance Criteria**:

  **TDD (tests first):**
  - [x] Test file: `tests/test_timing_instrumentation.py`
  - [x] Test: timing data structure has all required fields
  - [x] Test: timing endpoint returns valid JSON
  - [x] `pytest tests/test_timing_instrumentation.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Timing endpoint returns detailed breakdown
    Tool: Bash (curl)
    Preconditions: automation-service running on localhost:8001
    Steps:
      1. curl -s http://localhost:8001/api/timing | jq .
      2. Assert: response.last_loop_ms exists and is number
      3. Assert: response.phases.sensor_read_ms exists
      4. Assert: response.phases.pid_calc_ms exists
      5. Assert: response.phases.hardware_ops_ms exists
      6. Assert: response.avg_loop_ms exists
    Expected Result: Detailed timing breakdown with all phases
    Evidence: Response body saved to .sisyphus/evidence/task-1-timing.json

  Scenario: Timing histogram available
    Tool: Bash (curl)
    Preconditions: Service running, at least 10 control loops completed
    Steps:
      1. curl -s http://localhost:8001/api/timing/histogram | jq .
      2. Assert: response.buckets is array
      3. Assert: response.p50_ms, response.p95_ms, response.p99_ms exist
    Expected Result: Histogram data for loop timing distribution
    Evidence: Response saved
  ```

  **Commit**: YES
  - Message: `feat(automation): add control loop timing instrumentation`
  - Files: `app/control/timing.py`, `app/routes/timing.py`, `tests/test_timing_instrumentation.py`
  - Pre-commit: `pytest tests/test_timing_instrumentation.py`

---

- [x] 2. Feature Flag Infrastructure

  **What to do**:
  - Add feature flag system to automation_config.yaml
  - Create `optimizations` section with boolean flags
  - Implement flag checking in control loop
  - Allow runtime flag updates via API
  - Write tests for flag behavior

  **Must NOT do**:
  - Implement any actual optimizations yet
  - Change default behavior (all flags default OFF)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple config + flag checking implementation
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 6)
  - **Blocks**: Tasks 3, 4, 5
  - **Blocked By**: None

  **References**:
  - `automation_config.yaml` - Add optimizations section
  - `Infrastructure/automation-service/app/config.py` - Config loading pattern
  - `Infrastructure/automation-service/app/models/config.py` - Pydantic models

  **Acceptance Criteria**:

  **TDD:**
  - [x] Test file: `tests/test_feature_flags.py`
  - [x] Test: flags load from config with defaults
  - [x] Test: flags can be toggled via API
  - [x] Test: flag state persists across requests
  - [x] `pytest tests/test_feature_flags.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Feature flags load from config
    Tool: Bash (curl)
    Preconditions: automation-service running
    Steps:
      1. curl -s http://localhost:8001/api/config/optimizations | jq .
      2. Assert: response.dfr0971_fast_retry exists (boolean)
      3. Assert: response.parallel_i2c exists (boolean)
      4. Assert: response.redis_batch_sensors exists (boolean)
    Expected Result: All optimization flags visible with current state
    Evidence: .sisyphus/evidence/task-2-flags.json

  Scenario: Feature flag can be toggled
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -X POST http://localhost:8001/api/config/optimizations \
           -H "Content-Type: application/json" \
           -d '{"dfr0971_fast_retry": true}'
      2. Assert: HTTP status 200
      3. curl -s http://localhost:8001/api/config/optimizations | jq .dfr0971_fast_retry
      4. Assert: value is true
    Expected Result: Flag toggled successfully
    Evidence: Response captured
  ```

  **Commit**: YES
  - Message: `feat(automation): add feature flag infrastructure for optimizations`
  - Files: `automation_config.yaml`, `app/config.py`, `app/routes/config.py`, `tests/test_feature_flags.py`
  - Pre-commit: `pytest tests/test_feature_flags.py`

---

- [x] 6. Grafana Assessment & Optional Removal

  **What to do**:
  - Measure mothernode Grafana resource usage (CPU, memory)
  - Verify Iskra Grafana has all required dashboards
  - Document the resource savings from removal
  - If savings significant (>10% CPU or >500MB RAM): disable local Grafana
  - Update documentation if Grafana removed

  **Must NOT do**:
  - Remove Grafana if Iskra dashboards are incomplete
  - Delete any dashboard JSON files (keep for reference)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Assessment and optional service disable
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/grafana/` - Local Grafana config
  - Iskra machine Grafana dashboards
  - `systemctl status grafana-server` - Service status

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Measure Grafana resource usage
    Tool: Bash
    Preconditions: Grafana running on mothernode
    Steps:
      1. ps aux | grep grafana | grep -v grep | awk '{print $3, $4, $6}'
      2. Record: CPU%, MEM%, RSS KB
      3. systemctl status grafana-server | grep Memory
    Expected Result: Resource usage documented
    Evidence: .sisyphus/evidence/task-6-grafana-resources.txt

  Scenario: Verify Iskra dashboards complete
    Tool: Bash (curl)
    Preconditions: Iskra Grafana accessible
    Steps:
      1. curl -s http://iskra:3000/api/dashboards | jq length
      2. Assert: dashboard count >= mothernode dashboard count
      3. Compare dashboard UIDs between mothernode and Iskra
    Expected Result: Iskra has all dashboards
    Evidence: Dashboard comparison saved

  Scenario: Grafana disabled (if applicable)
    Tool: Bash
    Preconditions: Decision made to disable
    Steps:
      1. sudo systemctl stop grafana-server
      2. sudo systemctl disable grafana-server
      3. systemctl is-enabled grafana-server
      4. Assert: output is "disabled"
    Expected Result: Grafana no longer starts on boot
    Evidence: Service status captured
  ```

  **Commit**: YES (if changes made)
  - Message: `chore(infra): disable local Grafana, use Iskra for analytics`
  - Files: Documentation updates
  - Pre-commit: N/A

---

### Wave 2: Optimizations

- [ ] 3. DFR0971 Retry Optimization

  **What to do**:
  - Analyze current retry delays in dfr0971.py
  - Reduce conservative delays while maintaining reliability
  - Make retry timing configurable via feature flag
  - Implement exponential backoff instead of fixed delays
  - Target: reduce 50-290ms → 30-100ms per operation

  **Must NOT do**:
  - Remove retry logic entirely (hardware needs retries)
  - Change I2C bus configuration
  - Affect MCP23017 relay operations

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Careful hardware timing optimization
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `Infrastructure/automation-service/app/control/dfr0971.py` - DFR0971 driver with retry logic
  - `Infrastructure/automation-service/app/control/hardware_manager.py` - Hardware abstraction
  - I2C timing requirements from DFR0971 datasheet

  **Acceptance Criteria**:

  **TDD:**
  - [ ] Test file: `tests/test_dfr0971_timing.py`
  - [ ] Test: fast retry mode reduces operation time
  - [ ] Test: reliability maintained (mock I2C failures)
  - [ ] Test: feature flag controls behavior
  - [ ] `pytest tests/test_dfr0971_timing.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: DFR0971 operation time reduced with flag ON
    Tool: Bash (curl)
    Preconditions: Feature flag dfr0971_fast_retry = false, service running
    Steps:
      1. curl -s http://localhost:8001/api/timing | jq .phases.hardware_ops_ms
      2. Record baseline_ms
      3. curl -X POST http://localhost:8001/api/config/optimizations \
           -d '{"dfr0971_fast_retry": true}'
      4. Wait 5 seconds for next control loop
      5. curl -s http://localhost:8001/api/timing | jq .phases.hardware_ops_ms
      6. Assert: new_ms < baseline_ms * 0.7 (at least 30% improvement)
    Expected Result: Hardware ops time reduced
    Evidence: Before/after timing in .sisyphus/evidence/task-3-dfr-timing.json

  Scenario: DFR0971 handles transient I2C failures
    Tool: Bash
    Preconditions: Fast retry enabled
    Steps:
      1. Trigger 10 consecutive dimming operations via API
      2. Check logs for retry counts
      3. Assert: all operations succeeded
      4. Assert: no operation exceeded 150ms
    Expected Result: Retries work, timing bounded
    Evidence: Log excerpt captured
  ```

  **Commit**: YES
  - Message: `perf(automation): optimize DFR0971 retry timing with feature flag`
  - Files: `app/control/dfr0971.py`, `tests/test_dfr0971_timing.py`
  - Pre-commit: `pytest tests/test_dfr0971_timing.py`

---

- [ ] 4. Parallel I2C Operations

  **What to do**:
  - MCP23017 (bus 0) and DFR0971 (bus 1) are on different buses
  - Implement asyncio.gather() for parallel hardware operations
  - Gate behind `parallel_i2c` feature flag
  - Measure timing improvement

  **Must NOT do**:
  - Parallelize operations on same I2C bus (would cause conflicts)
  - Change operation order within same bus
  - Remove any safety interlocks

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Asyncio refactoring with hardware safety concerns
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 5)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `Infrastructure/automation-service/app/control/hardware_manager.py` - Hardware operations
  - `Infrastructure/automation-service/app/control/control_engine.py` - Control orchestration
  - `automation_config.yaml:hardware.mcp_i2c_bus` (0) and `dfr0971_i2c_bus` (1)

  **Acceptance Criteria**:

  **TDD:**
  - [ ] Test file: `tests/test_parallel_i2c.py`
  - [ ] Test: parallel mode executes relay+dimmer concurrently
  - [ ] Test: sequential mode preserved when flag OFF
  - [ ] Test: bus 0 and bus 1 operations don't interfere
  - [ ] `pytest tests/test_parallel_i2c.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Parallel I2C reduces total hardware time
    Tool: Bash (curl)
    Preconditions: parallel_i2c = false
    Steps:
      1. curl -s http://localhost:8001/api/timing | jq .phases.hardware_ops_ms
      2. Record baseline (relay_ms + dimmer_ms sequential)
      3. Enable parallel_i2c flag
      4. Wait 5 seconds
      5. curl -s http://localhost:8001/api/timing | jq .phases.hardware_ops_ms
      6. Assert: new_ms < baseline_ms * 0.6 (overlapping saves ~40%)
    Expected Result: Parallel execution reduces total time
    Evidence: .sisyphus/evidence/task-4-parallel-timing.json

  Scenario: No I2C bus conflicts
    Tool: Bash
    Preconditions: Parallel mode enabled, real hardware
    Steps:
      1. Run 100 control loops with parallel I2C
      2. grep -c "I2C error\|bus conflict" logs
      3. Assert: count = 0
    Expected Result: Zero I2C conflicts
    Evidence: Log analysis saved
  ```

  **Commit**: YES
  - Message: `perf(automation): parallelize I2C operations across buses`
  - Files: `app/control/hardware_manager.py`, `tests/test_parallel_i2c.py`
  - Pre-commit: `pytest tests/test_parallel_i2c.py`

---

- [ ] 5. Redis Sensor Batching Optimization

  **What to do**:
  - Audit current sensor read patterns in control loop
  - Ensure MGET is used for all batch sensor reads
  - Extend cache TTL for stable data (setpoints: 30s → 300s)
  - Gate behind `redis_batch_sensors` feature flag
  - Reduce Redis round-trips per control tick

  **Must NOT do**:
  - Change sensor data freshness guarantees (1Hz still available)
  - Modify Redis schema or key patterns
  - Affect other services' Redis access

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Redis optimization, well-scoped
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 4)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `Infrastructure/automation-service/app/repositories/sensors.py:get_sensor_values_batch()` - Existing MGET
  - `Infrastructure/automation-service/app/control/sensor_data_manager.py` - Cache implementation
  - `Infrastructure/automation-service/app/repositories/setpoints.py` - Setpoint caching

  **Acceptance Criteria**:

  **TDD:**
  - [ ] Test file: `tests/test_redis_batching.py`
  - [ ] Test: batch mode uses single MGET for all sensors
  - [ ] Test: setpoint cache respects extended TTL
  - [ ] Test: cache hit rate > 90% in steady state
  - [ ] `pytest tests/test_redis_batching.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Redis round-trips minimized
    Tool: Bash
    Preconditions: redis_batch_sensors = false
    Steps:
      1. redis-cli MONITOR for 5 seconds, count GET/MGET commands
      2. Record baseline_commands
      3. Enable redis_batch_sensors flag
      4. redis-cli MONITOR for 5 seconds, count commands
      5. Assert: new_commands < baseline_commands * 0.5
    Expected Result: Fewer Redis round-trips
    Evidence: Command counts in .sisyphus/evidence/task-5-redis.txt

  Scenario: Sensor freshness maintained
    Tool: Bash (curl)
    Preconditions: Batch mode enabled
    Steps:
      1. curl -s http://localhost:8001/api/sensors/Flower%20Room/main | jq .
      2. Assert: all sensor timestamps < 5 seconds old
      3. Repeat 3 times over 10 seconds
      4. Assert: timestamps update each time
    Expected Result: Sensors still fresh despite caching
    Evidence: Timestamp samples saved
  ```

  **Commit**: YES
  - Message: `perf(automation): optimize Redis sensor batching and cache TTL`
  - Files: `app/control/sensor_data_manager.py`, `app/repositories/sensors.py`, `tests/test_redis_batching.py`
  - Pre-commit: `pytest tests/test_redis_batching.py`

---

### Wave 3: Validation & Monitoring

- [ ] 7. Load Testing with 5 Clusters

  **What to do**:
  - Create load test simulating 5 clusters + 10 soil probes
  - Test with all optimization flags ON and OFF
  - Measure P50, P95, P99 loop times
  - Test edge cases: I2C failures, sensor timeouts, Redis latency
  - Verify <500ms P95, <1000ms P99

  **Must NOT do**:
  - Run destructive tests on production
  - Modify production config during tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Comprehensive load testing, multiple scenarios
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (Wave 3)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 3, 4, 5

  **References**:
  - `automation_config.yaml` - Zone/cluster configuration
  - `Infrastructure/automation-service/tests/` - Existing test patterns
  - Timing endpoints created in Task 1

  **Acceptance Criteria**:

  **TDD:**
  - [ ] Test file: `tests/test_load_performance.py`
  - [ ] Test: 5 clusters complete in <500ms P95
  - [ ] Test: simulated I2C failures don't exceed 1s
  - [ ] Test: startup rush (all sensors stale) <1s
  - [ ] `pytest tests/test_load_performance.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Full load performance test
    Tool: Bash
    Preconditions: All optimizations enabled, test config with 5 clusters
    Steps:
      1. Run 1000 control loops with timing capture
      2. Calculate P50, P95, P99 from timing data
      3. Assert: P50 < 300ms
      4. Assert: P95 < 500ms
      5. Assert: P99 < 800ms
      6. Assert: MAX < 1000ms
    Expected Result: All percentiles within bounds
    Evidence: .sisyphus/evidence/task-7-load-results.json

  Scenario: Stress test with simulated failures
    Tool: Bash
    Preconditions: Fault injection enabled in test mode
    Steps:
      1. Inject 10% I2C failure rate
      2. Run 100 control loops
      3. Assert: all loops complete
      4. Assert: no loop exceeds 1000ms
      5. Assert: average retry overhead < 50ms
    Expected Result: Graceful degradation under failures
    Evidence: Failure handling metrics saved

  Scenario: Startup rush performance
    Tool: Bash
    Preconditions: All sensor caches cleared
    Steps:
      1. Restart automation-service
      2. Capture first 10 loop timings
      3. Assert: first loop < 1000ms (cold cache)
      4. Assert: loops 5-10 < 500ms (warm cache)
    Expected Result: Startup doesn't exceed 1s
    Evidence: Startup timing captured
  ```

  **Commit**: YES
  - Message: `test(automation): add load tests for 5-cluster 1-second loop`
  - Files: `tests/test_load_performance.py`, test fixtures
  - Pre-commit: `pytest tests/test_load_performance.py`

---

- [ ] 8. Monitoring & Alerting

  **What to do**:
  - Add Prometheus metrics for loop timing histogram
  - Configure alert for loops > 900ms
  - Create Grafana dashboard panel (on Iskra) for loop timing
  - Add log warnings for loops > 800ms
  - Document monitoring and troubleshooting

  **Must NOT do**:
  - Add monitoring overhead that impacts loop timing
  - Create alerts that spam (use proper thresholds)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Metrics and alerting, standard patterns
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (final task)
  - **Blocks**: None
  - **Blocked By**: Task 7

  **References**:
  - `Infrastructure/automation-service/app/routes/metrics.py` - Existing Prometheus metrics
  - Iskra Grafana dashboards
  - Alertmanager configuration

  **Acceptance Criteria**:

  **TDD:**
  - [ ] Test file: `tests/test_monitoring.py`
  - [ ] Test: timing histogram metric exported
  - [ ] Test: warning logged for slow loops
  - [ ] `pytest tests/test_monitoring.py` → PASS

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Prometheus metrics exported
    Tool: Bash (curl)
    Preconditions: automation-service running
    Steps:
      1. curl -s http://localhost:8001/metrics | grep control_loop
      2. Assert: control_loop_duration_seconds_bucket exists
      3. Assert: control_loop_duration_seconds_sum exists
      4. Assert: control_loop_duration_seconds_count exists
    Expected Result: Histogram metrics available
    Evidence: .sisyphus/evidence/task-8-metrics.txt

  Scenario: Slow loop warning logged
    Tool: Bash
    Preconditions: Ability to simulate slow loop
    Steps:
      1. Inject artificial 850ms delay in test mode
      2. Trigger control loop
      3. grep "slow loop\|loop exceeded" logs
      4. Assert: warning present with timing value
    Expected Result: Warning logged for slow loops
    Evidence: Log excerpt captured

  Scenario: Grafana dashboard shows timing
    Tool: Bash (curl)
    Preconditions: Iskra Grafana accessible
    Steps:
      1. curl -s http://iskra:3000/api/dashboards/uid/control-timing | jq .
      2. Assert: dashboard exists
      3. Assert: panels include loop timing histogram
    Expected Result: Dashboard available on Iskra
    Evidence: Dashboard JSON excerpt saved
  ```

  **Commit**: YES
  - Message: `feat(automation): add control loop monitoring and alerting`
  - Files: `app/routes/metrics.py`, `tests/test_monitoring.py`
  - Pre-commit: `pytest tests/test_monitoring.py`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(automation): add control loop timing instrumentation` | timing.py, routes/timing.py | pytest |
| 2 | `feat(automation): add feature flag infrastructure` | config.py, routes/config.py | pytest |
| 3 | `perf(automation): optimize DFR0971 retry timing` | dfr0971.py | pytest |
| 4 | `perf(automation): parallelize I2C operations` | hardware_manager.py | pytest |
| 5 | `perf(automation): optimize Redis sensor batching` | sensor_data_manager.py | pytest |
| 6 | `chore(infra): disable local Grafana` (if applicable) | docs | N/A |
| 7 | `test(automation): add load tests for 1-second loop` | test_load_performance.py | pytest |
| 8 | `feat(automation): add control loop monitoring` | metrics.py | pytest |

---

## Success Criteria

### Verification Commands
```bash
# Run all tests
cd Infrastructure/automation-service && pytest

# Check timing endpoint
curl http://localhost:8001/api/timing | jq '.last_loop_ms < 500'

# Load test
pytest tests/test_load_performance.py -v

# Check metrics
curl http://localhost:8001/metrics | grep control_loop_duration
```

### Final Checklist
- [ ] All tests pass (`pytest` green)
- [ ] P95 loop time < 500ms with 5 clusters
- [ ] P99 loop time < 1000ms under stress
- [ ] Feature flags allow individual rollback
- [ ] Monitoring alerts configured for > 900ms
- [ ] Documentation updated
- [ ] Grafana dashboard on Iskra shows timing
