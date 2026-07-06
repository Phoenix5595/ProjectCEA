# dfr-relay-channel-fixes - Work Plan

## TL;DR (For humans)

**What you'll get:** The DFR dimming-channel panel stops showing relay numbers and relay GPIO-pin labels — each slot will read `DFR{board} · CH{channel}` instead. Dead relays that were ON before a restart will no longer show as ON — the service will sync its Redis relay cache to the actual hardware state (all OFF) after startup's force-off step.

**Why this approach:** DFR boards and relay boards are on separate I2C buses and serve different functions (dimming vs on/off). The old code imported relay-specific helpers into the DFR panel, coupling two hardware planes that must stay separate. For the stale-state bug, the MCP23017 driver forces all relays OFF at startup but nothing reconciles the Redis cache — `container.py` is the correct injection point because both the driver and Redis client are available there, between the force-off (line 119) and the control loop start (line 199).

**What it will NOT do:** It will not populate the empty `device_registry` table (separate bug), change any backend API, change any frontend API contract, create a new `dfrViewModel.ts` module, add simulation-mode handling (none exists), or place Redis logic inside the MCP23017 driver (which has no Redis client).

**Effort:** Short
**Risk:** Low — two isolated changes (frontend label, backend Redis reconciliation), both well-bounded with TDD.
**Decisions to sanity-check:** DFR label text = `DFR{board_id} · CH{ch}`. Reconciliation goes in `container.py` not `mcp23017.py`.

Your next move: approve, or run a high-accuracy review. Full execution detail follows below.

---

> TL;DR (machine): Short, Low risk — relabel DFR slots with DFR identity (remove relay imports), reconcile Redis relay cache with post-init hardware in container.py startup.

