# Relay Elapsed Time — Replace "Unknown" with Real State Change Timestamps

## TL;DR

> **Quick Summary**: Extend `GET /api/hardware/relays/state` to return a `timestamps` field — per-channel ISO8601 `last_changed_at` derived from `control_history.MAX(timestamp) GROUP BY channel`. The frontend uses these to replace the misleading "Unknown" elapsed time on every relay box with accurate "2h 18m" readings. Same data source powers a future relay state timeline component with zero new endpoints.
>
> **Deliverables**:
> - Extended `GET /api/hardware/relays/state` — adds `timestamps: [16 × ISO | null]` alongside existing `channels`
> - Extended `GET /api/control/history` — adds optional `channel`, `since`, `until` query filters
> - Fixed `RelayChannelBox` — uses real timestamps instead of `lastStateChangeByDevice` from control history polling
> - Fixed `ZoneConfig` — fetches relay state with timestamps, no more empty `{}` fallback
>
> **Estimated Effort**: Short (2 backend + 2 frontend + 1 verify)
> **Parallel Execution**: YES — backend and frontend in separate waves
> **Critical Path**: Backend DB query → API extension → frontend consumption → verification

---

## Context

### Original Request
> "it makes no sense, the light relay has been on for an hour"

The elapsed time field at the bottom of each relay box showed "Unknown" even when a relay had been ON for an hour. Root cause: the frontend derived `lastStateChangeAt` by scanning `control_history` entries fetched per-room (max 100, discarded in ZoneConfig entirely). Events older than ~2 minutes fell off the window.

### Metis Review
**Confirmed from codebase**:
- `control_history.channel` is `INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15)` — every row has a valid channel number
- `/api/hardware/relays/state` returns `{channels, mcp_connected, simulation}` — no timestamps (hardware.py:120-124)
- `/api/control/history` accepts only `location`, `cluster`, `limit` — no channel/time filters (devices.py:275-293)
- ZoneConfig line 94: `useState<boolean[]>` — strips all data except booleans
- ZoneConfig line 134: passes empty `{}` to `buildRelayChannelViewModels`
- `device_states` is keyed by device_name NOT channel — `control_history` is the only viable source

**Decisions forced by Metis**:
- Remove DeviceManager's redundant 15s `getControlHistory` polling — timestamps come from relay/state now
- Query is location/cluster-agnostic: relay channels are global (0-15), no room filter needed
- Add `WHERE timestamp > NOW() - INTERVAL '30 days'` bound on the aggregate query to prevent full hypertable scan (performance guard)

### Why This Approach
- **One data source**: `control_history` already records `timestamp` + `channel` for every state change
- **Zero new endpoints**: extend two existing APIs, backward-compatible
- **Future-proof**: same data powers a relay state timeline component later
- **No new Redis keys**: timestamps live in PostgreSQL where they belong (historical data)
- **Race condition acceptable**: eventual consistency between Redis (instant) and TimescaleDB (≤100ms batch) — frontend polls every 5s

### Research Findings (Comprehensive)
| Source | Has per-channel timestamp? | Used? |
|--------|---------------------------|-------|
| `control_history.timestamp` + `channel` | ✅ Yes | Currently wasted — queried by device_name, 100-row window |
| `cea:relay:channels` (Redis) | ❌ No | State only, no timestamp |
| `device_states.updated_at` | ❌ No | Keyed by device_name, not channel |
| `automation_state` | ❌ No | No channel column |
| `RelayManager._current_states` | ❌ No | In-memory, no timestamps |

**The data already exists.** The query `SELECT channel, MAX(timestamp) FROM control_history GROUP BY channel` returns exactly what's needed. It just needs to be exposed through the API.

---

## Work Objectives

### Core Objective
Replace the unreliable "Unknown" elapsed time on relay channel boxes with accurate, real timestamps derived from the `control_history` table. Use the same data source to power a future relay state timeline component.

