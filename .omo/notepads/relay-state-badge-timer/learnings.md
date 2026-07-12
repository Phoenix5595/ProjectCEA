# Task 1: Fix RELAY_TIMESTAMPS update in raw channel control route

## Date: 2026-07-08

## Problem
The `set_relay_channel_state()` route in `app/routes/hardware.py` did NOT update `RELAY_TIMESTAMPS[channel]` in Redis after a successful hardware write. This caused the relay matrix timer to show page-load time instead of last state-change time.

## Solution
1. Added `_maybe_update_relay_timestamp()` helper (async) in `hardware.py` that:
   - Reads existing `RELAY_TIMESTAMPS` from Redis (sync `automation_redis.get()`)
   - Seeds `[None] * 16` if key is missing
   - Updates `timestamps[channel] = now_iso` only when `old_state != new_state`
   - Writes back via `await asyncio.to_thread(automation_redis.redis_client.set, ...)`
   - Logs warning on failure instead of bare `except: pass`

2. In `set_relay_channel_state()`:
   - Read current channel state from `RELAY_CHANNELS` BEFORE hardware write
   - After each successful `relay_manager.set_channel_state()` call (all 3 branches: ON+duration, ON, OFF), await the helper
   - Only updates timestamp when state actually changed (matches `hardware_batch.py:488-493` pattern)

## Files Modified
- `Infrastructure/automation-service/app/routes/hardware.py`

## Files Created
- `Infrastructure/automation-service/tests/test_relay_timestamp_update.py`
  - Test 1: false→true (channel 5, duration) → timestamp updated
  - Test 2: true→true (channel 3, no duration) → timestamp NOT updated
  - Test 3: true→false (channel 7) → timestamp updated

## Verification
- `ruff check app/routes/hardware.py` → passed
- `pytest tests/test_relay_timestamp_update.py -v` → 3/3 passed

## Key Patterns
- Read-modify-write on `RELAY_TIMESTAMPS` is non-atomic (accepted race condition, same as batch executor)
- `automation_redis.get()` is synchronous; writes use `await asyncio.to_thread(...)`
- `RELAY_CHANNELS` and `RELAY_TIMESTAMPS` are already imported at `hardware.py:15`

## 2026-07-08 — Todo 2: Fix RELAY_TIMESTAMPS update in device control route

### Changes Made
- **File**: `Infrastructure/automation-service/app/routes/devices.py`
  - Added imports: `asyncio`, `json`, `AutomationRedisClient` (from `app.redis_client`), `RELAY_TIMESTAMPS` (from `app.redis.schema`)
  - Added DI stub: `get_automation_redis()` raising `RuntimeError("Dependency not injected")`
  - Added `automation_redis: AutomationRedisClient = Depends(get_automation_redis)` parameter to `control_device()`
  - After `relay_manager.set_device_state()` succeeds, added read-modify-write on `RELAY_TIMESTAMPS`:
    - Compare `bool(current_state)` vs `bool(request.state)`
    - Only update when state actually changed
    - Read existing timestamps via `automation_redis.get(RELAY_TIMESTAMPS)` (sync)
    - Seed `[None] * 16` if missing
    - Write back via `await asyncio.to_thread(automation_redis.redis_client.set, RELAY_TIMESTAMPS, json.dumps(timestamps))`
    - Wrapped in try/except with warning log on failure
- **File**: `Infrastructure/automation-service/app/routes/routes.py`
  - Added `app.dependency_overrides[devices.get_automation_redis] = container.get_automation_redis`
- **File**: `Infrastructure/automation-service/tests/test_device_timestamp_update.py` (created)
  - Test 1: off->on state change updates timestamp at channel index
  - Test 2: on->on no-op does NOT call redis set
  - Test 3: channel=None raises 404 before any Redis interaction

### Verification
```bash
cd Infrastructure/automation-service
ruff check app/routes/devices.py app/routes/routes.py  # passed
pytest tests/test_device_timestamp_update.py -v        # 3 passed
```