## Scope
### Must have
1. DFR0971 dimming-channel slot labels show `DFR{board_id} · CH{ch}` — no relay numbers, no GPIO pin labels.
2. Remove-light confirmation warning does not show the wrong relay number (no relay-context in DFR panel).
3. `DfrBoardsPanel.tsx` no longer imports from `relayViewModel.ts`.
4. Existing test `DfrBoardsPanel.test.tsx:116` updated to match the new warning text.
5. `container.py` post-init step writes actual MCP23017 hardware state to Redis `cea:relay:channels` and clears `cea:relay:timestamps`, between the `all_off()` block (line 119) and `background_tasks.start()` (line 199).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- MUST NOT create a new `dfrViewModel.ts` module — use inline label only (it's one template literal).
- MUST NOT place Redis logic inside `MCP23017Driver` — the driver has no Redis client and must stay a pure hardware abstraction (`mcp23017.py:32-37` signature is `(i2c_bus, i2c_address, active_low)`).
- MUST NOT add simulation-mode handling — probe failure is FATAL (`container.py:258-267`); there is no simulation path (`mcp23017.py:61` always creates `smbus2.SMBus`).
- MUST NOT change any backend API endpoint (routes, response shapes).
- MUST NOT change any frontend API contract (apiClient methods, response types).
- MUST NOT populate or modify the `device_registry` table (separate plan).
- MUST NOT clear `cea:relay:manual_override:{channel}` keys (out of scope — note as known limitation).
- MUST NOT use `getRelayNumber` or `getRelayPinLabel` anywhere in `DfrBoardsPanel.tsx` after the change.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD — write failing test first, then fix.
- Evidence: .omo/evidence/task-<N>-dfr-relay-channel-fixes.{txt,md}

## Execution strategy
### Parallel execution waves
- **Wave A (parallel):** Task 1 (frontend DFR label fix + test update) + Task 2 (backend Redis reconciliation + test). Independent — different files, different test suites.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (frontend label fix) | — | F1–F4 | 2 |
| 2 (backend Redis reconciliation) | — | F1–F4 | 1 |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Frontend: relabel DFR slots with DFR identity + fix remove-light warning + update existing test
  What to do:
  - **Write the test FIRST** (TDD): update `DfrBoardsPanel.test.tsx` to assert the NEW label text. The existing test at line 116 asserts `/This will also unbind relay/` — change it to assert the new text.
  - In `DfrBoardsPanel.tsx:579`, replace `R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}` with `DFR{board.board_id} · CH{ch}`. The `board` variable is available in the `.map((board) => ...)` scope (line 551). `ch` is the DFR dimming channel 0|1 (the `renderChannel` parameter). This is an inline template literal — NO new helper function, NO new module.
  - In `DfrBoardsPanel.tsx:737`, replace `This will also unbind relay R{getRelayNumber(ch)}. Remove light?` with `Remove light? (Its relay will also be unbound.)`. This removes the wrong relay number from the warning without importing `getRelayNumber`. The relay unbinding is handled by the backend cascade transparently — the DFR panel does not need to know which relay channel it is.
  - In `DfrBoardsPanel.tsx:8`, remove the import: `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'`. Verify no other code in `DfrBoardsPanel.tsx` references `getRelayNumber` or `getRelayPinLabel` (grep the file after editing).
  - Update existing test at `DfrBoardsPanel.test.tsx:116`: change `expect(screen.getByText(/This will also unbind relay/))` to `expect(screen.getByText(/Remove light/)).toBeInTheDocument()` and add `expect(screen.queryByText(/unbind relay R/)).not.toBeInTheDocument()` to lock the regression.
  - Add a new test asserting the DFR slot label shows `DFR0 · CH0` (matching the mock data: board_id=0, channel=0) and does NOT contain `R{` or `GPA` or `GPB`. Use `screen.getByTestId('dfr-slot-0-0')` (already exists at line 575 of DfrBoardsPanel.tsx) and check the label text within it.
  Must NOT do:
  - MUST NOT create `dfrViewModel.ts` — inline the label.
  - MUST NOT use `getRelayNumber` or `getRelayPinLabel` anywhere in the file after editing.
  - MUST NOT change the `assignment` type, the `DfrAssignment` interface, or any API call.
  - MUST NOT add `bound_relay_channel` to the `DfrAssignment` type — the DFR assignments API does not return it. The remove-light warning uses no relay number; it just says "relay will also be unbound" without specifying which channel.
  Parallelization: Wave A | Blocked by: — | Blocks: F1–F4 | Can parallelize with: 2
  References:
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:8` (import to remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:551` (`.map((board) => ...)` scope — `board.board_id` available)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:575` (`data-testid={`dfr-slot-${board.board_id}-${ch}`}` — use in test)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:579` (label to replace)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:737` (remove-light warning to replace)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:31` (`getRelayNumber` — source of wrong relay number)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:82` (`getRelayPinLabel` — source of wrong GPIO label)
  - `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx:1-124` (existing test file — MUST update line 116)
  - `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx:20-38` (mock data: board_id=0, assignment on ch0 → label should be `DFR0 · CH0`)
  Acceptance criteria (agent-executable):
  - `cd Infrastructure/frontend && npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` → all tests pass.
  - `grep -n "getRelayNumber\|getRelayPinLabel" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` → no matches (import removed, no usage).
  - `grep -n "DFR.*CH" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` → at least one match at the label line.
  QA scenarios:
  - Happy: `npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` — all 4 tests pass (3 existing updated + 1 new label test). Evidence: `.omo/evidence/task-1-dfr-relay-channel-fixes.txt`
  - Failure: revert the label fix only, re-run tests — the new label test fails (no `DFR0 · CH0` found) and the updated remove-warning test fails (old `/unbind relay/` text still present). Evidence: `.omo/evidence/task-1-failure-dfr-relay-channel-fixes.txt`
  Commit: Y | fix(frontend): relabel DFR slots with DFR identity, fix remove-light warning

- [x] 2. Backend: reconcile Redis relay state with post-init hardware in container.py
  What to do:
  - **Write the test FIRST** (TDD): create `Infrastructure/automation-service/tests/test_relay_redis_reconciliation.py`. Mock `smbus2.SMBus` (or mock `MCP23017Driver.get_all_channels` to return `[False]*16`). Assert that after the container's `all_off()` + reconciliation step, `automation_redis.get(RELAY_CHANNELS)` returns a JSON list of 16 `false` values and `automation_redis.get(RELAY_TIMESTAMPS)` returns a 16-element list of `null`.
  - In `container.py`, add a reconciliation block AFTER the `all_off()` block (line 119) and BEFORE the `background_tasks.start()` call (line 199). The block should be around line 120, after the `except` at line 119. Note: `self.automation_redis` is available from container init (used at line 158, but initialized earlier). Read `self.mcp23017.get_all_channels()` → write JSON to `cea:relay:channels` (key: `RELAY_CHANNELS` from `app/redis/schema.py:76`). Write `[null]*16` JSON to `cea:relay:timestamps` (key: `RELAY_TIMESTAMPS`). Wrap in try/except — failure is a warning (service continues, similar to the `all_off()` block above).
  - The reconciliation block should look approximately like:
    ```python
    # Reconcile Redis relay state with actual post-init hardware truth.
    # MCP23017 _initialize_hardware() + all_off() forced all relays OFF;
    # the Redis cache may still hold pre-restart ON states. Pin the cache
    # to hardware truth before the control loop starts writing its own state.
    if self.mcp23017 is not None and self.automation_redis is not None:
        try:
            import json as _json
            from app.redis.schema import RELAY_CHANNELS, RELAY_TIMESTAMPS
            hw_states = self.mcp23017.get_all_channels()
            self.automation_redis.set(
                RELAY_CHANNELS, _json.dumps([bool(s) for s in hw_states])
            )
            self.automation_redis.set(
                RELAY_TIMESTAMPS, _json.dumps([None] * 16)
            )
            logger.info(
                "Redis relay state reconciled with hardware: "
                f"{sum(hw_states)} ON / {len(hw_states) - sum(hw_states)} OFF"
            )
        except Exception as e:
            logger.warning(f"Failed to reconcile relay Redis state: {e}")
    ```
  - Verify the `automation_redis` client has a `.set(key, value)` method. If it only exposes `redis_client.setex`, adapt accordingly (check `app/redis_client.py`). The `hardware_batch.py` write path uses the raw redis client — match the established pattern.
  Must NOT do:
  - MUST NOT place this code inside `MCP23017Driver._initialize_hardware()` — the driver has no Redis client.
  - MUST NOT place this code after `background_tasks.start()` (line 199) — race window with hardware_batch.py:476.
  - MUST NOT clear `cea:relay:manual_override:{channel}` keys — out of scope.
  - MUST NOT change the `/api/hardware/relays/state` endpoint read-order (Redis-first, hardware-fallback) — the cache will now be correct post-init.
  - MUST NOT add simulation-mode handling.
  Parallelization: Wave A | Blocked by: — | Blocks: F1–F4 | Can parallelize with: 1
  References:
  - `Infrastructure/automation-service/app/container.py:106` (`_init_hardware()` call)
  - `Infrastructure/automation-service/app/container.py:111-119` (`all_off()` block — reconciliation goes AFTER this, around line 120)
  - `Infrastructure/automation-service/app/container.py:199` (`background_tasks.start()` — reconciliation goes BEFORE this)
  - `Infrastructure/automation-service/app/container.py:158` (`self.automation_redis` assertion — confirms it's available at this point)
  - `Infrastructure/automation-service/app/hardware/mcp23017.py:68-89` (`_initialize_hardware()` — writes 0xFF to GPIOA/GPIOB; does NOT update `_channel_states`)
  - `Infrastructure/automation-service/app/hardware/mcp23017.py:240-253` (`get_all_channels()` — reads hardware via `get_channel()`, falls back to `_channel_states` on error)
  - `Infrastructure/automation-service/app/hardware/mcp23017.py:276-278` (`all_off()` — calls `set_all_channels([False]*16)`)
  - `Infrastructure/automation-service/app/routes/hardware.py:176-229` (`relay_state` endpoint — reads Redis FIRST, fallback to `get_all_channels()`)
  - `Infrastructure/automation-service/app/redis/schema.py:76` (`RELAY_CHANNELS = "cea:relay:channels"`)
  - `Infrastructure/automation-service/app/redis/schema.py` (find `RELAY_TIMESTAMPS` constant — same file)
  - `Infrastructure/automation-service/app/control/hardware_batch.py:476,500` (control loop writes `RELAY_CHANNELS` and `RELAY_TIMESTAMPS` to Redis on every tick — this is why reconciliation must run BEFORE the loop starts)
  - `Infrastructure/automation-service/app/redis_client.py` (check `.set()` method signature)
  Acceptance criteria (agent-executable):
  - `cd Infrastructure/automation-service && .venv/bin/python -m pytest tests/test_relay_redis_reconciliation.py -v` → test passes.
  - Run: `redis-cli GET "cea:relay:channels"` → should be `[false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false]` after restart (will require a service restart to verify end-to-end; the unit test verifies the logic).
  QA scenarios:
  - Happy: unit test passes — after reconciliation, Redis `RELAY_CHANNELS` matches `get_all_channels()` (all OFF post-init), `RELAY_TIMESTAMPS` is 16-element null array. Evidence: `.omo/evidence/task-2-dfr-relay-channel-fixes.txt`
  - Failure: remove the reconciliation block, set stale Redis value before test, re-run — test fails (Redis still has stale ON values). Evidence: `.omo/evidence/task-2-failure-dfr-relay-channel-fixes.txt`
  Commit: Y | fix(automation): reconcile Redis relay state with post-init hardware

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verify every todo has References + agent-executable Acceptance + happy/failure QA + Commit line; dependency matrix consistent; Metis findings C1-C3 + M1-M10 folded in: C1 (test updated), C2 (reconciliation in container.py not driver), C3 (both force-off operations accounted for), M1 (race window pinned before line 199), M2 (no bound_relay_channel import needed — warning has no relay number), M4 (test mocks get_all_channels), M6 (no simulation handling), M9 (inline label, no new module).
- [ ] F2. Code quality review — verify `npx vitest run` passes for frontend, `pytest` passes for backend; ruff clean; pyright 0 errors on changed files; no bare excepts (except the reconciliation try/except which follows the `all_off()` pattern at container.py:118).
- [ ] F3. Real manual QA — (1) after deploy, load the DFR panel — every slot says `DFR{n} · CH{0|1}`, no `R{n}` or `GPA` labels; (2) click remove on an assigned light — warning says `Remove light? (Its relay will also be unbound.)`, no relay number; (3) after service restart, `redis-cli GET "cea:relay:channels"` returns all-false; (4) relay matrix in frontend shows all IDLE (no stale ON).
- [ ] F4. Scope fidelity — verify Must-NOT-have list honored: no `dfrViewModel.ts` created; no Redis logic in `mcp23017.py`; no API changes; no `device_registry` population; no `cea:relay:manual_override:*` key clearing; `DfrBoardsPanel.tsx` has zero `getRelayNumber`/`getRelayPinLabel` references.

## Commit strategy
- Task 1 + Task 2 in Wave A (parallel, independent). Each commits independently when its tests pass. Deploy after both merge (single deploy with `deploy.sh`).

## Success criteria
1. DFR panel slots show `DFR{board_id} · CH{ch}` — verified by test + manual check.
2. Remove-light warning shows no relay number — verified by test.
3. `DfrBoardsPanel.tsx` has zero imports or references to `getRelayNumber` or `getRelayPinLabel`.
4. After service restart, Redis `cea:relay:channels` matches actual hardware (all OFF post-init) — verified by `redis-cli GET` + unit test.
5. All tests pass: `npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` + `pytest tests/test_relay_redis_reconciliation.py`.
