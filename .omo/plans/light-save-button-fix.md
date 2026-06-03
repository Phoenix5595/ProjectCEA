# Light Intensity Save Button Fix

## TL;DR

> **Quick Summary**: Remove the inline "APPLY LIGHT CHANGES" save button from the `LightIntensity` component and integrate its save logic into ZoneConfig's main SAVE button (TopRibbon). All light intensity edits now save via the top-level SAVE alongside room parameters, schedule, and climate periods.
>
> **Deliverables**:
> - `LightIntensity.tsx` — remove inline save button, expose `savePendingChanges` via `forwardRef`
> - `ZoneConfig.tsx` — call `savePendingChanges` in `handleSave`
>
> **Estimated Effort**: Quick (2 files, ~30 LOC change)
> **Parallel Execution**: NO (sequential — ZoneConfig depends on LightIntensity ref interface)
> **Critical Path**: LightIntensity.tsx refactoring → ZoneConfig.tsx integration

---

## Context

### Original Request
> "When I edit light intensity in the frontend, there is a button that appears to save. It should not appear where it does. There should be no button and the saving should be done via the main save button up top."

### Interview Summary
**Key Discussions**:
- Located the inline "APPLY LIGHT CHANGES" button at `LightIntensity.tsx:334-343` — appears when `hasPendingChanges` is true
- Located the main SAVE button at `TopRibbon.tsx:176-182` — wired via `ControlActionsContext` from `ZoneConfig.handleSave`
- `ZoneConfig.handleSave` currently saves room parameters, schedule, and climate periods but NOT light intensity
- LightIntensity manages its own isolated state (`pendingTargets`, `hasPendingChanges`) and `savePendingChanges()` function
- `LightIntensity` is only used in one place: `ZoneConfig.tsx:345` with `compact={true}`

### Metis Review
**Identified Gaps** (all addressed):
1. ✅ Double-toast UX — accepted: LightIntensity fires its own toast on partial failure; ZoneConfig always shows "Saved"
2. ✅ Non-compact usage risk — validated: `LightIntensity` is used ONLY in ZoneConfig
3. ✅ `ManualLightControl.tsx` also calls `setLightIntensity` — verified: no inline save button there (3 mode toggle buttons only)
4. ✅ Navigation-away with pending changes — silent loss accepted as existing behavior

---

## Work Objectives

### Core Objective
Remove the inline "APPLY LIGHT CHANGES" save button from `LightIntensity` and wire `savePendingChanges` into ZoneConfig's `handleSave` so light intensity changes are saved via the main TopRibbon SAVE button.

### Concrete Deliverables
- `LightIntensity.tsx`: Remove lines 334-343 (inline save button). Add `forwardRef` + `useImperativeHandle` exposing `savePendingChanges`.
- `ZoneConfig.tsx`: Add `useRef` for LightIntensity, call `lightIntensityRef.current?.savePendingChanges()` in `handleSave`.

### Definition of Done
- [ ] `npm run build` passes with 0 errors
- [ ] `npx tsc --noEmit` passes (0 new errors)
- [ ] `grep "APPLY LIGHT CHANGES" LightIntensity.tsx` returns no matches
- [ ] Changing a light intensity slider on ZoneConfig page does NOT show an inline save button
- [ ] Clicking main SAVE button persists light intensity changes

### Must Have
- Inline "APPLY LIGHT CHANGES" button fully removed (JSX deleted)
- `savePendingChanges` callable from ZoneConfig via ref
- Light changes saved as part of main SAVE flow
- Existing save behavior (room params, schedule, climate) unchanged
- `LightIntensity` internal state management unchanged

### Must NOT Have (Guardrails)
- No changes to `TopRibbon.tsx`, `ControlActionsContext`, `Layout.tsx`, `apiClient`
- No changes to `ManualLightControl.tsx`
- No changes to `LightIntensity` polling (5s interval stays)
- No changes to `LightIntensity` toast behavior (still shows own toasts)
- No scope creep into browser-beforeunload warnings for pending changes
- No changes to `pendingTargets` / `hasPendingChanges` state management

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (npm/tsc)
- **Automated tests**: NO (no frontend test framework)
- **Framework**: N/A

### Agent-Executed QA Scenarios (MANDATORY)

Every task includes Agent-Executed QA scenarios. The executing agent directly verifies the deliverable.

**Verification Tool by Task**:

