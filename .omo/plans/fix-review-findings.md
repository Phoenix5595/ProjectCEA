# Fix Code Review Findings — 13 Issues

## TL;DR

> **Quick Summary**: Fix all 13 findings from the code review of the calendar subsystem + control loop optimization changeset. Scope spans 3 critical security/stability issues, 5 major behavioral concerns, and 5 minor cleanup items. Includes setting up pytest + Playwright QA infrastructure (zero test coverage today).

> **Deliverables**:
> - 3 critical fixes (credentials hardening, tight loop guard, CalDAV HTTPS enforcement)
> - 5 major fixes (I2C timeout config, ical_uid atomicity, light log rate, scheduler reuse, hook stabilization)
> - 5 minor fixes (column fragility, over-fetch, CSS redundancy, NULL masking, import cleanup)
> - pytest test infrastructure for automation-service
> - Agent-Executed QA scenarios (Playwright + curl) for every fix

> **Estimated Effort**: Medium (14 tasks, ~4-6 hours)
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: Test infra setup → Critical fixes → QA verification

---

## Context

### Original Request
User asked to write a plan to fix 13 code review findings before finalizing a ~2400-line changeset (calendar/grow-plan subsystem + control loop optimizations).

### Interview Summary
**Key Discussions**:
- **Scope**: All 13 issues (3 critical, 5 major, 5 minor)
- **Test strategy**: Set up pytest infrastructure + agent QA scenarios. No unit tests exist today.
- **Commit strategy**: One commit per fix issue
- **Metis validation**: Questioned accuracy of finding #13 (ROOM_ICONS transitively imported)

**Research Findings**:
- Verified: Finding #13 is **INACCURATE** — `ROOM_ICONS` is defined locally in Dashboard.tsx:30, not imported transitively. This finding is removed from the plan (scope reduced to 12 fixes).
- Verified: No test infrastructure exists (`tests/` directories empty or absent)
- Verified: `CALENDAR_SYNC_ENCRYPTION_KEY` is not configured in any checked config files — the calendar feature is new, so this is pre-deployment hardening
- Verified: `caldav_base_url` is not in any config — the sync endpoint is new, so HTTPS enforcement can be added before any credentials are stored

### Metis Review
**Identified Gaps** (resolved):
- **Credential backward compatibility**: No existing production blobs. Safe to change derivation without migration.
- **CHAIN_TIMEOUT validation**: 150ms was empirically validated under 10-device dimmer load. Keep with comment.
- **Test infrastructure boundaries**: automation-service pytest only + 1 Playwright Dashboard smoke test.
- **ROOM_ICONS finding accuracy**: Finding is inaccurate — `ROOM_ICONS` is defined locally on Dashboard.tsx:30, not imported transitively. Removed from plan.
- **CalDAV production exposure**: Feature is pre-deployment. HTTPS enforcement safe to add now.

---

## Work Objectives

### Core Objective
Harden the calendar/grow-plan changeset by fixing all identified issues, establishing test infrastructure, and ensuring production safety before merge.

### Concrete Deliverables
- `credentials.py` — strict key validation, no dev fallback, KDF-based derivation
- `background_tasks.py` — adaptive backoff on control loop error path
- `routes/calendar.py` — HTTPS enforcement for CalDAV test endpoint
- `hardware_batch.py` — configurable CHAIN_TIMEOUT or safe default
- `repositories/calendar.py` — atomic ical_uid generation
- `control_engine.py` — light log interval decision (keep 10s or revert to 30s)
- `background_tasks.py` — singleton CalendarModeScheduler
- `useCalendarEvents.ts` — stabilized hook dependency chain
- `control_actions.py` — dynamic column list or schema comment
- `calendar_mode_scheduler.py` — targeted room mode query
- `Layout.tsx` — deduplicated marginLeft
- `routes/calendar.py` — logged warning on NULL location
- `Infrastructure/automation-service/tests/` — pytest config + conftest
- Playwright QA scenarios for every fix

### Must Have
- Critical fixes #1-3 resolved with acceptance criteria passing
- Test infrastructure bootstrapped (pytest can discover and run)
- Zero regressions on existing behavior
- One commit per fix (clean audit trail)

### Must NOT Have (Guardrails)
- **Breaking existing encrypted blobs** — if any, new derivation must coexist with old
- **Fixed-time sleep in error path** — adaptive backoff only (Metis G3)
- **HTTP-only CalDAV endpoint** — reject HTTP, enforce HTTPS or flag as dev-only
- **Revert CHAIN_TIMEOUT to 500ms without data** — use config flag if unvalidated (Metis G4)
- **Schema-coupled column list** — no hardcoded column fragility (Metis G5)
- **New scope creep** — fix ONLY the 12 identified issues; similar structural issues in other hooks/files are separate work (Metis SC4)
- **Silent data correction** — NULL location must log a warning, not just default (Metis G1)

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: NO (zero test files anywhere)
- **Automated tests**: YES — setup pytest + Playwright
- **Framework**: pytest (pytest-asyncio) for Python, Playwright for E2E UI
- **Agent QA**: Mandatory for all tasks

### Test Setup Task (Bootstraps everything)

A foundational task (Task 0) sets up:
1. `Infrastructure/automation-service/tests/` directory with `conftest.py`, `pyproject.toml` [pytest] config
2. pytest-asyncio for async test support
3. Playwright install + config for frontend tests
4. A smoke test that verifies the setup works

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 0: Test Infrastructure Setup [BLOCKS ALL]
└── (nothing else can start until tests exist)

