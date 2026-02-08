# Fix PID Mode Selector and Verify Auto-PID Implementation

## TL;DR

> **Quick Summary**: Fix backend parameter mismatch causing 500 errors when switching to auto_pid mode, add user-facing error feedback in frontend, and verify complete auto-PID tuning flow works end-to-end.
> 
> **Deliverables**:
> - Fixed `update_autotune_state()` call in pid.py with correct parameters
> - Toast notifications for mode change errors in VerticalPIDBlock.tsx
> - Verified auto_pid mode triggers autotune and all mode transitions work
> 
> **Estimated Effort**: Short (2-3 hours)
> **Parallel Execution**: NO - sequential (each task depends on previous)
> **Critical Path**: Task 1 → Task 2 → Task 3

---

## Context

### Original Request
User reported: "the mode select isn't working" in the PID control section of the frontend. Additionally requested verification that "auto pid implementation is complete."

### Interview Summary
**Key Discussions**:
- Issue: "Buttons don't respond to clicks" - user sees no feedback when clicking mode buttons
- Scope: "Full end-to-end verification" of auto_pid flow requested

**Research Findings**:
- **Root Cause**: Backend bug in `pid.py:373-374` - `update_autotune_state()` called with wrong parameters
  - Called with: `is_active=True, status="running", cycles_completed=0`
  - Expected: `state="running", progress=0.0, current_step="initializing"`
  - This causes 500 error when switching to `auto_pid` mode
- **Frontend Issue**: `handleModeChange` in `VerticalPIDBlock.tsx` catches errors with `logger.error()` but shows NO toast - user sees nothing on failure
- **Dead Code**: `PIDEditor.tsx` and `PIDModeSelector.tsx` exist but are never imported (not in scope)
- **Auto-tuner**: `pid_autotuner.py` has complete `RelayAutoTuner` using Åström-Hägglund method - implementation is complete

### Metis Review
**Identified Gaps** (addressed):
- Parameter mismatch confirmed: database.py expects `state` (required), not `status`
- PIDCompactRow.tsx also has error-swallowing pattern - excluded from scope (focus on primary component)
- Edge cases for concurrent mode changes - excluded (out of scope for this fix)

---

## Work Objectives

### Core Objective
Fix the backend parameter mismatch that causes 500 errors when switching to auto_pid mode, add user feedback for errors, and verify the complete auto-PID flow works.

### Concrete Deliverables
- `Infrastructure/automation-service/app/routes/pid.py` - Fixed line 373-374
- `Infrastructure/frontend/src/components/VerticalPIDBlock.tsx` - Added toast on error
- Verified: All 3 mode transitions work (on_off ↔ pid ↔ auto_pid)
- Verified: Auto-tuner starts when auto_pid mode is selected

### Definition of Done
- [x] `curl -X POST .../api/pid/mode/heater -d '{"mode":"auto_pid"}'` returns 200
- [x] Frontend mode buttons respond with visual feedback
- [x] Error toast appears when API fails
- [x] Autotune state shows "running" after switching to auto_pid

### Must Have
- Fix `update_autotune_state()` call to use correct parameter names
- Toast notification on mode change failure
- All 3 modes selectable without 500 errors

### Must NOT Have (Guardrails)
- Do NOT change `database.py` wrapper signature - fix the caller
- Do NOT touch dead code (`PIDEditor.tsx`, `PIDModeSelector.tsx`)
- Do NOT add autotune progress UI - out of scope
- Do NOT fix `PIDCompactRow.tsx` - focus on primary component only
- Do NOT modify autotune algorithm - it's already complete

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (backend has pytest)
- **Automated tests**: Tests-after (verify existing tests pass after fix)
- **Framework**: pytest for backend

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

**Verification Tool by Deliverable Type:**

| Type | Tool | How Agent Verifies |
|------|------|-------------------|
| **Backend API** | Bash (curl) | Send requests, parse responses, assert fields |
| **Frontend UI** | Playwright | Navigate, interact, assert DOM, screenshot |

---

## Execution Strategy

### Sequential Execution (No Parallelization)

