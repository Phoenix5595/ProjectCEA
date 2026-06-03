# ZoneConfig Relay Matrix Learnings

## Task 1 Status: Complete

- Added state variables, stub, timer effect, and statusByChannel memo at lines 139-196
- Build passed after using @ts-ignore for forward-declared-but-not-yet-consumed state

## Task 3 Status: Complete

- Replaced stub `handleRelayMenuAction` at lines 146-150 with real `useCallback` implementation
- Implementation handles all 6 action types: `auto`, `off`, `timer-5m`, `timer-10m`, `timer-30m`, `timer-1h`
- Uses `cluster || 'main'` for device cluster normalization (not sensor sub-clusters)
- Closes dropdown (`setMenuOpenChannel(null)`) before API calls
- Tracks in-flight state with try/catch/finally
- Dependencies: `[relayChannels]` for useCallback
- Build passed: ✓ built in 10.81s
- All grep checks passed:
  - `handleRelayMenuAction` count: 3 ✓
  - `cluster || 'main'` found ✓
  - `setDeviceMode` count: 3 ✓
  - `controlDevice` count: 2 ✓