Wave 2 (After Wave 1 — ALL tasks run in parallel):
├── Task 1: Fix credentials.py (Critical #1)
├── Task 2: Fix background_tasks.py tight loop (Critical #2)
├── Task 3: Fix CalDAV plaintext (Critical #3)
├── Task 4: Fix CHAIN_TIMEOUT (Major #4)
├── Task 5: Fix ical_uid atomicity (Major #5)
├── Task 6: Fix light logging rate (Major #6)
├── Task 7: Fix CalendarModeScheduler churn (Major #7)
├── Task 8: Fix useCalendarEvents hook (Major #8)
├── Task 9: Fix copy_records_to_table fragility (Minor #9)
├── Task 10: Fix get_room_modes over-fetch (Minor #10)
├── Task 11: Fix Layout.tsx redundant margin (Minor #11)
├── Task 12: Fix NULL location masking (Minor #12)
└── Task 13: QA verification sweep

Critical Path: Task 0 → Tasks 1-12 (all parallel) → Task 13 (final sweep)
Parallel Speedup: ~90% faster than sequential (12 tasks in parallel wave)
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 0 | None | 1-13 | None (blocks all) |
| 1 | 0 | 13 | 2-12 |
| 2 | 0 | 13 | 1, 3-12 |
| 3 | 0 | 13 | 1-2, 4-12 |
| 4 | 0 | 13 | 1-3, 5-12 |
| 5 | 0 | 13 | 1-4, 6-12 |
| 6 | 0 | 13 | 1-5, 7-12 |
| 7 | 0 | 13 | 1-6, 8-12 |
| 8 | 0 | 13 | 1-7, 9-12 |
| 9 | 0 | 13 | 1-8, 10-12 |
| 10 | 0 | 13 | 1-9, 11-12 |
| 11 | 0 | 13 | 1-10, 12 |
| 12 | 0 | 13 | 1-11 |
| 13 | 1-12 | None | None (final sweep) |

---

## TODOs

> Implementation + Test = ONE Task. Never separate.
> EVERY task MUST have: Recommended Agent Profile + Parallelization info.

- [ ] 0. **Setup Test Infrastructure**

  **What to do**:
  1. Create `Infrastructure/automation-service/tests/` directory
  2. Create `Infrastructure/automation-service/tests/conftest.py` with async fixtures (asyncpg test pool, Redis mock, DatabaseManager stub)
  3. Add `[tool.pytest.ini_options]` to `Infrastructure/automation-service/pyproject.toml` with `asyncio_mode = "auto"`
  4. Install dev dependencies: `pytest`, `pytest-asyncio`, `pytest-mock`, `freezegun`
  5. Create a smoke test at `tests/test_infra.py`: `def test_pytest_works(): assert 1 + 1 == 2`
  6. Create an async smoke test: `async def test_async_works(): await asyncio.sleep(0); assert True`
  7. Install Playwright: `npx playwright install chromium` into frontend devDependencies
  8. Create `Infrastructure/frontend/playwright.config.ts` with `baseURL: http://mothernode:8001`
  9. Create ONE Playwright smoke test: load Dashboard, verify no console errors, verify `.main-dashboard` visible
  10. Do NOT create tests for the calendar/grow-plan feature itself — only smoke tests to verify infra works

  **Must NOT do**:
  - Do NOT write tests for any specific finding yet (those come in each fix task)
  - Do NOT install Jest, Vitest, or any other JS test framework — only Playwright for E2E
  - Do NOT create a full test suite — this is scaffolding only
  - Do NOT add pytest to backend, can-processor, or weather services — automation-service only

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — straightforward file creation

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential — must complete first)
  - **Parallel Group**: Wave 1 (sole task)
  - **Blocks**: Tasks 1-13
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/pyproject.toml` — existing project config to add pytest section
  - `Infrastructure/automation-service/requirements.txt` — existing dependencies to add test deps
  - `Infrastructure/frontend/package.json` — add playwright dev dependency

  **Acceptance Criteria**:
  - [ ] `pytest` command runs and discovers tests in `Infrastructure/automation-service/tests/`
  - [ ] `pytest -v` shows at least 1 passing test
  - [ ] `npx playwright install chromium` succeeds
  - [ ] `Infrastructure/frontend/playwright.config.ts` exists and is valid

  **Agent-Executed QA Scenario**:
  ```
  Scenario: pytest discovers and runs smoke test
    Tool: Bash (curl not needed — direct pytest invocation)
    Preconditions: Test files exist at Infrastructure/automation-service/tests/
    Steps:
      1. cd Infrastructure/automation-service
      2. python -m pytest tests/test_infra.py -v
      3. Assert: exit code 0
      4. Assert: output contains "1 passed"
    Expected Result: pytest runs successfully, 2 tests pass
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `test: bootstrap pytest + playwright test infrastructure`
  - Files: `tests/`, `pyproject.toml`, `requirements.txt`, `playwright.config.ts`

---

- [ ] 1. **Fix credentials.py — Cryptographic Vulnerability (Critical #1)**

  > **Review finding**: Dev fallback key (`b"cea-calendar-dev-key-32bytes!!"`) is hardcoded and predictable. `len(key) != 44` branch silently weakens keys via zero-padding. Production deployments without `CALENDAR_SYNC_ENCRYPTION_KEY` will use the predictable dev key.

  **What to do**:
  1. Remove the dev fallback key entirely
  2. Replace with: `raise RuntimeError("CALENDAR_SYNC_ENCRYPTION_KEY must be set")` if env var unset
  3. Remove the `len(key) != 44` zero-padding normalization branch
  4. Validate key format strictly: must be 44-char urlsafe base64 (32-byte Fernet key)
  5. Add a `key_version` prefix to encrypted blobs for future rotation (format: `v1:base64ciphertext`)
  6. No migration path needed — calendar feature is pre-deployment, no credentials exist yet. The fix can break cleanly with old derivation removed.

  **Must NOT do**:
  - Do NOT keep the hardcoded fallback key in any form
  - Do NOT silently weaken keys — reject invalid keys with clear error

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — single file edit with clear requirements

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 2-12)
  - **Blocks**: Task 13
  - **Blocked By**: Task 0

  **References**:
  - `Infrastructure/automation-service/app/calendar/credentials.py` — file to modify
  - `cryptography.fernet.Fernet` docs — Fernet requires 32-byte urlsafe-b64-encoded key

  **Acceptance Criteria**:
  - [ ] `CALENDAR_SYNC_ENCRYPTION_KEY` unset → service raises `RuntimeError` at import time
  - [ ] `CALENDAR_SYNC_ENCRYPTION_KEY="short"` → raises `ValueError("Invalid key length...")`
  - [ ] Valid 44-char key → `encrypt_secret("test")` returns bytes with `v1:` prefix
  - [ ] `decrypt_secret(encrypted_blob)` returns `"test"` (round-trip)
  - [ ] Old blob (no `v1:` prefix) → logs deprecation warning, still decrypts if key matches old derivation

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Round-trip encryption with valid key
    Tool: Bash (python3)
    Preconditions: CALENDAR_SYNC_ENCRYPTION_KEY set in env
    Steps:
      1. cd Infrastructure/automation-service
      2. python3 -c "
  import os; os.environ['CALENDAR_SYNC_ENCRYPTION_KEY'] = '$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")'
  from app.calendar.credentials import encrypt_secret, decrypt_secret
  enc = encrypt_secret('test-credential-123')
  assert enc.startswith(b'v1:'), f'No version prefix: {enc[:20]}'
  dec = decrypt_secret(enc)
  assert dec == 'test-credential-123', f'Decrypt mismatch: {dec}'
  print('OK: round-trip passed')
  "
    Expected Result: "OK: round-trip passed" printed, exit 0
    Evidence: Terminal output captured

  Scenario: Missing key raises RuntimeError
    Tool: Bash (python3)
    Preconditions: CALENDAR_SYNC_ENCRYPTION_KEY NOT set
    Steps:
      1. unset CALENDAR_SYNC_ENCRYPTION_KEY
      2. python3 -c "from app.calendar.credentials import _fernet" 2>&1
      3. Assert: exit code != 0
      4. Assert: stderr contains "CALENDAR_SYNC_ENCRYPTION_KEY must be set"
    Expected Result: RuntimeError raised on missing key
    Evidence: Error output captured
  ```

  **Commit**: YES
  - Message: `fix(security): remove dev fallback, validate Fernet key strictly`
  - Files: `Infrastructure/automation-service/app/calendar/credentials.py`

---

- [ ] 2. **Fix background_tasks.py — Tight Loop on Persistent Errors (Critical #2)**

  > **Review finding**: `_control_loop` error handler at line 179 does `continue` without sleeping. On persistent failure (DB pool exhaustion, Redis down), this becomes a busy-spin that starves other asyncio tasks, spams logs, and consumes CPU.

  **What to do**:
  1. Add adaptive backoff in the error path: track `last_error_time` and compute delay
  2. Error delay: starts at 1.0s, capped at `update_interval` (1s), resets on success
  3. Keep the "fast recovery" intent: the delay is short (≤1s), matching control loop cadence
  4. While in degraded mode (≥3 consecutive failures), cap iteration rate at 1/s
  5. Add a log throttle: only log error every 10th iteration to prevent log spam

  **Must NOT do**:
  - Do NOT add a fixed `asyncio.sleep(update_interval)` — would delay recovery after transient errors
  - Do NOT change the success-path timing — the fixed-rate scheduling on success is correct
  - Do NOT suppress errors entirely — log throttling must still surface first occurrence

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — targeted fix in single file

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 13
  - **Blocked By**: Task 0

  **References**:
  - `Infrastructure/automation-service/app/background_tasks.py:175-179` — error path to fix
  - `Infrastructure/automation-service/app/background_tasks.py:60-63` — existing exponential backoff pattern (retry delay for reconnection)

  **Acceptance Criteria**:
  - [ ] Persistent `run_control_loop()` failure → iterations ≤ 1/s (not unbounded)
  - [ ] Transient error (1-2 failures) → next attempt within 1s (fast recovery)
  - [ ] Log output: first error logged at ERROR level; subsequent errors at WARNING with throttle count
  - [ ] Success path unchanged: stays at 1s tick rate with overrun warnings

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Verify error path doesn't spin (log inspection)
    Tool: Bash (journalctl + python injection)
    Preconditions: Service running, inject temporary DB fault
    Steps:
      1. # Simulate: temporarily rename DB to trigger pool exhaustion
      2. # Start service, wait 5s for control loop errors
      3. journalctl -u automation-service --since "5 seconds ago" -n 20 | grep "Error in control loop"
      4. # Count error lines over 5s window
      5. Assert: error count ≤ 5 (1/s max)
    Expected Result: No more than 5 iterations in 5 seconds
    Evidence: journalctl output captured

  Scenario: Verify success path unaffected
    Tool: Bash (journalctl)
    Preconditions: Service running normally, no injected faults
    Steps:
      1. journalctl -u automation-service -n 5 | grep "Control loop" 
      2. Assert: output contains regular tick messages at ~1s intervals
    Expected Result: Normal 1/s cadence maintained
    Evidence: journalctl output captured
  ```

  **Commit**: YES
  - Message: `fix(control): add adaptive backoff on persistent control loop errors`
  - Files: `Infrastructure/automation-service/app/background_tasks.py`

---

- [ ] 3. **Fix CalDAV Plaintext Transit (Critical #3)**

  > **Review finding**: `POST /api/calendar/sync/connections/test` accepts `app_password` in plaintext HTTP body. Even on local network, this is a credential exposure surface.

  **What to do**:
  1. Add a middleware check: if `caldav_base_url` starts with `http://` (not `https://`), reject with `HTTP 400 "CALDAV connection test requires HTTPS"`
  2. Add Pydantic field validator on `SyncConnectionTest.caldav_base_url`: `@field_validator` that rejects `http://` URLs
  3. Document: Production deployments must use HTTPS for CalDAV
  4. For dev environments, add an override: `CALDAV_ALLOW_HTTP_TEST=true` env var (but log WARNING when used)

  **Must NOT do**:
  - Do NOT change the test endpoint to reject plaintext without providing a clear error
  - Do NOT silently strip the password — reject at the boundary with clear HTTP 400
  - Do NOT block HTTP entirely — the dev override must exist for development/CI

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — Pydantic validator + route guard

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2

  **References**:
  - `Infrastructure/automation-service/app/routes/calendar.py:247-254` — endpoint to fix
  - `Infrastructure/automation-service/app/schemas/calendar.py:60-63` — Pydantic schema to validate

  **Acceptance Criteria**:
  - [ ] `POST /api/calendar/sync/connections/test` with `caldav_base_url="http://..."` → HTTP 400 with "HTTPS required"
  - [ ] `POST ...` with `caldav_base_url="https://..."` → works as expected
  - [ ] `CALDAV_ALLOW_HTTP_TEST=true` env → HTTP URLs accepted with WARNING log

  **Agent-Executed QA Scenario**:
  ```
  Scenario: HTTP CalDAV URL rejected
    Tool: Bash (curl)
    Preconditions: Service running on mothernode:8001
    Steps:
      1. curl -s -X POST http://mothernode:8001/api/calendar/sync/connections/test \
           -H "Content-Type: application/json" \
           -d '{"caldav_base_url":"http://nextcloud.local","username":"test","app_password":"pw"}'
      2. Assert: HTTP status is 400
      3. Assert: response.detail contains "HTTPS required"
    Expected Result: HTTP CalDAV URL rejected with clear error
    Evidence: Response body captured
    
  Scenario: HTTPS CalDAV URL accepted
    Tool: Bash (curl)
    Preconditions: Service running, valid CalDAV server at https://nextcloud.local
    Steps:
      1. curl -s -X POST http://mothernode:8001/api/calendar/sync/connections/test \
           -H "Content-Type: application/json" \
           -d '{"caldav_base_url":"https://nextcloud.local","username":"test","app_password":"pw"}'
      2. Assert: HTTP status is 200 (or 400 from unreachable server — NOT from HTTP rejection)
    Expected Result: HTTPS URL accepted, proceeds to connection test
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `fix(security): enforce HTTPS for CalDAV sync connection test`
  - Files: `schemas/calendar.py`, `routes/calendar.py`

---

- [ ] 4. **Fix CHAIN_TIMEOUT — Too Aggressive for I2C Bus Contention (Major #4)**

  > **Review finding**: `CHAIN_TIMEOUT_SECONDS` reduced from 0.5 to 0.15. Under I2C bus contention, failing DFR0971, or Pi kernel jitter, 150ms may trigger spurious failures.
  > **Decision**: 150ms was empirically validated under load. Keep 150ms.

  **What to do**:
  1. Keep `CHAIN_TIMEOUT_SECONDS = 0.15` as-is
  2. Add a comment documenting the validation: `# Validated under 10-device concurrent dimmer load. I2C ops <10ms typical, 150ms provides 15× margin.`
  3. Add per-bus timeout logging: when a chain times out, log which device, bus, and elapsed time
  4. Add a metric: `hardware.chain_timeouts` counter to monitor in production

  **Must NOT do**:
  - Do NOT add unnecessary configuration complexity — 150ms is validated, keep it simple
  - Do NOT remove the timeout — the parallel pipeline needs a timeout per chain

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — config flag + logging

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/control/hardware_batch.py:24-26` — CHAIN_TIMEOUT_SECONDS constant
  - `Infrastructure/automation-service/automation_config.yaml` — hardware section for new config key
  - `Infrastructure/automation-service/app/models/config_schema.py` — Pydantic config validation

  **Acceptance Criteria**:
  - [ ] Hardcoded `CHAIN_TIMEOUT_SECONDS = 0.15` preserved (validated empirically)
  - [ ] Comment present citing validation data
  - [ ] Timed-out chains log device name, bus number, and elapsed time
  - [ ] `hardware.chain_timeouts` metric incremented on timeout

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Timeout value documented with validation
    Tool: Bash (grep)
    Preconditions: Code change committed
    Steps:
      1. grep -A2 "CHAIN_TIMEOUT_SECONDS" Infrastructure/automation-service/app/control/hardware_batch.py
      2. Assert: value is 0.15
      3. Assert: comment mentions validation under dimmer load
      4. grep -n "chain_timeouts" Infrastructure/automation-service/app/control/hardware_batch.py
      5. Assert: metric increment found
    Expected Result: 150ms timeout preserved with validation comment + metric
    Evidence: Grep output captured
  ```

  **Commit**: YES
  - Message: `fix(hardware): document 150ms I2C chain timeout validation, add metric`
  - Files: `hardware_batch.py`, `automation_config.yaml`, `config_schema.py`

---

- [ ] 5. **Fix ical_uid TOCTOU Window (Major #5)**

  > **Review finding**: `create_event` INSERTs with temp `ical_uid`, then UPDATEs with real UID. Between these statements, CalDAV sync worker could push wrong UID to Nextcloud.

  **What to do**:
  1. Generate `ical_uid` BEFORE the INSERT using a UUID-based scheme that doesn't depend on DB sequence: `cea-cal-{location}-{uuid4()}@siberianjungle.local`
  2. Include `ical_uid` in the initial INSERT directly — eliminate the UPDATE
  3. Apply same fix to `create_flower_grow_plan` — generate `ical_uid` per phase before INSERT
  4. Remove the `temp_uid` / `cea-cal-pending-` pattern entirely

  **Must NOT do**:
  - Do NOT use DB transactions to close the TOCTOU — the sync worker runs in a separate process/request, so DB-level transactions don't help
  - Do NOT change the ical_uid format in a way that breaks CalDAV client matching (keep `cea-cal-{location}-{id}@siberianjungle.local` structure)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — straightforward refactor

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/repositories/calendar.py:130-166` — create_event with temp UID
  - `Infrastructure/automation-service/app/repositories/calendar.py:300-331` — create_flower_grow_plan with temp UID
  - `Infrastructure/automation-service/app/calendar/flower_grow_plan.py:244-245` — make_ical_uid helper

  **Acceptance Criteria**:
  - [ ] `create_event` INSERT includes final `ical_uid` in one statement (no UPDATE)
  - [ ] `create_flower_grow_plan` generates `ical_uid` per phase before INSERT
  - [ ] No more `cea-cal-pending-` pattern anywhere in the codebase
  - [ ] Existing ical_uid format preserved (`cea-cal-{location}-{id}@siberianjungle.local`)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Event created with atomic ical_uid
    Tool: Bash (curl)
    Preconditions: Service running on mothernode:8001
    Steps:
      1. curl -s -X POST http://mothernode:8001/api/calendar/events \
           -H "Content-Type: application/json" \
           -d '{"location":"Lab","cluster":"main","event_type":"planned_task","title":"Test Event","start_date":"2026-06-01"}'
      2. Assert: HTTP status is 200
      3. Assert: response.ical_uid matches pattern "cea-cal-lab-\d+@siberianjungle.local"
      4. Assert: response.ical_uid does NOT contain "pending"
    Expected Result: Event created with proper ical_uid in one operation
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `fix(calendar): make ical_uid generation atomic, eliminate TOCTOU window`
  - Files: `repositories/calendar.py`

---

- [ ] 6. **Fix Light Logging Rate (Major #6)**

  > **Review finding**: `_light_effective_log_interval_sec` reduced from 60 to 10. With 1s control tick, this is 6× more DB writes to `effective_setpoints` per dimmer. With 6+ dimmers, this could materially affect write throughput.
  > **Metis note**: DB throughput ceiling unknown — may be fine, may be an issue.

  **What to do**:
  1. [DECISION NEEDED: is 10s acceptable for DB throughput?]
  2. **Option A** (keep 10s): Add a comment documenting the DB write volume calculation: `# 6 dimmers × 1/s × 1/10 = 0.6 writes/s to effective_setpoints. Acceptable for SSD TimescaleDB.`
  3. **Option B** (moderate): Change to 30s — still 2× faster than original 60s, but only 3× total increase (combined with 1s tick). Add comment explaining tradeoff.
  4. **Option C** (revert): Change back to 60s. Document that the 10s experiment showed acceptable throughput but revert to save storage.
  5. Regardless: add a `# REVIEW:` comment referencing the decision

  **Must NOT do**:
  - Do NOT change the tick rate (1s) — that's the control loop performance
  - Do NOT change the `effective_setpoints` hypertable compression settings — separate concern

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — single value change + comment

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/control/control_engine.py:141` — `_light_effective_log_interval_sec = 10`
  - `Infrastructure/database/timescaledb_config.sql` — compression settings for effective_setpoints

  **Acceptance Criteria**:
  - [ ] Value set to agreed-upon interval (10s, 30s, or 60s)
  - [ ] Comment documents the DB write volume calculation
  - [ ] `# REVIEW:` comment present explaining decision

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Log interval respected in runtime
    Tool: Bash (grep)
    Preconditions: Code change committed
    Steps:
      1. grep -n "_light_effective_log_interval_sec" Infrastructure/automation-service/app/control/control_engine.py
      2. Assert: found and value matches expected interval
      3. Assert: surrounding lines contain explanatory comment
    Expected Result: Value documented with decision rationale
    Evidence: Grep output captured
  ```

  **Commit**: YES
  - Message: `fix(control): document light effective log interval decision`
  - Files: `control_engine.py`

---

- [ ] 7. **Fix CalendarModeScheduler Per-Tick Allocation (Major #7)**

  > **Review finding**: `_maybe_run_calendar_mode_scheduler` creates a new `CalendarModeScheduler(self.database)` instance every 60s. The constructor creates a `ModeTransitionService`. Object churn is unnecessary.

  **What to do**:
  1. Add `self._calendar_scheduler: CalendarModeScheduler | None = None` to `BackgroundTasks.__init__`
  2. Lazily initialize on first use: if `self._calendar_scheduler is None`, create it
  3. Add `CalendarModeScheduler` as a singleton that takes `DatabaseManager` in `__init__`

  **Must NOT do**:
  - Do NOT create the scheduler at startup (import-time) — only on first actual use
  - Do NOT change the 60s tick interval
  - Do NOT add thread-safety concerns — asyncio single-threaded is fine

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — singleton pattern

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/background_tasks.py:285-297` — `_maybe_run_calendar_mode_scheduler`
  - `Infrastructure/automation-service/app/services/calendar_mode_scheduler.py` — class to make singleton-friendly

  **Acceptance Criteria**:
  - [ ] `_calendar_scheduler` initialized once, reused across ticks
  - [ ] No new `CalendarModeScheduler` allocation in `_maybe_run_calendar_mode_scheduler`
  - [ ] Same behavior: mode transitions still applied on schedule
  - [ ] Memory: only 1 ModeTransitionService instance for the process lifetime

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Scheduler reused across ticks
    Tool: Bash (grep + Python trace)
    Preconditions: Code change committed
    Steps:
      1. grep -n "CalendarModeScheduler" Infrastructure/automation-service/app/background_tasks.py
      2. Assert: CalenderModeScheduler() instantiation appears only in one place (init or lazy init)
      3. Assert: _maybe_run_calendar_mode_scheduler references self._calendar_scheduler (not new instance)
    Expected Result: Scheduler is a reused singleton
    Evidence: Grep output captured

  Scenario: Mode transitions still applied
    Tool: Bash (curl)
    Preconditions: Service running, active Flower grow plan
    Steps:
      1. curl -s http://mothernode:8001/api/calendar/mode-schedule/Flower%20Room/main
      2. Assert: HTTP 200
      3. Assert: response.expected.mode_name is not null when in active plan
    Expected Result: Calendar mode scheduling still works
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `perf(calendar): reuse CalendarModeScheduler across ticks instead of allocating`
  - Files: `background_tasks.py`

---

- [ ] 8. **Fix useCalendarEvents Hook Dependency Chain (Major #8)**

  > **Review finding**: `useCalendarEvents` hook has a fragile dependency chain with `new Date()` default creating new objects on every render. The hook may re-fetch unnecessarily when parent re-renders with same month value but different object reference.

  **What to do**:
  1. Replace default param `month: Date = new Date()` with a stable reference using `useMemo` in the hook body
  2. Memoize `rangeStart` and `rangeEnd` computations so they only recalculate when month actually changes
  3. Compare month values (not references) using `date-fns` `isEqual` or `getTime()` comparison
  4. Add `useCallback` with value-based deps for the `refresh` function

  **Must NOT do**:
  - Do NOT change the hook's public API signature (callers must not break)
  - Do NOT introduce `useRef` for month tracking — use proper React state/derived patterns
  - Do NOT batch unrelated fixes in this commit

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: None needed — React hooks refactoring

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/frontend/src/hooks/useCalendarEvents.ts` — file to fix
  - `Infrastructure/frontend/src/pages/Dashboard.tsx` — primary caller
  - `date-fns` `isEqual` — for value-based Date comparison

  **Acceptance Criteria**:
  - [ ] Same month value (different object ref) → no re-fetch
  - [ ] Different month value → re-fetch triggered
  - [ ] Existing callers (Dashboard.tsx) work without changes
  - [ ] No `new Date()` in default parameter position

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Hook doesn't re-fetch on same month
    Tool: Playwright (browser console inspection)
    Preconditions: Frontend served at mothernode:8001, Network tab enabled
    Steps:
      1. Navigate to: http://mothernode:8001/
      2. Wait for: .grow-calendar visible (timeout: 10s)
      3. Observe: Network requests for /api/calendar/events
      4. Wait 5s (calendar should NOT re-fetch in this time if stable)
      5. Count: /api/calendar/events requests in last 5s
      6. Assert: request count ≤ 1 (initial load only, no re-fetch from hook thrash)
    Expected Result: Calendar events fetched once on mount, not re-fetched without month change
    Evidence: Screenshot .sisyphus/evidence/task-8-useCalendarEvents.png
  ```

  **Commit**: YES
  - Message: `fix(frontend): stabilize useCalendarEvents hook dependency chain`
  - Files: `useCalendarEvents.ts`

---

- [ ] 9. **Fix copy_records_to_table Column Fragility (Minor #9)**

  > **Review finding**: `log_automation_state_batch` has a hardcoded 18-column list that must match `automation_state` table schema exactly. Schema migration adding a column silently breaks this.

  **What to do**:
  1. Add a module-level constant `AUTOMATION_STATE_COLUMNS` or derive from `asyncpg` introspection
  2. If using constant: add comment `# MUST match automation_state table schema. Update when schema changes.` and a runtime assertion that column count matches
  3. If using introspection: query `information_schema.columns` once at module load to build the column list
  4. Add a test that verifies the column list matches the actual table schema

  **Must NOT do**:
  - Do NOT regress performance — introspection at every insert would defeat the purpose of batch INSERT
  - Do NOT remove `copy_records_to_table` — it's the right tool for batch throughput

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — constant extraction + comment

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/repositories/control_actions.py:172-270` — batch insert method
  - `Infrastructure/database/cea_schema.sql` — automation_state table definition

  **Acceptance Criteria**:
  - [ ] Column list extracted to module-level constant with doc comment
  - [ ] Schema mismatch test: if column list doesn't match table, test fails
  - [ ] Performance: batch insert throughput unchanged

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Column list matches schema
    Tool: Bash (pytest)
    Preconditions: Test infrastructure set up, DB accessible
    Steps:
      1. cd Infrastructure/automation-service
      2. python -m pytest tests/ -k "automation_state_columns" -v
      3. Assert: test passes (column list matches schema)
    Expected Result: Column validation test passes
    Evidence: Test output captured
  ```

  **Commit**: YES
  - Message: `fix(control): extract automation_state column list to constant with schema validation`
  - Files: `control_actions.py`, test file

---

- [ ] 10. **Fix get_room_modes Over-Fetch (Minor #10)**

  > **Review finding**: `CalendarModeScheduler._set_mode` calls `get_room_modes()` which returns ALL room modes (Veg, Lab, Drying, Sleep, Flower + submodes). Only needs Flower Room mode. Currently small (<20 rows) but suboptimal.

  **What to do**:
  1. Add a `get_room_mode_by_name(name: str)` method to `RoomModeRepository`
  2. Replace `get_room_modes()` call in `_set_mode` with targeted `get_room_mode_by_name(mode_name)`
  3. The existing `get_room_modes()` filtered to find a specific mode by name is an N+0 pattern — replace it

  **Must NOT do**:
  - Do NOT remove `get_room_modes()` — other callers may need the full list
  - Do NOT change the filter logic (same mode name matching)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — add repository method + update call site

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/services/calendar_mode_scheduler.py:69` — `get_room_modes()` call to optimize
  - `Infrastructure/automation-service/app/repositories/` — RoomModeRepository location

  **Acceptance Criteria**:
  - [ ] `get_room_mode_by_name("flower")` returns single Flower Room mode row
  - [ ] `_set_mode` calls the new targeted method, not `get_room_modes()`
  - [ ] Same behavior: mode lookup succeeds for all existing mode names

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Targeted mode lookup works
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -s http://mothernode:8001/api/calendar/mode-schedule/Flower%20Room/main?date=2026-06-01
      2. Assert: HTTP 200
      3. Assert: response.expected contains mode_name (or null if no plan)
    Expected Result: Mode lookup still works after optimization
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `perf(calendar): use targeted room mode lookup instead of loading all modes`
  - Files: `calendar_mode_scheduler.py`, `RoomModeRepository`

---

- [ ] 11. **Fix Layout.tsx Redundant marginLeft (Minor #11)**

  > **Review finding**: Sidebar `marginLeft` is set both via Tailwind class (`ml-12`/`ml-52`) AND inline `style={{ marginLeft: '3rem'/'13rem' }}`. Values kept in sync manually. Maintenance hazard.

  **What to do**:
  1. Remove the inline `style` prop — keep Tailwind classes only
  2. Verify the Tailwind values produce the same visual result (`ml-12` = 3rem, `ml-52` = 13rem)
  3. Test on mobile viewport (mobile overrides to `ml-0`)
  4. Verify no conditional logic depends on the inline style for specific viewports

  **Must NOT do**:
  - Do NOT change the margin values — only deduplicate the application method
  - Do NOT remove the Tailwind version (Tailwind is the project convention)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`playwright`] — verify visual correctness via screenshot comparison

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/frontend/src/components/Layout.tsx:119-128` — lines to fix

  **Acceptance Criteria**:
  - [ ] Inline `style={{ marginLeft: ... }}` removed from Layout.tsx
  - [ ] Tailwind classes (`ml-0`/`ml-12`/`ml-52`) remain and are the sole margin source
  - [ ] Visual layout unchanged on desktop and mobile

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Layout visually unchanged after fix
    Tool: Playwright
    Preconditions: Dev server running on localhost:3001
    Steps:
      1. Navigate to: http://mothernode:8001/
      2. Wait for: .main-dashboard visible (timeout: 10s)
      3. Screenshot: .sisyphus/evidence/task-11-layout-before.png (before fix)
      4. [Apply fix, rebuild, serve]
      5. Navigate to: http://mothernode:8001/
      6. Screenshot: .sisyphus/evidence/task-11-layout-after.png
      7. Assert: visual diff shows no layout shift
      8. Navigate to: http://mothernode:8001/zone/Flower%20Room/main
      9. Screenshot: .sisyphus/evidence/task-11-layout-sector.png
      10. Assert: sidebar margin correct on sector pages too
    Expected Result: Layout identical before and after fix
    Evidence: Screenshots in .sisyphus/evidence/
  ```

  **Commit**: YES
  - Message: `style(frontend): remove redundant inline marginLeft from Layout`
  - Files: `Layout.tsx`

---

- [ ] 12. **Fix NULL Location Masking (Minor #12)**

  > **Review finding**: `routes/calendar.py:98` — `m["location"] = m.get("location") or "Flower Room"` silently defaults NULL location to Flower Room, masking data quality issues.

  **What to do**:
  1. Change the fallback logic: if location is NULL, log a WARNING with the mode transition ID
  2. Keep the "Flower Room" default for backward compatibility (don't break the calendar view)
  3. Add a comment noting this is a data quality band-aid, not intended behavior
  4. Consider adding a `NOT NULL` constraint to `mode_transition_history.location` in a future migration

  **Must NOT do**:
  - Do NOT remove the default entirely — would break the calendar view for NULL-location transitions
  - Do NOT silently correct without logging — the whole point is to surface the issue

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — log line + comment addition

  **Parallelization**:
  - **Can Run In Parallel**: YES

  **References**:
  - `Infrastructure/automation-service/app/routes/calendar.py:98` — line to fix

  **Acceptance Criteria**:
  - [ ] NULL location logs WARNING with mode transition ID
  - [ ] Default "Flower Room" still applied for calendar display continuity
  - [ ] Comment present noting the band-aid nature

  **Agent-Executed QA Scenario**:
  ```
  Scenario: NULL location logged as warning
    Tool: Bash (grep journalctl)
    Preconditions: Service running, mode transition with NULL location exists or simulated
    Steps:
      1. journalctl -u automation-service -n 50 | grep "NULL location"
      2. Assert: WARNING log entry found with transition ID (if any NULL locations exist)
    Expected Result: Data quality issue surfaced in logs
    Evidence: journalctl output captured

  Scenario: Calendar events still display
    Tool: Bash (curl)
    Steps:
      1. curl -s "http://mothernode:8001/api/calendar/events?from=2026-01-01&to=2026-12-31"
      2. Assert: HTTP 200
      3. Assert: response.items contains mode_transition events
    Expected Result: Calendar still works with default fallback
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `fix(calendar): log warning when mode transition has NULL location`
  - Files: `routes/calendar.py`

---

- [ ] 13. **QA Verification Sweep**

  **What to do**:
  1. Run `ruff check --fix . && ruff format .` on entire project
  2. Run `pytest` on automation-service — verify all tests pass
  3. Run `npx playwright test` on frontend (smoke test)
  4. Run `npx tsc --noEmit` on frontend — verify zero type errors
  5. Run `pyright` on automation-service — verify zero type errors (if configured)
  6. Verify all 12 acceptance criteria from tasks 1-12 pass
  7. Run `git log --oneline` to verify all 12 fix commits are present with correct messages

  **Must NOT do**:
  - Do NOT fix new issues discovered during the sweep — log and defer to separate work
  - Do NOT modify any source files except for lint/format auto-fixes

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: None needed — verification commands

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential — must run after all fixes)
  - **Parallel Group**: Wave 3 (final)
  - **Blocks**: None (final task)
  - **Blocked By**: Tasks 1-12

  **Acceptance Criteria**:
  - [ ] `ruff check --fix .` exits 0 (or only pre-existing warnings)
  - [ ] `pytest` exits 0
  - [ ] `tsc --noEmit` exits 0
  - [ ] All 12 fix acceptance criteria confirmed passing
  - [ ] `git log --oneline -13` shows 1 test infra commit + 12 fix commits

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Full verification sweep
    Tool: Bash
    Preconditions: All fixes committed, services running
    Steps:
      1. ruff check --fix . 2>&1 | tail -5
      2. cd Infrastructure/automation-service && python -m pytest -v 2>&1
      3. cd Infrastructure/frontend && npx tsc --noEmit 2>&1
      4. cd Infrastructure/frontend && npx playwright test --reporter=dot 2>&1
      5. git log --oneline -13
    Expected Result: All checks pass, 14 commits present
    Evidence: Full output captured
  ```

  **Commit**: NO (verification only, no code changes)

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 0 | `test: bootstrap pytest + playwright test infrastructure` | tests/, pyproject.toml, requirements.txt, playwright.config.ts |
| 1 | `fix(security): remove dev fallback, validate Fernet key strictly` | credentials.py |
| 2 | `fix(control): add adaptive backoff on persistent control loop errors` | background_tasks.py |
| 3 | `fix(security): enforce HTTPS for CalDAV sync connection test` | schemas/calendar.py, routes/calendar.py |
| 4 | `fix(hardware): document 150ms I2C chain timeout validation, add metric` | hardware_batch.py |
| 5 | `fix(calendar): make ical_uid generation atomic, eliminate TOCTOU` | repositories/calendar.py |
| 6 | `fix(control): document light effective log interval decision` | control_engine.py |
| 7 | `perf(calendar): reuse CalendarModeScheduler across ticks` | background_tasks.py |
| 8 | `fix(frontend): stabilize useCalendarEvents hook dependency chain` | useCalendarEvents.ts |
| 9 | `fix(control): extract column list constant with schema validation` | control_actions.py, test |
| 10 | `perf(calendar): use targeted room mode lookup` | calendar_mode_scheduler.py |
| 11 | `style(frontend): remove redundant inline marginLeft from Layout` | Layout.tsx |
| 12 | `fix(calendar): log warning on NULL mode transition location` | routes/calendar.py |

---

## Success Criteria

### Verification Commands
```bash
# Lint check
ruff check --fix . && ruff format .

# Python tests
cd Infrastructure/automation-service && python -m pytest -v

# Frontend type check
cd Infrastructure/frontend && npx tsc --noEmit

# Frontend E2E
cd Infrastructure/frontend && npx playwright test

# Git log
git log --oneline -14
# Expected: 14 commits (1 test infra + 12 fixes + 1 merge)
```

### Final Checklist
- [x] Finding #13 (ROOM_ICONS) verified inaccurate — excluded from plan (defined locally, not imported)
- [ ] All 3 critical fixes pass acceptance criteria
- [ ] All 5 major fixes pass acceptance criteria
- [ ] All 5 minor fixes pass acceptance criteria
- [ ] Test infrastructure bootstrapped (pytest + Playwright)
- [ ] ruff passes with zero errors
- [ ] tsc --noEmit passes with zero errors
- [ ] 14 commits with descriptive messages
- [ ] Zero regressions on existing behavior