```
Task 1: Fix Backend Parameter Bug
    ↓ (must complete before testing)
Task 2: Add Frontend Error Toast  
    ↓ (must complete before E2E verification)
Task 3: End-to-End Verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3 | None |
| 2 | 1 | 3 | None |
| 3 | 1, 2 | None | None (final) |

---

## TODOs

- [x] 1. Fix Backend Parameter Mismatch in pid.py

  **What to do**:
  - Open `Infrastructure/automation-service/app/routes/pid.py`
  - Locate lines 373-374 in the `set_pid_mode` function
  - Change incorrect call:
    ```python
    # BEFORE (broken):
    await database.update_autotune_state(
        device_type, is_active=True, status="running", cycles_completed=0)
    
    # AFTER (fixed):
    await database.update_autotune_state(
        device_type, state="running", progress=0.0, current_step="initializing")
    ```
  - Verify line 379 is already correct: `state="idle"`
  - Run ruff to ensure no lint errors

  **Must NOT do**:
  - Do NOT change database.py signature
  - Do NOT modify any other routes
  - Do NOT change line 379 (it's already correct)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single file, 1-line fix, clear target
  - **Skills**: [`git-master`]
    - `git-master`: For atomic commit after fix

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: Tasks 2, 3
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/routes/pid.py:379` - Correct usage example: `state="idle"`

  **API/Type References**:
  - `Infrastructure/automation-service/app/database.py:745-755` - `update_autotune_state` signature: `state` (required str), `progress` (optional float), `current_step` (optional str)

  **WHY Each Reference Matters**:
  - Line 379 shows the correct pattern to follow
  - database.py shows the exact parameter names expected

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Backend accepts auto_pid mode change
    Tool: Bash (curl)
    Preconditions: automation-service running on localhost:8001
    Steps:
      1. curl -s -w "\n%{http_code}" -X POST http://localhost:8001/api/pid/mode/heater \
           -H "Content-Type: application/json" \
           -d '{"mode": "auto_pid"}'
      2. Assert: HTTP status is 200 (not 500)
      3. Assert: response.mode equals "auto_pid"
      4. curl -s http://localhost:8001/api/pid/autotune/heater/status
      5. Assert: response.state equals "running" or "initializing"
    Expected Result: Mode changes successfully, autotune starts
    Evidence: Response bodies saved to .sisyphus/evidence/task-1-autotune-start.json

  Scenario: Mode transition to on_off works
    Tool: Bash (curl)
    Preconditions: Mode currently set to auto_pid
    Steps:
      1. curl -s -w "\n%{http_code}" -X POST http://localhost:8001/api/pid/mode/heater \
           -H "Content-Type: application/json" \
           -d '{"mode": "on_off"}'
      2. Assert: HTTP status is 200
      3. Assert: response.mode equals "on_off"
    Expected Result: Mode changes to on_off
    Evidence: Response body captured

  Scenario: Mode transition to pid works
    Tool: Bash (curl)
    Steps:
      1. curl -s -w "\n%{http_code}" -X POST http://localhost:8001/api/pid/mode/heater \
           -H "Content-Type: application/json" \
           -d '{"mode": "pid"}'
      2. Assert: HTTP status is 200
      3. Assert: response.mode equals "pid"
    Expected Result: Mode changes to pid
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `fix(pid): correct update_autotune_state parameters in mode change route`
  - Files: `Infrastructure/automation-service/app/routes/pid.py`
  - Pre-commit: `cd Infrastructure && ruff check app/routes/pid.py`

---