### Concrete Deliverables
- `Infrastructure/automation-service/app/repositories/control_actions.py` — new method: `get_last_changed_per_channel()`
- `Infrastructure/automation-service/app/routes/hardware.py` — extend `relay_state` endpoint with `timestamps` field
- `Infrastructure/automation-service/app/routes/devices.py` — extend `get_control_history` with `channel`, `since`, `until` filters
- `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` — use API timestamps
- `Infrastructure/frontend/src/pages/ZoneConfig.tsx` — store full response with timestamps
- `Infrastructure/frontend/src/types/relay.ts` — update `RelayBoardStateResponse` type

### Definition of Done
- [ ] `GET /api/hardware/relays/state` returns `timestamps: [string | null]` alongside `channels`
- [ ] Timestamps are ISO8601 `MAX(timestamp)` per channel from `control_history`, or `null` if never changed
- [ ] Response shape backward-compatible: `channels` unchanged
- [ ] `GET /api/control/history?channel=N&since=&until=` works with new optional filters
- [ ] `RelayChannelBox` shows real elapsed time ("2h 18m" instead of "Unknown")
- [ ] ZoneConfig relay boxes also show real elapsed time (currently always null)
- [ ] `npm run build` passes, `ruff check` passes
- [ ] No new API endpoints, no new Redis keys

### Must Have
- Backend: per-channel timestamps in `/api/hardware/relays/state` response
- Backend: channel/time filters on `/api/control/history`
- Frontend: use API timestamps directly instead of `lastStateChangeByDevice` map
- Frontend: fix ZoneConfig to receive timestamps

### Must NOT Have (Guardrails)
- No new API endpoints — extend existing only
- No new Redis keys — `control_history` table is the source of truth
- No changes to `control_history` write path
- No changes to `hardware_batch.py` relay state write
- No changes to `relayViewModel.ts` core logic
- No changes to MCP23017 driver or control loop
- No changes to polling intervals (5s relay, 15s history stay)
- No removal of existing `lastStateChangeByDevice` for other consumers

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**

### Test Decision
- **Infrastructure exists**: YES (pytest, curl, npm build)
- **Automated tests**: NO (API extension — existing infrastructure sufficient)
- **Framework**: curl for API, grep for frontend, npm build for verification

### Agent-Executed QA Scenarios

| Task | Tool | How Agent Verifies |
|------|------|-------------------|
| 1 (DB query) | Bash (curl) | Verify timestamps in API response |
| 2 (API extension) | Bash (curl) | Verify response shape backward-compatible |
| 3 (History filters) | Bash (curl) | Verify new query params work |
| 4 (Frontend types) | Bash (npm build + grep) | Build passes, types updated |
| 5 (Box + ZoneConfig) | Bash (npm build + grep) | Build passes, no more "Unknown" fallback |
| 6 (Final verify) | Bash (curl + build) | End-to-end sweep |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Backend — sequential due to dependency):
├── Task 1: DB repository — get_last_changed_per_channel() query
└── Task 2: Extend relay_state API with timestamps field

Wave 2 (Backend + Frontend — parallel):
├── Task 3: Extend control_history API with filters
├── Task 4: Update frontend types
└── Task 5: Update RelayChannelBox + ZoneConfig

Wave 3 (Final):
└── Task 6: End-to-end verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2 | None (foundation) |
| 2 | 1 | 5, 6 | 3 |
| 3 | None | 6 | 2, 4, 5 |
| 4 | 2 | 5 | 3 |
| 5 | 2, 4 | 6 | None |
| 6 | 2, 3, 5 | None | None (final) |

---

## TODOs

