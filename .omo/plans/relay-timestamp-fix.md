# Relay Timestamp Fix — Stop Polling, Track Changes in the Loop

## TL;DR

> **Problem**: `hardware_batch.py` polls MCP23017 before/after every tick (30 I2C reads/min) to detect relay state changes. This is wasteful, causes timestamp jitter, and makes elapsed timers reset every 2–5 seconds.
>
> **Root cause**: The batch executor reads hardware state (`self._mcp.get_all_channels()`) twice per execution to compare before/after. But the control loop is the ONLY thing that changes relays — the batch already knows which operations it queued.
>
> **Fix**: Replace hardware polling with `relay_manager.get_device_state()` (internal cache, zero I2C). Only update `cea:relay:timestamps` when a queued operation's target state differs from the cached current state.
>
> **Files**: `hardware_batch.py` (remove polling), `relay_manager.py` (ensure cache accuracy), `hardware.py` (read from Redis)
>
> **Estimated Effort**: Short (1 backend file + verification)
>
> **Critical Path**: hardware_batch.py fix → deploy → verify stable timestamps

---

## Context

### Current Broken Behavior
1. Page loads → shows "IDLE" for a few seconds
2. Relay turns ON → elapsed timer starts from 0
3. Timer resets every 2–5 seconds (whenever API returns a new timestamp)
4. Timestamps in API response update every 2s even though channels stay ON

### Why It Breaks
- `hardware_batch.py:383` captures `relay_states_before = self._mcp.get_all_channels()` (16 I2C reads)
- Operations execute
- `hardware_batch.py:465` captures `relay_states = self._mcp.get_all_channels()` (16 more I2C reads)
- Comparison `before != after` is somehow true for channels that didn't change
- Every channel gets a fresh `now_iso` timestamp on every tick
- API reads these timestamps → frontend timer resets

### Why Polling Is Wrong
The control loop is the sole authority on relay state. It decides "turn heater ON" and queues that operation. Whether the heater was already ON or not, the loop knows. Reading from the MCP to "discover" what changed is:
- **Wasteful**: 32 I2C reads per tick (before + after)
- **Unreliable**: I2C read might differ from internal state due to timing, caching, or race conditions
- **Unnecessary**: The batch executor has the operations list — it knows exactly which devices it touched

---

## Work Objectives

### Core Objective
Make relay elapsed timestamps stable, accurate, and event-driven. A relay that turned ON 3 hours ago should display "3h 12m" and count up smoothly without resetting.

### Concrete Deliverables
- `hardware_batch.py`: Remove `relay_states_before/after` polling; use operation-aware change tracking
- `relay_manager.py` (verify): Ensure `get_device_state()` returns accurate cached state
- `hardware.py`: Unchanged — already reads from Redis correctly

### Definition of Done
- [x] Timestamps only update when a relay's state actually changes (ON→OFF or OFF→ON)
- [x] Timestamps DO NOT update on redundant commands (ON→ON, OFF→OFF)
- [x] Elapsed timer counts up smoothly (1s tick) without resetting
- [x] No I2C polling in `hardware_batch.py` for timestamp detection
- [x] `ruff check` passes, deploy succeeds, health checks pass

### Must Have
- Event-driven timestamp updates (only on real state transitions)
- Zero hardware polling for change detection
- Frontend timer stability (no resets)

### Must NOT Have
- No additional DB queries
- No new Redis keys (reuse `cea:relay:timestamps`)
- No changes to control loop logic outside batch executor
- No changes to frontend (already correct)

---

## Execution Strategy

### Wave 1 (Single Task — Foundation Fix)
```
└── Task 1: Refactor hardware_batch.py to track changes via operation state, not hardware polling
```

### Dependency Matrix
| Task | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| 1 | None | 2 | None |
| 2 | 1 | None | None |

---

## TODOs

