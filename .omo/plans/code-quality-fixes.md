# Code Quality Standardization — 10-Issue Fix

## TL;DR

> **Quick Summary**: Standardize error handling, logging, config patterns, repository access, import style, type annotations, and cluster topology sync across all 6 Python services + React frontend. Fixes 42 bare `except Exception:`, 20+ emoji-infected log lines, 7 `console.log` calls, adds CI for Python/TS sync, and propagates the automation-service's proven patterns (structured errors, repository pattern) to backend.
>
> **Deliverables**:
> - 0 bare `except Exception:` anywhere (replaced with specific exception types)
> - 0 emoji in backend logs (replaced with structured JSON logging)
> - 0 `console.log` in frontend production code
> - Backend gets structured error middleware + repository pattern
> - CI script validates `cluster_topology.py` ↔ `clusterTopology.ts` parity
> - StateManager fully typed (no `Any` usage, behavior preserved)
> - Import style standardized across automation-service
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves, 4 tasks parallel in Wave 1
> **Critical Path**: Wave 1 → Wave 2 → Wave 3 → Wave 4 (each wave gates the next)

---

## Context

### Original Request
"make a plan to comprehensively fix all 10 issues" — referring to the 10 inconsistency categories identified in the code quality audit.

### Interview Summary
**Key Discussions**:
- **Test strategy**: NO automated tests. Agent-Executed QA Scenarios (curl, Playwright, systemctl) are the primary verification method.
- **Plan scope**: Single comprehensive plan with risk-based phases. Not split across sessions.
- **Issue #2 (ConfigLoader)**: The difference is intentional. Task is to verify and improve documentation, not restructure code.
- **Repository pattern scope**: Backend is the priority target. Soil-sensor and weather can wait.

**Research Findings**:
- 42 bare `except Exception:` confirmed across 19 Python files (not the initially estimated 69)
- 7 `console.log` instances remain in frontend (not 61 — AGENTS.md count is stale)
- Backend has no structured error middleware; automation-service does
- `shared/config.py` lines 20-29 explicitly document ConfigLoader is different by design
- Both `cluster_topology.py` and `clusterTopology.ts` currently in sync (last sync: Phase 5e commit `0334bca`)