- [ ] 1. **Backend: Add `get_last_changed_per_channel()` repository method**

  **What to do**:
  In `Infrastructure/automation-service/app/repositories/control_actions.py`, add a new method:

  ```python
  async def get_last_changed_per_channel(self) -> list[dict[str, Any]]:
      """Return the most recent timestamp per relay channel (0-15).
      
      Returns a list of 16 dicts: {"channel": int, "last_changed": str | None}
      where last_changed is ISO8601 or null if never changed.
      
      Bounded to last 30 days to prevent full hypertable scan (Metis guard).
      """
      async with self.db.pool.acquire() as conn:
          rows = await conn.fetch("""
              SELECT channel, MAX(timestamp) AS last_changed
              FROM control_history
              WHERE channel BETWEEN 0 AND 15
                AND timestamp > NOW() - INTERVAL '30 days'
              GROUP BY channel
              ORDER BY channel
          """)
      
      # Build full 16-channel array with nulls for never-changed channels
      channel_map: dict[int, str | None] = {}
      for row in rows:
          channel_map[row["channel"]] = row["last_changed"].isoformat() if row["last_changed"] else None
      
      return [
          {"channel": i, "last_changed": channel_map.get(i)}
          for i in range(16)
      ]
  ```

  **Must NOT do**:
  - Do NOT modify `log_control_action()` — write path unchanged
  - Do NOT modify `get_recent_control_history()` — read path unchanged
  - Do NOT add Redis writes — PostgreSQL is the source of truth for timestamps

  **References**:
  - `Infrastructure/automation-service/app/repositories/control_actions.py:43-85` — existing `log_control_action()` method
  - `Infrastructure/automation-service/app/repositories/control_actions.py:87-124` — existing `get_recent_control_history()` pattern
  - `Infrastructure/automation-service/alembic/versions/001_baseline.py:40-62` — `control_history` table schema (has `channel` + `timestamp`)

  **Acceptance Criteria**:
  - [ ] Method exists at `control_actions.py` as `get_last_changed_per_channel()`
  - [ ] Returns list of 16 dicts with `channel` (0-15) and `last_changed` (ISO string or None)
  - [ ] Channels with no history return `last_changed: null`

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Repository method returns per-channel timestamps
    Tool: Bash (Python unit test via pytest)
    Preconditions: control_history table has sample data
    Steps:
      1. cd Infrastructure/automation-service
      2. python3 -c "
import asyncio
from app.repositories.control_actions import ControlActionRepository
# Integration test: verify method exists and returns 16 items
print('Method exists:', hasattr(ControlActionRepository, 'get_last_changed_per_channel'))
       "
    Expected Result: Method exists, returns structured data
    Evidence: Terminal output captured
  ```
  
  Actually, let's verify differently — check the method at import time:
  ```
  Scenario: Repository method is callable
    Tool: Bash (Python import check)
    Steps:
      1. cd Infrastructure/automation-service
      2. python3 -c "from app.repositories.control_actions import ControlActionRepository; print('OK' if hasattr(ControlActionRepository, 'get_last_changed_per_channel') else 'MISSING')"
      3. Assert: output contains "OK"
    Expected Result: Method exists and is importable
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `feat(relay): add get_last_changed_per_channel() — per-channel MAX(timestamp) from control_history`
  - Files: `control_actions.py`

---