### Key Patterns
- `AutomationRedisClient.get()` is synchronous (no await)
- `automation_redis.redis_client.set` must be wrapped in `asyncio.to_thread()` for async safety
- Read-modify-write on `RELAY_TIMESTAMPS` is non-atomic (accepted race condition, same as hardware_batch.py)
- Timestamp format: `datetime.now(UTC).isoformat().replace("+00:00", "Z")`

## 2026-07-08 — Todo 3: Extend relay_state() GET response with modes[] and override_expires_at[]

### Changes Made
- **File**: `Infrastructure/automation-service/app/routes/hardware.py`
  - In `relay_state()` GET endpoint, added two new arrays to the response dict:
    - `override_expires_at: list[str | None]` (16 elements): reads all 16 override keys in one MGET call via `await asyncio.to_thread(automation_redis.redis_client.mget, ...)`, parses `json.loads(raw)["expires_at"]`, compares with `datetime.now(UTC)`, returns `None` for expired or missing overrides.
    - `modes: list[str | None]` (16 elements): for each channel 0-15:
      - If `override_expires_at[channel]` is not null → `"manual"` (active timer override)
      - Else if channel has a device assignment in `device_states` table → mode from DB (`"auto"`, `"manual"`, or `"scheduled"`)
      - Else → `"off"` (unassigned, no automation control)
  - Uses sync `automation_redis.get()` for existing RELAY_CHANNELS/RELAY_TIMESTAMPS reads (matching existing pattern).
  - Uses `await asyncio.to_thread(...)` for the 16-key MGET override read (single round-trip).
  - Calls `await database.device_repo.get_all_device_states()` once and builds a `channel → mode` map.
  - `"scheduled"` mode is passed through as-is (frontend maps both `"auto"` and `"scheduled"` to AUTO badge).
  - Did NOT change existing `channels`, `timestamps`, or `mcp_connected` fields.

- **File**: `Infrastructure/automation-service/tests/test_relay_state_extended.py` (created)
  - Test 1: mock Redis MGET returns active override for channel 3 (future expiry) + DB mode `"auto"` for channel 0; asserts `override_expires_at[3]` is ISO string, `modes[3]` == `"manual"`, `modes[0]` == `"auto"`, `override_expires_at[0]` is None.
  - Test 2: mock expired override for channel 3 + DB mode `"scheduled"`; asserts `override_expires_at[3]` is None (filtered out), `modes[3]` == `"scheduled"` (falls through to DB).
  - Test 3: no overrides, no device states; asserts all modes == `"off"`, all `override_expires_at` == None.

### Verification
```bash
cd Infrastructure/automation-service
ruff check app/routes/hardware.py  # passed
pytest tests/test_relay_state_extended.py -v  # 3 passed
```

### Key Patterns
- `automation_redis.redis_client.mget()` must be wrapped in `await asyncio.to_thread(...)` for async safety.
- `datetime.fromisoformat()` handles both `+00:00` and `Z` suffixes in Python 3.11+.
- `get_all_device_states()` returns `list[dict[str, Any]]` with `channel` (int) and `mode` (str) fields.
- The `database` dependency is already injected in `relay_state()` via `Depends(get_database)`.
- `relay_raw_override_key(channel)` returns `f"cea:relay:manual_override:{channel}"`.
- Redis override JSON structure: `{"expires_at": "...", "state": 1}`.

## 2026-07-08 — Todo 4: Frontend types + view model + timestamp mapping fix + formatCountdown()

### Changes Made

- **File**: `Infrastructure/frontend/src/types/relay.ts`
  - Added `modes: (string | null)[]` and `override_expires_at: (string | null)[]` to `RelayBoardStateResponse` interface.

