# ZoneConfig Compact Relay Matrix

## TL;DR

> **Quick Summary**: Add the existing compact `RelayChannelMatrix` (2 columns × 8 rows, all 16 MCP23017 channels) to the ZoneConfig control tab page. Reuse the existing `RelayChannelMatrix` + `RelayChannelBox` components. Wire relay state polling, Auto/Off/Timer dropdown menus, and adjust the 2-column layout to 3 columns (Light Schedule ~25% / Climate+Lights ~40% / Relay Matrix ~35%).
>
> **Deliverables**:
> - Relay data fetching in ZoneConfig (`getChannels()` + `getRelayBoardState()`)
> - Menu state management (dropdown per channel, manual timers)
> - API actions (Auto / Off / Timer control via `setDeviceMode` + `controlDevice`)
> - Layout: 2-column → 3-column, relay matrix on the right
> - Error states (MCP disconnected, API failures)
>
> **Estimated Effort**: Quick (1 file modified, 2 existing components reused)
> **Parallel Execution**: NO — sequential (data wiring → UI integration)
> **Critical Path**: Data fetching → menu state → API actions → layout integration

---

## Context

### Original Request
> "in the frontend, can you do a vertical rectangular box, containing inside 2 columns of 8 boxes... it might live on the side of the light intensity slider and climate table in the control tab of each room... the active ones that aren't greyed out are interactive with a click making a drop down menu appear with the options on auto and off"

### Interview Summary
**Key Discussions**:
- **Placement**: Relay matrix on the RIGHT side of the Light Schedule + Climate Periods row; LightIntensity + ClimatePeriodsTable become thinner
- **Content**: All 16 MCP23017 relay channels, shown globally (not room-filtered); unassigned channels greyed out
- **Interactivity**: Assigned channels → click state badge → dropdown (Auto, Off, ON 5m/10m/30m/1h)
- **Reuse**: Existing `RelayChannelMatrix` (variant="compact"), `RelayChannelBox`, `relayViewModel.ts`
- **Polling**: 5-second interval for relay state (same as DeviceManager)

### Metis Review
**Identified Gaps** (all addressed):
- ✅ Cluster normalization: use `main` for device APIs (ZoneConfig already defaults to `main` — safe)
- ✅ Timer lifecycle: cleanup on unmount/route change (follow DeviceManager pattern)
- ✅ Error state: `mcp_connected: false` → grey out all channels with error indicator
- ✅ Layout fit: ClimatePeriodsTable + LightIntensity must shrink to ~40% width gracefully
- ✅ All 16 channels confirmed by user (global view, grey out unassigned rooms)

---

## Work Objectives

### Core Objective
Add a compact relay control matrix to ZoneConfig's control tab, reusing existing components, so operators can toggle relay devices (Auto/Off/Timers) directly from the room control page.

### Concrete Deliverables
- `ZoneConfig.tsx` — Updated with relay data fetching, menu state, API actions, relay matrix JSX, 3-column layout
- No new files (reuse `RelayChannelMatrix`, `RelayChannelBox`, `relayViewModel`)

### Definition of Done
- [ ] `npm run build` passes (0 errors)
- [ ] ZoneConfig page renders 16-channel relay matrix alongside LightIntensity + ClimatePeriods
- [ ] Assigned channels: clicking state badge opens Auto/Off/Timer dropdown
- [ ] Unassigned channels: greyed out, state badge disabled
- [ ] Auto/Off/Timer actions call correct backend APIs
- [ ] `mcp_connected: false` shows error indicator
- [ ] Timers expire correctly (cleanup on unmount)
- [ ] Layout fits viewport at 1920px (no horizontal scroll)

### Must Have
- All 16 relay channels displayed in compact 2-column × 8-row grid
- Relay state (ON/OFF/Unknown) visible per channel
- Device name, location, elapsed time displayed per channel
- Dropdown menu on assigned channels (Auto, Off, Timers)
- Greyed-out styling for unassigned channels
- 5-second relay state polling
- `mcp_connected: false` error indicator
- Channel data loaded once on mount, relay state polled

