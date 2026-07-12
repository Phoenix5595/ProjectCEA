# relay-state-badge-timer - Draft

## Status: awaiting-approval (CLEAR intent, Standard scale)

## Request
Fix relay matrix timer to show elapsed since last state CHANGE (not page reload).
Badge should show: countdown (blue) when manual timer active, "AUTO" when in auto mode, "OFF" when manually off.
AUTO button outline: green when ON, red when OFF. LED: green(AUTO+ON), red(AUTO+OFF), blue(MANUAL+timer), black(MANUAL+OFF).

## User decisions (from interview)
1. MANUAL+ON+timer: blue outline/accent, blue LED, countdown text inside badge
2. NO indefinite ON option exists — all manual ON has a timer (intentional design)
3. Badge outline: green when ON, red when OFF (except manual+timer = blue)

## Final state table

| State | Badge Text | Badge Outline | LED |
|-------|-----------|---------------|-----|
| AUTO+ON | "AUTO" | green | green |
| AUTO+OFF | "AUTO" | red | red |
| MANUAL+ON+timer | countdown | blue | blue |
| MANUAL+OFF | "OFF" | red | black |

## Exploration findings (key)

### Bug: Timestamps not updated on manual control
- `RELAY_TIMESTAMPS` in Redis updated ONLY by `HardwareBatchExecutor.execute()` (hardware_batch.py:461-500)
- `relay_manager.set_channel_state()` does NOT update timestamps
- Frontend maps timestamps through device-key mapping ONLY for assigned channels

### Mode system gaps
- Per-device mode in DB (`device_states.mode`) + in-memory (`relay_manager._current_modes`)
- Redis mode is PER-ROOM, not per-device
- For unassigned channels: NO mode tracking
- `/api/hardware/relays/state` returns channels[] + timestamps[] but NOT modes or override expiry

### Override timer
- Unassigned: Redis key `cea:relay:manual_override:{channel}` with `{"expires_at": iso, "state": 1}`
- Assigned: `control_history.manual_expires_at` column
- NO endpoint to read override `expires_at` for the relay matrix

### control_history table
- Has channel, timestamp, old_state, new_state, mode, manual_expires_at
- Raw channel overrides DON'T write to control_history (Redis-only)
- `get_last_changed_per_channel()` queries DB (fallback when Redis timestamps all null)

## Planned approach
1. **Backend: Fix timestamp updates** — `set_channel_state()` updates `RELAY_TIMESTAMPS` in Redis
2. **Backend: Extend relay state response** — add `override_expires_at: (string|null)[]` and `modes: (string|null)[]` to `/api/hardware/relays/state`
   - `override_expires_at[]`: read all 16 Redis override keys
   - `modes[]`: query `device_states` table for assigned channels; infer "manual"/"off" for unassigned from override key existence
3. **Frontend: Fix timestamp mapping** — pass timestamps per-channel, not through device-key mapping
4. **Frontend: Update RelayChannelViewModel** — add `mode: string | null` and `overrideExpiresAt: string | null`
5. **Frontend: Update RelayChannelBox** — new badge logic + LED colors based on state table above
6. **Frontend: Add countdown formatting** — `formatCountdown(expiresAt, nowMs)` returns "Mm Ss" remaining

## Approval gate
status: metis-complete (21 findings, 5 CRITICAL, all folded silently into plan)
pending action: present plan summary + start-or-high-accuracy question
approach: Fix backend timestamp updates + extend relay state API + rewrite RelayChannelBox badge/LED logic + update ZoneConfig/RelayChannelMatrix (Metis F1)

## Test strategy
- Backend: pytest for timestamp update in set_channel_state + extended relay state response
- Frontend: existing relayMatrix tests + tsc strict + build

## Drift audit (2026-07-08, pre-execution)

### Context
The plan was finalized before the `centralized-device-table` commit (`66825a5`, 2026-07-07) landed. That commit massively refactored `DeviceManager.tsx` (780→307 lines, extracting DeviceTable/DfrBoardsPanel/SystemSettingsPanel into separate components). A re-audit was requested before starting work.

### `device_registry` table impact: NONE
The new `device_registry` table (unified device list backed by migration 008/009) is purely hardware mapping (`channel`, `board_id`, `dimming_channel`, `display_name`, `device_type`, `location`, `cluster`). It has **NO `mode` or `state` field**. The `Device` and `LightDevice` Pydantic models confirm this — neither carries mode/state. Mode is still tracked in `device_states` table (written by `control_device()` and `set_device_mode()` routes). Todo 2's approach — reading mode from `get_all_device_states()` (which hits `device_states`) — is **still correct** and unaffected by the registry.