- **File**: `Infrastructure/frontend/src/services/api.ts`
  - Imported `RelayBoardStateResponse` type from `../types/relay`.
  - Changed `getRelayBoardState()` return type from inline object type to `Promise<RelayBoardStateResponse>`.

- **File**: `Infrastructure/frontend/src/components/devices/relayViewModel.ts`
  - Added `mode: string | null` and `overrideExpiresAt: string | null` as REQUIRED fields to `RelayChannelViewModel` interface (13 fields total, up from 11).
  - Changed `buildRelayChannelViewModels()` signature: removed `lastStateChangeByDevice: Record<string, string>` parameter, added `timestamps: (string | null)[]`, `modes: (string | null)[]`, `overrideExpiresAt: (string | null)[]` parameters.
  - **Core fix**: timestamps now mapped DIRECTLY by channel index (`timestamps[channelNumber]`) instead of through device-key mapping. This fixes the timer bug for unassigned channels — previously, unassigned channels had no device key so `lastStateChangeAt` was always null even when the backend provided a timestamp.
  - `mode` and `overrideExpiresAt` also mapped directly by channel index.
  - Added `formatCountdown(expiresAt, nowMs)` utility — returns remaining time string (`"3h 45m"`, `"12m 30s"`, `"45s"`) or empty string if expired/missing. Modeled after `formatElapsedSince()`.

- **File**: `Infrastructure/frontend/src/components/DeviceManager.tsx`
  - Updated `DEFAULT_RELAY_STATE` with `modes: Array(16).fill(null)` and `override_expires_at: Array(16).fill(null)`.
  - Updated `relayChannels` useMemo: removed `lastStateMap` device-key mapping logic, now passes `relayState.timestamps`, `relayState.modes`, `relayState.override_expires_at` directly to `buildRelayChannelViewModels()`.
  - Removed unused `makeDeviceKey` import.

- **File**: `Infrastructure/frontend/src/components/devices/__tests__/relayMatrix.test.tsx`
  - Updated `makeVm()` helper: added `mode: null` and `overrideExpiresAt: null` to satisfy new required interface fields.

- **File**: `Infrastructure/frontend/src/components/devices/__tests__/relayMap.test.ts`
  - Updated `mockChannels` array: added `mode: null` and `overrideExpiresAt: null` to satisfy new required interface fields.