### Must NOT Have (Guardrails)
- No changes to `RelayChannelMatrix.tsx` or `RelayChannelBox.tsx` (reuse only)
- No changes to `relayViewModel.ts`
- No new API client methods in `api.ts`
- No new backend endpoints
- No changes to Climate Timeline row or Notes row
- No channel assignment UI (belongs on Devices page)
- No WebSocket subscriptions (5s polling is sufficient)
- No room filtering of channels (show all 16 globally)
- No editingChannel prop (not needed for read-only display)
- No changes to the main Devices page relay matrix
- No changes to `TopRibbon.tsx`, `ControlActionsContext.tsx`, or `Layout.tsx`

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> ALL tasks MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (npm/tsc)
- **Automated tests**: NO (no frontend test framework)
- **Framework**: N/A

### Agent-Executed QA Scenarios (MANDATORY)

| Task | Tool | How Agent Verifies |
|------|------|-------------------|
| 1 (Data fetching) | Bash (npm build + lint) | Build passes, imports resolve, no TypeScript errors |
| 2 (Menu state) | Bash (npm build) | Build passes |
| 3 (API actions) | Bash (npm build) | Build passes |
| 4 (Layout + JSX) | Playwright + Bash | Navigate to page, verify relay matrix presence, verify layout width |
| 5 (Error states) | Bash + pattern check | Verify error handling code paths exist in file |

---

## Execution Strategy

### Sequential Execution