- [ ] 2. **Backend: Extend `GET /api/hardware/relays/state` with timestamps**

  **What to do**:
  Modify `Infrastructure/automation-service/app/routes/hardware.py` (lines 89-124):

  1. Inject `DatabaseManager` dependency (already available via `get_database`)
  2. After getting channel states from Redis/hardware, call `database.control_action_repo.get_last_changed_per_channel()`
  3. Extract timestamps into a parallel array: `[ts0, ts1, ..., ts15]`
  4. Add to response dict: `"timestamps": timestamps`

  **Target response shape**:
  ```json
  {
    "channels": [true, false, true, false, ...],
    "timestamps": ["2026-06-01T12:00:00Z", null, "2026-06-01T09:15:00Z", null, ...],
    "mcp_connected": true,
    "simulation": false
  }
  ```

  **Backward compat**: `channels`, `mcp_connected`, `simulation` unchanged. `timestamps` is additive — old frontends ignore it.

  **Must NOT do**:
  - Do NOT change the existing `channels` array shape or order
  - Do NOT remove `mcp_connected` or `simulation` fields
  - Do NOT add per-channel Redis writes for timestamps

  **References**:
  - `Infrastructure/automation-service/app/routes/hardware.py:89-124` — current endpoint
  - `Infrastructure/automation-service/app/routes/hardware.py:1-20` — existing imports and dependencies
  - `Infrastructure/automation-service/app/container.py` — `get_database` dependency (if needed)

  **Acceptance Criteria**:
  - [ ] Response includes `"timestamps"` field — array of 16 ISO strings or null
  - [ ] `timestamps[i]` corresponds to `channels[i]` (same channel ordering)
  - [ ] `channels` field unchanged (backward compatible)
  - [ ] Timestamps are real ISO8601 strings, not "Unknown" placeholders

  **Agent-Executed QA Scenario**:
  ```
  Scenario: API returns timestamps alongside channels
    Tool: Bash (curl)
    Preconditions: automation-service running, control_history has entries
    Steps:
      1. curl -s http://localhost:8001/api/hardware/relays/state
      2. Assert: response.timestamps exists
      3. Assert: response.timestamps.length === 16
      4. Assert: response.timestamps[0] is either null or matches ISO8601 regex (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})
      5. Assert: response.channels.length === 16 (unchanged)
    Expected Result: timestamps array present, channels unchanged
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `feat(relay): add timestamps to relay/state response — per-channel last_changed from DB`
  - Files: `routes/hardware.py`

---

- [ ] 3. **Backend: Extend `GET /api/control/history` with channel + time filters**

  **What to do**:
  Modify `Infrastructure/automation-service/app/routes/devices.py` (lines 275-301):

  1. Add optional query params: `channel: int | None = None`, `since: str | None = None`, `until: str | None = None`
  2. Pass to repository method
  3. Add a new repository method `get_control_history_filtered()` with the optional WHERE clauses

  **New query** (in `control_actions.py`):
  ```python
  async def get_control_history_filtered(
      self, location: str, cluster: str, limit: int = 100,
      channel: int | None = None, since: str | None = None, until: str | None = None,
  ) -> list[dict[str, Any]]:
      query = """
          SELECT timestamp, location, cluster, device_name, old_state, new_state, mode, reason, load_percent, channel
          FROM control_history
          WHERE location = $1 AND cluster = $2
      """
      params: list[Any] = [location, cluster]
      param_idx = 3
      
      if channel is not None:
          query += f" AND channel = ${param_idx}"
          params.append(channel)
          param_idx += 1
      if since is not None:
          query += f" AND timestamp >= ${param_idx}"
          params.append(since)
          param_idx += 1
      if until is not None:
          query += f" AND timestamp <= ${param_idx}"
          params.append(until)
          param_idx += 1
      
      query += f" ORDER BY timestamp DESC LIMIT ${param_idx}"
      params.append(limit)
      
      async with self.db.pool.acquire() as conn:
          rows = await conn.fetch(query, *params)
      return [dict(row) for row in rows]
  ```

  **Must NOT do**:
  - Do NOT change the existing `get_control_history` endpoint signature — add optional params
  - Do NOT remove or rename existing params (`location`, `cluster`, `limit`)
  - Do NOT change the response shape for existing callers (no new fields added by default)

  **References**:
  - `Infrastructure/automation-service/app/routes/devices.py:275-301` — current endpoint
  - `Infrastructure/automation-service/app/repositories/control_actions.py:87-124` — current query method

  **Acceptance Criteria**:
  - [ ] `?channel=5` filters to only entries for channel 5
  - [ ] `?since=2026-06-01T00:00:00Z&until=2026-06-02T00:00:00Z` filters to date range
  - [ ] Existing calls (no new params) behave identically — backward compatible
  - [ ] `channel` field included in response when filtered

  **Agent-Executed QA Scenario**:
  ```
  Scenario: History endpoint accepts new filters
    Tool: Bash (curl)
    Preconditions: automation-service running
    Steps:
      1. curl -s "http://localhost:8001/api/control/history?location=Flower+Room&cluster=main&limit=5"
      2. Assert: returns array (backward compatible — no new params needed)
      3. curl -s "http://localhost:8001/api/control/history?location=Flower+Room&cluster=main&channel=3&limit=5"
      4. Assert: all returned entries have channel=3
      5. curl -s "http://localhost:8001/api/control/history?location=Flower+Room&cluster=main&limit=5&since=2024-01-01T00:00:00Z"
      6. Assert: returns entries (since filter works)
    Expected Result: All three calls succeed, filters work
    Evidence: Response bodies captured
  ```

  **Commit**: YES
  - Message: `feat(relay): add channel/since/until filters to control/history endpoint`
  - Files: `routes/devices.py`, `control_actions.py`

---

- [ ] 4. **Frontend: Update types to include timestamps**

  **What to do**:
  1. Update `Infrastructure/frontend/src/types/relay.ts` — add `timestamps` to `RelayBoardStateResponse`:
     ```typescript
     export interface RelayBoardStateResponse {
       channels: boolean[]
       timestamps: (string | null)[]  // ISO8601 or null per channel
       mcp_connected: boolean
       simulation: boolean
     }
     ```
  2. Update `DEFAULT_RELAY_STATE` in `DeviceManager.tsx` to include `timestamps: Array(16).fill(null)`

  **Must NOT do**:
  - Do NOT change `channels` type or position in the interface
  - Do NOT add timestamps to `ChannelInfo` or `RelayChannelViewModel`

  **References**:
  - `Infrastructure/frontend/src/types/relay.ts:35-39` — current `RelayBoardStateResponse`
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:45-49` — `DEFAULT_RELAY_STATE`

  **Acceptance Criteria**:
  - [ ] `RelayBoardStateResponse` has `timestamps: (string | null)[]`
  - [ ] `DEFAULT_RELAY_STATE` includes `timestamps: Array(16).fill(null)`
  - [ ] `npm run build` passes

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Types updated, build passes
    Tool: Bash (grep + npm build)
    Preconditions: Working directory = Infrastructure/frontend
    Steps:
      1. grep "timestamps" src/types/relay.ts
      2. Assert: ≥ 1 match (field exists in type)
      3. npm run build 2>&1 | tail -1
      4. Assert: contains "built in"
    Expected Result: Type updated, build clean
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `feat(frontend): add timestamps to RelayBoardStateResponse type`
  - Files: `types/relay.ts`, `DeviceManager.tsx` (DEFAULT_RELAY_STATE only)

