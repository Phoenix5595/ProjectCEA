# Fix Soil Sensor Service Blocked Event Loop

## TL;DR

> **Quick Summary**: Fix blocking Modbus RTU I/O in async context that prevents HTTP health endpoint from responding when no sensors are connected.
>
> **Deliverables**:
> - `background_tasks.py`: Wrap blocking serial calls with `asyncio.to_thread()`
> - Verify `/health` endpoint responds within 5s with no sensors
>
> **Estimated Effort**: Small
> **Parallel Execution**: NO - sequential
> **Critical Path**: Wrap scan call → Wrap poll call → Test

---

## Context

### Original Request
User reports soil sensor service always shows as "timed out" in mothernode status section of frontend, even with no sensors plugged in.

### Interview Summary
**Key Discussions**:
- Root cause: Synchronous Modbus RTU I/O in async background tasks blocks the event loop
- The `/health` endpoint is static and should always respond regardless of sensor state
- With no sensors, `_scan_bus_for_sensors()` blocks for ~127 seconds (254 IDs × 0.5s timeout)
- The event loop cannot process HTTP requests during this blocking

**Research Findings**:
- `ss -tlnp` confirms port 8002 IS listening
- `curl` connects but times out after 5 seconds
- Logs show "Incomplete response header" errors at ~0.5s intervals
- Health check timeout is 2 seconds in automation-service

### Metis Review
**Identified Gaps** (addressed):
- Scope creep: Only wrap blocking calls, do NOT change health endpoint behavior or scan algorithm
- Missing acceptance criteria: Added explicit test commands for both health and sensor reading
- Edge case: Concurrent requests during scan

---

## Work Objectives

### Core Objective
Fix event loop blocking so `/health` endpoint responds within 5s even when no sensors are connected.

### Concrete Deliverables
- `Infrastructure/soil-sensor-service/app/background_tasks.py`: Wrap blocking Modbus calls in `asyncio.to_thread()`
- Verify health endpoint responds correctly
- Verify sensor reading still works (regression test)

### Definition of Done
- [ ] `curl --max-time 5 http://127.0.0.1:8002/health` returns within 5s
- [ ] `curl --max-time 5 http://127.0.0.1:8002/` returns within 5s
- [ ] Service logs show no "Incomplete response header" (or significantly reduced)
- [ ] Sensor reading still works when sensors are connected

### Must Have
- `/health` endpoint must respond within 5 seconds regardless of sensor state
- Sensor reading functionality must not be broken

### Must NOT Have (Guardrails)
- DO NOT modify `/health` response format or add sensor status to it
- DO NOT change scan frequency, timeout values, or retry logic
- DO NOT modify `soil_sensor_reader.py` internal logic
- DO NOT change Modbus protocol implementation

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Agent-Executed QA Scenarios

**Scenario: Health endpoint responds with no sensors**
  Tool: Bash (curl)
  Preconditions: Soil sensor service running, no sensors connected
  Steps:
    1. `curl -s -w "\nHTTP_CODE: %{http_code}\nTIME: %{time_total}s\n" --max-time 5 http://127.0.0.1:8002/health`
    2. Assert: HTTP status is 200
    3. Assert: response contains "healthy" or "ok"
    4. Assert: TIME is less than 5.0 seconds
  Expected Result: Health endpoint responds within 5 seconds
  Evidence: curl output saved

**Scenario: Concurrent health requests don't queue**
  Tool: Bash (parallel curl)
  Preconditions: Soil sensor service running
  Steps:
    1. `curl -s -w "REQ1_TIME: %{time_total}s\n" --max-time 15 http://127.0.0.1:8002/health -o /dev/null &`
    2. `curl -s -w "REQ2_TIME: %{time_total}s\n" --max-time 15 http://127.0.0.1:8002/health -o /dev/null &`
    3. Wait for both jobs
    4. Assert: Both times are under 10 seconds (not sequential ~10+ seconds)
  Expected Result: Both requests complete concurrently
  Evidence: curl output saved

**Scenario: Root endpoint also responds**
  Tool: Bash (curl)
  Preconditions: Soil sensor service running
  Steps:
    1. `curl -s -w "\nTIME: %{time_total}s\n" --max-time 5 http://127.0.0.1:8002/`
    2. Assert: TIME is less than 5.0 seconds
  Expected Result: Root endpoint responds within 5 seconds
  Evidence: curl output saved

---

## Execution Strategy

### Sequential Execution

```
Step 1: Wrap _scan_bus_for_sensors() blocking call
Step 2: Wrap _poll_all_sensors() blocking call  
Step 3: Restart service and verify
```

---

## TODOs

