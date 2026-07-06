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