```
Task 1: Data fetching (relay state + channels)
    ↓
Task 2: Menu state management (timers, menuOpenChannel)
    ↓
Task 3: API actions (handleRelayMenuAction)
    ↓
Task 4: Layout + JSX integration (RelayChannelMatrix + 3-column)
    ↓
Task 5: Verification (build + Playwright)
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 4 | None (foundation) |
| 2 | 1 | 3, 4 | None |
| 3 | 2 | 4 | None |
| 4 | 1, 2, 3 | 5 | None |
| 5 | 4 | None | None (final) |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 1 | 1 | `category="visual-engineering", skills=["frontend-ui-ux"]` |
| 2 | 2, 3 | `category="visual-engineering", skills=["frontend-ui-ux"]` (sequential) |
| 3 | 4 | `category="visual-engineering", skills=["frontend-ui-ux"]` |
| 4 | 5 | `category="quick", skills=[]` (build verification) |

---

## TODOs

- [x] 1. Add Relay Data Fetching to ZoneConfig

  **What to do**:
  - Import `RelayChannelMatrix` from `../components/devices/RelayChannelMatrix`
  - Import `buildRelayChannelViewModels` from `../components/devices/relayViewModel`
  - Import types: `ChannelInfo` from `../types/relay`, `RelayChannelViewModel` from relayViewModel
  - Add 4 new state variables near the top of the ZoneConfig function body (after line 84, before `roomMode`):
    ```typescript
    const [relayChannels, setRelayChannels] = useState<RelayChannelViewModel[]>([])
    const [relayState, setRelayState] = useState<boolean[] | null>(null)
    const [mcpConnected, setMcpConnected] = useState<boolean>(true)
    const [channelInfoList, setChannelInfoList] = useState<ChannelInfo[]>([])
    ```
  - Add a `fetchRelayData` useCallback:
    ```typescript
    const fetchRelayData = useCallback(async () => {
      try {
        const stateRes = await apiClient.getRelayBoardState()
        setRelayState(stateRes.channels)
        setMcpConnected(stateRes.mcp_connected)
      } catch (err) {
        logger.error('Failed to fetch relay board state:', err)
        setRelayState(null)
        setMcpConnected(false)
      }
    }, [])
    ```
  - Add `loadChannels` useCallback (called once on mount):
    ```typescript
    const loadChannels = useCallback(async () => {
      try {
        const res = await apiClient.getChannels()
        setChannelInfoList(Object.values(res.channels))
      } catch (err) {
        logger.error('Failed to fetch channel assignments:', err)
      }
    }, [])
    ```
  - Add useEffect to load channels once on mount
  - Add useEffect to poll relay state every 5000ms (cleanup on unmount)
  - Add useEffect to compute `relayChannels` from `channelInfoList` + `relayState`:
    ```typescript
    useEffect(() => {
      setRelayChannels(buildRelayChannelViewModels(channelInfoList, relayState, {}))
    }, [channelInfoList, relayState])
    ```
  - Place all new code BEFORE the existing `loadRoomMode` function (around line ~108)

  **Must NOT do**:
  - Do NOT remove any existing state or functions
  - Do NOT change the existing polling (LightIntensity, etc.)
  - Do NOT add WebSocket subscriptions

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: React/TypeScript component wiring with hooks and state

  **Parallelization**:
  - **Can Run In Parallel**: NO (foundation)
  - **Blocks**: Tasks 2, 3, 4
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/components/devices/DeviceManager.tsx:81-166` — Full data wiring pattern (getChannels + getRelayBoardState + view models)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:125-158` — `buildRelayChannelViewModels()` signature
  - `Infrastructure/frontend/src/services/api.ts:630-650` — `getRelayBoardState()` and `getChannels()` method signatures
  - `Infrastructure/frontend/src/types/relay.ts:13-21` — `ChannelInfo` type definition
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:82-84` — Existing state declarations (target insertion point)

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] Imports for `RelayChannelMatrix`, `buildRelayChannelViewModels`, `ChannelInfo`, `RelayChannelViewModel` resolve
  - [ ] `relayChannels` state computed from `channelInfoList` + `relayState`
  - [ ] 5s relay state polling active with cleanup
  - [ ] Channels loaded once on mount
  - [ ] `mcpConnected` tracks hardware connectivity

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build passes after data fetching additions
    Tool: Bash
    Preconditions: Data fetching code added, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
    Expected Result: Clean build, all imports resolve
    Evidence: .sisyphus/evidence/task-1-build-output.txt

  Scenario: Relay polling and channel loading functions exist
    Tool: Bash
    Preconditions: Code added to ZoneConfig.tsx
    Steps:
      1. grep -c "fetchRelayData" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      2. Assert: output ≥ 2 (declaration + useEffect usage)
      3. grep -c "loadChannels" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      4. Assert: output ≥ 2
      5. grep -c "buildRelayChannelViewModels" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      6. Assert: output ≥ 1
    Expected Result: All data fetching functions present and wired
    Evidence: .sisyphus/evidence/task-1-grep-data.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add relay data fetching to ZoneConfig`
  - Files: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

- [x] 2. Add Menu State Management (Timers + Dropdown)

  **What to do**:
  - Add 3 new state variables (near the relay data states from Task 1):
    ```typescript
    const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)
    const [manualTimersByChannel, setManualTimersByChannel] = useState<Record<number, number>>({})
    const [timerActionInFlight, setTimerActionInFlight] = useState<Record<number, boolean>>({})
    ```
  - Add a `useEffect` to handle timer expiration (same pattern as DeviceManager lines 109-120):
    ```typescript
    useEffect(() => {
      const activeTimers = Object.entries(manualTimersByChannel).filter(([, expiry]) => expiry > 0)
      if (activeTimers.length === 0) return
      const timer = setInterval(() => {
        const now = Date.now()
        const expired: number[] = []
        Object.entries(manualTimersByChannel).forEach(([channel, expiry]) => {
          if (expiry > 0 && expiry <= now) expired.push(Number(channel))
        })
        if (expired.length > 0) {
          setManualTimersByChannel(prev => {
            const next = { ...prev }
            expired.forEach(ch => delete next[ch])
            return next
          })
          expired.forEach(channel => {
            handleRelayMenuAction(channel, 'auto') // call Task 3 implementation
          })
        }
      }, 1000)
      return () => clearInterval(timer)
    }, [manualTimersByChannel])
    ```
  - Build `statusByChannel` memo with timer countdown text (same pattern as DeviceManager lines 133-155):
    ```typescript
    const statusByChannel = useMemo(() => {
      const map: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }> = {}
      const now = Date.now()
      for (const channel of relayChannels) {
        const expiry = manualTimersByChannel[channel.channel]
        if (expiry && expiry > now) {
          const remaining = Math.ceil((expiry - now) / 1000)
          const minutes = Math.floor(remaining / 60)
          const seconds = remaining % 60
          map[channel.channel] = {
            text: `${minutes}:${String(seconds).padStart(2, '0')}`,
            tone: 'active',
          }
        }
      }
      return map
    }, [relayChannels, manualTimersByChannel])
    ```
  - Place new code after Task 1's data fetching code, before `handleSave`

  **Must NOT do**:
  - Do NOT use Redux or Context for timer state (local state is fine)
  - Do NOT persist timer state across sessions

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 1, blocks Task 3)
  - **Blocks**: Task 3, 4
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/frontend/src/components/devices/DeviceManager.tsx:109-117` — Timer countdown effect pattern
  - `Infrastructure/frontend/src/components/devices/DeviceManager.tsx:133-155` — `statusByChannel` useMemo pattern
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` — Target file for additions

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] `menuOpenChannel` state exists (number | null)
  - [ ] `manualTimersByChannel` state exists (Record<number, number>)
  - [ ] Timer expiration effect present with 1s interval
  - [ ] `statusByChannel` memo present with countdown text

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build passes after menu state additions
    Tool: Bash
    Preconditions: Menu state code added, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
    Expected Result: Clean build
    Evidence: .sisyphus/evidence/task-2-build-output.txt

  Scenario: Timer state and menu state present
    Tool: Bash
    Preconditions: Code added to ZoneConfig.tsx
    Steps:
      1. grep -c "menuOpenChannel" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      2. Assert: output ≥ 2 (state + usage)
      3. grep -c "manualTimersByChannel" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      4. Assert: output ≥ 2
      5. grep -c "statusByChannel" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      6. Assert: output ≥ 2 (memo + usage)
    Expected Result: All state management present and wired
    Evidence: .sisyphus/evidence/task-2-grep-state.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): add relay menu state and timer management to ZoneConfig`
  - Files: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