---

- [ ] 5. **Frontend: Use API timestamps in RelayChannelBox + fix ZoneConfig + remove redundant polling**

  **What to do**:

  ### A. RelayChannelBox.tsx — use timestamps directly
  Instead of deriving `elapsedLabel` from `channel.lastStateChangeAt` (which comes from the unreliable `lastStateChangeByDevice` map), use the `relayState.timestamps` array directly.

  **Approach**: Pass `timestamp` as a prop to `RelayChannelBox`. The parent (DeviceManager or ZoneConfig) provides `relayState.timestamps[channel]`.

  1. Add `timestamp: string | null` to `RelayChannelBoxProps`
  2. Replace `elapsedLabel` computation:
     ```typescript
     const elapsedLabel = timestamp 
       ? formatElapsedSince(timestamp, nowMs) 
       : '—'
     ```

  ### B. DeviceManager.tsx — pass timestamps to box, remove control_history polling
  1. In the `renderChannelBox` function (via `RelayChannelMatrix`), pass `relayState.timestamps[channel.channel]` as the `timestamp` prop
  2. **Remove the `getControlHistory` polling** (lines 352-389): the `useEffect` that fetches history per location/cluster pair, calls `buildLastStateChangeMap`, and refreshes every 15s
  3. **Remove `lastStateChangeByDevice` state**: no longer needed since timestamps come from `relay/state`
  4. **Remove `buildLastStateChangeByDevice` from `useMemo`**: `buildRelayChannelViewModels` no longer needs the third argument — or pass `{}` since the timestamp now comes from the component prop, not the view model

  ### C. ZoneConfig.tsx — store full response
  1. Change `relayState` type from `boolean[]` to `{ channels: boolean[], timestamps: (string|null)[] }` (or full `RelayBoardStateResponse`)
  2. Store full response in `fetchRelayData()`: `setRelayState({ channels: stateRes.channels, timestamps: stateRes.timestamps })` 
  3. Default: `{ channels: Array(16).fill(false), timestamps: Array(16).fill(null) }`
  4. Pass `relayState.timestamps[channel]` to `RelayChannelBox`

  **Must NOT do**:
  - Do NOT remove `lastStateChangeByDevice` if other parts of DeviceManager use it — check first
  - Do NOT change `buildRelayChannelViewModels` signature
  - Do NOT change `RelayChannelViewModel` type
  - Do NOT change the polling interval for relay state (5s stays)

  **References**:
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:42` — current `elapsedLabel` computation
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:4-15` — props interface
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:352-389` — control_history polling useEffect (to remove)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:88` — `lastStateChangeByDevice` state (to remove)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:133-141` — relayChannels useMemo (to simplify)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:93-94` — relayState declaration
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:99-108` — fetchRelayData

  **Acceptance Criteria**:
  - [ ] `RelayChannelBox` accepts `timestamp` prop
  - [ ] Elapsed time shows "—" when `timestamp` is null (never changed)
  - [ ] Elapsed time shows "2h 18m" when `timestamp` is valid ISO
  - [ ] `grep "Unknown" RelayChannelBox.tsx` → 0 matches (no more "Unknown" string)
  - [ ] `grep "getControlHistory" DeviceManager.tsx` → 0 matches (polling removed)
  - [ ] `grep "lastStateChangeByDevice" DeviceManager.tsx` → 0 matches (state removed)
  - [ ] ZoneConfig relay boxes receive timestamps (no longer empty `{}`)
  - [ ] `npm run build` passes

  **Agent-Executed QA Scenario**:
  ```
  Scenario: RelayChannelBox uses real timestamps, old polling removed
    Tool: Bash (grep + npm build)
    Preconditions: All backend tasks complete
    Steps:
      1. grep -c "Unknown" src/components/devices/RelayChannelBox.tsx
      2. Assert: output is "0" (no more "Unknown" string)
      3. grep -c "getControlHistory" src/components/DeviceManager.tsx
      4. Assert: output is "0" (polling removed)
      5. grep -c "lastStateChangeByDevice" src/components/DeviceManager.tsx
      6. Assert: output is "0" (state removed)
      7. grep -c "timestamps" src/pages/ZoneConfig.tsx
      8. Assert: output ≥ 1 (timestamps used)
      9. npm run build 2>&1 | tail -1
      10. Assert: contains "built in"
    Expected Result: No "Unknown", old polling gone, timestamps used, build passes
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `fix(frontend): use API timestamps for relay elapsed time — replace "Unknown", remove redundant polling`
  - Files: `RelayChannelBox.tsx`, `DeviceManager.tsx`, `ZoneConfig.tsx`

