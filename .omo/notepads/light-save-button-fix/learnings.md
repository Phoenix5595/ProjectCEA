## Light Save Button Fix - Learnings

### Key File Findings

**LightIntensity.tsx:**
- Line 28: named function `function LightIntensity(...)` — `forwardRef` wrapping works fine
- Lines 160-186: `savePendingChanges` is `async function` returning `Promise<void>`
- Lines 334-343: inline save button block to remove
- Line 148: `hasPendingChanges` derived from `Object.keys(pendingTargets).length > 0`
- Line 2: imports `useState, useEffect, useCallback` only — no `forwardRef` yet

**ZoneConfig.tsx:**
- Line 2: imports `useState, useEffect, useCallback` from React — `useRef` NOT imported
- Line 156-211: `handleSave` async function
- Line 345: `<LightIntensity>` rendered as `<LightIntensity location={location} cluster={cluster} compact={true} />`
- Need to add `useRef` import

### Task 1 (LightIntensity.tsx) ✅ COMPLETE
1. Added `forwardRef`, `useImperativeHandle` to imports
2. Wrapped with `forwardRef<{ savePendingChanges: () => Promise<void> }, LightIntensityProps>`
3. Added `ref` parameter + `useImperativeHandle` exposing `savePendingChanges`
4. Deleted lines 334-343 (button JSX)
5. Removed `hasPendingChanges` state (dead code — broke build otherwise with TS6133)
6. Removed `setHasPendingChanges(true)` from handleTargetChange (dead code)
7. Removed `useEffect` that synced hasPendingChanges from pendingTargets (dead code)

### Task 2 (ZoneConfig.tsx) ✅ COMPLETE
1. Added `useRef` to React imports (line 2)
2. Created ref: `const lightIntensityRef = useRef<{ savePendingChanges: () => Promise<void> }>(null)` at line 75
3. Passed ref to `<LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact={true} />` at line 348
4. Called `await lightIntensityRef.current?.savePendingChanges()` in handleSave at line 193 before "Saved" toast

### Final Verification
- npm run build: ✅ 0 errors, built in 14.75s
- APPLY LIGHT CHANGES: ✅ 0 matches in LightIntensity.tsx
- savePendingChanges in LightIntensity.tsx: ✅ 3 matches
- forwardRef: ✅ 2 matches
- useImperativeHandle: ✅ 2 matches
- useRef in ZoneConfig.tsx: ✅ 2 matches
- lightIntensityRef in ZoneConfig.tsx: ✅ 3 matches
- ref={lightIntensityRef}: ✅ 1 match
- savePendingChanges in ZoneConfig.tsx: ✅ 2 matches
