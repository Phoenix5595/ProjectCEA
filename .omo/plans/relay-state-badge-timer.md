# relay-state-badge-timer - Work Plan

## TL;DR (For humans)

**What you'll get:** The relay matrix timer now shows how long ago each relay last changed state (pulled from the database, not the page load). The mode badge on each relay box shows "AUTO" (green outline when on, red when off) for automation-controlled relays, a blue countdown timer when a manual override is active, or "OFF" (red outline, black dot) when manually turned off. The LED dot follows the same color scheme.

**Why this approach:** The root cause was that manual relay toggles via the raw channel route bypassed the batch executor that writes state-change timestamps to Redis. The fix adds timestamp writes directly to the route after each successful hardware command. The badge/LED rewrite replaces the old 3-state ON/OFF/Unknown system with a 4-state Auto-On/Auto-Off/Manual-Timer/Manual-Off system, driven by a single extended API response (no new polling endpoint needed).

**What it will NOT do:**
- No frontend `setTimeout` countdown — the existing 1-second `nowMs` tick drives the display from a backend `expires_at` timestamp.
- No DB migration — uses existing `device_states` table + Redis override keys.
- No indefinite ON option — all manual ON actions have a timer (intentional design).

**Effort:** Short
**Risk:** Low — extends existing API response + rewrites one component; no schema changes.
**Decisions to sanity-check:** Blue badge/LED for manual timer (ISA-101 standard); modes inferred from `device_states` table + Redis override key existence (no per-channel Redis mode keys).

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> **Drift audit 2026-07-08:** Plan re-verified against current codebase after `centralized-device-table` commit (`66825a5`). The new `device_registry` table does NOT affect this plan (it's hardware mapping only — no mode/state; mode is still in `device_states`). DeviceManager.tsx line refs corrected (780→307 line refactor). `get_all_device_states()` ref corrected (82-92→169-179). All other line refs verified accurate. See `.omo/drafts/relay-state-badge-timer.md` § Drift audit for full details.

---

> TL;DR (machine): <1 line - effort, risk, deliverables>

## Scope
### Must have
- `set_relay_channel_state()` route updates `RELAY_TIMESTAMPS[channel]` in Redis on every successful hardware write (fixes the "timer shows page-load time" bug).
- `control_device()` route updates `RELAY_TIMESTAMPS[channel]` in Redis on device-assigned state change.
- `/api/hardware/relays/state` response extended with `modes: (string|null)[]` and `override_expires_at: (string|null)[]` arrays (16 elements each).
- `RelayChannelViewModel` gains `mode` and `overrideExpiresAt` fields; `buildRelayChannelViewModels` maps timestamps by channel index (not device-key).
- `formatCountdown()` utility added for remaining-time display.
- `RelayChannelBox` badge + LED rewritten: 4-state — AUTO (green outline when ON, red when OFF), MANUAL+timer (blue, countdown), MANUAL+OFF (black outline, black LED), unknown (amber).
- `statusText`/`statusTone` props removed from `RelayChannelBoxProps`; `statusByChannel` memo removed from DeviceManager.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT use frontend `setTimeout` for countdown — the existing 1-second `nowMs` interval tick drives it (backend provides `expires_at`, frontend calculates remaining).
- Do NOT add DB migrations — uses existing `device_states` table + existing Redis override keys.
- Do NOT add a new polling endpoint — extend the existing `/api/hardware/relays/state` response (single poll, no extra requests).
- Do NOT add per-channel Redis mode keys — infer mode from `device_states` table (assigned) + override key existence (unassigned).
- Do NOT change the elapsed-time display in the bottom span (line 152) — it still shows `formatElapsedSince(channel.lastStateChangeAt, nowMs)`.
- Do NOT break ZoneConfig.tsx's relay matrix usage — if it passes `statusText`/`statusTone`, it must be updated in the same todo.
- Do NOT change `relay_manager.set_channel_state()` signature — the route owns the Redis timestamp write (matches the existing override-key pattern).
- Do NOT remove `formatElapsedSince()` — still used for the bottom elapsed-time span.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after. Backend: pytest (2 new test files). Frontend: `tsc --noEmit` + `npm run build` + existing relayMatrix tests.
- Evidence: `.omo/evidence/task-N-relay-state-badge-timer.<ext>`

## Execution strategy
### Parallel execution waves
- **Wave 1 (backend, parallel):** Todo 1 (hardware.py POST timestamp fix) + Todo 3 (devices.py timestamp fix) run in parallel. Todo 2 (hardware.py GET API extension) is blocked by Todo 1 (same file) — runs after Todo 1.
- **Wave 2 (frontend, sequential):** Todo 4 (types + view model + formatCountdown) → Todo 5 (RelayChannelBox rewrite). Sequential because both touch the same component pipeline.
- **Wave 3 (final):** Todo 6 (build + tests + deploy + verify). Depends on all.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 2, 4 | 3 |
| 2 | 1 | 4 | — |
| 3 | — | 4 | 1 |
| 4 | 2, 3 | 5 | — |
| 5 | 4 | 6 | — |
| 6 | 4, 5 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Fix RELAY_TIMESTAMPS update in raw channel control route (`hardware.py`)
  What to do / Must NOT do:
  - In `set_relay_channel_state()` route (`app/routes/hardware.py:108-173`), BEFORE calling `relay_manager.set_channel_state(channel, state)`, read the current channel state. After the hardware call succeeds, compare old vs new state — only update `RELAY_TIMESTAMPS[channel]` if `old_state != new_state` (matches the batch executor pattern at `hardware_batch.py:488-493`; prevents spurious timestamp resets on no-op writes).
  - To read old state: parse `automation_redis.get(RELAY_CHANNELS)` (sync — matches existing pattern at `hardware.py:188`) and check `bool(parsed[channel])`, or call `relay_manager.mcp23017.get_channel(channel)`.
  - To write: read the existing `RELAY_TIMESTAMPS` JSON array from Redis via `automation_redis.get(RELAY_TIMESTAMPS)` (sync — matches existing pattern at `hardware.py:206`); if null, seed `[None]*16`; update index `channel`; write back via `await asyncio.to_thread(automation_redis.redis_client.set, RELAY_TIMESTAMPS, json.dumps(timestamps))`.
  - This must happen for ALL three branches: state=1+duration, state=1 no-duration, state=0. Only when the state actually changed.
  - Do NOT change `relay_manager.set_channel_state()` itself — the route owns the Redis write (matches the existing pattern where the route writes override keys).
  - Do NOT break existing override key logic (the `relay_raw_override_key` writes stay as-is).
  - Use a consistent read pattern: `automation_redis.get()` (sync) for reads, `await asyncio.to_thread(...)` for writes — matching the existing pattern in the same route.
  - Note: the read-modify-write on `RELAY_TIMESTAMPS` is non-atomic (race condition on concurrent requests). This is accepted — low probability (the same pattern exists in the batch executor) and low impact (only affects timestamp display). Do NOT add Redis transactions (WATCH/MULTI/EXEC) — out of scope.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2, 4 | Can parallelize with: 3
  References:
  - `Infrastructure/automation-service/app/routes/hardware.py:15` (imports — `RELAY_TIMESTAMPS` already imported)
  - `Infrastructure/automation-service/app/routes/hardware.py:108-173` (`set_relay_channel_state` route)
  - `Infrastructure/automation-service/app/routes/hardware.py:188` (existing sync `automation_redis.get(RELAY_CHANNELS)` pattern — match this for reads)
  - `Infrastructure/automation-service/app/routes/hardware.py:138-143` (existing async `await asyncio.to_thread(automation_redis.redis_client.setex, ...)` pattern — match this for writes)
  - `Infrastructure/automation-service/app/control/hardware_batch.py:485-498` (CORRECT pattern: `before != op.state` comparison before timestamp update)
  - `Infrastructure/automation-service/app/redis/schema.py` (`RELAY_TIMESTAMPS` constant definition)
  - `Infrastructure/automation-service/app/redis/__init__.py:135-146` (`AutomationRedisClient.get()` is SYNCHRONOUS — no `await` needed)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/routes/hardware.py` passes.
   - New pytest `tests/test_relay_timestamp_update.py`: mock `relay_manager.set_channel_state` to return True; mock `automation_redis.get(RELAY_CHANNELS)` to return `prev_state` where channel is already `[false]*16`; call route with `{"state":1,"duration_seconds":300}` on channel `{test_channel}`; assert `RELAY_TIMESTAMPS` was updated at index `{test_channel}` (state changed false→true). Also test: mock channel as already `[false,...,true,...` (index {test_channel}=true); call `{"state":1}` on that channel; assert timestamp NOT updated (state unchanged true→true). Also test state=0 branch updates timestamp when state was ON.
  - `cd Infrastructure/automation-service && pytest tests/test_relay_timestamp_update.py -v` passes.
  QA scenarios: happy — manual channel toggle updates the timestamp when state changes. failure — timestamp NOT updated when state doesn't change (no-op write); hardware error (set_channel_state returns False) skips timestamp update. Evidence `.omo/evidence/task-1-relay-state-badge-timer.txt`
  Commit: Y | fix(hardware): update RELAY_TIMESTAMPS on manual channel control

- [x] 2. Extend relay state API response with `modes[]` and `override_expires_at[]` (`hardware.py`)
  What to do / Must NOT do:
  - In the `relay_state()` GET endpoint (`app/routes/hardware.py:176-229`), add two new arrays to the response:
    - `override_expires_at: list[str | None]` — read all 16 override keys in one `MGET` call: `await asyncio.to_thread(automation_redis.redis_client.mget, [relay_raw_override_key(ch) for ch in range(16)])`. For each non-null result, `json.loads(raw)["expires_at"]`; then compare `expires_at` with `datetime.now(UTC)` — if expired (past), return `null` (the Redis key has a 1-day TTL grace period, so expired keys still exist). If key is null, return `null`.
    - `modes: list[str | None]` — for each channel 0-15:
      - If `override_expires_at[channel]` is not null (active override) → `"manual"` (timer active)
      - Else if channel has a device assignment → call `await database.device_repo.get_all_device_states()` (EXISTS — `app/repositories/devices.py:169-179`, drift-corrected from 82-92), build a `channel → mode` map, read mode from DB (`"auto"`, `"manual"`, or `"scheduled"`)
      - Else → `"off"` (unassigned, no timer — not controlled by automation)
  - Use sync `automation_redis.get()` for the existing `RELAY_CHANNELS`/`RELAY_TIMESTAMPS` reads (matching existing pattern at `hardware.py:188,206`). Use `await asyncio.to_thread(automation_redis.redis_client.mget, ...)` for the 16-key override read (single round-trip).
  - The `database` dependency is already injected at `hardware.py:180` — use `database.device_repo.get_all_device_states()`.
  - Add to the return dict: `"modes": modes, "override_expires_at": override_expires_at`.
  - Handle `"scheduled"` mode: treat it the same as `"auto"` in the mode field (it's an automation-controlled mode); the frontend badge logic maps both to the AUTO state.
  - Must NOT change the existing `channels` or `timestamps` arrays.
  - Must NOT add a new endpoint — extend the existing one.
  - Must NOT add DB migrations — uses existing `device_states` table.
  Parallelization: Wave 1 | Blocked by: 1 (same file `hardware.py`) | Blocks: 4
  References:
  - `Infrastructure/automation-service/app/routes/hardware.py:176-229` (`relay_state` endpoint — current return shape; `database` already injected at line 180)
  - `Infrastructure/automation-service/app/redis/schema.py:148-149` (`relay_raw_override_key(channel)` helper)
  - `Infrastructure/automation-service/app/repositories/devices.py:169-179` (`get_all_device_states()` — EXISTS, returns all rows with `channel` and `mode` fields, drift-corrected from 82-92)
  - `Infrastructure/automation-service/alembic/versions/001_baseline.py:22-32` (`device_states` table DDL with `mode TEXT NOT NULL CHECK (mode IN ('manual', 'auto', 'scheduled'))`)
  - `Infrastructure/automation-service/app/control/relay_manager.py:49-51` (`_current_modes` in-memory dict — alternate source but DB is more reliable)
  - `Infrastructure/automation-service/app/redis/__init__.py:135-146` (`AutomationRedisClient.get()` is SYNCHRONOUS — no `await`)
  Acceptance criteria:
  - `curl -s -H "X-API-Key: $CEA_API_KEY" http://mothernode:8080/api/hardware/relays/state | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'modes' in d and len(d['modes'])==16; assert 'override_expires_at' in d and len(d['override_expires_at'])==16; print('OK')"` succeeds.
  - `cd Infrastructure/automation-service && ruff check app/routes/hardware.py` passes.
  - New pytest `tests/test_relay_state_extended.py`: mock Redis MGET to return non-null (with valid `{"expires_at":"2026-07-05T23:00:00+00:00","state":1}`) for channel {test_channel} and null for all others; mock `database.device_repo.get_all_device_states()` to return `[{channel:0, mode:"auto"}]`; mock `datetime.now()` to return before expiry; call the GET endpoint; assert `response["override_expires_at"][3]` is the ISO string; assert `response["modes"][3]` == "manual"; assert `response["modes"][0]` == "auto"; assert `response["override_expires_at"][0]` is None. Also test: mock `expires_at` in the past; assert `override_expires_at[3]` is None (expired override filtered out); assert `modes[3]` falls through to DB mode or "off".
  - `cd Infrastructure/automation-service && pytest tests/test_relay_state_extended.py -v` passes.
  QA scenarios: happy — API returns modes + override_expires_at arrays with correct values. failure — Redis miss returns nulls gracefully; expired override returns null (not the past timestamp); DB query failure returns null modes (doesn't crash). Evidence `.omo/evidence/task-2-relay-state-badge-timer.txt`
  Commit: Y | feat(hardware): extend relay state API with modes + override expiry

- [x] 3. Fix RELAY_TIMESTAMPS update in device control route (`devices.py`)
  What to do / Must NOT do:
  - In `control_device()` route (`app/routes/devices.py`), AFTER `relay_manager.set_device_state()` succeeds, look up the channel for that device via `relay_manager.get_channel(location, cluster, device_name)` and update `RELAY_TIMESTAMPS[channel]` in Redis (same pattern as Todo 1: read old state, compare, only update if changed).
  - **CRITICAL DI setup (Metis F3):** `devices.py` does NOT currently have `AutomationRedisClient` imported or `get_automation_redis()` defined. Must add:
    1. Import: `from app.redis_client import AutomationRedisClient` (matching `hardware.py:16` — `app/redis_client.py` is a backward-compat re-export shim; `from app.redis import ...` also works but `hardware.py` uses the `redis_client` path)
    2. Add dependency function: `def get_automation_redis() -> AutomationRedisClient: raise NotImplementedError("Dependency not injected")` (same pattern as `get_relay_manager()` and `get_database()` already in `devices.py:61-68`)
    3. Add route parameter: `automation_redis: AutomationRedisClient = Depends(get_automation_redis)`
    4. Verify DI wiring in `app/container.py` — check if `get_automation_redis` override exists for `hardware.py` and replicate for `devices.py`
  - Import `RELAY_TIMESTAMPS` from `app.redis.schema`.
  - Only update timestamp if `old_state != new_state` (compare before/after, matching the batch executor pattern).
  - Must NOT change the `log_control_action()` call — it already logs state changes to control_history.
  - Must NOT break the existing `manual_expires_at` logic.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 4 | Can parallelize with: 1
  References:
  - `Infrastructure/automation-service/app/routes/devices.py` (find `control_device` route — has `relay_manager` and `database` but NOT `automation_redis`)
  - `Infrastructure/automation-service/app/routes/devices.py:61-68` (`get_relay_manager()` and `get_database()` dependency function pattern — replicate for `get_automation_redis()`)
  - `Infrastructure/automation-service/app/routes/hardware.py:1-15,33-35` (imports and `get_automation_redis()` definition in `hardware.py` — replicate in `devices.py`)
  - `Infrastructure/automation-service/app/container.py` (DI overrides — verify `get_automation_redis` wiring exists for `hardware.py`)
  - `Infrastructure/automation-service/app/control/relay_manager.py:93-141` (`set_device_state` — does NOT update Redis timestamps)
  - `Infrastructure/automation-service/app/control/relay_manager.py:65-77` (`get_channel` method to look up channel from device)
  - `Infrastructure/automation-service/app/redis/schema.py` (`RELAY_TIMESTAMPS` constant)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/routes/devices.py` passes.
  - `grep -n "get_automation_redis" Infrastructure/automation-service/app/routes/devices.py | head -3` returns the function definition + route parameter.
  - `grep -n "AutomationRedisClient" Infrastructure/automation-service/app/routes/devices.py | head -3` returns the import + type annotation.
  - Manual verification: `curl -X POST -H "X-API-Key: $KEY" http://mothernode:8080/api/devices/Flower%20Room/main/exhaust_fan/control -d '{"state":1}'` then `redis-cli GET cea:relay:timestamps | python3 -c "import sys,json; d=json.load(sys.stdin); assert d[channel] is not None"` passes (channel = the exhaust fan's mapped channel).
  QA scenarios: happy — device control via API updates RELAY_TIMESTAMPS for that device's channel when state changes. failure — `relay_manager.get_channel` returns None for unknown device (skip timestamp update gracefully); state unchanged (skip update). Evidence `.omo/evidence/task-3-relay-state-badge-timer.txt`
  Commit: Y | fix(devices): update RELAY_TIMESTAMPS on manual device control

- [x] 4. Update frontend types + view model + fix timestamp mapping
  What to do / Must NOT do:
  - Update `RelayBoardStateResponse` type in `types/relay.ts` to add `modes: (string | null)[]` and `override_expires_at: (string | null)[]`.
  - Update `api.ts:239-242` inline return type in `getRelayBoardState()` to include `modes` and `override_expires_at` fields (replace inline type with `RelayBoardStateResponse` OR add the new fields to the inline type — Metis F6).
  - Update `DEFAULT_RELAY_STATE` in `DeviceManager.tsx` (lines 19-24 — Metis F7, drift-corrected from 45-50) to include `modes: Array(16).fill(null)`, `override_expires_at: Array(16).fill(null)`, AND preserve existing `simulation: false` field.
  - Update `RelayChannelViewModel` interface in `relayViewModel.ts:64-76` to add `mode: string | null` and `overrideExpiresAt: string | null` (as REQUIRED fields, not optional — ensures all callers must provide them).
  - Update `buildRelayChannelViewModels()` in `relayViewModel.ts:175-208` to:
    - Accept `modes: (string | null)[]` and `overrideExpiresAt: (string | null)[]` parameters.
    - Map timestamps DIRECTLY by channel index `relayState.timestamps[channelNumber]` (NOT through device-key mapping — this is the core fix for the timer bug for unassigned channels).
    - Map `mode: modes[channelNumber]` and `overrideExpiresAt: overrideExpiresAt[channelNumber]` directly by channel index.
  - Update the `relayChannels` useMemo in `DeviceManager.tsx:40-55` (drift-corrected from 132-146):
    - Pass `relayState.modes` and `relayState.override_expires_at` to `buildRelayChannelViewModels()`.
    - REMOVE the `lastStateMap` device-key mapping (no longer needed — timestamps come directly from the array).
  - Add `formatCountdown(expiresAt: string | null, nowMs: number): string` function to `relayViewModel.ts` (returns `"4m 32s"` / `"1h 5m"` / `"12s"` / `""` when null/expired; model after `formatElapsedSince` at lines 210-245).
  - Update `relayMatrix.test.tsx` `makeVm()` helper (line 6-20) to include `mode: null` and `overrideExpiresAt: null` in the returned object (Metis F8 — required fields will cause TS errors if missing).
  - Must NOT rename or remove existing exports — additive changes only.
  - Update ZoneConfig.tsx's relay matrix usage if it calls `buildRelayChannelViewModels` with the old signature (check `pages/ZoneConfig.tsx` for `buildRelayChannelViewModels` calls — update the call site to pass the new parameters). ZoneConfig.tsx's `statusByChannel` memo will be removed in Todo 5.
  Parallelization: Wave 2 | Blocked by: 2 (API contract), 3 (devices.py, same backend wave) | Blocks: 5
  References:
  - `Infrastructure/frontend/src/types/relay.ts` (`RelayBoardStateResponse` type — find and extend)
  - `Infrastructure/frontend/src/services/api.ts:239-242` (`getRelayBoardState` inline return type — Metis F6)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:64-76` (`RelayChannelViewModel` interface)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:175-208` (`buildRelayChannelViewModels` — current signature takes `channels`, `relayStates`, `lastStateChangeByDevice`)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:210-245` (`formatElapsedSince` — model `formatCountdown` after this)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:19-24` (`DEFAULT_RELAY_STATE` constant — Metis F7: drift-corrected from 45-50)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:40-55` (`relayChannels` useMemo — current device-key mapping logic, drift-corrected from 132-146)
  - `Infrastructure/frontend/src/components/devices/__tests__/relayMatrix.test.tsx:6-20` (`makeVm()` helper — Metis F8)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` (check for `buildRelayChannelViewModels` calls — update call site)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes with 0 errors.
  - `grep -n "mode:" Infrastructure/frontend/src/components/devices/relayViewModel.ts | grep "string | null"` returns the new field on `RelayChannelViewModel`.
  - `grep -n "formatCountdown" Infrastructure/frontend/src/components/devices/relayViewModel.ts` returns the new function.
  - `grep -n "overrideExpiresAt" Infrastructure/frontend/src/components/devices/relayViewModel.ts` returns both the interface field and the mapping line.
  - `grep -n "mode: null" Infrastructure/frontend/src/components/devices/__tests__/relayMatrix.test.tsx` returns the test fix.
  QA scenarios: happy — types compile, view model has mode + overrideExpiresAt fields, timestamps mapped by index. failure — tsc strict errors on missing required fields. Evidence `.omo/evidence/task-4-relay-state-badge-timer.txt`
  Commit: N | (bundled with Todo 5 frontend commit)

- [x] 5. Rewrite RelayChannelBox badge + LED logic + update ZoneConfig + RelayChannelMatrix
  What to do / Must NOT do:
  - In `RelayChannelBox.tsx`, replace the badge text + outline color + LED color logic entirely with mode-based logic:
    - **Override active** (`channel.overrideExpiresAt` not null): badge text = `formatCountdown(channel.overrideExpiresAt, nowMs)`; badge outline = blue (`border-status-info-border/80` + `bg-status-info-bg/50` + `text-status-info-text` — or use a blue accent class); LED = blue (`bg-blue-500 shadow-[0_0_5px_var(--blue-400)]`).
    - **Auto mode** (`channel.mode === "auto"` OR `channel.mode === "scheduled"` — Metis F9): badge text = `"AUTO"`; badge outline = green when `channel.isActive` (`bg-status-success-bg/50 text-status-success-text border-status-success-border/80`) / red when `!channel.isActive` (`bg-status-danger-bg/30 text-status-danger-text border-status-danger-border/60`); LED = green when active (`bg-status-success-vivid`) / red when inactive (`bg-status-danger-vivid shadow-[0_0_4px_var(--status-danger-vivid)]`).
    - **Manual off** (`channel.mode === "off"` or `channel.mode === "manual"` with no override): badge text = `"OFF"`; badge outline = black (`bg-black/40 text-text-muted border border-border-emphasis`); LED = black (`bg-black` or `bg-surface-quinary border border-border-emphasis`).
    - **Unknown** (mode is null): badge text = `"?"`; badge outline = amber; LED = amber (existing `unknown` tone).
  - Remove the `statusText` and `statusTone` props from `RelayChannelBoxProps` (Metis F13: also remove `statusByChannel` field from `ChannelBoxRenderProps` in `RelayChannelMatrix.tsx:22-32` and from `RelayChannelMatrixProps` interface).
  - Remove `stateBadgeClasses()` and `RelayStatusLed` helper functions — replace with inline logic or a new `resolveBadgeState(channel, nowMs)` function that returns `{ text, outlineClass, ledClass }`.
  - Update `DeviceManager.tsx`: remove `statusByChannel` useMemo (lines 57-91, drift-corrected from 149-183) entirely. Remove `statusByChannel={statusByChannel}` prop from `<RelayChannelMatrix>` usage (line 295, drift-corrected from 774).
  - **Update `pages/ZoneConfig.tsx` (Metis F1):** remove the `statusByChannel` useMemo (lines 186-205 approximately), remove `@ts-ignore` (line 185), and remove `statusByChannel={statusByChannel}` prop from `<RelayChannelMatrix>` (line 467). ZoneConfig's `<RelayChannelMatrix>` will now rely on the view model's `mode` + `overrideExpiresAt` fields instead of the `statusByChannel` prop.
  - Update `RelayChannelMatrix.tsx`: remove `statusByChannel` from `ChannelBoxRenderProps` interface (line 27) and from `RelayChannelMatrixProps`. Remove the `statusText={...}` and `statusTone={...}` prop passing in `renderChannelBox()` (lines 44-45).
  - KEEP the elapsed time display in the bottom span (line 152): `{elapsedLabel}` stays — shows time since last state change.
  - KEEP tooltip, menu, and all other parts of RelayChannelBox unchanged.
  - Must NOT use `setTimeout` for countdown — the existing 1-second `nowMs` interval tick provides the update.
  - Must NOT change `formatElapsedSince` — it still shows elapsed time since `channel.lastStateChangeAt`.
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: 6
  References:
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:1-167` (entire component — the file being rewritten)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:18-28` (`stateBadgeClasses` — to be replaced)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:30-39` (`RelayStatusLed` — to be replaced)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:63-65` (`resolvedTone`/`resolvedText` — to be replaced)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:130-145` (button with `stateBadgeClasses(resolvedTone)` — to be rewritten)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:150-153` (elapsed label — KEEP)
- `Infrastructure/frontend/src/components/DeviceManager.tsx:57-91` (`statusByChannel` memo — to be removed, drift-corrected from 149-183)
- `Infrastructure/frontend/src/components/DeviceManager.tsx:295` (`RelayChannelMatrix` usage with `statusByChannel` prop — to remove status prop, drift-corrected from 768-780)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:22-32` (`ChannelBoxRenderProps` — remove `statusByChannel` field, NOT `statusText`/`statusTone` per Metis F13)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:44-45` (`renderChannelBox` — remove `statusText`/`statusTone` prop passing)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:185-205,467` (`statusByChannel` memo + prop — remove per Metis F1)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit && npm run build` pass.
  - `grep -n "statusTone\|statusText" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` returns nothing (props removed).
  - `grep -rn "statusByChannel" Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx` returns nothing (prop removed).
  - `grep -rn "statusByChannel" Infrastructure/frontend/src/components/DeviceManager.tsx` returns nothing (memo removed).
  - `grep -rn "statusByChannel" Infrastructure/frontend/src/pages/ZoneConfig.tsx` returns nothing (memo + prop removed — Metis F1).
  - `grep -n "formatCountdown" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` returns the countdown usage.
  - `grep -n "overrideExpiresAt" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` returns the override check.
  - Existing tests: `cd Infrastructure/frontend && npx vitest run src/components/devices/__tests__/relayMatrix.test.tsx` passes.
  QA scenarios: happy — RelayChannelBox shows countdown badge (blue) when manual timer active, "AUTO" badge (green/red outline) when auto/scheduled, "OFF" badge (red outline, black LED) when manual off. failure — null mode shows "?" badge; tsc/build errors on removed props (ZoneConfig not updated — Metis F1). Evidence `.omo/evidence/task-5-relay-state-badge-timer.txt`
  Commit: Y | feat(frontend): mode-based relay badge + countdown timer + LED colors

- [x] 6. Final: build + type-check + backend tests + deploy + post-deploy verification
  What to do / Must NOT do:
  - `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q` (all tests including new ones from Todos 1-2).
  - `cd Infrastructure/frontend && npx tsc --noEmit && npm run build`.
  - `./deploy.sh` from repo root.
  - Post-deploy verification:
    1. `curl -s -H "X-API-Key: $CEA_API_KEY" http://mothernode:8080/api/hardware/relays/state | python3 -c "import sys,json;d=json.load(sys.stdin);print('modes:',d.get('modes','MISSING'));print('override_expires_at:',d.get('override_expires_at','MISSING'))"` — confirms new API fields exist.
     2. `curl -X POST -H "X-API-Key: $CEA_API_KEY" http://mothernode:8080/api/hardware/relays/channel/{test_channel}/state -d '{"state":1,"duration_seconds":300}'` then `redis-cli GET cea:relay:timestamps | python3 -c "import sys,json;d=json.load(sys.stdin);print('channel {test_channel} timestamp:',d[{test_channel}]);assert d[{test_channel}] is not None"` — confirms timestamp updated.
     3. `curl -s -H "X-API-Key: $CEA_API_KEY" http://mothernode:8080/api/hardware/relays/state | python3 -c "import sys,json;d=json.load(sys.stdin);print('override_expires_at[{test_channel}]:',d['override_expires_at'][{test_channel}]);print('modes[{test_channel}]:',d['modes'][{test_channel}]);assert d['override_expires_at'][{test_channel}] is not None;assert d['modes'][{test_channel}]=='manual'"` — confirms override + mode returned.
     4. `curl -X POST -H "X-API-Key: $CEA_API_KEY" http://mothernode:8080/api/hardware/relays/channel/{test_channel}/state -d '{"state":0}'` then check timestamp updated and override cleared.
  - Must NOT roll back unless health checks fail.
  Parallelization: Wave 3 | Blocked by: 4, 5 | Blocks: —
  References:
  - `deploy.sh`, `rollback-deploy.sh`
  - `/opt/projectcea/shared/env/api_key.env` (API key for curl)
  Acceptance criteria: deploy succeeds (health checks pass), the curl sequence confirms API contract + timestamp updates + mode/override fields work end-to-end.
  QA scenarios: happy — deploy + all curl checks green. failure — deploy rolls back on health fail; timestamp not updated (regression). Evidence `.omo/evidence/task-6-relay-state-badge-timer.txt`
  Commit: Y | (deploy only; code commits in Todos 1-5)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity

## Commit strategy
- Wave 1 (backend): Todo 1 (fix), Todo 2 (feat), Todo 3 (fix) — each gets its own commit.
- Wave 2 (frontend): Todos 4+5 bundled into ONE commit: `feat(frontend): mode-based relay badge + countdown timer + LED colors`.
- Wave 3 (deploy): Todo 6 produces no separate code commit; `deploy.sh` tags the release.

## Success criteria
- The relay matrix elapsed-time display shows the time since the LAST STATE CHANGE (from Redis `RELAY_TIMESTAMPS`), not the page-load time — verified by toggling a relay and confirming the timer resets.
- The badge area shows: "AUTO" (green outline when ON, red when OFF) for automation-controlled relays; a countdown (blue) when a manual timer is active; "OFF" (red outline, black LED) when manually turned off.
- The countdown is backend-driven (reads `expires_at` from `/api/hardware/relays/state` response) and survives page reloads — verified by toggling a 5-minute timer, reloading the page, and confirming the countdown continues.
- The LED dot is: green for AUTO+ON, red for AUTO+OFF, blue for MANUAL+timer, black for MANUAL+OFF (both outline and LED).
- `ruff check .` (backend) and `tsc --noEmit` + `npm run build` (frontend) pass; new pytest files pass.
- Single deploy via `deploy.sh` succeeds and health checks stay green.