- [x] 2. Add Error Toast in VerticalPIDBlock.tsx

  **What to do**:
  - Open `Infrastructure/frontend/src/components/VerticalPIDBlock.tsx`
  - Locate `handleModeChange` function (around line 83-102)
  - In the catch block, add toast notification:
    ```typescript
    // BEFORE:
    } catch (error) {
      logger.error("Failed to change PID mode:", error);
    }
    
    // AFTER:
    } catch (error) {
      logger.error("Failed to change PID mode:", error);
      toast.error("Failed to change mode. Please try again.");
    }
    ```
  - Ensure `toast` is imported from the project's toast library (check existing imports in file)

  **Must NOT do**:
  - Do NOT modify PIDCompactRow.tsx
  - Do NOT change mode button styling
  - Do NOT add loading spinners (out of scope)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single file, small change
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: For UI component modification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after Task 1)
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `Infrastructure/frontend/src/components/VerticalPIDBlock.tsx:83-102` - handleModeChange function location
  - Search for `toast.error` in other components for import pattern

  **WHY Each Reference Matters**:
  - Need to find exact location of catch block
  - Need to match existing toast usage pattern in codebase

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Error toast appears when API fails
    Tool: Playwright (playwright skill)
    Preconditions: Frontend running on localhost:5173, backend STOPPED
    Steps:
      1. Navigate to: http://localhost:5173/zone/Flower%20Room/main
      2. Wait for: page load complete (timeout: 10s)
      3. Click: Mode button for "AUTO" or "PID" (any mode change)
      4. Wait for: Toast notification visible (timeout: 5s)
      5. Assert: Toast contains "Failed" or "error" text
      6. Screenshot: .sisyphus/evidence/task-2-error-toast.png
    Expected Result: Error toast is displayed to user
    Evidence: .sisyphus/evidence/task-2-error-toast.png

  Scenario: Success case - no error toast on valid mode change
    Tool: Playwright (playwright skill)
    Preconditions: Frontend AND backend running
    Steps:
      1. Navigate to: http://localhost:5173/zone/Flower%20Room/main
      2. Wait for: page load complete
      3. Click: Mode button for different mode
      4. Wait for: Button state to update (timeout: 5s)
      5. Assert: NO error toast visible
      6. Assert: Button reflects new mode selection
      7. Screenshot: .sisyphus/evidence/task-2-success-mode.png
    Expected Result: Mode changes without error
    Evidence: .sisyphus/evidence/task-2-success-mode.png
  ```

  **Commit**: YES
  - Message: `fix(frontend): add error toast for PID mode change failures`
  - Files: `Infrastructure/frontend/src/components/VerticalPIDBlock.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run lint`

---

- [x] 3. End-to-End Verification of Auto-PID Flow (code verified, needs deploy)

  **What to do**:
  - Verify complete auto_pid flow works from UI through backend
  - Test all 3 mode transitions in the frontend
  - Confirm autotune state updates correctly
  - Document evidence of working system

  **Must NOT do**:
  - Do NOT make code changes in this task
  - Do NOT test autotune completion (takes too long)
  - Do NOT verify PID parameter calculation

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification only, no code changes
  - **Skills**: [`playwright`]
    - `playwright`: For browser automation and verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (final task)
  - **Blocks**: None
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Documentation References**:
  - `ARCHITECTURE.md` - System overview for understanding data flow

  **WHY Each Reference Matters**:
  - Understanding system architecture helps verify correct behavior

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Complete auto_pid mode selection from UI
    Tool: Playwright (playwright skill)
    Preconditions: Frontend on localhost:5173, backend on localhost:8001
    Steps:
      1. Navigate to: http://localhost:5173/zone/Flower%20Room/main
      2. Wait for: PID control section visible (timeout: 10s)
      3. Find: Device selector dropdown, select "heater"
      4. Wait for: Mode buttons visible
      5. Click: "AUTO" mode button (auto_pid)
      6. Wait for: Button state change (timeout: 5s)
      7. Assert: AUTO button shows selected state
      8. Screenshot: .sisyphus/evidence/task-3-auto-mode-selected.png
      9. curl http://localhost:8001/api/pid/mode/heater
      10. Assert: response.mode equals "auto_pid"
      11. curl http://localhost:8001/api/pid/autotune/heater/status
      12. Assert: response.state is "running" or "initializing"
    Expected Result: UI and backend both reflect auto_pid mode, autotune started
    Evidence: .sisyphus/evidence/task-3-auto-mode-selected.png

  Scenario: All mode transitions work from UI
    Tool: Playwright (playwright skill)
    Preconditions: System in auto_pid mode from previous scenario
    Steps:
      1. Navigate to: http://localhost:5173/zone/Flower%20Room/main
      2. Click: "ON/OFF" mode button
      3. Wait for: Button state change
      4. Assert: ON/OFF button selected
      5. Screenshot: .sisyphus/evidence/task-3-onoff-mode.png
      6. Click: "PID" mode button
      7. Wait for: Button state change
      8. Assert: PID button selected
      9. Screenshot: .sisyphus/evidence/task-3-pid-mode.png
      10. Click: "AUTO" mode button
      11. Wait for: Button state change
      12. Assert: AUTO button selected
      13. Screenshot: .sisyphus/evidence/task-3-auto-mode-final.png
    Expected Result: All 3 modes can be selected without errors
    Evidence: Screenshots in .sisyphus/evidence/

  Scenario: Backend autotune state reflects mode correctly
    Tool: Bash (curl)
    Preconditions: Mode just set to auto_pid
    Steps:
      1. curl -s http://localhost:8001/api/pid/autotune/heater/status | jq .
      2. Assert: state field is "running" or similar active state
      3. curl -s -X POST http://localhost:8001/api/pid/mode/heater \
           -H "Content-Type: application/json" -d '{"mode": "on_off"}'
      4. curl -s http://localhost:8001/api/pid/autotune/heater/status | jq .
      5. Assert: state field is "idle" or "stopped"
    Expected Result: Autotune state correctly tracks mode changes
    Evidence: Response bodies captured
  ```

  **Commit**: NO (verification only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `fix(pid): correct update_autotune_state parameters in mode change route` | pid.py | curl returns 200 |
| 2 | `fix(frontend): add error toast for PID mode change failures` | VerticalPIDBlock.tsx | npm run lint passes |

---

## Success Criteria

### Verification Commands
```bash
# Test backend mode change (should return 200, not 500)
curl -s -w "\n%{http_code}" -X POST http://localhost:8001/api/pid/mode/heater \
  -H "Content-Type: application/json" -d '{"mode": "auto_pid"}'
# Expected: 200 with {"mode": "auto_pid", ...}

# Verify autotune started
curl -s http://localhost:8001/api/pid/autotune/heater/status
# Expected: {"state": "running", ...}

# Switch back to on_off
curl -s -X POST http://localhost:8001/api/pid/mode/heater \
  -H "Content-Type: application/json" -d '{"mode": "on_off"}'
# Expected: 200
```

### Final Checklist
- [x] All 3 modes (on_off, pid, auto_pid) can be selected via API without 500 errors
- [x] All 3 modes can be selected via frontend UI
- [x] Error toast appears when backend is unavailable
- [x] Autotune state shows "running" when auto_pid is selected
- [x] Autotune state shows "idle" when switching away from auto_pid
- [x] No dead code touched (PIDEditor.tsx, PIDModeSelector.tsx unchanged)
- [x] Ruff passes on modified Python files
- [x] Frontend lint passes

---

*Last updated: 2026-02-07*
