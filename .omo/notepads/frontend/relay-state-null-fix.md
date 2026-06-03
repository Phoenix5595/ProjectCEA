
## relayState = null → Default Object Fix (2026-06-01)

### Problem
`relayState = null` as initial state caused "Unknown" flash on DeviceManager page load.
`buildRelayChannelViewModels` received `null` for channels, triggering `isStateKnown = false` → "Unknown" status.

### Solution Applied
1. **Added `DEFAULT_RELAY_STATE`** constant (structured object, not null):
   ```typescript
   const DEFAULT_RELAY_STATE: RelayBoardStateResponse = {
     channels: Array(16).fill(false),  // All channels OFF = IDLE/gray
     mcp_connected: false,
     simulation: false,
   }
   ```

2. **Changed state initialization** (line 81):
   - Before: `useState<RelayBoardStateResponse | null>(null)`
   - After: `useState<RelayBoardStateResponse>(DEFAULT_RELAY_STATE)`

3. **Removed `relayState?.channels` null-safe access** in `relayChannels` useMemo:
   - Before: `relayState?.channels || null`
   - After: `relayState.channels`

4. **Simplified `relayStatusLabel` / `relayStatusClasses`** ternary chains:
   - Removed outer `relayState ?` checks since relayState is never null
   - Unavailable state now shows on `!relayState.mcp_connected` (disconnected hardware)

5. **Changed error handler** in `refreshRelayState()`:
   - Before: `setRelayState(null)` (would restore Unknown state on API failure)
   - After: `setRelayState(DEFAULT_RELAY_STATE)` (keeps IDLE state, logs warning)

### Files Modified
- `Infrastructure/frontend/src/components/DeviceManager.tsx`

### Verification
- `grep "relayState?." DeviceManager.tsx` → 0 matches ✓
- `npm run build` → exits 0 ✓

### Behavior Change
- Page load: Channels show "IDLE" (gray) instead of "Unknown" (yellow)
- API failure: Channels remain "IDLE" instead of flashing to "Unknown"
- Hardware disconnected: Shows "Unavailable" badge + "IDLE" channels (graceful degradation)
