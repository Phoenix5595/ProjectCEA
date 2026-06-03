## 2026-06-02 21:08 — Plan Complete

### Problem
`hardware_batch.py` was polling MCP23017 (`get_all_channels()`) twice per batch execution — 32 I2C reads per 2s tick — just to detect relay state changes. The comparison `before != after` from hardware readback was always true for channels that stayed ON, causing timestamps to update on every tick and elapsed timers to reset every 2–5 seconds.

### Root Cause
Hardware polling is unreliable for change detection. I2C timing/type differences made `zip(relay_states_before, relay_states)` return different states even when channels didn't change.

### Fix
Replaced hardware polling with `relay_manager.get_device_state()` — a cached internal state lookup with zero I2C reads.
- Before execution: snapshot cached states via `relay_manager.get_device_state()`
- After execution: only update `cea:relay:timestamps` when `before != op.state`
- This only triggers on actual state transitions

### Verification
- Polled API 4 times over 15s — all timestamps identical (`2026-06-02T21:06:29.425122Z`)
- `ruff check` passed, deploy health checks all green
- No I2C polling remains for change detection

### Key Insight
The control loop is the sole authority on relay state. The batch executor knows which operations it queued — reading from hardware to "discover" what changed is unnecessary and unreliable.

### Release
`20260602-170146-ceb622f` — hardware_batch.py only