- [x] 1. Wrap blocking Modbus call in `_scan_bus_for_sensors()`

  **What to do**:
  - In `Infrastructure/soil-sensor-service/app/background_tasks.py`
  - Line 162: `registers = temp_modbus.read_holding_registers(modbus_id, 0x0000, 1)`
  - Change to: `registers = await asyncio.to_thread(temp_modbus.read_holding_registers, modbus_id, 0x0000, 1)`

  **Must NOT do**:
  - DO NOT change timeout values or scan frequency
  - DO NOT modify the Modbus protocol implementation

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple one-line async wrapper change, minimal risk
  - **Skills**: None required
  - **Skills Evaluated but Omitted**:
    - N/A

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: None (first step)

  **References**:
  - `Infrastructure/soil-sensor-service/app/background_tasks.py:162` - Where blocking call occurs
  - `Infrastructure/soil-sensor-service/app/modbus_rtu.py:87-162` - The blocking read_holding_registers() implementation

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios (MANDATORY):**

  \`\`\`
  Scenario: Health endpoint responds during no-sensor scan
    Tool: Bash (curl)
    Preconditions: Service running, no sensors connected, scan in progress
    Steps:
      1. curl -s -w "\nTIME: %{time_total}s\n" --max-time 5 http://127.0.0.1:8002/health
      2. Assert: TIME < 5.0 seconds
      3. Assert: HTTP 200 returned
    Expected Result: Health responds quickly even during sensor scan
    Evidence: curl output with timing
  \`\`\`

  **Commit**: YES
  - Message: `fix(soil-sensor): wrap blocking Modbus call in asyncio.to_thread()`
  - Files: `Infrastructure/soil-sensor-service/app/background_tasks.py`
  - Pre-commit: N/A

- [x] 2. Wrap blocking sensor poll call in `_poll_all_sensors()`

  **What to do**:
  - In `Infrastructure/soil-sensor-service/app/background_tasks.py`
  - Line 259: `readings = reader.read_all_parameters()`
  - Change to: `readings = await asyncio.to_thread(reader.read_all_parameters)`
  - Note: `read_all_parameters()` is a method that returns a dict, not a function, so we use `reader.read_all_parameters` without ()

  **Must NOT do**:
  - DO NOT change sensor reading logic or error handling
  - DO NOT modify soil_sensor_reader.py

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple one-line async wrapper change, follows pattern from step 1
  - **Skills**: None required
  - **Skills Evaluated but Omitted**:
    - N/A

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/soil-sensor-service/app/background_tasks.py:259` - Where blocking call occurs
  - `Infrastructure/soil-sensor-service/app/soil_sensor_reader.py:58-87` - The blocking read_all_parameters() implementation

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios (MANDATORY):**

  \`\`\`
  Scenario: Sensor reading still works after async wrapping
    Tool: Bash (curl + journalctl)
    Preconditions: Service running, sensors connected (if available)
    Steps:
      1. Check service logs for recent sensor readings: journalctl -u soil-sensor-service --since "1 minute ago" | grep -i "soil_sensor"
      2. OR if no sensors: Verify no blocking behavior by checking health endpoint responds
    Expected Result: Either sensor readings appear OR health responds (depending on sensor state)
    Evidence: Log output or curl timing output
  \`\`\`

  **Commit**: YES
  - Message: `fix(soil-sensor): wrap sensor poll call in asyncio.to_thread()`
  - Files: `Infrastructure/soil-sensor-service/app/background_tasks.py`
  - Pre-commit: N/A

- [x] 3. Restart service and verify

  **What to do**:
  - Restart the soil-sensor-service: `sudo systemctl restart soil-sensor-service`
  - Wait for startup: `sleep 3`
  - Run verification tests:
    1. Health endpoint: `curl --max-time 5 http://127.0.0.1:8002/health`
    2. Root endpoint: `curl --max-time 5 http://127.0.0.1:8002/`
    3. Concurrent requests test

  **Must NOT do**:
  - DO NOT modify any other files

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification steps only, no code changes
  - **Skills**: None required
  - **Skills Evaluated but Omitted**:
    - N/A

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: Task 2

  **References**:
  - Systemd service: `soil-sensor-service.service`

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios (MANDATORY):**

  \`\`\`
  Scenario: Health endpoint returns 200 within 5 seconds
    Tool: Bash (curl)
    Preconditions: Service restarted, no sensors
    Steps:
      1. curl -s -w "HTTP:%{http_code} TIME:%{time_total}s\n" --max-time 5 http://127.0.0.1:8002/health
      2. Assert: HTTP code is 200
      3. Assert: TIME < 5.0
    Expected Result: Health returns 200 within 5 seconds
    Evidence: curl output

  Scenario: Root endpoint returns within 5 seconds  
    Tool: Bash (curl)
    Preconditions: Service restarted
    Steps:
      1. curl -s -w "HTTP:%{http_code} TIME:%{time_total}s\n" --max-time 5 http://127.0.0.1:8002/
      2. Assert: HTTP code is 200
      3. Assert: TIME < 5.0
    Expected Result: Root returns 200 within 5 seconds
    Evidence: curl output

  Scenario: Concurrent requests complete in parallel
    Tool: Bash (parallel curl)
    Preconditions: Service restarted
    Steps:
      1. (curl -s -w "T1:%{time_total}s\n" --max-time 10 http://127.0.0.1:8002/health -o /dev/null &); C1=$!
      2. (curl -s -w "T2:%{time_total}s\n" --max-time 10 http://127.0.0.1:8002/health -o /dev/null &); C2=$!
      3. wait $C1 $C2
      4. Assert: Both times < 10 seconds (not ~10+ which would indicate sequential blocking)
    Expected Result: Concurrent execution
    Evidence: Combined curl timing output
  \`\`\`

  **Commit**: NO

---

## Success Criteria

### Verification Commands
```bash
# Health endpoint responds
curl -s -w "\nTIME: %{time_total}s\n" --max-time 5 http://127.0.0.1:8002/health

# Expected: {"status":"healthy"} and TIME < 5.0s

# Root endpoint responds
curl -s -w "\nTIME: %{time_total}s\n" --max-time 5 http://127.0.0.1:8002/

# Expected: {"service":"Soil Sensor Service"...} and TIME < 5.0s

# Concurrent requests don't block each other
(time curl -s --max-time 10 http://127.0.0.1:8002/health -o /dev/null) &
(time curl -s --max-time 10 http://127.0.0.1:8002/health -o /dev/null) &
wait

# Expected: Total wall time < 15 seconds (parallel), not > 20 seconds (sequential)
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] Health endpoint responds within 5s with no sensors
- [ ] Sensor reading still functional (if sensors connected)