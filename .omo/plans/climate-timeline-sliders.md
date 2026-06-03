# Replace Circular Time Picker with Sliders on Climate Timeline

## TL;DR

> **Quick Summary**: Replace the canvas-based CircularTimePicker with interactive draggable edge handles directly on the ClimatePeriodTimeline's day band. Remove the circular picker components entirely and make the timeline the sole light schedule control surface.
>
> **Deliverables**:
> - Interactive ClimatePeriodTimeline with draggable edge handles + body drag + ramp gradient + right-click popover
> - Updated ZoneConfig layout: 25/40/35 three-column → remove 25% → 40%+35% share full width
> - Vitest + React Testing Library test infrastructure setup + TDD test suite
> - Removed dead code: CircularTimePicker, CircularClockFace, useClockInteraction, angle utilities
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 2 waves (tests + implementation can partially overlap)
> **Critical Path**: Tests → Interactive Timeline → ZoneConfig Integration → Cleanup

---

## Context

### Original Request
Replace the circular time picker with slider handles on the climate timeline. Brainstorm and explore how the interaction model would work.

### Interview Summary
**Key Discussions**:
- **Approach**: Option A — draggable edge handles rendered directly ON the ClimatePeriodTimeline's sun/day band
- **Interaction**: Drag left/right edges for start/end times (5-minute snap), drag body to shift entire period, right-click for ramp editing
- **Locked photoperiod**: Edges locked together — dragging either adjusts the other to maintain 12h/18h duration
- **Layout**: Timeline height 300px (from 270px). Remove the 25% left column (CircularTimePicker); the remaining 40% (ClimatePeriodsTable+LightIntensity) and 35% (RelayChannelMatrix) columns share the freed space (flex-1 each).
- **Ramps**: Gradient visualization at band edges + right-click popover with number input for ramp duration
- **No text input fallbacks**: Purely visual/drag interaction
- **Test strategy**: TDD with Vitest + React Testing Library (test infrastructure must be set up first — Task 0)

**Research Findings**:
- CircularTimePicker is a 392-line canvas-based component with companion CircularClockFace (192 lines) and useClockInteraction hook (206 lines)
- ClimatePeriodTimeline is currently a pure display component (264 lines) with no event handlers — must become interactive
- ZoneConfig uses `handleParamChange` for immediate state updates; API save is deferred until SAVE button
- No component library exists (pure Tailwind CSS v4) — slider handles must be custom-built
- Two separate ramp systems exist: light ramps (schedules table) and climate ramps (climate_periods table). The timeline right-click targets light ramps.

### Metis Review
**Identified Gaps** (addressed):
- **Ramp gradient rendering**: Resolved → Use separate gradient overlay divs positioned at sun band edges (CSS `linear-gradient`)
- **Right-click popover scope**: Resolved → Popover targets light ramps only (`light_ramp_up_minutes` / `light_ramp_down_minutes`)
- **Locked edge behavior**: Resolved → "Edges locked together" means both handles remain interactive; dragging either auto-adjusts the other to maintain photoperiod duration
- **Touch interaction**: Explicitly EXCLUDED — desktop mouse only
- **Edge cases**: All addressed with constraints (min photoperiod gap, boundary clamping, zero-ramp handling)

### Momus Review (Round 1)
**Blocking Issues Found** (all fixed):
- **Test infrastructure does not exist**: `package.json` has no test runner or RTL. Fixed → Added Task 0 to install Vitest (Vite-native) + @testing-library/react + @testing-library/jest-dom. `jsdom` already in devDependencies. Exception added to "Must NOT Have" for test-only devDependencies.
- **ZoneConfig layout mischaracterized**: Plan described 30%/70% two-column layout; actual code has 25%/40%/35% three columns (CircularTimePicker / ClimateTable+LightIntensity / RelayChannelMatrix). Fixed → All layout descriptions now show correct three-column layout. Removing 25% column leaves 40%+35% columns to share space via `flex-1`.
- **Wrong file references**: Plan cited `climatePeriodTimeline.ts` for `buildSegments` and `getPosition`; these are local functions in `ClimatePeriodTimeline.tsx`. Fixed → All references updated.

---

## Work Objectives

### Core Objective
Transform the ClimatePeriodTimeline from a passive display component into an interactive light schedule editor, replacing the CircularTimePicker with inline draggable handles on the day band.

### Concrete Deliverables
1. **Modified `ClimatePeriodTimeline.tsx`**: Interactive with edge handles, body drag, ramp gradients, right-click popover
2. **New test file `ClimatePeriodTimeline.interaction.test.tsx`**: Vitest + RTL tests for all interaction modes
3. **Modified `ZoneConfig.tsx`**: Removed CircularTimePicker, updated layout, wired new callbacks
4. **Cleaned `timeMath.ts`**: Angle/circular utilities removed
5. **Deleted files**: `CircularTimePicker.tsx`, `CircularClockFace.tsx`, `useClockInteraction.ts`

### Definition of Done
- [ ] All TDD tests pass: `npx vitest run -- ClimatePeriodTimeline` → 0 failures
- [ ] Dragging left/right edges updates day_start/day_end times with 5-minute snap
- [ ] Locked photoperiod: dragging either edge auto-adjusts the opposite edge
- [ ] Dragging band body shifts entire photoperiod
- [ ] Right-click band shows popover with ramp number input (0-180)
- [ ] Ramp gradient renders at band edges with width proportional to ramp_minutes
- [ ] ZoneConfig layout shows timeline at 300px, right panel at full width
- [ ] CircularTimePicker, CircularClockFace, useClockInteraction files deleted
- [ ] `npm run dev` → timeline is interactive, no errors
- [ ] `npm run build` succeeds with 0 errors