Frontend channel assignment API is also unchanged: `apiClient.getChannels()` (api.ts:294) still feeds `buildRelayChannelViewModels()`. The new `apiClient.getDeviceRegistry()` (api.ts:152) feeds `DeviceTable.tsx` only — a parallel concern that does not interact with the relay matrix view model.

### CRITICAL drift found (line numbers)

**DeviceManager.tsx** — refactored from ~780→307 lines. All line refs in Todos 4+5 are stale:
| Plan reference | Plan says | Actual now |
|---|---|---|
| `DEFAULT_RELAY_STATE` | lines 45-50 | **lines 19-24** |
| `relayChannels` useMemo | lines 132-146 | **lines 40-55** |
| `statusByChannel` useMemo | lines 149-183 | **lines 57-91** |
| `<RelayChannelMatrix>` with `statusByChannel` prop | lines 768-780 / 774 | **line 295** |

### MINOR drift
- `get_all_device_states()` location: plan says `repositories/devices.py:82-92` → actual **lines 169-179** (file grew from device_registry additions)

### NO DRIFT — still accurate (verified against current source)
- `hardware.py` `set_relay_channel_state()`: lines 108-173 (3 branches, no timestamp write) ✅
- `hardware.py` `relay_state()`: lines 176-229 (returns `channels`, `timestamps`, `mcp_connected`) ✅
- `hardware.py` imports + `get_automation_redis()`: lines 15-16, 34-36 ✅
- `devices.py` `control_device()`: lines 188-244 (has `relay_manager` + `database`, NO `automation_redis`) ✅
- `devices.py` dependency functions: `get_relay_manager()` 61-63, `get_database()` 66-68 ✅
- `relayViewModel.ts` `RelayChannelViewModel`: lines 64-76 (11 fields, no mode/override) ✅
- `relayViewModel.ts` `buildRelayChannelViewModels()`: lines 175-208 (3-arg, device-key mapping) ✅
- `relayViewModel.ts` `formatElapsedSince`: line 210+ ✅
- `RelayChannelBox.tsx`: 167 lines, `stateBadgeClasses` 18-28, `RelayStatusLed` 30-39, `resolvedTone`/`resolvedText` 63-65, badge button 130-145, elapsed 150-153 ✅
- `RelayChannelMatrix.tsx`: 129 lines, `ChannelBoxRenderProps` 22-32 (has `statusByChannel`), `renderChannelBox` 34-51 (passes `statusText`/`statusTone` at 44-45) ✅
- `ZoneConfig.tsx`: 484 lines, `statusByChannel` memo 186-205, `@ts-ignore` 185, `statusByChannel` prop 467 ✅
- `relayMatrix.test.tsx` `makeVm()`: lines 6-20 (11 fields, no mode/override) ✅
- `hardware_batch.py` timestamp pattern: lines 478-500 (`before != op.state` at 492-493) ✅
- `schema.py` `relay_raw_override_key()`: lines 148-149 ✅
- `AutomationRedisClient.get()`: synchronous, defined at `redis/__init__.py:135-146` ✅
- `container.py` `get_automation_redis()`: line 389 ✅
- `types/relay.ts` `RelayBoardStateResponse`: lines 37-42 (4 fields, no modes/override_expires_at) ✅
- `api.ts` `getRelayBoardState()`: line 320 (inline return type, not `RelayBoardStateResponse`) ✅ (Metis F6 valid)

### Import path note (Todo 3)
Plan says `from app.redis import AutomationRedisClient` (line 130). The actual code in `hardware.py` uses `from app.redis_client import AutomationRedisClient` (line 16). Both paths work — `app/redis_client.py` is a backward-compat re-export shim. Workers should match the existing `hardware.py` pattern: `from app.redis_client import AutomationRedisClient`. The `get_automation_redis()` return type should be non-optional `AutomationRedisClient` (matching hardware.py), not `AutomationRedisClient | None` (which is the failsafe/mode pattern).

### Verdict
Plan is solid — only 4 stale line refs in DeviceManager.tsx + 1 in repositories/devices.py need patching. The `device_registry` table does not apply.