---

- [ ] 6. **End-to-End Verification**

  **What to do**:
  1. Run `ruff check --fix . && ruff format .` on automation-service
  2. Deploy via `./deploy.sh`
  3. Verify API: `curl http://mothernode:8001/api/hardware/relays/state` → has `timestamps` field
  4. Verify backward compat: `channels` field unchanged
  5. Verify history filters: `curl ...?channel=5&since=...` works
  6. Verify frontend build: `npm run build` passes
  7. Verify frontend: no "Unknown" string in `RelayChannelBox.tsx`

  **Acceptance Criteria**:
  - [ ] `ruff check` passes
  - [ ] `npm run build` passes
  - [ ] API returns `timestamps` array with 16 entries
  - [ ] History endpoint accepts `channel`, `since`, `until` params
  - [ ] Frontend uses timestamps, no "Unknown" fallback

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Full end-to-end sweep
    Tool: Bash (curl + grep + build)
    Steps:
      1. ruff check --fix . && ruff format . 2>&1 | tail -3
      2. cd Infrastructure/frontend && npm run build 2>&1 | tail -2
      3. curl -s http://localhost:8001/api/hardware/relays/state | python3 -c "
  import sys,json
  d = json.load(sys.stdin)
  assert 'timestamps' in d, 'Missing timestamps'
  assert len(d['timestamps']) == 16, f'Bad timestamps len {len(d[\"timestamps\"])}'
  assert 'channels' in d and len(d['channels']) == 16
  print('OK')
  "
      4. grep -c "Unknown" src/components/devices/RelayChannelBox.tsx
      5. Assert: output is 0
    Expected Result: All checks pass
    Evidence: Full terminal output captured
  ```

  **Commit**: YES
  - Message: `test(relay): verify end-to-end relay timestamps after deploy`
  - Files: no code changes (verification only)

---

## Commit Strategy

| After Task | Message | Files |
|-----------|---------|-------|
| 1 | `feat(relay): add get_last_changed_per_channel() — per-channel MAX(timestamp) from control_history` | `control_actions.py` |
| 2 | `feat(relay): add timestamps to relay/state response — per-channel last_changed from DB` | `routes/hardware.py` |
| 3 | `feat(relay): add channel/since/until filters to control/history endpoint` | `routes/devices.py`, `control_actions.py` |
| 4 | `feat(frontend): add timestamps to RelayBoardStateResponse type` | `types/relay.ts`, `DeviceManager.tsx` |
| 5 | `fix(frontend): use API timestamps for relay elapsed time — replace "Unknown"` | `RelayChannelBox.tsx`, `DeviceManager.tsx`, `ZoneConfig.tsx` |
| 6 | `test(relay): verify end-to-end relay timestamps after deploy` | (verification only) |

---

## Success Criteria

### Verification Commands
```bash
# API returns timestamps
curl -s http://localhost:8001/api/hardware/relays/state | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert 'timestamps' in d and len(d['timestamps'])==16
assert 'channels' in d and len(d['channels'])==16
print('OK')
"

