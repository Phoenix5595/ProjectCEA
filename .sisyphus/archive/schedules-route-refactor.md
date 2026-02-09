# Refactor routes/schedules.py God Object

## TL;DR

> **Quick Summary**: Extract the 1400+ line schedules.py route file into focused modules with single responsibilities.
> 
> **Deliverables**:
> - Split schedules.py into 3 focused route modules
> - Extract business logic into service layer
> - Reduce main file to <300 lines
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4

---

## Context

### Audit Finding
`Infrastructure/automation-service/app/routes/schedules.py` is a god object:
- **Lines**: 1400+
- **Largest function**: `save_room_schedule()` - 340 lines
- **Responsibilities**: Climate schedules, light schedules, room schedules, bulk operations, mode transitions
- **Violation**: Single Responsibility Principle

### Root Cause
All schedule-related endpoints were added to one file over time without extraction.

---

## Work Objectives

### Core Objective
Split schedules.py into focused modules while maintaining API compatibility.

### Concrete Deliverables
- `routes/schedules/climate.py` - Climate schedule endpoints
- `routes/schedules/lights.py` - Light schedule endpoints  
- `routes/schedules/room.py` - Room schedule endpoints
- `routes/schedules/__init__.py` - Router aggregation
- `services/schedule_service.py` - Extracted business logic

### Must Have
- All existing endpoints must continue working
- No breaking changes to API contracts
- Tests must pass after each change

### Must NOT Have (Guardrails)
- Do NOT change endpoint paths or response shapes
- Do NOT refactor DatabaseManager (already acceptable)
- Do NOT touch other route files
- Do NOT add new features during refactoring

---

## TODOs

- [ ] 1. Analyze schedules.py structure and identify split points

  **What to do**:
  - Read full schedules.py file
  - Identify logical groupings: climate, lights, room, bulk ops
  - Map function dependencies
  - Document which functions go where

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Tasks 2, 3

  **References**:
  - `Infrastructure/automation-service/app/routes/schedules.py` - The file to analyze

  **Acceptance Criteria**:
  - [ ] Split plan documented in `.sisyphus/drafts/schedules-split-plan.md`
  - [ ] Each function assigned to target module
  - [ ] Dependencies mapped

---

- [ ] 2. Create schedule route submodules

  **What to do**:
  - Create `routes/schedules/` directory
  - Create `climate.py` with climate schedule endpoints
  - Create `lights.py` with light schedule endpoints
  - Create `room.py` with room schedule endpoints
  - Create `__init__.py` that aggregates routers

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/routes/devices.py` - Example route module pattern
  - Split plan from Task 1

  **Acceptance Criteria**:
  - [ ] `routes/schedules/` directory created
  - [ ] All 4 files created with proper imports
  - [ ] Router aggregation in `__init__.py`
  - [ ] ruff check passes

---

- [ ] 3. Extract business logic to service layer

  **What to do**:
  - Create `services/schedule_service.py`
  - Move complex logic from `save_room_schedule()` to service
  - Keep routes thin - just validation and delegation
  - Largest functions should be <50 lines after extraction

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 2

  **References**:
  - Original `schedules.py` for business logic
  - New route modules from Task 2

  **Acceptance Criteria**:
  - [ ] `services/schedule_service.py` created
  - [ ] Complex logic extracted
  - [ ] Route functions are thin (<50 lines each)
  - [ ] ruff check passes

---

- [ ] 4. Update imports and verify

  **What to do**:
  - Update `routes/__init__.py` or `routes.py` to use new schedule router
  - Remove old `schedules.py` (after confirming new modules work)
  - Run full test suite
  - Verify all endpoints work

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocked By**: Task 3

  **References**:
  - `Infrastructure/automation-service/app/routes/routes.py` - Router registration

  **Acceptance Criteria**:
  - [ ] Old schedules.py removed or renamed
  - [ ] New modules registered in router
  - [ ] All tests pass
  - [ ] API endpoints respond correctly

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Verify schedule endpoints still work
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8001/api/schedules/Flower%20Room/main
      2. Assert: HTTP 200 returned
      3. Assert: JSON response contains schedule data
    Expected Result: Endpoints work as before
  ```

---

## Success Criteria

### Verification Commands
```bash
# Lint check
ruff check Infrastructure/automation-service/app/routes/schedules/

# Line count - should be <300 per file
wc -l Infrastructure/automation-service/app/routes/schedules/*.py

# Test endpoints
curl http://localhost:8001/api/schedules/Flower%20Room/main
```

### Final Checklist
- [ ] schedules.py split into 3+ focused modules
- [ ] Each module <300 lines
- [ ] No function >50 lines in routes
- [ ] Business logic in services/
- [ ] All tests pass
- [ ] API compatibility maintained