| Task | Tool | How Agent Verifies |
|------|------|-------------------|
| 1 (LightIntensity) | Bash (npm run build, grep) | Build check, string removal check, TypeScript check |
| 2 (ZoneConfig) | Playwright | Navigate to ZoneConfig, change slider, verify no inline button, click SAVE |

---

## Execution Strategy

### Sequential Execution

```
Task 1 (LightIntensity.tsx refactoring)
    ↓
Task 2 (ZoneConfig.tsx integration)
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2 | None |
| 2 | 1 | None | None |

---

## TODOs

- [x] 1. Remove Inline Save Button & Expose savePendingChanges via forwardRef

  **What to do**:
  - Add `forwardRef` and `useImperativeHandle` imports to `LightIntensity.tsx`
  - Wrap component with `forwardRef`, add typed `ref` parameter
  - Use `useImperativeHandle` to expose `savePendingChanges` as a public method
  - Remove lines 334-343 (the conditional div with "APPLY LIGHT CHANGES" button)
  - Keep ALL other code unchanged (state, effects, handlers, JSX above line 333)
  - Export remains `export default LightIntensity`

  **Must NOT do**:
  - Do NOT change `savePendingChanges` function implementation
  - Do NOT change `handleTargetChange`
  - Do NOT change `fetchLightsAndStatus` or polling
  - Do NOT remove `hasPendingChanges` state (still used for visual indicators?)
  - Do NOT change any toast calls

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: React/TypeScript component refactoring with forwardRef pattern

  **Parallelization**:
  - **Can Run In Parallel**: NO (foundation for Task 2)
  - **Blocks**: Task 2
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:1-346` — Full component to refactor
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:28` — Current function signature: `({ location, cluster, compact }: LightIntensityProps)`
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:160-186` — `savePendingChanges` implementation (the function to expose)
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:334-343` — Inline save button to remove
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:31-33` — State declarations (unchanged)

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] `npx tsc --noEmit` passes (0 new errors in LightIntensity.tsx)
  - [ ] `grep "APPLY LIGHT CHANGES" Infrastructure/frontend/src/components/LightIntensity.tsx` returns no matches
  - [ ] `grep "savePendingChanges" Infrastructure/frontend/src/components/LightIntensity.tsx` returns matches (function NOT removed)
  - [ ] `useImperativeHandle` present in the file
  - [ ] `forwardRef` wraps the component

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build and type check pass after refactoring
    Tool: Bash
    Preconditions: Working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
      3. cd Infrastructure/frontend && npx tsc --noEmit 2>&1
      4. Assert: no new TypeScript errors introduced in LightIntensity.tsx
    Expected Result: Clean build, no type errors
    Evidence: .sisyphus/evidence/task-1-build-output.txt

  Scenario: Inline save button text is gone
    Tool: Bash
    Preconditions: File saved with changes
    Steps:
      1. grep -c "APPLY LIGHT CHANGES" Infrastructure/frontend/src/components/LightIntensity.tsx
      2. Assert: output is "0" (no matches)
    Expected Result: String "APPLY LIGHT CHANGES" not found anywhere in the file
    Evidence: .sisyphus/evidence/task-1-grep-output.txt

  Scenario: savePendingChanges function still exists
    Tool: Bash
    Preconditions: File saved with changes
    Steps:
      1. grep -c "savePendingChanges" Infrastructure/frontend/src/components/LightIntensity.tsx
      2. Assert: output is ≥ 2 (function declaration + useImperativeHandle reference)
    Expected Result: savePendingChanges preserved and exposed via ref
    Evidence: .sisyphus/evidence/task-1-grep-save-function.txt
  ```

  **Commit**: YES
  - Message: `fix(ui): remove inline save button, expose savePendingChanges via forwardRef`
  - Files: `Infrastructure/frontend/src/components/LightIntensity.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