- **File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Removed `lastStateMap` building code in useEffect (lines 143-150).
  - Updated `buildRelayChannelViewModels` call to pass `relayTimestamps`, `Array(16).fill(null)`, `Array(16).fill(null)` for modes/overrideExpiresAt (ZoneConfig is a secondary view, doesn't need real mode/badge data).
  - Removed unused `makeDeviceKey` import.

### Verification
```bash
cd Infrastructure/frontend
npx tsc --noEmit  # 0 errors
npx vitest run src/components/devices/__tests__/  # 73 passed, 2 pre-existing failures (relayMatrix panel variant — unrelated to this task)
```

### Key Patterns
- Direct array indexing by channel number is simpler and more correct than device-key mapping for timestamp/mode/override data — the backend already provides per-channel arrays.
- The device-key mapping approach (`makeDeviceKey` → `lastStateChangeByDevice` map) was a workaround for when timestamps were only available through control history entries. Now that the backend returns `timestamps[]` directly, the mapping layer is unnecessary.
- `formatCountdown()` returns empty string (not "Unknown") for missing/expired values — the caller decides whether to show a badge or hide it.
- ZoneConfig passes `Array(16).fill(null)` for modes/overrideExpiresAt because its relay matrix is a compact secondary view; the `statusByChannel` memo there will be removed in Todo 5.
- Pre-existing test failures in `relayMatrix.test.tsx` (panel variant) are unrelated — they expect `R1 · CH 15` text but the component renders `R1 · GPB7` (GP pin labels). Confirmed by running tests against unmodified code via `git stash`.

## 2026-07-08 — Todo 5: Rewrite RelayChannelBox badge + LED logic to mode-based 4-state system

### Changes Made

- **File**: `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx`
  - Added `resolveBadgeState(channel, nowMs)` function returning `{ text, outlineClass, ledClass }` based on 4-state mode logic:
    1. Override active (`overrideExpiresAt` not null + countdown not expired) → blue badge with countdown text, blue LED
    2. Auto mode (`mode === "auto"` or `"scheduled"`) → green AUTO badge when active, red AUTO badge when inactive
    3. Manual off (`mode === "off"` or `"manual"` with no override) → black OFF badge, black LED
    4. Unknown (`mode === null`) → amber "?" badge, amber LED
  - Removed `statusText?: string` and `statusTone?: 'unknown' | 'active' | 'idle'` from `RelayChannelBoxProps`
  - Removed `stateBadgeClasses()` and `RelayStatusLed` helper functions — replaced by `resolveBadgeState()`
  - Removed `resolvedTone` and `resolvedText` local variables — replaced by `badge` object from `resolveBadgeState()`
  - LED now rendered inline as `<span>` using `badge.ledClass` instead of `<RelayStatusLed>` component
  - Badge button uses `badge.outlineClass` and `badge.text` instead of `stateBadgeClasses(resolvedTone)` and `resolvedText`
  - Added `formatCountdown` to imports from `relayViewModel`
  - Kept elapsed time display in bottom span (`{elapsedLabel}`) unchanged
  - Kept tooltip, menu, and all other parts unchanged

- **File**: `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx`
  - Removed `statusByChannel` from `RelayChannelMatrixProps` interface
  - Removed `statusByChannel` from `ChannelBoxRenderProps` interface
  - Removed `statusText={...}` and `statusTone={...}` prop passing in `renderChannelBox()`
  - Removed `statusByChannel` from `boxProps` object

- **File**: `Infrastructure/frontend/src/components/DeviceManager.tsx`
  - Removed `statusByChannel` useMemo (was lines 52-86, ~35 lines computing elapsed time per channel)
  - Removed `statusByChannel={statusByChannel}` prop from `<RelayChannelMatrix>` usage

- **File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Removed `// @ts-ignore` before `menuOpenChannel` state declaration (line 155)
  - Removed `// @ts-ignore` before `statusByChannel` memo (line 185)
  - Removed `statusByChannel` useMemo (was lines 186-205, ~20 lines computing countdown timer)
  - Removed `statusByChannel={statusByChannel}` prop from `<RelayChannelMatrix>` usage
  - Removed unused `useMemo` import from React

### Verification
```bash
cd Infrastructure/frontend
npx tsc --noEmit  # 0 errors
npm run build     # exit 0, built in 12.82s
npx vitest run src/components/devices/__tests__/  # 73 passed, 2 pre-existing failures (relayMatrix panel variant — unrelated)
```

### Key Patterns
- `resolveBadgeState()` handles the override-expired edge case: if `overrideExpiresAt` is not null but `formatCountdown()` returns empty string (expired between backend response and frontend tick), it falls through to mode-based logic instead of showing an empty badge.
- The backend already filters expired overrides (returns `None` for expired), so the fall-through is a safety net for race conditions, not a common path.
- The 4-state system replaces two separate `statusByChannel` memos (DeviceManager computed elapsed time, ZoneConfig computed countdown) with a single self-contained function in `RelayChannelBox` — no parent computation needed.
- The `nowMs` 1-second interval tick (already present in both DeviceManager and ZoneConfig) drives the countdown update — no `setTimeout` needed.
- ZoneConfig passes `Array(16).fill(null)` for modes/overrideExpiresAt (from Todo 4), so all channels show the amber "?" badge there — this is expected for the compact secondary view.
- Removing the `@ts-ignore` before `menuOpenChannel` in ZoneConfig was safe — the variable IS used in the JSX (`menuOpenChannel={menuOpenChannel}`) and `onToggleMenu` callback.