- [x] 3. Wire API Actions (handleRelayMenuAction)

  **What to do**:
  - Add `handleRelayMenuAction` function (place after Task 2 code, before existing `handleSave`):
    ```typescript
    const handleRelayMenuAction = useCallback(async (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => {
      const ch = relayChannels.find(c => c.channel === channel)
      if (!ch?.assignedDeviceName || !ch.location) return

      const device = ch.assignedDeviceName
      const location = ch.location
      const cluster = ch.cluster || 'main' // MUST use device cluster, not sensor sub-cluster

      // Close dropdown
      setMenuOpenChannel(null)

      // Track in-flight state
      setTimerActionInFlight(prev => ({ ...prev, [channel]: true }))
      try {
        if (action === 'auto') {
          await apiClient.setDeviceMode(location, cluster, device, 'auto')
        } else if (action === 'off') {
          await apiClient.setDeviceMode(location, cluster, device, 'manual')
          await apiClient.controlDevice(location, cluster, device, 0, 'Manual override: OFF')
        } else {
          // Timer actions: ON for N minutes, then auto
          const minutes = action === 'timer-5m' ? 5 : action === 'timer-10m' ? 10 : action === 'timer-30m' ? 30 : 60
          await apiClient.setDeviceMode(location, cluster, device, 'manual')
          await apiClient.controlDevice(location, cluster, device, 1, `Manual override: ON ${minutes}m`)
          setManualTimersByChannel(prev => ({
            ...prev,
            [channel]: Date.now() + minutes * 60 * 1000,
          }))
        }
      } catch (err) {
        logger.error(`Relay action failed for channel ${channel}:`, err)
      } finally {
        setTimerActionInFlight(prev => ({ ...prev, [channel]: false }))
      }
    }, [relayChannels])
    ```
  - The `cluster || 'main'` ensures device cluster is always `main` even if ZoneConfig's URL cluster is `front`/`back` (sensor sub-clusters)

  **Must NOT do**:
  - Do NOT use sensor sub-clusters (`front`/`back`) for device control
  - Do NOT hardcode location — use `ch.location` from the channel info
  - Do NOT skip the `cluster || 'main'` fallback

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 4
  - **Blocked By**: Task 2

  **References**:
  - `Infrastructure/frontend/src/components/devices/DeviceManager.tsx:168-235` — Full `handleRelayMenuAction` pattern (timer logic, API calls)
  - `Infrastructure/frontend/src/services/api.ts:588-618` — `setDeviceMode()` and `controlDevice()` method signatures
  - `Infrastructure/automation-service/app/routes/devices.py:241-272` — `set_device_mode` backend endpoint (auto/manual/scheduled)
  - `AGENTS.md` — Cluster Topology Contract: "main" is device cluster, "front"/"back" are sensor sub-clusters

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] `handleRelayMenuAction` function present with 6 action cases (auto, off, 5m, 10m, 30m, 1h)
  - [ ] `cluster || 'main'` fallback present for device cluster normalization
  - [ ] `setDeviceMode` called for auto and off actions
  - [ ] `controlDevice` called for off and timer actions
  - [ ] Timer expiry set in `manualTimersByChannel` for timer actions
  - [ ] Error handling with try/catch + logger

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build passes after API actions
    Tool: Bash
    Preconditions: API action code added, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
    Expected Result: Clean build
    Evidence: .sisyphus/evidence/task-3-build-output.txt

  Scenario: API actions function present with all cases
    Tool: Bash
    Preconditions: Code added to ZoneConfig.tsx
    Steps:
      1. grep -c "handleRelayMenuAction" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      2. Assert: output ≥ 2 (declaration + usage in timer effect)
      3. grep -c "setDeviceMode" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      4. Assert: output ≥ 1
      5. grep -c "controlDevice" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      6. Assert: output ≥ 1
      7. grep "cluster || 'main'" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      8. Assert: match found (cluster normalization)
    Expected Result: All API action code present
    Evidence: .sisyphus/evidence/task-3-grep-actions.txt
  ```

  **Commit**: YES
  - Message: `feat(ui): wire relay menu actions (Auto/Off/Timers) in ZoneConfig`
  - Files: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

- [x] 4. Add RelayChannelMatrix JSX + Adjust Layout to 3 Columns

  **What to do**:
  - **Layout change**: In the middle row (current lines ~312-351), convert from 2-column to 3-column:
    - Light Schedule column: change from `w-[30%]` to `w-[25%]`
    - Climate + Light column: change from `w-[70%]` to `w-[40%]`
    - Add new Relay Matrix column: `w-[35%]`
  - **Relay Matrix JSX**: Add after the Climate+Light column:
    ```tsx
    <div className="w-[35%] h-full">
      {!mcpConnected && (
        <div className="mb-1 rounded-sm border border-status-error-border/80 bg-status-error-bg/30 px-2 py-1 text-[10px] font-semibold text-status-error-text">
          MCP23017 disconnected
        </div>
      )}
      <RelayChannelMatrix
        channels={relayChannels}
        nowMs={Date.now()}
        variant="compact"
        statusByChannel={statusByChannel}
        menuOpenChannel={menuOpenChannel}
        onToggleMenu={(ch) => setMenuOpenChannel(prev => prev === ch ? null : ch)}
        onMenuAction={handleRelayMenuAction}
      />
    </div>
    ```

  **Must NOT do**:
  - Do NOT change the Climate Timeline row (line 294-307)
  - Do NOT change the Notes row (line 353-355)
  - Do NOT modify `RelayChannelMatrix` or `RelayChannelBox`
  - Do NOT pass `editingChannel` or `onSelectChannel` props (not needed)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: React/TypeScript layout and component integration

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 5
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:312-351` — Current 2-column middle row (target for modification)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:88-99` — `RelayChannelMatrix` props interface
  - `Infrastructure/frontend/src/components/devices/DeviceManager.tsx:821-833` — RelayChannelMatrix usage example
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:5-19` — Props: channels, nowMs, variant, statusByChannel, menuOpenChannel, onToggleMenu, onMenuAction

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] Middle row has 3 columns (w-[25%], w-[40%], w-[35%])
  - [ ] `<RelayChannelMatrix>` rendered with `variant="compact"`
  - [ ] `mcpConnected` error banner renders when false
  - [ ] All RelayChannelMatrix props passed (channels, nowMs, variant, statusByChannel, menuOpenChannel, onToggleMenu, onMenuAction)
  - [ ] No `editingChannel` or `onSelectChannel` props passed

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build passes after layout + JSX integration
    Tool: Bash
    Preconditions: JSX code added, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
    Expected Result: Clean build
    Evidence: .sisyphus/evidence/task-4-build-output.txt

  Scenario: Relay matrix and layout columns present
    Tool: Bash
    Preconditions: Code added to ZoneConfig.tsx
    Steps:
      1. grep -c "RelayChannelMatrix" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      2. Assert: output ≥ 1
      3. grep -c "w-\[25%\]" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      4. Assert: output ≥ 1 (LightSchedule width)
      5. grep -c "w-\[40%\]" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      6. Assert: output ≥ 1 (Climate+Lights width)
      7. grep -c "w-\[35%\]" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      8. Assert: output ≥ 1 (Relay Matrix width)
      9. grep "mcpConnected" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      10. Assert: match found (error banner conditional)
    Expected Result: Layout structure and relay matrix present
    Evidence: .sisyphus/evidence/task-4-grep-layout.txt

  Scenario: ZoneConfig page renders relay matrix (Playwright)
    Tool: Playwright (playwright skill)
    Preconditions: Frontend built and served. Dev server or static build accessible.
    Steps:
      1. Load skill: playwright
      2. Navigate to: http://localhost:8001/flower/main/control (or equivalent route)
      3. Wait for: page load complete (timeout: 10s)
      4. Set viewport: 1920×1080
      5. Verify: horizontal scroll is not needed (document scrollWidth === clientWidth)
      6. Verify: at least one element with "relay" in class or title exists
      7. Screenshot: .sisyphus/evidence/task-4-zoneconfig-relay.png
    Expected Result: Relay matrix visible on ZoneConfig page, layout fits viewport
    Failure Indicators: Horizontal scrollbar visible, relay matrix not found
    Evidence: .sisyphus/evidence/task-4-zoneconfig-relay.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add compact relay matrix to ZoneConfig with 3-column layout`
  - Files: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

- [x] 5. End-to-End Verification

  **What to do**:
  - Run `npm run build` and verify 0 errors
  - Run `npx tsc --noEmit` and verify 0 new TypeScript errors in ZoneConfig.tsx
  - Verify all imports resolve correctly
  - Verify no console errors on the ZoneConfig page (Playwright)
  - Verify dropdown menu opens on assigned channel click (Playwright)
  - Verify `mcpConnected: false` renders error banner
  - Run ruff check/format (AGENTS.md rule)

  **Must NOT do**:
  - Do NOT skip the TypeScript check
  - Do NOT skip Playwright verification

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (final task)
  - **Blocks**: None
  - **Blocked By**: Task 4

  **References**:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` — Full modified file

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] `npm run build` → ZoneConfig chunk size increased reasonably (< 5KB increase)
  - [ ] All 5 grep assertions from Tasks 1-4 pass
  - [ ] Playwright: ZoneConfig page loads without errors
  - [ ] Playwright: relay matrix visible at 1920px width
  - [ ] Playwright: no horizontal scroll at 1920px

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Full build and TypeScript check
    Tool: Bash
    Preconditions: All tasks complete, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0
      3. cd Infrastructure/frontend && npx tsc --noEmit 2>&1 | grep "ZoneConfig"
      4. Assert: no TypeScript errors mentioning ZoneConfig.tsx
    Expected Result: Clean build, 0 TypeScript errors
    Evidence: .sisyphus/evidence/task-5-build-output.txt

  Scenario: All grep assertions pass
    Tool: Bash
    Preconditions: All tasks complete
    Steps:
      1. grep -c "RelayChannelMatrix" Infrastructure/frontend/src/pages/ZoneConfig.tsx → ≥1
      2. grep -c "buildRelayChannelViewModels" Infrastructure/frontend/src/pages/ZoneConfig.tsx → ≥1
      3. grep -c "handleRelayMenuAction" Infrastructure/frontend/src/pages/ZoneConfig.tsx → ≥2
      4. grep -c "statusByChannel" Infrastructure/frontend/src/pages/ZoneConfig.tsx → ≥2
      5. grep "cluster || 'main'" Infrastructure/frontend/src/pages/ZoneConfig.tsx → found
    Expected Result: All assertions pass
    Evidence: .sisyphus/evidence/task-5-grep-all.txt

  Scenario: Ruff check passes (AGENTS.md rule)
    Tool: Bash
    Preconditions: All tasks complete
    Steps:
      1. cd /home/antoine/ProjectCEA && ruff check --fix . && ruff format .
      2. Assert: ruff check returns clean (no unfixed errors)
    Expected Result: No ruff violations in ZoneConfig.tsx
    Evidence: .sisyphus/evidence/task-5-ruff-output.txt

  Scenario: ZoneConfig relay matrix interactive (Playwright)
    Tool: Playwright (playwright skill)
    Preconditions: Frontend served, backend available
    Steps:
      1. Load skill: playwright
      2. Navigate to: http://localhost:8001/flower/main/control
      3. Wait for page load
      4. Scroll to relay matrix section
      5. Locate a channel box with an assigned device (not greyed out)
      6. Click the state badge button (text content ON/IDLE)
      7. Wait for dropdown menu to appear
      8. Assert: Dropdown contains "Auto", "Off" text options
      9. Screenshot: .sisyphus/evidence/task-5-relay-dropdown.png
    Expected Result: Dropdown menu opens with Auto/Off options
    Evidence: .sisyphus/evidence/task-5-relay-dropdown.png
  ```

  **Commit**: YES
  - Message: `test(ui): verify ZoneConfig relay matrix end-to-end`
  - Files: no code changes (verification only)
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|-----------|---------|-------|--------------|
| 1 | `feat(ui): add relay data fetching to ZoneConfig` | `ZoneConfig.tsx` | `npm run build` |
| 2 | `feat(ui): add relay menu state and timer management to ZoneConfig` | `ZoneConfig.tsx` | `npm run build` |
| 3 | `feat(ui): wire relay menu actions (Auto/Off/Timers) in ZoneConfig` | `ZoneConfig.tsx` | `npm run build` |
| 4 | `feat(ui): add compact relay matrix to ZoneConfig with 3-column layout` | `ZoneConfig.tsx` | `npm run build` |
| 5 | `test(ui): verify ZoneConfig relay matrix end-to-end` | N/A (verification) | `npm run build` |

---

## Success Criteria

### Verification Commands
```bash
# Build
cd Infrastructure/frontend && npm run build