- [x] 2. Integrate Light Save into ZoneConfig handleSave

  **What to do**:
  - Import `useRef` in ZoneConfig.tsx (already imported at line 2)
  - Create a typed ref: `const lightIntensityRef = useRef<{ savePendingChanges: () => Promise<void> }>(null)`
  - Pass the ref to `<LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact={true} />`
  - In `handleSave`, add `await lightIntensityRef.current?.savePendingChanges()` before the "Saved" success line
  - Keep ALL existing API calls in `handleSave` (room params, schedule, climate)

  **Must NOT do**:
  - Do NOT remove any existing API calls from `handleSave`
  - Do NOT change the `handleSave` error handling flow
  - Do NOT add light save to `setActions` or `ControlActionsContext`
  - Do NOT change the save button in TopRibbon

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: React/TypeScript component wiring

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None (final task)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:2` — Confirms `useRef` is already available via React imports
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:156-211` — `handleSave` function to modify
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:345` — Where `<LightIntensity>` is rendered
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:68-72` — Function signature with destructured props

  **Acceptance Criteria**:

  - [ ] `npm run build` passes (0 errors)
  - [ ] `npx tsc --noEmit` passes (0 new errors in ZoneConfig.tsx)
  - [ ] `grep "savePendingChanges" Infrastructure/frontend/src/pages/ZoneConfig.tsx` returns matches
  - [ ] `grep "lightIntensityRef" Infrastructure/frontend/src/pages/ZoneConfig.tsx` returns matches
  - [ ] `<LightIntensity` JSX element includes `ref={lightIntensityRef}` prop

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Build passes with ZoneConfig changes
    Tool: Bash
    Preconditions: Task 1 complete, working in Infrastructure/frontend/
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0, no errors in output
    Expected Result: Clean build after ZoneConfig changes
    Evidence: .sisyphus/evidence/task-2-build-output.txt

  Scenario: Ref and save integration present in code
    Tool: Bash
    Preconditions: File saved with changes
    Steps:
      1. grep -c "lightIntensityRef" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      2. Assert: output is ≥ 2 (declaration + usage in handleSave)
      3. grep "ref={lightIntensityRef}" Infrastructure/frontend/src/pages/ZoneConfig.tsx
      4. Assert: match found (ref passed to LightIntensity JSX)
    Expected Result: Ref created, used in handleSave, passed to LightIntensity
    Evidence: .sisyphus/evidence/task-2-grep-output.txt

  Scenario: Light intensity slider change + SAVE persists changes (Playwright)
    Tool: Playwright
    Preconditions: Frontend is deployed/build is served. Service running at localhost:8001.
    Steps:
      1. Load skill: playwright
      2. Navigate to ZoneConfig page (e.g., http://localhost:8001/flower/control)
      3. Wait for LightIntensity section to load
      4. Locate the range slider for the first light in the Light Intensity panel
      5. Change the slider to a new value (e.g., 75)
      6. Assert: NO "APPLY LIGHT CHANGES" button appears anywhere on the page
      7. Click the main "SAVE" button in the TopRibbon at the top of the page
      8. Wait for success toast: text contains "Saved"
      9. Reload the page
      10. Assert: The saved light intensity value persists (check CUR value)
    Expected Result: Inline save button gone; main SAVE persists light changes
    Failure Indicators: "APPLY LIGHT CHANGES" text visible on page; light value not persisted after SAVE+reload
    Evidence: .sisyphus/evidence/task-2-light-save-flow.png
  ```

  **Commit**: YES
  - Message: `fix(ui): integrate light intensity save into ZoneConfig main SAVE`
  - Files: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
  - Pre-commit: `cd Infrastructure/frontend && npm run build`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|-----------|---------|-------|--------------|
| 1 | `fix(ui): remove inline save button, expose savePendingChanges via forwardRef` | `LightIntensity.tsx` | `npm run build` |
| 2 | `fix(ui): integrate light intensity save into ZoneConfig main SAVE` | `ZoneConfig.tsx` | `npm run build` |

---

## Success Criteria

### Verification Commands
```bash
# TypeScript check
cd Infrastructure/frontend && npx tsc --noEmit

# Production build
cd Infrastructure/frontend && npm run build

# Confirm button text is gone
grep -c "APPLY LIGHT CHANGES" Infrastructure/frontend/src/components/LightIntensity.tsx
# Expected: 0

# Confirm save function is exposed via ref
grep -c "savePendingChanges" Infrastructure/frontend/src/components/LightIntensity.tsx
# Expected: ≥ 2

# Confirm ref is wired in ZoneConfig
grep -c "lightIntensityRef" Infrastructure/frontend/src/pages/ZoneConfig.tsx
# Expected: ≥ 2
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] `npm run build` passes with 0 errors
- [x] Inline "APPLY LIGHT CHANGES" button string completely removed
- [x] `savePendingChanges` callable via ref from ZoneConfig
- [x] Light changes persist through main SAVE button
- [x] No changes to TopRibbon, ControlActionsContext, ManualLightControl
- [x] No new toast behavior or error handling patterns