### Must Have
- Drag interaction: left edge, right edge, body drag (locked + unlocked)
- 5-minute time snap grid
- Ramp gradient visualization + right-click popover
- Locked photoperiod preservation (12h Flower / 18h Veg)
- TDD: tests written first, then implementation
- API save flow unchanged (updateRoomParameters → saveRoomSchedule → saveClimatePeriods)

### Must NOT Have (Guardrails)
- **NO text input fallbacks** — purely visual/drag
- **NO touch/mobile support** — desktop mouse only
- **NO keyboard navigation** — not in scope
- **NO tap-to-edit** (click band to open time popover) — not in spec
- **NO animation** on drag beyond native cursor feedback
- **NO undo/redo**
- **NO per-period climate drag** — only the light/day band is interactive; climate periods remain table-edited
- **DO NOT change the API save sequence** in handleSave
- **DO NOT change time format** — "HH:MM" strings throughout
- **DO NOT add external dependencies** — no new npm packages (exception: vitest + @testing-library/* for TDD setup in Task 0)
- **DO NOT touch `LightIntensity.tsx`, `ClimatePeriodsTable.tsx`, or `climatePeriodTimeline.ts`** — only the timeline component and ZoneConfig layout

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: NO (test runner + RTL not yet installed — set up in Task 0)
- **Automated tests**: TDD (Task 0 installs infrastructure → Task 1 writes failing tests → Tasks 3-4 implement)
- **Framework**: Vitest + React Testing Library + @testing-library/jest-dom (jsdom already in devDependencies)
- **Test script**: Added to `package.json` as `"test": "vitest run"`

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

> Whether TDD is enabled or not, EVERY task MUST include Agent-Executed QA Scenarios.
> These describe how the executing agent DIRECTLY verifies the deliverable.

**Verification Tool by Deliverable Type:**
| Type | Tool | How Agent Verifies |
|------|------|-------------------|
| Frontend/UI (timeline) | Playwright (playwright skill) | Navigate ZoneConfig, drag handles, verify band resize |
| Component tests | Bash (npm test) | Run Vitest, verify all pass |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Setup):
└── Task 0: Set up Vitest + RTL test infrastructure [no dependencies]

Wave 1 (After Wave 0):
├── Task 1: Write failing TDD tests [depends: 0]
└── Task 2: Clean up dead code (remove files, clean timeMath.ts) [no dependencies]

Wave 2 (After Wave 1):
├── Task 3: Add interactive handles to ClimatePeriodTimeline [depends: 1]
├── Task 4: Add ramp gradient + right-click popover [depends: 1]
└── Task 5: Update ZoneConfig layout + wire callbacks [depends: 2]

Wave 3 (After Wave 2):
└── Task 6: Integration verification + final cleanup [depends: 3, 4, 5]

Critical Path: Task 0 → Task 1 → Task 3 → Task 6
Parallel Speedup: ~30% faster than sequential
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 0 | None | 1 | None |
| 1 | 0 | 3, 4 | None |
| 2 | None | 5 | None |
| 3 | 1 | 6 | 4 |
| 4 | 1 | 6 | 3 |
| 5 | 2 | 6 | None |
| 6 | 3, 4, 5 | None | None |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 0 | 0 | `task(category="quick", load_skills=[], ...)` — install + config |
| 1 | 1, 2 | `task(category="quick", load_skills=[], ...)` — test file + file deletion |
| 2 | 3, 4, 5 | `task(subagent_type="coder", load_skills=["frontend-ui-ux"], ...)` — visual/interactive |
| 3 | 6 | `task(subagent_type="coder", load_skills=["playwright"], ...)` — browser verification |

---

## TODOs

- [ ] 0. **Set Up Vitest + React Testing Library Test Infrastructure**

  **What to do**:
  - Install Vitest and testing libraries:
    ```bash
    cd Infrastructure/frontend
    npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event
    ```
    Note: `jsdom` (v28.1.0) is already in devDependencies — no need to reinstall.
  - Add `"test"` script to `package.json`:
    ```json
    "scripts": {
      ...
      "test": "vitest run",
      "test:watch": "vitest"
    }
    ```
  - Create `Infrastructure/frontend/vitest.config.ts`:
    ```typescript
    import { defineConfig } from 'vitest/config'
    import react from '@vitejs/plugin-react'

    export default defineConfig({
      plugins: [react()],
      test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: ['./src/test-setup.ts'],
      },
    })
    ```
  - Create `Infrastructure/frontend/src/test-setup.ts`:
    ```typescript
    import '@testing-library/jest-dom'
    ```
  - Create `Infrastructure/frontend/src/components/__tests__/` directory
  - Create example test to verify setup:
    ```typescript
    // src/components/__tests__/example.test.tsx
    import { render, screen } from '@testing-library/react'
    import { describe, it, expect } from 'vitest'

    describe('test infrastructure', () => {
      it('renders jsdom', () => {
        render(<div data-testid="test">Hello</div>)
        expect(screen.getByTestId('test')).toHaveTextContent('Hello')
      })
    })
    ```
  - Verify: `npm test` → 1 test passes

  **Must NOT do**:
  - Don't install Jest (Vitest is Vite-native, better for this project)
  - Don't configure coverage or CI integration yet (keep it minimal)
  - Don't modify `tsconfig.json` unless needed for JSX transform

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: npm install + config file creation
  - **Skills**: `[]`
    - No specialized skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 0 (prerequisite for all test-related tasks)
  - **Blocks**: Task 1
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/package.json` — Current scripts and devDependencies (jsdom@28.1.0 already present)
  - `Infrastructure/frontend/vite.config.ts` — Existing Vite config (reference for plugin/react setup)

  **Acceptance Criteria**:
  - [ ] `npm ls vitest @testing-library/react @testing-library/jest-dom` → all installed
  - [ ] `node -e "require('./vitest.config.ts')"` — or `npm test` reports no config errors
  - [ ] `npm test` → 1 test passes (example test)

  **Commit**: NO (combined with Task 1-2)

---

- [ ] 1. **Write Failing TDD Tests for Timeline Interactions**

  **What to do**:
  - Create test file: `Infrastructure/frontend/src/components/__tests__/ClimatePeriodTimeline.interaction.test.tsx`
  - Write Vitest + RTL tests that will FAIL because the interaction features don't exist yet:

  **Test 1 — Edge handle drag updates start time (5-min snap)**:
  ```typescript
  import { describe, it, expect, vi } from 'vitest'
  import { render, screen, fireEvent } from '@testing-library/react'

  test('dragging left edge handle updates day start time with 5-min snap', () => {
    const onDayStartChange = vi.fn()
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      onDayStartChange={onDayStartChange}
      onDayEndChange={vi.fn()}
    />)
    const leftHandle = screen.getByTestId('timeline-handle-start')
    fireEvent.mouseDown(leftHandle, { clientX: 250 })
    fireEvent.mouseMove(window, { clientX: 300 })
    fireEvent.mouseUp(window)
    expect(onDayStartChange).toHaveBeenCalledWith(expect.stringMatching(/:0[05]$|:1[05]$|:2[05]$|:3[05]$|:4[05]$|:5[05]$/))
  })
  ```

  **Test 2 — Locked photoperiod: dragging either edge adjusts both**:
  ```typescript
  test('locked photoperiod: dragging right edge adjusts left edge to maintain duration', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      lockedPhotoperiodHours={12}
      onDayStartChange={onDayStartChange}
      onDayEndChange={onDayEndChange}
    />)
    const rightHandle = screen.getByTestId('timeline-handle-end')
    fireEvent.mouseDown(rightHandle, { clientX: 750 })
    fireEvent.mouseMove(window, { clientX: 780 })
    fireEvent.mouseUp(window)
    expect(onDayEndChange).toHaveBeenCalled()
    expect(onDayStartChange).toHaveBeenCalled()
  })
  ```

  **Test 3 — Body drag (unlocked) shifts entire band**:
  ```typescript
  test('dragging body of day band shifts the band', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      onDayStartChange={onDayStartChange}
      onDayEndChange={onDayEndChange}
    />)
    const bandBody = screen.getByTestId('timeline-day-band')
    fireEvent.mouseDown(bandBody, { clientX: 500 })
    fireEvent.mouseMove(window, { clientX: 550 })
    fireEvent.mouseUp(window)
    expect(onDayStartChange).toHaveBeenCalled()
    expect(onDayEndChange).toHaveBeenCalled()
  })
  ```

  **Test 4 — Right-click popover renders with number inputs**:
  ```typescript
  test('right-click on day band shows ramp duration popover', () => {
    const onRampChange = vi.fn()
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      rampUpDuration={15}
      rampDownDuration={30}
      onRampUpChange={onRampChange}
      onRampDownChange={onRampChange}
    />)
    const band = screen.getByTestId('timeline-day-band')
    fireEvent.contextMenu(band, { clientX: 400, clientY: 150 })
    expect(screen.getByTestId('timeline-ramp-popover')).toBeInTheDocument()
    expect(screen.getByLabelText('Ramp up (min)')).toHaveValue(15)
    expect(screen.getByLabelText('Ramp down (min)')).toHaveValue(30)
  })
  ```

  **Test 5 — Ramp gradient renders with proportional width**:
  ```typescript
  test('ramp gradient width is proportional to ramp duration', () => {
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      rampUpDuration={15}
    />)
    const rampGradient = screen.getByTestId('timeline-ramp-up-gradient')
    const widthPercent = parseFloat(rampGradient.style.width)
    expect(widthPercent).toBeCloseTo((15 / 1440) * 100, 1)
  })
  ```

  **Test 6 — Locked photoperiod: edge cursor shows ew-resize**:
  ```typescript
  test('locked photoperiod: edge handles still have ew-resize cursor', () => {
    render(<ClimatePeriodTimeline
      periods={[]}
      lightDayStart="06:00"
      lightDayEnd="18:00"
      lockedPhotoperiodHours={12}
    />)
    const leftHandle = screen.getByTestId('timeline-handle-start')
    expect(leftHandle).toHaveStyle({ cursor: 'ew-resize' })
  })
  ```

  - Run: `npx vitest run -- ClimatePeriodTimeline.interaction` → **Expected: 6 tests FAIL** (features not implemented yet)

  **Must NOT do**:
  - Don't test visual appearance of gradients (test behavior, not pixels)
  - Don't mock browser APIs that aren't needed (use jsdom defaults)
  - Don't test SVG polylines (existing display, unchanged)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward test file creation using existing patterns
  - **Skills**: `[]`
    - No specialized skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 3, Task 4
  - **Blocked By**: Task 0 (test infrastructure must be set up)

  **References**:
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx` — Component under test (current props, DOM structure)
  - `Infrastructure/frontend/package.json` — Vitest dependency check
  - `Infrastructure/frontend/vitest.config.ts` — Test config (created in Task 0)
  - `Infrastructure/frontend/src/utils/timeMath.ts:timeToMinutes` — Time conversion for assertion math
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:70` — Local `getPosition` function: `(minutes / 1440) * 100` — coordinate system for handle positioning

  **Acceptance Criteria**:
  - [ ] Test file created: `Infrastructure/frontend/src/components/__tests__/ClimatePeriodTimeline.interaction.test.tsx`
  - [ ] `npx vitest run -- ClimatePeriodTimeline.interaction` → 6 tests, 6 failures (features not implemented)
  - [ ] Each test uses `data-testid` selectors for stable element targeting
  - [ ] No test passes accidentally (real failures, not false positives)

  **Commit**: NO (combined with Task 3)

---

- [ ] 2. **Remove Dead Code: Circular Picker Components + Angle Utilities**

  **What to do**:
  - Delete `Infrastructure/frontend/src/components/CircularTimePicker.tsx`
  - Delete `Infrastructure/frontend/src/components/CircularClockFace.tsx`
  - Delete `Infrastructure/frontend/src/hooks/useClockInteraction.ts`
  - From `timeMath.ts`, remove angle-related functions that are only used by the circular picker:
    - `angleToMinutes`
    - `minutesToAngle`
    - `calculatePhotoperiod`
    - `getAngleFromMouse`
    - `getDistanceFromCenter`
    - `normalizeAngle`
    - `calculateMidAngle`
    - `isOvernightPeriod`
  - Keep: `timeToMinutes` (used by ClimatePeriodTimeline), `minutesToTime` (needed by new drag handlers)
  - Verify no remaining imports of deleted files: `rg "CircularTimePicker|CircularClockFace|useClockInteraction" Infrastructure/frontend/src/` → no results

  **Must NOT do**:
  - Don't touch `timeToMinutes` or `minutesToTime` — still needed
  - Don't delete `timeMath.ts` entirely — keep the remaining utilities
  - Don't modify ZoneConfig yet (Task 5 handles that) — but do verify ZoneConfig will have import errors (expected, resolved in Task 5)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: File deletion + targeted function removal
  - **Skills**: `[]`
    - No specialized skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:
  - `Infrastructure/frontend/src/utils/timeMath.ts` — Current file to edit
  - `Infrastructure/frontend/src/components/CircularTimePicker.tsx` — File to delete
  - `Infrastructure/frontend/src/components/CircularClockFace.tsx` — File to delete
  - `Infrastructure/frontend/src/hooks/useClockInteraction.ts` — File to delete

  **Acceptance Criteria**:
  - [ ] `ls Infrastructure/frontend/src/components/CircularTimePicker.tsx` → file not found
  - [ ] `ls Infrastructure/frontend/src/components/CircularClockFace.tsx` → file not found
  - [ ] `ls Infrastructure/frontend/src/hooks/useClockInteraction.ts` → file not found
  - [ ] `timeMath.ts` only contains `timeToMinutes`, `minutesToTime` (and exports)
  - [ ] `rg "angleToMinutes|minutesToAngle|calculatePhotoperiod|getAngleFromMouse|getDistanceFromCenter|normalizeAngle|calculateMidAngle|isOvernightPeriod" Infrastructure/frontend/src/` → no results (except timeMath.ts itself which was just cleaned)
  - [ ] `npm run build` may fail at this point (ZoneConfig still imports CircularTimePicker — this is expected, fixed in Task 5)

  **Commit**: NO (combined with Task 5)

---

- [ ] 3. **Add Interactive Handles to ClimatePeriodTimeline**

  **What to do**:
  - Transform ClimatePeriodTimeline from display-only → interactive component
  - Add new props: `onDayStartChange`, `onDayEndChange`, `lockedPhotoperiodHours`, `rampUpDuration`, `rampDownDuration`, `onRampUpChange`, `onRampDownChange`
  - Add draggable edge handles to the day band:

  **Edge Handle Implementation**:
  - Render two absolutely-positioned handle divs at the left and right edges of the sun band
  - Handle dimensions: `20px × 100%` (tall, narrow grab zone), `data-testid="timeline-handle-start"` / `"timeline-handle-end"`
  - Cursor: `ew-resize` on handles (even when locked — edges remain interactive per design)
  - Hover: slight opacity/scale change for affordance (`hover:opacity-100` vs `opacity-60`)
  - Visual: vertical line 2px wide in accent color (`bg-accent` / `#06b6d4`), semi-transparent fill

  **Drag Math (Linear Timeline)**:
  - On `mousedown` on a handle: record `dragTarget = 'start' | 'end'` and offset
  - On `mousemove` (attached to `window`): convert mouse X to minutes
    ```
    containerRect = timelineRef.current.getBoundingClientRect()
    fractionX = (event.clientX - containerRect.left) / containerRect.width
    rawMinutes = Math.round(fractionX * 1440 / 5) * 5  // 5-min snap
    clampedMinutes = Math.max(0, Math.min(1435, rawMinutes))  // clamp to valid range
    ```
  - Call `onDayStartChange(minutesToTime(clampedMinutes))` or `onDayEndChange(...)` immediately (no debounce)
  - On `mouseup` (attached to `window`): clear drag state

  **Locked Photoperiod Logic**:
  - When `lockedPhotoperiodHours !== null` and user drags either edge:
    - Get the dragged edge's new minutes
    - Calculate the opposite edge as: `newOppositeMinutes = (draggedMinutes + lockedDuration) % 1440`
    - Call BOTH `onDayStartChange` and `onDayEndChange`
  - The body of the band is also draggable (shift entire period) — same as current yellow slider behavior
  - `data-testid="timeline-day-band"` on the band body for body-drag testing

  **Band Body Drag**:
  - On `mousedown` on band body (not on handles): record initial mouse position + initial start minutes
  - On `mousemove`: calculate delta minutes from initial, apply to both start and end
  - Cursor: `grab` on body, `grabbing` while dragging

  **Overnight Period Handling**:
  - Use existing `buildSegments()` logic — the band already handles wrap-around (two segments if end < start)
  - Handles render on each segment's edges when period wraps midnight
  - Drag math in overnight mode: wrap minutes > 1440 back to 0-1439 range

  **Boundary Constraints**:
  - Min photoperiod: 0 minutes (edges can meet)
  - Max: 1435 minutes (can't wrap fully around to same point)
  - Clamp all minutes to [0, 1435]
  - No inversion prevention — edges can be dragged past each other (day becomes night, band updates accordingly)

  **Must NOT do**:
  - Don't change the SVG polyline rendering (setpoint curves remain display-only)
  - Don't change the night band (moonSegments) rendering — only sun band gets handles
  - Don't add debounce or throttle — callbacks fire immediately (matches current CircularTimePicker behavior)
  - Don't prevent edge crossing — allows full 24h flexibility

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Frontend interaction + visual handle design + CSS
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Handle styling, cursor states, hover affordances, visual polish

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 4)
  - **Blocks**: Task 6
  - **Blocked By**: Task 1 (tests must exist first)

  **References**:
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:121-263` — Current render structure (bands, SVG, hour grid)
  - `Infrastructure/frontend/src/utils/timeMath.ts:timeToMinutes, minutesToTime` — Time conversion to use
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:18-25 buildSegments` — Local function for building time segments (overnight handling)
  - `Infrastructure/frontend/src/hooks/useClockInteraction.ts:39-142` — Current drag interaction pattern (reference for mousedown/move/up flow)
  - `Infrastructure/frontend/src/components/LightIntensity.tsx:278-292` — Existing `<input type="range">` pattern (cursor, styling reference)
  - `Infrastructure/frontend/src/styles/index.css` — Design tokens (--accent, --surface-*, --text-*)

  **Acceptance Criteria**:

  **TDD (Tests must pass from Task 1)**:
  - [ ] `npx vitest run -- ClimatePeriodTimeline.interaction` → Tests 1, 2, 3, 6 pass
  - [ ] Test 1: dragging left edge → `onDayStartChange` called with 5-min snapped value
  - [ ] Test 2: locked photoperiod, drag right edge → `onDayStartChange` AND `onDayEndChange` both called
  - [ ] Test 3: body drag → both edges shift together
  - [ ] Test 6: locked mode edges still have `cursor: ew-resize`

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Drag left handle adjusts day start time
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running on localhost:3001, navigate to /vegetation/control
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Wait for: [data-testid="timeline-handle-start"] visible (timeout: 10s)
      3. Get initial bounding box of [data-testid="timeline-day-band"]
      4. Hover over [data-testid="timeline-handle-start"] — assert cursor is "ew-resize"
      5. Mouse down on handle-left-edge
      6. Mouse move right by ~50px
      7. Mouse up
      8. Assert: [data-testid="timeline-day-band"] left position changed
      9. Screenshot: .sisyphus/evidence/task-3-handle-drag-after.png
    Expected Result: Day band left edge moved right, width changed
    Evidence: .sisyphus/evidence/task-3-handle-drag-after.png

  Scenario: Drag handle to timeline boundary clamps
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running, day_start = "06:00"
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Mouse down on left handle
      3. Mouse move far left past 00:00 position
      4. Mouse up
      5. Assert: handle did not move past 0% position
      6. Screenshot: .sisyphus/evidence/task-3-handle-clamp.png
    Expected Result: Handle clamped at left boundary
    Evidence: .sisyphus/evidence/task-3-handle-clamp.png
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-3-handle-drag-after.png`
  - [ ] `.sisyphus/evidence/task-3-handle-clamp.png`

  **Commit**: YES
  - Message: `feat(timeline): add interactive draggable edge handles to day band`
  - Files: `ClimatePeriodTimeline.tsx`
  - Pre-commit: `npx vitest run -- ClimatePeriodTimeline.interaction`

---

- [ ] 4. **Add Ramp Gradient Visualization + Right-Click Popover**

  **What to do**:

  **A. Ramp Gradient Rendering**:
  - Add gradient overlay divs at the left edge (ramp-up) and right edge (ramp-down) of the sun band
  - Each gradient div:
    - `position: absolute`, height: `100%`, top: `0`
    - Left gradient: anchored to band's `left` edge, width = `(rampUpDuration / 1440) * 100%`
    - Right gradient: anchored to band's `right` edge (right-aligned), width = `(rampDownDuration / 1440) * 100%`
    - CSS: `background: linear-gradient(to right, rgba(234,179,8,0) 0%, rgba(234,179,8,0.45) 100%)` (transparent → sun color)
    - When `rampDuration === 0`: gradient div has `width: 0%` (hidden)
    - `data-testid="timeline-ramp-up-gradient"` / `"timeline-ramp-down-gradient"`
    - `z-index: 3` (above moon band at z-1, below handles at z-10)

  **B. Right-Click Popover**:
  - On `contextmenu` (right-click) on the day band body (`data-testid="timeline-day-band"`):
    - Prevent default browser context menu (`event.preventDefault()`)
    - Position popover near cursor: `position: fixed`, `left: event.clientX + 8px`, `top: event.clientY + 8px`
    - Render a small popover div with:
      - Label "Ramp up (min):" + `<input type="number" min="0" max="180" value={rampUpDuration ?? 0}>`
      - Label "Ramp down (min):" + `<input type="number" min="0" max="180" value={rampDownDuration ?? 0}>`
      - On input change: call `onRampUpChange(parseInt(value))` / `onRampDownChange(parseInt(value))`
      - "Done" button to close popover
    - Close on: outside click (`mousedown` on document), Escape key, or Done button
    - `data-testid="timeline-ramp-popover"`
    - Styling: `bg-surface-primary border border-border-default rounded-lg shadow-lg p-3` (matches existing design tokens)

  **Must NOT do**:
  - Don't use a third-party popover library — pure div with fixed positioning
  - Don't show climate period ramps (ramp_minutes from table) — only light ramps
  - Don't animate the popover (appear/disappear instantly)
  - Don't show ramp inputs inline (only on right-click)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: UI component (popover + gradient) with styling
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Popover positioning, gradient styling, number input styling

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 3)
  - **Blocks**: Task 6
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:72-73` — sunColor for gradient reference
  - `Infrastructure/frontend/src/components/CircularTimePicker.tsx:29-31` — Existing input styling classes (`inputRampClass`)
  - `Infrastructure/frontend/src/styles/index.css` — Design tokens for popover styling
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:70` — Local `getPosition` function: `(minutes / 1440) * 100`

  **Acceptance Criteria**:

  **TDD (Tests from Task 1)**:
  - [ ] `npx vitest run -- ClimatePeriodTimeline.interaction` → Tests 4, 5 pass
  - [ ] Test 4: right-click shows popover with ramp inputs
  - [ ] Test 5: ramp gradient width is proportional to duration

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Right-click band shows ramp popover
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running, navigate to /vegetation/control
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Right-click on [data-testid="timeline-day-band"]
      3. Assert: [data-testid="timeline-ramp-popover"] is visible
      4. Assert: input[aria-label="Ramp up (min)"] has value matching current ramp
      5. Clear input, type "45", press Tab
      6. Assert: ramp gradient width updated
      7. Click "Done" button in popover
      8. Assert: popover is removed from DOM
      9. Screenshot: .sisyphus/evidence/task-4-ramp-popover.png
    Expected Result: Popover appears, edits ramp, closes cleanly
    Evidence: .sisyphus/evidence/task-4-ramp-popover.png

  Scenario: Ramp gradient renders at zero and non-zero values
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running, rampUpDuration=0, rampDownDuration=30
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Assert: [data-testid="timeline-ramp-up-gradient"] width is 0px or 0%
      3. Assert: [data-testid="timeline-ramp-down-gradient"] width > 0
      4. Screenshot: .sisyphus/evidence/task-4-gradient.png
    Expected Result: Only ramp-down gradient visible
    Evidence: .sisyphus/evidence/task-4-gradient.png
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-4-ramp-popover.png`
  - [ ] `.sisyphus/evidence/task-4-gradient.png`

  **Commit**: YES
  - Message: `feat(timeline): add ramp gradient visualization and right-click popover`
  - Files: `ClimatePeriodTimeline.tsx`
  - Pre-commit: `npx vitest run -- ClimatePeriodTimeline.interaction`

---

- [ ] 5. **Update ZoneConfig Layout + Wire New Callbacks**

  **What to do**:
  - Remove `<CircularTimePicker>` import and usage from ZoneConfig.tsx
  - The current middle row has THREE columns (25% / 40% / 35%):
    - 25%: CircularTimePicker / ManualLightControl (lines 453-477)
    - 40%: ClimatePeriodsTable (55.7%) + LightIntensity (44.3%) stacked (lines 479-489)
    - 35%: RelayChannelMatrix with MCP23017 status (lines 490-505)
  - Remove the 25% column entirely
  - Make the remaining two columns share the freed space using `flex-1` (50/50 split):
  - Change timeline height from `h-[270px]` to `h-[300px]` (increase 30px)
  - Wire the new ClimatePeriodTimeline props to the existing state:

  **Before** (current ZoneConfig.tsx lines 452-506):
  ```tsx
  <div className="flex gap-1 h-[450px] shrink-0">
    <div className="w-[25%] h-full">
      <div className="bg-surface-primary ...">
        {!isConstant ? (
          <CircularTimePicker ... />     ← REMOVE ENTIRELY
        ) : (
          <ManualLightControl ... />
        )}
      </div>
    </div>

    <div className="w-[40%] flex flex-col gap-1 h-full overflow-hidden">
      <div className="...flex-[55.7]...">
        <ClimatePeriodsTable ... />
      </div>
      <div className="flex-[44.3]...">
        <LightIntensity ... />
      </div>
    </div>
    <div className="w-[35%] h-full">
      <RelayChannelMatrix ... />
    </div>
  </div>
  ```

  **After**:
  ```tsx
  <div className="flex gap-1 h-[450px] shrink-0">
    <div className="flex-1 flex flex-col gap-1 h-full overflow-hidden">
      <div className="bg-surface-primary rounded-lg border border-border-subtle p-1 flex-[55.7] overflow-auto">
        <ClimatePeriodsTable
          periods={climatePeriods}
          onChange={setClimatePeriods}
        />
      </div>
      <div className="flex-[44.3] overflow-auto">
        <LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact={true} />
      </div>
    </div>
    <div className="flex-1 h-full">
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
        onToggleMenu={(ch: number) => setMenuOpenChannel(prev => prev === ch ? null : ch)}
        onMenuAction={handleRelayMenuAction}
      />
    </div>
  </div>
  ```
  Note: In constant mode, ManualLightControl goes in the first `flex-1` column (replacing the table/intensity stack). The `<div className="text-[14px]...">Light Schedule</div>` header at line 455-457 is also removed (timeline handles make the purpose obvious).

  - Update ClimatePeriodTimeline usage to pass new props:
  ```tsx
  <ClimatePeriodTimeline
    periods={climatePeriods}
    lightDayStart={params?.day_start_time || '06:00'}
    lightDayEnd={params?.night_start_time || '18:00'}
    className="h-full"
    // NEW PROPS:
    onDayStartChange={(time) => handleParamChange({ day_start_time: time })}
    onDayEndChange={(time) => handleParamChange({ night_start_time: time })}
    lockedPhotoperiodHours={lockedPhotoperiod}
    rampUpDuration={params.light_ramp_up_minutes}
    rampDownDuration={params.light_ramp_down_minutes}
    onRampUpChange={(d) => handleParamChange({ light_ramp_up_minutes: d ?? 0 })}
    onRampDownChange={(d) => handleParamChange({ light_ramp_down_minutes: d ?? 0 })}
  />
  ```

  **Must NOT do**:
  - Don't change the SAVE logic (lines 157-214, `handleSave`) — API calls remain identical
  - Don't change the `handleParamChange` function (lines 125-131) — same state update pattern
  - Don't remove the `isConstant` check — constant modes still show ManualLightControl (but now in a `flex-1` column)
  - Don't change any other import or section
  - Don't touch RelayChannelMatrix or its MCP23017 status indicator

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Import removal + prop wiring + layout change — straightforward
  - **Skills**: `[]`
    - No specialized skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 6
  - **Blocked By**: Task 2 (dead code must be removed first)

  **References**:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:1-17` — Current imports (remove line 11 `import CircularTimePicker`)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:425-428` — `lockedPhotoperiod` calculation
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:430-516` — Full JSX layout to modify (three columns at 452-505)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:125-131` — `handleParamChange` (unchanged)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:157-214` — `handleSave` (unchanged)

  **Acceptance Criteria**:
  - [ ] `npm run build` succeeds with 0 errors
  - [ ] `rg "CircularTimePicker" Infrastructure/frontend/src/` → no results
  - [ ] ZoneConfig renders without the 25% column div
  - [ ] ClimatePeriodsTable+LightIntensity and RelayChannelMatrix each take ~50% width (flex-1)
  - [ ] Timeline renders at 300px height
  - [ ] Constant mode: ManualLightControl shows full-width in first flex-1 column

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: ZoneConfig page renders without circular picker, correct three-column layout
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running on localhost:3001
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Wait for page load (timeout: 15s)
      3. Assert: no canvas element (circular clock) on page
      4. Assert: [data-testid="timeline-handle-start"] is visible
      5. Assert: ClimatePeriodsTable is visible
      6. Assert: LightIntensity is visible  
      7. Assert: RelayChannelMatrix is visible (third column preserved)
      8. Assert: two flex-1 columns share ~50/50 width
      9. Screenshot: .sisyphus/evidence/task-5-layout.png
    Expected Result: Timeline with handles, two equal-width columns below
    Evidence: .sisyphus/evidence/task-5-layout.png

  Scenario: Constant mode shows ManualLightControl full-width
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Switch mode to "Drying" or "Sleep" (constant mode)
      3. Assert: ManualLightControl component visible at full width
      4. Assert: No timeline (constant mode hides timeline)
      5. Screenshot: .sisyphus/evidence/task-5-constant-mode.png
    Expected Result: ManualLightControl in full-width column
    Evidence: .sisyphus/evidence/task-5-constant-mode.png
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-5-layout.png`
  - [ ] `.sisyphus/evidence/task-5-constant-mode.png`

  **Commit**: YES
  - Message: `refactor(zoneconfig): remove CircularTimePicker, wire timeline handles, full-width layout`
  - Files: `ZoneConfig.tsx`
  - Pre-commit: `npm run build`

---

- [ ] 6. **Integration Verification + Final Cleanup**

  **What to do**:
  - Run full test suite: `npm test` → all tests pass, 0 failures
  - Run build: `npm run build` → succeeds with 0 errors
  - Verify no dead imports remain:
    ```bash
    rg "CircularTimePicker|CircularClockFace|useClockInteraction" Infrastructure/frontend/src/ --include '*.ts' --include '*.tsx'
    ```
    → no results
  - Verify no unused exports from `timeMath.ts`:
    ```bash
    rg "angleToMinutes|minutesToAngle|calculatePhotoperiod|getAngleFromMouse|getDistanceFromCenter|normalizeAngle|calculateMidAngle|isOvernightPeriod" Infrastructure/frontend/src/ --include '*.ts' --include '*.tsx'
    ```
    → no results (except timeMath.ts if they were already removed)
  - Final manual check: all `data-testid` attributes on new elements are consistent with tests

  **Must NOT do**:
  - Don't add new features — this is verification only
  - Don't touch any files not already modified in Tasks 2-5

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
    - Reason: Integration verification requires thorough checking across all modified files
  - **Skills**: [`playwright`]
    - `playwright`: Browser-based end-to-end verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final)
  - **Blocks**: None
  - **Blocked By**: Task 3, Task 4, Task 5

  **References**:
  - All files from Tasks 1-5

  **Acceptance Criteria**:
  - [ ] `npm test` → 0 failures
  - [ ] `npm run build` → success, 0 errors
  - [ ] No dead imports of removed components
  - [ ] No unused angle utility imports
  - [ ] All `data-testid` attributes consistent between component and test file

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Full interaction flow — drag edge, adjust ramp, verify save
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running on localhost:3001
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Wait for page load (timeout: 15s)
      3. Drag left handle right by ~100px (adjust day start later)
      4. Drag right handle left by ~50px (adjust day end earlier)
      5. Right-click day band
      6. In popover: set ramp up to 45, ramp down to 60
      7. Click Done
      8. Assert: gradient widths updated proportionally
      9. Click SAVE button in ribbon
      10. Wait for success toast: "Saved"
      11. Screenshot: .sisyphus/evidence/task-6-full-flow.png
    Expected Result: All interactions work, save succeeds
    Evidence: .sisyphus/evidence/task-6-full-flow.png

  Scenario: Drag handle past midnight (overnight period)
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running
    Steps:
      1. Navigate to: http://localhost:3001/vegetation/control
      2. Set day_start to "20:00" via handle drag
      3. Set day_end to "04:00" via opposite handle drag
      4. Assert: Two separate band segments visible (wrap-around)
      5. Assert: Handles on both segments
      6. Screenshot: .sisyphus/evidence/task-6-overnight.png
    Expected Result: Overnight band renders as two segments with handles
    Evidence: .sisyphus/evidence/task-6-overnight.png

  Scenario: Locked photoperiod — drag edge maintains 12h duration
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running, /flower/control (12h locked)
    Steps:
      1. Navigate to: http://localhost:3001/flower/control
      2. Note current day_start and day_end times
      3. Drag right handle right by ~30px
      4. Assert: day_start also shifted by same amount (duration = 12h preserved)
      5. Screenshot: .sisyphus/evidence/task-6-locked.png
    Expected Result: Photoperiod duration unchanged after edge drag
    Evidence: .sisyphus/evidence/task-6-locked.png
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-6-full-flow.png`
  - [ ] `.sisyphus/evidence/task-6-overnight.png`
  - [ ] `.sisyphus/evidence/task-6-locked.png`

  **Commit**: YES
  - Message: `test(timeline): integration verification — all interactions + edge cases`
  - Files: (verification only, no code changes expected)
  - Pre-commit: `npm test && npm run build`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 3 | `feat(timeline): add interactive draggable edge handles to day band` | `ClimatePeriodTimeline.tsx` | `npx vitest run -- ClimatePeriodTimeline.interaction` |
| 4 | `feat(timeline): add ramp gradient visualization and right-click popover` | `ClimatePeriodTimeline.tsx` | `npx vitest run -- ClimatePeriodTimeline.interaction` |
| 5 | `refactor(zoneconfig): remove CircularTimePicker, wire timeline handles, full-width layout` | `ZoneConfig.tsx` | `npm run build` |
| 6 | `test(timeline): integration verification — all interactions + edge cases` | (verification only) | `npm test && npm run build` |

---

## Success Criteria

### Verification Commands
```bash
# Unit tests (TDD — all must pass)
npx vitest run -- ClimatePeriodTimeline.interaction   # Expected: 6/6 pass

# Full test suite
npm test                                        # Expected: all pass, 0 failures

# Production build
npm run build                                   # Expected: success, 0 errors

# Dead code check
rg "CircularTimePicker|CircularClockFace|useClockInteraction" Infrastructure/frontend/src/
# Expected: no results

# Removed utilities check
rg "angleToMinutes|minutesToAngle|calculatePhotoperiod|getAngleFromMouse|getDistanceFromCenter|normalizeAngle|calculateMidAngle|isOvernightPeriod" Infrastructure/frontend/src/ --include '*.ts' --include '*.tsx'
# Expected: no results
```

### Final Checklist
- [ ] All 6 TDD tests pass
- [ ] Left/right edge drag updates times with 5-min snap
- [ ] Body drag shifts entire photoperiod
- [ ] Locked photoperiod: edges linked together
- [ ] Right-click popover for ramp editing works
- [ ] Ramp gradient renders proportionally
- [ ] ZoneConfig layout: timeline 300px, right panel full width
- [ ] CircularTimePicker, CircularClockFace, useClockInteraction deleted
- [ ] `timeMath.ts` cleaned of angle utilities
- [ ] `npm run build` succeeds
- [ ] No dead imports remaining
- [ ] All "Must NOT Have" absent
