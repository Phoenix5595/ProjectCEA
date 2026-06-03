# Plan: Redis-Resilient Light Control & Mode Fallback (v2)

Fixes the issue where lights remain ON and setpoints disappear from Grafana during "Unknown" states or database failures. Ensures the system always resolves to a valid mode and maintains data logging integrity.

## Context

### Original Request
The user reported that lights were not setting to 0 during nights.

### Interview Summary
- **Root Cause**: `ControlEngine.py` relies on DB reads. If they fail, mode becomes `None`.
- **New Finding**: Setpoints disappear because logging is skipped if schedules fail to fetch.
- **Decision**: Restructure loop to always resolve a mode (default NIGHT) and always log setpoints.

---

## Work Objectives

### Core Objective
Restructure the control engine loop to guarantee mode resolution and setpoint logging even during catastrophic database failures.

### Concrete Deliverables
- `Infrastructure/automation-service/app/control/control_engine.py`: Refactored `run_control_loop`.
- `Infrastructure/automation-service/tests/test_light_redis_resilience.py`: Updated tests verifying setpoint logging.

### Definition of Done
- [ ] Setpoints are logged to the DB (and visible in Grafana) even if the primary schedule fetch fails (falling back to Redis).
- [ ] Lights set to 0.0 intensity whenever mode resolves to NIGHT (via DB, Redis, or safety fallback).
- [ ] `pytest Infrastructure/automation-service/tests/test_light_redis_resilience.py` passes.

---

## TODOs

- [ ] 1. Restructure ControlEngine mode resolution and setpoint logic
  **What to do**:
  - Refactor `Infrastructure/automation-service/app/control/control_engine.py`:
    - Move `light_schedule` and `climate_schedule` resolution to a dedicated block that attempts DB then Redis.
    - Ensure `current_mode` is resolved using `scheduler.get_climate_mode` if schedules are found.
    - If `current_mode` is still `None` (no schedules or resolution failed), force `current_mode = "NIGHT"`.
    - Move `get_setpoint`, `compute_effective_setpoints`, and `log_effective_setpoints` OUTSIDE the schedule-fetch block so they run for the resolved mode.
    - Pass `light_schedule` to `scheduler.get_light_intensity_details` in the dfr0971 logging block.
  **Acceptance Criteria**:
  - Code is logically structured to always compute and log setpoints for the resolved mode.

- [ ] 2. Update Resilience Tests
  **What to do**:
  - Update `Infrastructure/automation-service/tests/test_light_redis_resilience.py`:
    - Add assertions to `test_redis_fallback_when_db_fails` to verify `database.log_effective_setpoints` is called even when DB fetch fails.
    - Add assertions to `test_safety_night_when_all_fails` to verify `current_mode` is "NIGHT" and setpoints are logged.
  **Acceptance Criteria**:
  - Tests verify both control (lights) and logging (Grafana) resilience.

- [ ] 3. Verify Final Safety
  **What to do**:
  - Run the tests and verify zero regressions.
  **Acceptance Criteria**:
  - All tests PASS.


---

## Success Criteria

### Verification Commands
```bash
cd Infrastructure/automation-service && pytest tests/test_light_redis_resilience.py
```

### Final Checklist
- [ ] Redis used for primary mode resolution.
- [ ] DB downtime does not affect light schedules.
- [ ] NIGHT fallback works in catastrophic data loss.
- [ ] Manual control preserved.