- [x] 1. **Refactor `hardware_batch.py` — replace hardware polling with operation-aware change tracking**

  **What to do**:
  1. Remove `relay_states_before = self._mcp.get_all_channels()` (line ~383)
  2. Before executing relay chains, build a map of current cached states:
     ```python
     relay_states_before: dict[str, int] = {}
     for chain in relay_chains:
         for op in chain.operations:
             if isinstance(op, RelayOperation):
                 key = f"{op.location}::{op.cluster}::{op.device_name}"
                 current = op.relay_manager.get_device_state(
                     op.location, op.cluster, op.device_name
                 )
                 relay_states_before[key] = current or 0
     ```
  3. Execute relay chains as before
  4. After execution, for each successful relay operation, compare `relay_states_before[key]` with `op.state`:
     ```python
     for chain in relay_chains:
         success, _ = relay_results.get(chain.device_key, (False, None))
         if not success:
             continue
         for op in chain.operations:
             if isinstance(op, RelayOperation):
                 key = f"{op.location}::{op.cluster}::{op.device_name}"
                 before = relay_states_before.get(key, 0)
                 if before != op.state:
                     # REAL state change — update timestamp
                     channel = op.relay_manager.get_channel(
                         op.location, op.cluster, op.device_name
                     )
                     if channel is not None and 0 <= channel <= 15:
                         timestamps[channel] = now_iso
     ```
  5. Keep `relay_states = self._mcp.get_all_channels()` (single read) for writing `cea:relay:channels` — this is legitimate hardware state mirroring, not change detection
  6. Remove the `zip(relay_states_before, relay_states)` comparison entirely

  **Must NOT do**:
  - Do NOT remove `cea:relay:channels` write — that's legitimate state mirroring
  - Do NOT add I2C reads back in for change detection
  - Do NOT change `RelayOperation` dataclass fields
  - Do NOT touch control engine or device controller logic

  **References**:
  - `hardware_batch.py:383` — current `relay_states_before` polling (REMOVE)
  - `hardware_batch.py:465-485` — current after-execution comparison block (REPLACE)
  - `hardware_batch.py:260-288` — `RelayOperation` dataclass (unchanged)
  - `app/control/relay_manager.py` — verify `get_device_state()` and `get_channel()` exist and work

  **Acceptance Criteria**:
  - [x] `relay_states_before = self._mcp.get_all_channels()` line is gone
  - [x] Before-execution state snapshot uses `relay_manager.get_device_state()` (zero I2C)
  - [x] After-execution, timestamps only update for operations where `before != op.state`
  - [x] `zip(relay_states_before, relay_states)` comparison is gone
  - [x] `ruff check --fix . && ruff format .` passes

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Timestamps are stable when relay stays ON
    Tool: Bash (curl loop)
    Preconditions: automation-service running, at least one relay is ON
    Steps:
      1. VITE_CEA_API_KEY=$(grep VITE_CEA_API_KEY ...)
      2. for i in 1 2 3 4 5; do
           curl -s -H "X-API-Key: $VITE_CEA_API_KEY" http://127.0.0.1:8001/api/hardware/relays/state | python3 -c "
import sys,json
d=json.load(sys.stdin)
ts = d['timestamps']
# Find first ON channel with a timestamp
for i,ch in enumerate(d['channels']):
    if ch and ts[i]:
        print(ts[i])
        break
"
           sleep 3
         done
      3. Assert: all 5 timestamps are identical (or within 2s of each other if a real change happened)
      4. If timestamps differ by >5s between calls, FAIL
    Expected Result: Timestamps are stable across multiple API calls
    Evidence: Terminal output showing stable timestamps
  ```

  **Commit**: YES
  - Message: `fix(relay): track state changes via operation target, not hardware polling`
  - Files: `hardware_batch.py`

---

- [x] 2. **End-to-end verification**

  **What to do**:
  1. `ruff check --fix . && ruff format .`
  2. `./deploy.sh`
  3. Verify API: poll `/api/hardware/relays/state` 5 times over 15s
  4. Assert: timestamps for stable ON channels do not change
  5. Verify frontend: open Devices page, check elapsed time counts up smoothly
  6. Trigger a manual relay toggle via UI → verify timestamp updates to "now"

  **Acceptance Criteria**:
  - [x] Timestamps stable for channels that don't change state
  - [x] Timestamps update to "now" when a relay is manually toggled
  - [x] Elapsed timer counts up: 1s, 2s, 3s... without resetting
  - [x] `npm run build` passes, deploy health checks pass

  **Agent-Executed QA Scenario**:
  ```
  Scenario: End-to-end timestamp stability
    Tool: Bash (curl + timing)
    Preconditions: Deploy completed
    Steps:
      1. Record timestamp for an ON channel (T0)
      2. Wait 10 seconds
      3. Query API again, record timestamp (T1)
      4. Assert: T0 == T1 (stable)
      5. Wait another 10 seconds
      6. Query API again, record timestamp (T2)
      7. Assert: T0 == T2 (still stable)
      8. Calculate elapsed from T0 to now — should be ~20 seconds
    Expected Result: Timestamps unchanged, elapsed ~20s
    Evidence: Terminal output with all three timestamps
  ```

  **Commit**: NO (verification only)

---

## Commit Strategy

| After Task | Message | Files |
|-----------|---------|-------|
| 1 | `fix(relay): track state changes via operation target, not hardware polling` | `hardware_batch.py` |

---

## Success Criteria

### Verification Commands
```bash
# Stable timestamps test
VITE_CEA_API_KEY=$(grep VITE_CEA_API_KEY Infrastructure/frontend/.env.production | cut -d= -f2)
for i in 1 2 3 4 5; do
  curl -s -H "X-API-Key: $VITE_CEA_API_KEY" http://127.0.0.1:8001/api/hardware/relays/state | \
    python3 -c "import sys,json; d=json.load(sys.stdin); ts=[t for t,c in zip(d['timestamps'],d['channels']) if c and t]; print(ts[0] if ts else 'none')"
  sleep 3
done
# Expected: all 5 lines show the same timestamp (±2s)

# Ruff
cd Infrastructure/automation-service && ruff check --fix . && ruff format .
```

### Final Checklist
- [x] No `self._mcp.get_all_channels()` used for change detection
- [x] `relay_manager.get_device_state()` used for before-state snapshot
- [x] Timestamps only update on `before != target_state`
- [x] Frontend elapsed time counts up smoothly (no resets)
- [x] `ruff check` passes, deploy health checks pass