# History endpoint accepts new filters
curl -s "http://localhost:8001/api/control/history?location=Flower+Room&cluster=main&channel=0&limit=3"

# Frontend build
cd Infrastructure/frontend && npm run build

# No "Unknown" in RelayChannelBox
grep -c "Unknown" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx
# Expected: 0

# Timestamps prop in RelayChannelBox
grep -c "timestamp" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx
# Expected: ≥ 2

# Ruff
cd Infrastructure/automation-service && ruff check --fix . && ruff format .
```

### Final Checklist
- [ ] `GET /api/hardware/relays/state` returns `timestamps` alongside `channels` (backward compatible)
- [ ] `GET /api/control/history` accepts `?channel=N&since=&until=` filters
- [ ] `RelayChannelBox` elapsed time uses API timestamps — no "Unknown" string
- [ ] ZoneConfig relay boxes show real elapsed times (not always "—")
- [ ] DeviceManager's redundant `getControlHistory` polling removed (Metis: no longer needed)
- [ ] `lastStateChangeByDevice` state removed from DeviceManager
- [ ] `npm run build` passes, `ruff check` passes
- [ ] No new endpoints, no new Redis keys
- [ ] Same data source (`control_history`) will power future timeline component
- [ ] Query bounded to 30 days to prevent full hypertable scan (Metis guard)
- [ ] Ready for relay state duration component — just subtract `now - timestamp`
