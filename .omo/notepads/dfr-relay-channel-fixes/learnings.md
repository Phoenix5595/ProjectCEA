# DFR/Relay Channel Fixes — Learnings

## Task 1: DFR dimming-channel panel identity fix (2026-07-06)

### What was wrong
`DfrBoardsPanel.tsx` displayed MCP23017 relay identity on DFR0971 dimming channels:
- Slot label used `R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}` — relay numbers
  (e.g. `R2`) and GPIO pin labels (`GPA0`/`GPB0`) belong to the MCP23017 relay
  expander, NOT the DFR0971 dimmer. For ch0 this rendered as `R2 · GPA0`.
- Remove confirmation warned `This will also unbind relay R{getRelayNumber(ch)}.`
  — used the DFR channel index as a relay number (wrong namespace).
- `relayViewModel.ts` (`CHANNEL_TO_RELAY` map) was imported into DFR code at all.

### Fix (inline, no new module)
- Slot label -> `DFR{board.board_id} · CH{ch}` (inline template literal).
  `board` comes from the `.map((board) => ...)` at line 551; `ch` is the
  `renderChannel` parameter (0|1). No helper function, no `dfrViewModel.ts`.
- Remove warning -> `Remove light? (Its relay will also be unbound.)`
  (no relay number embedded — the side-effect is described generically).
- Removed `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'`
  entirely. Grep confirms zero references to either function or the module
  in `DfrBoardsPanel.tsx`.

### TDD flow
1. Updated `DfrBoardsPanel.test.tsx:116` assertion from
   `/This will also unbind relay/` to
   `/Remove light\? \(Its relay will also be unbound\.\)/`.
2. Added new test `shows DFR board_id and channel label instead of relay identity`
   that queries `screen.getByTestId('dfr-slot-0-0')` (testid already existed at
   line 575), reads the `.text-xs.font-semibold` child, and asserts:
   - textContent === `'DFR0 · CH0'` (mock data: board_id=0, ch0 assignment)
   - does NOT contain `R{`, `GPA`, or `GPB`.
3. Ran tests BEFORE the code fix -> 2 failed (red), confirming the test catches
   the bug. `R2 · GPA0` != `DFR0 · CH0`.
4. Applied the 3 edits (import removal, label, warning).
5. Ran tests AFTER -> 4/4 pass (green). `tsc --noEmit` clean.

### Key facts for downstream tasks
- The `data-testid="dfr-slot-{board_id}-{ch}"` attribute already existed at
  line 575 — no DOM change was needed for the new test.
- The slot label lives in a `<div className="text-xs font-semibold text-text-default">`
  inside the slot container; the test selects it via
  `getByTestId(...).querySelector('.text-xs.font-semibold')`.
- `relayViewModel.ts` is still used by the relay panel (MCP23017) — do NOT
  delete the module. Only the DFR import was removed.
- The `DfrAssignment` type was NOT modified; no `bound_relay_channel` field
  was added. The warning text is now generic and does not need the relay
  number to render.
- `board.board_id` is a `number`; template literal coerces it. Mock data uses
  `board_id: 0` -> `DFR0`.

### Files touched
- `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx`
  - line 8: removed `relayViewModel` import
  - line 579: `R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}` -> `DFR{board.board_id} · CH{ch}`
  - line 737: `This will also unbind relay R{getRelayNumber(ch)}. Remove light?` -> `Remove light? (Its relay will also be unbound.)`
- `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx`
  - line 116: updated assertion regex
  - appended new test for DFR slot label identity

## Task 2: Redis relay state reconciliation at startup (2026-07-06)

### What was wrong
After service restart, the MCP23017 hardware is forced OFF via `all_off()`, but the
Redis cache keys `cea:relay:channels` and `cea:relay:timestamps` were not updated.
The `/api/hardware/relays/state` endpoint reads Redis FIRST and falls back to
hardware only on cache miss. A stale or missing cache meant the endpoint could
return wrong state until the control loop wrote fresh values (race window).

### Fix
Added a reconciliation block in `container.py` immediately after the `all_off()`
block (line ~120) and BEFORE `background_tasks.start()` (line ~199):
- Reads `self.mcp23017.get_all_channels()`
- Writes JSON list of 16 booleans to Redis key `RELAY_CHANNELS`
- Writes JSON list of 16 `null`s to Redis key `RELAY_TIMESTAMPS`
- Wrapped in try/except; failure logs WARNING and continues
- Added `set()` method to `AutomationRedisClient` (app/redis/__init__.py) to
  match the existing `get()` raw-key accessor pattern.

### TDD flow
1. Wrote `tests/test_relay_redis_reconciliation.py` with 4 tests:
   - `test_reconciliation_writes_relay_channels_and_timestamps` — asserts both
     Redis keys are set correctly after `container.initialize()`
   - `test_reconciliation_logs_info` — asserts INFO log emitted on success
   - `test_reconciliation_failure_does_not_crash_init` — asserts WARNING log and
     init continues when `get_all_channels()` raises
   - `test_reconciliation_runs_after_all_off_and_before_background_tasks` —
     asserts ordering via call-log wrapping
2. Ran tests BEFORE code fix -> 4 failed (red), confirming the test catches
   the missing reconciliation.
3. Applied the reconciliation block + `set()` method.
4. Ran tests AFTER -> 4/4 pass (green).
5. Ran existing `test_startup_force_off.py` -> 4/4 pass (no regression).

### Key facts for downstream tasks
- `AutomationRedisClient` now exposes `.set(key, value)` in addition to `.get(key)`.
  This is the canonical way to do raw key writes from container-level code.
- The reconciliation block uses `import json as _json` to avoid shadowing any
  module-level `json` import in `container.py`.
- Constants `RELAY_CHANNELS` and `RELAY_TIMESTAMPS` are imported from
  `app.redis.schema` — no hardcoded strings.
- The block is guarded by `if self.mcp23017 is not None and self.automation_redis is not None:`.
- `sum(hw_states)` in the INFO log counts `True` values (ON relays).

### Files touched
- `Infrastructure/automation-service/app/container.py`
  - appended reconciliation block after `all_off()` block (~line 120)
- `Infrastructure/automation-service/app/redis/__init__.py`
  - added `set(self, key, value)` method to `AutomationRedisClient`
- `Infrastructure/automation-service/tests/test_relay_redis_reconciliation.py`
  - new file with 4 tests
