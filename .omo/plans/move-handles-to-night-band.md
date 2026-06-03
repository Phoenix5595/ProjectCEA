# Move Interactive Handles from Day Band to Night Band

## TL;DR

> **Quick Summary**: Move the draggable edge handles, body drag, and right-click popover from the orange day band to the purple night band. The night band's edges are the day boundaries: night start = lights OFF, night end = lights ON. Dragging the night band shifts the entire photoperiod. The day band becomes passive display only.
>
> **Deliverables**:
> - Handles, gradients, and popover moved from `sunSegments` to `moonSegments`
> - Edge semantics swapped: night-left → dayEnd, night-right → dayStart
> - Updated tests to match new data-testids
>
> **Estimated Effort**: Quick (1 file + tests)
> **Parallel Execution**: NO — sequential (component → tests → verify)

---

## Context

### Original Request
> "i need bands only before and after the night" — the interactive handles should live on the night band's edges, not the day band.

### Current State
- **Day band** (orange `sunSegments`, moon on z-1 below): has edge handles, body drag, ramp gradients, right-click popover
- **Night band** (purple `moonSegments`, moon on z-1 behind day band): purely passive display
- **Semantics**: `lightDayStart` = day start, `lightDayEnd` = day end (= night start)

### Desired State
- **Day band** (orange): passive display only — no handles, no interactivity
- **Night band** (purple): interactive — edge handles at night boundaries, body drag, right-click popover
- **Night band left edge** → controls day end (`onDayEndChange`)
- **Night band right edge** → controls day start (`onDayStartChange`)
- **Night band body drag** → shifts entire photoperiod

---

## Work Objectives

### Core Objective
Move all interactive elements (edge handles, body drag, right-click popover, ramp gradients) from the day band to the night band, swapping edge semantics so the night band's edges control day start/end.

### Concrete Deliverables
- Modified `ClimatePeriodTimeline.tsx` — handles on moon, day band passive
- Modified `__tests__/ClimatePeriodTimeline.interaction.test.tsx` — updated data-testids

### Definition of Done
- [ ] No edge handles visible on the orange day band
- [ ] Edge handles visible on the purple night band
- [ ] Dragging night band left edge → `onDayEndChange` fires
- [ ] Dragging night band right edge → `onDayStartChange` fires
- [ ] Body drag on night band shifts photoperiod
- [ ] Right-click on night band opens ramp popover
- [ ] All 6 TDD tests pass with updated data-testids
- [ ] `npm run build` passes

---

## TODOs

- [ ] 1. Move Handles, Gradients, and Popover from Sun to Moon Segments

  **What to do**:

  ### A. Remove interactivity from sunSegments (lines 312-372)
  - Remove `onMouseDown={handleBodyMouseDown}` from sun band div
  - Remove `onContextMenu` from sun band div
  - Remove `data-testid="timeline-day-band"` from sun band div
  - Remove both edge handle divs (lines 331-350)
  - Remove both ramp gradient divs (lines 351-370)
  - Remove `cursor-grab` class from sun band
  - Result: sun band is a plain `<div>` with only `left`, `width`, `backgroundColor` styling

  ### B. Add interactivity to moonSegments (lines 300-310)
  - Add `onMouseDown={handleBodyMouseDown}` to moon band div
  - Add `onContextMenu` to moon band div
  - Add `data-testid="timeline-night-band"` to moon band div
  - Add `cursor-grab` class to moon band
  - Add both edge handle divs to moon band (same JSX structure, different data-testids)
  - Add both ramp gradient divs to moon band

  ### C. Flip edge handle semantics
  - **Night band LEFT edge** (where night begins) → `handleEdgeMouseDown('end')` → controls `onDayEndChange`
  - **Night band RIGHT edge** (where night ends) → `handleEdgeMouseDown('start')` → controls `onDayStartChange`
  - `data-testid="timeline-handle-night-start"` for right edge (night end = day start)
  - `data-testid="timeline-handle-night-end"` for left edge (night start = day end)

  ### D. Update body drag
  - `handleBodyMouseDown` is already generic — it uses `dayStartMin`/`dayEndMin` which come from props. No change needed in the logic, just attach to moon band instead.

  ### Must NOT do:
  - Don't change the drag math or snap logic
  - Don't change the ramp popover logic
  - Don't change the handle visual styling (keep cyan borders, opacity, hover)
  - Don't change the ZoneConfig.tsx wiring

  **Commit**: YES
  - Message: `fix(timeline): move interactive handles from day band to night band`
  - Files: `ClimatePeriodTimeline.tsx`

- [ ] 2. Update TDD Tests for New Data-TestIDs

  **What to do**:
  - In `__tests__/ClimatePeriodTimeline.interaction.test.tsx`:
    - `timeline-handle-start` → `timeline-handle-night-end` (right edge of night = day start)
    - `timeline-handle-end` → `timeline-handle-night-start` (left edge of night = day end)
    - `timeline-day-band` → `timeline-night-band`
    - `timeline-ramp-up-gradient` → keep same (gradient now on night band, same testid)
    - `timeline-ramp-down-gradient` → keep same
    - `timeline-ramp-popover` → keep same
  - All test logic remains identical — only data-testid selectors change
  - Run: `npx vitest run -- ClimatePeriodTimeline.interaction` → 6 passed

  **Commit**: YES (combined with Task 1)
  - Files: `__tests__/ClimatePeriodTimeline.interaction.test.tsx`

- [ ] 3. Build + Integration Verification

  **What to do**:
  - `cd Infrastructure/frontend && npm run build` → 0 errors
  - `npm test` → 7 passed (6 interaction + 1 example)
  - Verify no remaining references to old data-testids

  **Commit**: NO (built-in verification)

---

## Success Criteria

### Verification Commands
```bash
# Unit tests
cd Infrastructure/frontend && npx vitest run -- ClimatePeriodTimeline.interaction
# Expected: 6 passed

# Full test suite
cd Infrastructure/frontend && npm test
# Expected: 7 passed

# Build
cd Infrastructure/frontend && npm run build
# Expected: 0 errors

# Dead reference check
grep -rn "timeline-handle-start\|timeline-handle-end\|timeline-day-band" Infrastructure/frontend/src/
# Expected: no results (except this plan)
```