# TypeScript check (ZoneConfig only)
cd Infrastructure/frontend && npx tsc --noEmit 2>&1 | grep -i "ZoneConfig"

# Key assertions
grep -c "RelayChannelMatrix" Infrastructure/frontend/src/pages/ZoneConfig.tsx    # ≥ 1
grep -c "buildRelayChannelViewModels" Infrastructure/frontend/src/pages/ZoneConfig.tsx  # ≥ 1
grep -c "handleRelayMenuAction" Infrastructure/frontend/src/pages/ZoneConfig.tsx # ≥ 2
grep "cluster || 'main'" Infrastructure/frontend/src/pages/ZoneConfig.tsx         # found

# Ruff
cd /home/antoine/ProjectCEA && ruff check --fix . && ruff format .
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] `npm run build` passes with 0 errors
- [x] `npx tsc --noEmit` passes (0 new errors in ZoneConfig.tsx)
- [x] Relay matrix renders 16 channels in 2-column × 8-row compact grid
- [x] Assigned channels interactive with Auto/Off/Timer dropdown
- [x] Unassigned channels greyed out
- [x] `mcpConnected: false` shows error banner
- [x] Cluster normalization (`main`, not `front`/`back`) for device APIs
- [x] Layout: 3 columns (25% / 40% / 35%)
- [x] No changes to Climate Timeline, Notes row, or Devices page
- [x] No modifications to RelayChannelMatrix, RelayChannelBox, or relayViewModel
- [x] No new API client methods or backend endpoints
- [ ] No console errors on ZoneConfig page