### Metis Review
**Identified Gaps** (addressed):
- **Scope collapse**: 10 issues consolidated to 7 real work items (issues #1+#4, #3+#10, #5+#9 are overlapping)
- **ConfigLoader is intentional**: Issue #2 is not a bug — task reduced to documentation verification
- **Risk-based phasing**: Cleanup tasks first (no control loop impact), architectural changes last
- **StateManager is control-loop adjacent**: Typing it requires pre/post timing verification (`/api/health`)
- **Rollback strategy**: Every Phase 3+ task needs a verified rollback path

---

## Work Objectives

### Core Objective
Standardize error handling, logging, repository access, import style, type annotations, and cluster topology sync across all ProjectCEA services, eliminating 42 bare excepts, 20+ emoji log lines, and 7 console.log instances.

### Concrete Deliverables
- All 42 bare `except Exception:` replaced with specific exception types
- Structured error middleware added to backend service (matching automation-service format)
- 7 `console.log` → `logger.*` replacements in frontend
- All emoji removed from backend log messages
- CI validation script for `cluster_topology.py` ↔ `clusterTopology.ts` parity
- automation-service imports standardized to absolute (`from shared.`) style
- `shared/config.py` docstring verified/improved for ConfigLoader design rationale
- StateManager fully typed without behavior changes
- Backend gets repository-based data access layer

### Definition of Done
- [ ] `grep -r "except Exception:" Infrastructure/ --include="*.py" | grep -v .venv | grep -v __pycache__` returns **0**
- [ ] `grep -r "⚠️\|✅\|🛑" Infrastructure/backend/app/` returns **0**
- [ ] `grep -r "console\.\(log\|warn\|error\|debug\)" Infrastructure/frontend/src/ --include="*.ts" --include="*.tsx"` returns **0** (except in logger.ts itself)
- [ ] `python Infrastructure/scripts/validate_cluster_topology.py` exits 0
- [ ] `curl -s http://mothernode:8001/api/health | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"` succeeds
- [ ] `systemctl is-active automation-service cea-backend` returns `active active`

### Must Have
- All 42 bare excepts gone
- All emoji and console.log gone
- Control loop latency ≤5s (verified after every Phase 3+ task)
- Backward compatibility: no API response format changes that break frontend

### Must NOT Have (Guardrails)
- **Do NOT force `ConfigLoader` to inherit from `YamlConfigLoader`** — this is documented as intentional
- **Do NOT touch `can-processor-service`** — CAN bus ingestion is performance-critical and out of scope
- **Do NOT modify I2C hardware config** (MCP23017 bus 0, DFR0971 bus 1)
- **Do NOT change StateManager behavior or timing** — only add type annotations
- **Do NOT change database schema** without migration files
- **Do NOT add new `except Exception:`** during any fix
- **Do NOT mix response formats within a service** — once backend has middleware, ALL routes must use it

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks verified by the executing agent using tools. No human testing required.

### Test Decision
- **Infrastructure exists**: YES (minimal — 2 test files in automation-service, frontend test setup exists)
- **Automated tests**: NO — Agent-Executed QA Scenarios are PRIMARY verification
- **Framework**: N/A (no new tests added)

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

**Verification Tool by Deliverable Type:**

| Type | Tool | How Agent Verifies |
|------|------|-------------------|
| **Backend API** | Bash (curl) | Send requests, parse JSON, assert fields/status codes |
| **Automation API** | Bash (curl) | Send requests, assert structured error format |
| **Frontend** | Playwright (playwright skill) | Navigate, verify UI renders, check console for errors |
| **Service health** | Bash (systemctl) | Check service status, journalctl for errors |
| **CI Script** | Bash (python) | Run script, assert exit code 0 |
| **Import check** | Bash (grep/ruff) | Verify no relative imports remain |

---

## Execution Strategy

### Parallel Execution Waves

> Maximize throughput by grouping independent tasks into parallel waves.

```
Wave 1 (Start Immediately — 4 tasks, NO dependencies):
├── Task 1: Remove emoji from backend logs
├── Task 2: Replace console.log in frontend
├── Task 3: Cluster topology CI script
└── Task 4: Fix import style in automation-service

Wave 2 (After Wave 1 — 2 tasks):
├── Task 5: ConfigLoader documentation
└── Task 6: Add structured error middleware to backend

Wave 3 (After Wave 2 — 2 tasks, partial parallel):
├── Task 7: Replace bare excepts in automation-service repos
└── Task 8: Add repository pattern to backend

Wave 4 (After Wave 3 — sequential, highest risk):
├── Task 9: Type StateManager (control-loop adjacent)
└── Task 10: Final verification — all services healthy
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | None | 2, 3, 4 |
| 2 | None | None | 1, 3, 4 |
| 3 | None | None | 1, 2, 4 |
| 4 | None | None | 1, 2, 3 |
| 5 | None (after Wave 1) | None | 6 |
| 6 | None (after Wave 1) | 7, 8 | 5 |
| 7 | 6 | None | 8 |
| 8 | 6 | None | 7 |
| 9 | 1-8 | 10 | None (sequential) |
| 10 | 9 | None | None (final) |

**Critical Path**: Wave 1 → Task 6 → Task 7 or 8 → Task 9 → Task 10
**Parallel Speedup**: ~50% faster than sequential (4 parallel in Wave 1, 2 in Wave 2, 2 in Wave 3)

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 1 | 1, 2, 3, 4 | 4 `task` calls: quick (emoji), quick (console.log), quick (CI script), quick (imports) |
| 2 | 5, 6 | 2 `task` calls: writing (docs), quick (middleware) |
| 3 | 7, 8 | 2 `task` calls: quick (excepts), ultrabrain (repos) |
| 4 | 9, 10 | 1 `task` call: deep (typing), then verify |

---

## TODOs

> Implementation + Test = ONE Task. Never separate.
> EVERY task MUST have: Recommended Agent Profile + Parallelization info.

---

- [x] 1. **Remove emoji from backend log messages** (Issues #5) ✅

  **What to do**:
  - Replace all emoji-prefixed log messages in `backend/app/main.py` and `backend/app/background_tasks.py` with plain text
  - Specific replacements:
    - `⚠️ ` → `WARNING: ` or remove (log level already communicates severity)
    - `✅ ` → remove prefix (INFO level is sufficient)
    - `🛑 ` → remove prefix (ERROR/WARNING level sufficient)
    - `🔄 ` → remove prefix
    - `ℹ️ ` → remove prefix
  - Verify all log calls use `shared.infra_logging.get_logger(__name__)` (already the case)
  - Approx 15-20 log calls across 2 files

  **Must NOT do**:
  - Do NOT change log levels (INFO stays INFO, WARNING stays WARNING)
  - Do NOT change log message content beyond emoji removal
  - Do NOT touch any file outside `Infrastructure/backend/app/`

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple mechanical text replacement in 2 files, no logic changes
  - **Skills**: None needed
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: Not relevant — backend-only change

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:
  - `Infrastructure/backend/app/main.py:51,67,95,99,105,125,139,141,143,159,164,166,168` — Emoji locations to clean
  - `Infrastructure/backend/app/background_tasks.py:132,139,153` — More emoji locations
  - `Infrastructure/shared/infra_logging.py` — Canonical logger pattern (JSON formatter, ConsoleFormatter)

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Backend logs contain no emoji after fix
    Tool: Bash (grep)
    Preconditions: Fix applied, backend code updated
    Steps:
      1. grep -rn "⚠️|✅|🛑|🔄|ℹ️" Infrastructure/backend/app/ --include="*.py"
      2. Assert: output is empty (0 matches)
    Expected Result: No emoji characters in any backend Python file
    Evidence: grep output (empty)

  Scenario: Backend starts without errors after emoji removal
    Tool: Bash (systemctl + journalctl)
    Preconditions: Fix applied
    Steps:
      1. sudo systemctl restart cea-backend
      2. sleep 3
      3. systemctl is-active cea-backend → assert "active"
      4. journalctl -u cea-backend --since "1 min ago" --no-pager | grep -i "error\|traceback" → assert empty
    Expected Result: Backend restarts cleanly, no errors in logs
    Evidence: systemctl status output
  ```

  **Commit**: YES
  - Message: `fix(backend): remove emoji from log messages`
  - Files: `Infrastructure/backend/app/main.py`, `Infrastructure/backend/app/background_tasks.py`

---

- [x] 2. **Replace console.log with logger in frontend** (Issue #9) ✅

  **What to do**:
  - Replace 7 `console.*` calls in frontend with centralized `logger.*`:
    - `hooks/useSystemStatus.ts:82,113` — 2 calls
    - `hooks/useSensorPolling.ts:99,142,188,192` — 4 calls
    - `config/env.ts:99` — 1 call
  - Import pattern: `import { logger } from '../utils/logger'` (already used in websocket.ts)
  - Map `console.warn` → `logger.warn`, `console.error` → `logger.error`, `console.log` → `logger.info`

  **Must NOT do**:
  - Do NOT change log message content or severity levels
  - Do NOT touch `utils/logger.ts` itself (centralized logger is correct)
  - Do NOT add new dependencies

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical replacement in 3 files, simple import addition
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/utils/logger.ts` — Centralized logger API (LogLevel type, logger.info/warn/error)
  - `Infrastructure/frontend/src/services/websocket.ts` — Example of correct `import { logger }` usage
  - `Infrastructure/frontend/src/hooks/useSystemStatus.ts:82,113` — console.warn to replace
  - `Infrastructure/frontend/src/hooks/useSensorPolling.ts:99,142,188,192` — console.error/log/warn to replace
  - `Infrastructure/frontend/src/config/env.ts:99` — console.warn to replace

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: No console.* calls remain in frontend source (except logger.ts)
    Tool: Bash (grep)
    Preconditions: Fix applied
    Steps:
      1. grep -rn "console\.\(log\|warn\|error\|debug\)" Infrastructure/frontend/src/ --include="*.ts" --include="*.tsx" | grep -v "utils/logger.ts"
      2. Assert: output is empty (0 matches)
    Expected Result: Zero console.* calls outside logger.ts
    Evidence: grep output (empty)

  Scenario: Frontend builds without errors
    Tool: Bash (npm)
    Preconditions: Fix applied
    Steps:
      1. cd Infrastructure/frontend && npm run build
      2. Assert: exit code 0
      3. Assert: no TypeScript errors in output
    Expected Result: Clean production build
    Evidence: Build output
  ```

  **Commit**: YES
  - Message: `fix(frontend): replace console.log with centralized logger`
  - Files: `useSystemStatus.ts`, `useSensorPolling.ts`, `env.ts`

---

- [x] 3. **Create CI validation script for cluster topology sync** (Issue #8) ✅

  **What to do**:
  - Create `Infrastructure/scripts/validate_cluster_topology.py`
  - Script reads `shared/cluster_topology.py` → extracts `_TOPOLOGY` dict and canonical data
  - Script reads `frontend/src/config/clusterTopology.ts` → parses `TOPOLOGY` object
  - Validates parity: same rooms, same device clusters (`main`), same sensor sub-clusters (`front`, `back`), same sensor URL slugs
  - Exits 0 on match, exits 1 with diff details on mismatch
  - Add to `Infrastructure/.pre-commit-config.yaml` if exists, or document as manual pre-commit hook
  - Also validate import path consistency (Python uses `shared.cluster_topology`, TS uses `config/clusterTopology`)

  **Must NOT do**:
  - Do NOT generate TS from Python automatically (complex workflow change; diff validation is simpler and sufficient)
  - Do NOT change either topology file — validate only, don't auto-fix
  - Do NOT add as a blocking CI step without user approval (suggest, don't enforce)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single ~80-line Python script, well-defined input/output
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/shared/cluster_topology.py` — Python source of truth (canonical `_TOPOLOGY` dict, `get_sensor_subclusters()`)
  - `Infrastructure/frontend/src/config/clusterTopology.ts` — TypeScript mirror (`TOPOLOGY` object, `sensorUrlClustersFor()`)
  - AGENTS.md "Cluster Topology Contract" section — describes the contract this script enforces
  - `Infrastructure/.pre-commit-config.yaml` — Check if exists for hook integration point

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Script passes when topology files are in sync
    Tool: Bash (python)
    Preconditions: Both topology files are currently in sync (commit 0334bca)
    Steps:
      1. python Infrastructure/scripts/validate_cluster_topology.py
      2. Assert: exit code 0
      3. Assert: stdout contains "OK" or "in sync"
    Expected Result: Script exits 0, confirms sync
    Evidence: stdout + exit code

  Scenario: Script fails when topology files diverge
    Tool: Bash (python)
    Preconditions: Temporarily modify one file to create intentional mismatch
    Steps:
      1. cp Infrastructure/frontend/src/config/clusterTopology.ts /tmp/backup.ts
      2. Edit: remove "Flower Room" from TS TOPOLOGY
      3. python Infrastructure/scripts/validate_cluster_topology.py
      4. Assert: exit code 1
      5. Assert: stderr/out contains "Flower Room" or "missing"
      6. Restore: cp /tmp/backup.ts Infrastructure/frontend/src/config/clusterTopology.ts
    Expected Result: Script exits 1, reports the mismatch
    Evidence: stdout + stderr + exit code
  ```

  **Commit**: YES
  - Message: `feat(ci): add cluster topology sync validation script`
  - Files: `Infrastructure/scripts/validate_cluster_topology.py`

---

- [x] 4. **Fix import style in automation-service** (Issue #6) ✅

  **What to do**:
  - Replace relative imports (`from ..database import`, `from ..control.schedule_merge import`) with absolute imports (`from app.database import`, `from app.control.schedule_merge import`)
  - Target files: all `routes/` files, `services/`, `control/` that use `from ..` relative imports
  - Run `ruff check --fix .` after changes to catch import ordering issues
  - Verify all imports resolve correctly (import the module)

  **Must NOT do**:
  - Do NOT change `from shared.X import Y` (already correct)
  - Do NOT change `from app.X import Y` (already correct)
  - Do NOT touch `TYPE_CHECKING` blocks (those are fine as-is)
  - Do NOT change imports in `__init__.py` files (package re-exports)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical find-and-replace across ~15 files, well-defined pattern
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/routes/lights.py:15` — Example: `from ..database import DatabaseManager` → `from app.database import DatabaseManager`
  - `Infrastructure/automation-service/app/routes/schedules/room.py:517` — Example: `from ..state import get_state_manager` → `from app.state import get_state_manager`
  - `Infrastructure/automation-service/app/services/mode_transition_service.py:8` — Example: `from ..database import DatabaseManager` → `from app.database import DatabaseManager`
  - Other services (backend, soil-sensor, weather) — Reference for correct absolute import style

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: No relative imports remain in automation-service
    Tool: Bash (grep)
    Preconditions: Fix applied
    Steps:
      1. grep -rn "from \.\." Infrastructure/automation-service/app/ --include="*.py" | grep -v "__init__.py" | grep -v ".venv"
      2. Assert: output is empty (0 matches outside __init__.py)
    Expected Result: Zero relative imports in application code
    Evidence: grep output (empty)

  Scenario: Automation service imports work correctly
    Tool: Bash (python)
    Preconditions: Fix applied
    Steps:
      1. cd Infrastructure/automation-service
      2. python -c "from app.main import app; print('imports OK')"
      3. Assert: exit code 0, stdout contains "imports OK"
    Expected Result: All imports resolve without ImportError
    Evidence: stdout + exit code
  ```

  **Commit**: YES
  - Message: `refactor(automation): standardize imports to absolute style`
  - Files: All modified routes/, services/, control/ files

---

- [x] 5. **Verify and improve ConfigLoader documentation** (Issue #2) ✅

  **What to do**:
  - Read `Infrastructure/shared/config.py` lines 20-29 (the docstring that explains why automation-service ConfigLoader deliberately doesn't inherit)
  - Verify the docstring clearly explains:
    - That automation-service ConfigLoader is DIFFERENT by design
    - WHY: Pydantic validation, device-type canonicalization, Flower Room legacy merging
    - That this is intentional, not a missing inheritance
  - If the docstring is insufficient, improve it with the rationale from Metis's findings
  - `automation-service/app/config.py:520` — check if it documents the non-inheritance rationale

  **Must NOT do**:
  - Do NOT force ConfigLoader to inherit from YamlConfigLoader
  - Do NOT restructure the config class hierarchy
  - Do NOT change any config loading behavior

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Docstring improvement, no code logic changes
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 6)
  - **Blocks**: None
  - **Blocked By**: None (after Wave 1 completes)

  **References**:
  - `Infrastructure/shared/config.py:20-29` — Existing docstring explaining design intent
  - `Infrastructure/automation-service/app/config.py:1-30` — ConfigLoader class docstring (check if rationale documented)
  - `Infrastructure/backend/app/config.py:1-30` — Backend ConfigLoader (correctly inherits from YamlConfigLoader — reference pattern)

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Docstring documents that non-inheritance is intentional
    Tool: Bash (grep)
    Preconditions: Fix applied (or verified as already sufficient)
    Steps:
      1. grep -A8 "Deliberately.*not.*scope\|intentional\|by design" Infrastructure/shared/config.py
      2. Assert: finds rationale explaining ConfigLoader difference
      3. grep -A5 "YamlConfigLoader\|ConfigLoader" Infrastructure/automation-service/app/config.py
      4. Assert: if ConfigLoader class exists, docstring references shared/config.py rationale
    Expected Result: Clear documentation that the design difference is intentional
    Evidence: grep output showing rationale
  ```

  **Commit**: YES (if changes made) or SKIP (if already sufficient)
  - Message: `docs(config): clarify ConfigLoader design rationale`
  - Files: `Infrastructure/shared/config.py`, possibly `automation-service/app/config.py`

---

- [x] 6. **Add structured error middleware to backend** (Issues #1 + #4) ✅

  **What to do**:
  - Create `Infrastructure/backend/app/middleware/exception_handler.py` with:
    - `APIError` base exception (status_code, message, error_code, details)
    - `NotFoundError`, `ValidationAPIError`, `ConflictError` subclasses
    - Global exception handler registered in FastAPI app
    - Consistent JSON response: `{"error": {"status_code": X, "message": Y, "error_code": Z, "details": ...}}`
  - Pattern: Copy from automation-service's `app/middleware/exception_handler.py` (the canonical implementation)
  - Register in `backend/app/main.py` via `app.add_exception_handler(APIError, api_error_handler)`
  - Update ONE route as example to use the new exceptions (e.g., `routes/sensors.py` topology validation)

  **Must NOT do**:
  - Do NOT change existing route handlers yet (Task 7 handles that)
  - Do NOT break existing API responses — existing `HTTPException` calls should still work during transition
  - Do NOT change the middleware file from automation-service beyond adapting to backend's module paths

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Copy + adapt existing middleware file, register in main.py. Well-defined pattern from automation-service.
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 5)
  - **Blocks**: Tasks 7, 8 (must have middleware before replacing excepts)
  - **Blocked By**: None (after Wave 1 completes)

  **References**:
  - `Infrastructure/automation-service/app/middleware/exception_handler.py` — Canonical implementation to copy/adapt
  - `Infrastructure/automation-service/app/middleware/__init__.py` — Middleware package pattern
  - `Infrastructure/backend/app/main.py` — Where to register the exception handler
  - `Infrastructure/backend/app/routes/sensors.py` — Example route to update first

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Backend returns structured error for 404
    Tool: Bash (curl)
    Preconditions: Backend running, middleware registered, example route updated
    Steps:
      1. curl -s -w "\n%{http_code}" http://mothernode:8000/api/sensors/Nonexistent/main/live
      2. Assert: HTTP status is 404 or 400
      3. Assert: response contains "error" key
      4. Assert: response.error contains "status_code", "message", "error_code" fields
    Expected Result: Structured JSON error response matching automation-service format
    Evidence: curl response body + status code

  Scenario: Backend starts without errors after middleware addition
    Tool: Bash (systemctl)
    Preconditions: Fix applied
    Steps:
      1. sudo systemctl restart cea-backend
      2. sleep 3
      3. systemctl is-active cea-backend → assert "active"
    Expected Result: Service healthy
    Evidence: systemctl status
  ```

  **Commit**: YES
  - Message: `feat(backend): add structured error middleware (APIError, NotFoundError, etc.)`
  - Files: `Infrastructure/backend/app/middleware/exception_handler.py`, `Infrastructure/backend/app/middleware/__init__.py`, `Infrastructure/backend/app/main.py`

---

- [x] 7. **Replace bare excepts in automation-service repos** (Issue #1 — automation-service) ✅

  **What to do**:
  - Replace 42 bare `except Exception:` across 19 files with specific exception types
  - Priority order (highest risk last):
    1. Repository files (`repositories/*.py`) — `except Exception:` → `except (asyncpg.PostgresError, ValueError) as e:`
    2. Route files (`routes/*.py`) — `except Exception:` → specific HTTP-related exceptions + log
    3. Control files (`control/*.py`) — careful, control loop adjacent, only where safe
    4. StateManager (`state/__init__.py`) — HIGH RISK, defer to Task 9
  - For database operations: catch `asyncpg.PostgresError` + `asyncio.TimeoutError`
  - For Redis operations: catch `redis.RedisError` + `ConnectionError`
  - For JSON/parsing: catch `json.JSONDecodeError` + `ValueError`
  - For I2C hardware: catch `IOError` + `OSError` with specific error messages
  - Every replacement must include `logger.error(f"Failed to X: {e}", exc_info=True)` for traceability

  **Must NOT do**:
  - Do NOT touch StateManager's bare excepts (deferred to Task 9 — control loop adjacent)
  - Do NOT change exception handling logic — only narrow the caught exception types
  - Do NOT add new bare excepts
  - Do NOT remove the `logger.error` calls that should be there

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Systematic find-and-replace across 19 files, each replacement follows a template
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 8)
  - **Parallel Group**: Wave 3 (with Task 8)
  - **Blocks**: None
  - **Blocked By**: Task 6 (need middleware pattern as reference for error types)

  **References**:
  - `Infrastructure/automation-service/app/repositories/devices.py` — Example: `except Exception:` → `except asyncpg.PostgresError as e:`
  - `Infrastructure/automation-service/app/repositories/schedules.py` — Same pattern
  - `Infrastructure/automation-service/app/routes/lights.py` — Route handlers with bare excepts
  - `Infrastructure/automation-service/app/middleware/exception_handler.py` — Reference for which exception types to use
  - `Infrastructure/shared/infra_logging.py` — `get_logger(__name__)` pattern for logging

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Zero bare excepts in automation-service repos and routes
    Tool: Bash (grep)
    Preconditions: Fix applied (excluding state/__init__.py)
    Steps:
      1. grep -rn "except Exception:" Infrastructure/automation-service/app/repositories/ --include="*.py"
      2. Assert: output is empty
      3. grep -rn "except Exception:" Infrastructure/automation-service/app/routes/ --include="*.py"
      4. Assert: output is empty
    Expected Result: No bare excepts in repos or routes
    Evidence: grep output (empty)

  Scenario: Automation service restarts and control loop healthy
    Tool: Bash (systemctl + curl)
    Preconditions: Fix applied, service restarted
    Steps:
      1. sudo systemctl restart automation-service
      2. sleep 5
      3. systemctl is-active automation-service → assert "active"
      4. curl -s http://mothernode:8001/api/health | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"
      5. journalctl -u automation-service --since "30 sec ago" --no-pager | grep -i "error\|traceback" → assert empty
    Expected Result: Service healthy, no errors, health endpoint returns ok
    Evidence: curl response + systemctl status
  ```

  **Commit**: YES (multiple commits grouped by directory)
  - Message: `fix(automation): replace bare excepts with specific exception types in repositories`
  - Files: All modified repository files
  - Follow-up commit: `fix(automation): replace bare excepts with specific exception types in routes`

---

- [x] 8. **Add repository pattern to backend** (Issues #3 + #10) ✅

  **What to do**:
  - Create `Infrastructure/backend/app/repositories/` directory with:
    1. `base.py` — `BaseRepository` with asyncpg pool injection
    2. `sensor_repository.py` — Extract sensor queries from `database.py` and `routes/sensors.py`
    3. `config_repository.py` — Extract config queries from `routes/config.py`
  - Move raw SQL from routes into repository methods:
    - `SensorRepository.get_sensor_data(location, cluster, sensor_type, time_range)` → wraps `_pick_aggregate_tier` logic
    - `SensorRepository.get_live_sensors(location, cluster)` → wraps Redis stream read fallback
    - `ConfigRepository.get_config(location, cluster)` → wraps YAML config read
  - Backend routes call repository methods instead of raw `conn.fetch()`
  - Preserve the aggregate tier selection logic (`_pick_aggregate_tier`) — critical for query performance
  - Preserve the sensor name pattern generation (`_sensor_name_patterns`) — already uses `shared.cluster_topology`

  **Must NOT do**:
  - Do NOT change query logic or SQL — extract as-is, don't refactor
  - Do NOT add caching (keep it simple, caching can be added later)
  - Do NOT delete `backend/app/database.py` — keep `DatabaseManager` for pool management
  - Do NOT touch soil-sensor-service or weather-service
  - Do NOT change the aggregate tier ladder (hard-coded SQL generation optimized for TimescaleDB)

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
    - Reason: Extracting SQL from routes into repositories requires understanding query semantics, preserving TimescaleDB optimizations, and ensuring backward compatibility
  - **Skills**: None needed (codebase-adjacent work)

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 7)
  - **Parallel Group**: Wave 3 (with Task 7)
  - **Blocks**: None
  - **Blocked By**: Task 6 (need middleware in place)

  **References**:
  - `Infrastructure/automation-service/app/repositories/base.py` — Canonical repository pattern from automation-service
  - `Infrastructure/automation-service/app/repositories/sensors.py` — Reference for sensor data repository
  - `Infrastructure/backend/app/database.py:274-295` — `_sensor_name_patterns()` (aggregate tier + sensor naming logic to preserve)
  - `Infrastructure/backend/app/routes/sensors.py` — Sensor routes with inline SQL to extract
  - `Infrastructure/backend/app/routes/config.py` — Config routes with YAML read to extract
  - `Infrastructure/shared/cluster_topology.py` — Used by sensor repository for naming patterns

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Sensor data API returns identical results after repository extraction
    Tool: Bash (curl)
    Preconditions: Fix applied, backend restarted. Known test data exists.
    Steps:
      1. curl -s http://mothernode:8000/api/sensors/Flower%20Room/back/live
      2. Assert: HTTP status 200
      3. Assert: response is valid JSON with sensor data fields (dry_bulb, humidity, co2, etc.)
      4. curl -s "http://mothernode:8000/api/sensors/Flower%20Room/back/history?hours=1"
      5. Assert: HTTP status 200
      6. Assert: response contains array of timestamped measurements
    Expected Result: Identical API behavior, same data structure
    Evidence: curl response bodies (both endpoints)

  Scenario: No raw conn.fetch/execute in backend routes after extraction
    Tool: Bash (grep)
    Preconditions: Fix applied
    Steps:
      1. grep -rn "conn\.\(fetch\|execute\)" Infrastructure/backend/app/routes/ --include="*.py"
      2. Assert: output is empty (0 matches)
    Expected Result: All database access goes through repositories
    Evidence: grep output (empty)
  ```

  **Commit**: YES (multiple commits)
  - Message: `refactor(backend): add repository pattern for sensor and config data access`
  - Files: `Infrastructure/backend/app/repositories/*.py`, modified routes

---

- [x] 9. **Type StateManager** (Issue #7) ✅

  **What to do**:
  - Add concrete types to `Infrastructure/automation-service/app/state/__init__.py`:
    - Replace `dict[str, Any]` return types with `TypedDict` or dataclass types where possible
    - Replace `Any` parameters with union types (`str | int | float | dict | None`)
    - Add type overloads for `get()` method (returns `T | None`)
    - Create `PIDParams`, `RampState`, `AlarmDict`, `FailsafeState` TypedDict types
  - Do NOT change any method logic — only add type annotations
  - Verify all 30+ methods still work identically
  - Run `pyright` or `mypy` against the file to verify type correctness

  **Must NOT do**:
  - Do NOT change any method behavior, timing, or control flow
  - Do NOT add new dependencies
  - Do NOT change Redis interaction patterns
  - Do NOT modify `_serialize_for_redis` or `_deserialize_redis_payload` functions
  - Do NOT touch the TTL eviction logic

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: StateManager is 1048 lines, 30+ methods, control-loop adjacent. Types must be correct without changing behavior. Requires understanding Redis data shapes and in-memory caching.
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential — Wave 4 (after all other tasks)
  - **Blocks**: Task 10
  - **Blocked By**: All Wave 1-3 tasks complete

  **References**:
  - `Infrastructure/automation-service/app/state/__init__.py` — Full file (1048 lines, 30+ methods)
  - `Infrastructure/automation-service/app/redis/schema.py` — Redis key schema (informs return types for get/set methods)
  - `Infrastructure/automation-service/app/models/config_schema.py` — Pydantic models (reference for type patterns in this service)
  - `Infrastructure/shared/cluster_topology.py` — TypedDict/dataclass patterns used elsewhere in the project

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: StateManager still works after typing (smoke test)
    Tool: Bash (python)
    Preconditions: Fix applied
    Steps:
      1. cd Infrastructure/automation-service
      2. python -c "
  from app.state import StateManager
  sm = StateManager(default_ttl=10.0, max_entries=100)
  import asyncio
  async def test():
      await sm.set('test:key', 'value')
      v = await sm.get('test:key')
      assert v == 'value', f'Expected value, got {v}'
      stats = await sm.get_stats()
      assert stats['total_entries'] == 1
      print('StateManager OK')
  asyncio.run(test())
  "
      3. Assert: exit code 0, stdout contains "StateManager OK"
    Expected Result: Core get/set/stats methods work unchanged
    Evidence: stdout + exit code

  Scenario: Automation service starts and control loop healthy after StateManager typing
    Tool: Bash (systemctl + curl)
    Preconditions: Fix applied, service restarted
    Steps:
      1. sudo systemctl restart automation-service
      2. sleep 5
      3. systemctl is-active automation-service → assert "active"
      4. curl -s http://mothernode:8001/api/health | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"
      5. journalctl -u automation-service --since "30 sec ago" --no-pager | grep -i "typeerror\|attributeerror" → assert empty
    Expected Result: Service healthy, control loop running, no type errors
    Evidence: curl response + systemctl status
  ```

  **Commit**: YES
  - Message: `refactor(state): add comprehensive type annotations to StateManager`
  - Files: `Infrastructure/automation-service/app/state/__init__.py`

---

- [x] 10. **Final verification — all services healthy** (Integration check) ✅

  **What to do**:
  - Restart all affected services in correct order:
    ```bash
    sudo systemctl restart redis-server postgresql
    sudo systemctl restart can-processor soil-sensor-service weather-service
    sudo systemctl restart cea-backend automation-service
    ```
  - Run comprehensive health check:
    ```bash
    curl -s http://mothernode:8001/api/health  # automation
    curl -s http://mothernode:8000/api/health   # backend (if endpoint exists)
    curl -s http://mothernode:8003/api/health   # weather (if endpoint exists)
    ```
  - Verify control loop running: check `/api/health` includes `control_loop_running: true` and `tick_interval` <5s
  - Verify frontend loads: Playwright navigates to dashboard, checks for sensor data
  - Run the cluster topology CI script to confirm sync
  - Run final grep for bare excepts, emoji, console.log — all must return 0

  **Must NOT do**:
  - Do NOT deploy to production without the user's explicit direction
  - Do NOT modify any files — this is verification only

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification only — restart services, run curl commands, check output
  - **Skills**: `playwright` (for frontend verification)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential — Wave 4 (after Task 9)
  - **Blocks**: Nothing (final task)
  - **Blocked By**: Task 9 (all other tasks complete)

  **References**:
  - `deploy.sh` — Service restart order reference
  - `ARCHITECTURE.md` — Service dependency graph
  - `Infrastructure/scripts/validate_cluster_topology.py` — Created in Task 3

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: All services healthy after all fixes
    Tool: Bash (systemctl + curl)
    Preconditions: All fixes applied, production deploy done (user-initiated)
    Steps:
      1. systemctl is-active automation-service cea-backend can-processor soil-sensor-service weather-service
      2. Assert: all return "active"
      3. curl -s http://mothernode:8001/api/health | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"
      4. sleep 2
      5. Repeat curl → assert still "ok" (control loop stable)
    Expected Result: All 5 services active, automation health ok twice (stable)
    Evidence: systemctl status + curl responses

  Scenario: Final code quality checks pass
    Tool: Bash (grep)
    Preconditions: All fixes applied
    Steps:
      1. grep -rn "except Exception:" Infrastructure/ --include="*.py" | grep -v ".venv" | grep -v "__pycache__" | grep -v "state/__init__.py" → assert empty (state may have deliberate bare excepts at module level for optional imports)
      2. grep -rn "⚠️\|✅\|🛑" Infrastructure/backend/app/ → assert empty
      3. grep -rn "console\.\(log\|warn\|error\)" Infrastructure/frontend/src/ --include="*.ts" --include="*.tsx" | grep -v "logger.ts" → assert empty
      4. python Infrastructure/scripts/validate_cluster_topology.py → exit 0
    Expected Result: All quality checks pass
    Evidence: grep output (all empty) + script exit code

  Scenario: Frontend dashboard loads and shows data
    Tool: Playwright (playwright skill)
    Preconditions: All services running, frontend built and served
    Steps:
      1. Navigate to: http://mothernode:8001
      2. Wait for: .dashboard or sensor value visible (timeout: 10s)
      3. Assert: page contains sensor data (temperature, humidity values)
      4. Assert: no console errors (check browser console)
      5. Screenshot: .sisyphus/evidence/task-10-dashboard-healthy.png
    Expected Result: Dashboard renders with live sensor data, no errors
    Evidence: .sisyphus/evidence/task-10-dashboard-healthy.png
  ```

  **Commit**: NO (verification only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `fix(backend): remove emoji from log messages` | `backend/app/main.py`, `backend/app/background_tasks.py` | grep for emoji → 0 |
| 2 | `fix(frontend): replace console.log with centralized logger` | `useSystemStatus.ts`, `useSensorPolling.ts`, `env.ts` | grep for console.log → 0 |
| 3 | `feat(ci): add cluster topology sync validation script` | `scripts/validate_cluster_topology.py` | script exits 0 |
| 4 | `refactor(automation): standardize imports to absolute style` | ~15 files in routes/, services/, control/ | python -c "from app.main import app" |
| 5 | `docs(config): clarify ConfigLoader design rationale` | `shared/config.py` (possibly) | grep for rationale |
| 6 | `feat(backend): add structured error middleware` | `backend/app/middleware/exception_handler.py`, `main.py` | curl 404 → structured JSON error |
| 7 | `fix(automation): replace bare excepts in repositories` | 11+ repo files | grep for except Exception → 0 in repos |
| 7b | `fix(automation): replace bare excepts in routes` | ~8 route files | grep for except Exception → 0 in routes |
| 8 | `refactor(backend): add repository pattern for data access` | `backend/app/repositories/*.py`, modified routes | grep for conn.fetch in routes → 0 |
| 9 | `refactor(state): add comprehensive type annotations to StateManager` | `state/__init__.py` | control loop health check |
| 10 | — | — | Full integration verification |

---

## Success Criteria

### Verification Commands
```bash
# Bare except check (excluding state/__init__.py which has module-level fallback)
grep -rn "except Exception:" Infrastructure/ --include="*.py" | grep -v ".venv" | grep -v "__pycache__" | grep -v "state/__init__.py"
# Expected: 0 matches

# Emoji check
grep -rn "⚠️\|✅\|🛑\|🔄\|ℹ️" Infrastructure/backend/app/
# Expected: 0 matches

# console.log check
grep -rn "console\.\(log\|warn\|error\|debug\)" Infrastructure/frontend/src/ --include="*.ts" --include="*.tsx" | grep -v "logger.ts"
# Expected: 0 matches

# Relative imports check
grep -rn "from \.\." Infrastructure/automation-service/app/ --include="*.py" | grep -v "__init__.py" | grep -v ".venv"
# Expected: 0 matches

# Cluster topology sync
python Infrastructure/scripts/validate_cluster_topology.py
# Expected: exit 0

# Control loop health
curl -s http://mothernode:8001/api/health | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'"
# Expected: exit 0
```

### Final Checklist
- [x] All 42 bare excepts gone (or justified in state/__init__.py module-level fallback)
- [x] All emoji removed from backend logs
- [x] All console.log replaced with logger in frontend
- [x] Cluster topology CI script exists and passes
- [x] Import style standardized across automation-service
- [x] ConfigLoader design documented
- [x] Backend has structured error middleware
- [x] Backend has repository pattern for data access
- [x] StateManager fully typed
- [ ] All 5 services healthy (`systemctl is-active` all active) — pending deploy
- [ ] Control loop latency ≤5s — pending deploy
- [x] Frontend dashboard builds successfully
