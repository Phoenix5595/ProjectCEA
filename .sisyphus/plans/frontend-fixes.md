# Frontend UI Fixes — Theme & Layout Issues

## TL;DR

> **Quick Summary**: Fix two critical issues discovered during half-screen testing: (1) Theme switching doesn't work because `@theme inline` uses hardcoded hex values instead of theme variable references, (2) Layout overflows in compact view — light names overlap and temp/ramp times spill over.
>
> **Deliverables**:
> - Theme switching functional across all 6 themes
> - Responsive layout that works in half-screen (desktop split view)
>
> **Estimated Effort**: Quick
> **Parallel Execution**: NO — sequential fixes
> **Critical Path**: Fix 1 → Fix 2 → Verify

---

## Context

### Original Request
User tested the modernized frontend at http://mothernode:3001 and found:
1. Theme switcher changes data-theme attribute but visual appearance remains identical
2. In half-screen (desktop split view), light names overflow and temp times below circular time picker spill over

### Root Cause Analysis

**Issue 1: Theme Not Working**
- `themes.css` defines theme variables inside `[data-theme="..."]` selectors: `--surface-base`, `--text-default`, etc.
- `index.css` has `@theme inline` block that maps Tailwind color utilities to CSS variables
- **BUG**: `@theme inline` uses hardcoded hex values (`--color-surface-base: #030712`) instead of referencing theme variables (`var(--surface-base)`)
- Result: Tailwind classes like `bg-surface-base` always use the hardcoded color, completely ignoring the theme

**Issue 2: Layout Overflow**
- Light name rows (Flower Room: Chilled Front, Apache, Chilled Back / Veg Room: similar) don't wrap on narrow viewports
- Temp/ramp time displays below circular time picker overflow their containers
- These sections were designed for full-screen and don't have responsive breakpoints

### Research Findings
- `@theme inline` block in `index.css` lines 92-180 has ~50 hardcoded color values
- Light name sections in `Dashboard.tsx` use flex row without wrap
- SetpointTimeline and related components have a `compact` prop that could help

---

## Work Objectives

### Core Objective
Fix theme switching and responsive layout for half-screen desktop usage.

### Concrete Deliverables
- `src/styles/index.css` — `@theme inline` uses `var()` references
- `src/pages/Dashboard.tsx` — Light names wrap responsively
- `src/pages/Dashboard.tsx` — Temp times stack vertically on compact

### Definition of Done
- [x] Theme switcher changes visual appearance (test all 6 themes)
- [x] Light names display correctly in half-screen
- [x] Temp times display correctly in half-screen
- [x] `npm run build` passes
- [x] `npm test` passes

### Must Have
- Theme switching must work for all 6 themes
- Layout must work in half-screen (approximately 960px width)
- No layout regressions in full-screen

### Must NOT Have (Guardrails)
- No changes to production repo (`/home/antoine/ProjectCEA/`)
- No new npm packages
- No layout changes to full-screen view
- No removal of existing functionality

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (Vitest from Task 10)
- **Automated tests**: YES — add theme visual regression tests
- **Agent-Executed QA**: YES — manual browser verification

### Agent-Executed QA Scenarios

**Scenario 1: Theme Switching**
```
Tool: Bash (curl + grep)
Steps:
  1. Start dev server: npm run dev
  2. Verify data-theme attribute changes: curl -s http://localhost:3001 | grep -o 'data-theme="[^"]*"'
  3. Check CSS variables: curl -s http://localhost:3001/src/styles/themes.css | grep "precision-void" | head -5
Expected Result: data-theme changes, CSS variables present
Evidence: Console output
```

**Scenario 2: Responsive Layout**
```
Tool: Manual (user verifies in browser)
Steps:
  1. Open http://mothernode:3001 in browser
  2. Resize to half-screen width
  3. Check light names row
  4. Check temp times section
Expected Result: No overflow, content readable
Evidence: User confirmation
```

---

## Execution Strategy

### Sequential Execution

```
Task 1: Fix @theme inline (CRITICAL - unblocks theme switching)
    ↓
Task 2: Fix light names overflow (layout fix)
    ↓
Task 3: Fix temp times overflow (layout fix)
    ↓
Verify: Build + test + user review
```

---

## TODOs

- [x] 1. Fix @theme inline to Use Theme Variable References

  **What to do**:
  - Edit `src/styles/index.css`
  - Find the `@theme inline` block (approximately lines 92-180)
  - Replace all hardcoded hex values with `var()` references to theme variables
  - Example mapping:
    ```css
    /* BEFORE (hardcoded): */
    --color-surface-base: #030712;
    --color-surface-primary: #111827;
    --color-text-default: #ffffff;
    --color-text-secondary: #d1d5db;
    --color-accent: #06b6d4;
    /* ... etc */

    /* AFTER (theme-aware): */
    --color-surface-base: var(--surface-base);
    --color-surface-primary: var(--surface-primary);
    --color-text-default: var(--text-default);
    --color-text-secondary: var(--text-secondary);
    --color-accent: var(--accent);
    /* ... etc */
    ```
  - The theme variables are defined in `themes.css` inside `[data-theme="..."]` selectors
  - All variables from `:root` block should be mapped

  **Must NOT do**:
  - Do NOT change the variable names (keep `--color-*` pattern for Tailwind)
  - Do NOT modify `:root` block
  - Do NOT change `themes.css`
  - Do NOT touch files outside `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`

  **Parallelization**:
  - **Can Run In Parallel**: NO — blocks tasks 2 and 3
  - **Parallel Group**: Sequential
  - **Blocks**: Tasks 2, 3
  - **Blocked By**: None (can start immediately)

  **References**:
  - `src/styles/index.css:92-180` — `@theme inline` block to fix
  - `src/styles/themes.css:1-198` — Theme variable definitions
  - `src/styles/index.css:45-63` — `:root` fallback values

  **Acceptance Criteria**:
  - [ ] All `--color-*` values in `@theme inline` use `var()` references
  - [ ] No hardcoded hex values remain in `@theme inline`
  - [ ] `npm run build` passes
  - [ ] Theme switcher changes visual appearance

  **Commit**: YES
  - Message: `fix(frontend): link @theme inline colors to theme variables`
  - Files: `src/styles/index.css`
  - Pre-commit: `npm run build`

---

- [x] 2. Fix Light Names Overflow in Half-Screen

  **What to do**:
  - Edit `src/pages/Dashboard.tsx`
  - Find light name sections (Flower Room around line 789-826, Veg Room around 605-645)
  - Current: Light names in single flex row
  - Fix options (choose one):
    - **Option A**: Add `flex-wrap: wrap` to allow 2 rows
    - **Option B**: Use smaller text on compact screens with media query
    - **Option C**: Truncate names with ellipsis on narrow viewports
  - Recommended: **Option A** (wrap to 2 rows) — preserves full names
  - Implementation:
    ```tsx
    // Add flex-wrap to the light names container
    <div className="flex flex-wrap gap-2">
      {/* light name elements */}
    </div>
    ```
  - Test at half-screen width (~960px)

  **Must NOT do**:
  - Do NOT change font sizes for full-screen view
  - Do NOT remove light names
  - Do NOT change the light control functionality
  - Do NOT touch files outside `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`

  **Parallelization**:
  - **Can Run In Parallel**: NO — depends on Task 1
  - **Parallel Group**: Sequential
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References**:
  - `src/pages/Dashboard.tsx:605-645` — Veg Room light names
  - `src/pages/Dashboard.tsx:789-826` — Flower Room light names

  **Acceptance Criteria**:
  - [ ] Light names display correctly in half-screen
  - [ ] No horizontal overflow
  - [ ] Full names visible (or truncated gracefully)
  - [ ] Full-screen layout unchanged

  **Commit**: YES
  - Message: `fix(frontend): responsive light names wrap in half-screen`
  - Files: `src/pages/Dashboard.tsx`

---

- [x] 3. Fix Temp Times Overflow Below Circular Time Picker

  **What to do**:
  - Edit `src/pages/Dashboard.tsx`
  - Find the section with ramp times and sun start/end times (below circular time picker)
  - Current: Side-by-side layout spills over in half-screen
  - Fix: Stack ramp times above/below sun start/end times
  - Implementation:
    ```tsx
    // Change from side-by-side to stacked on compact
    <div className="flex flex-col gap-2 lg:flex-row lg:gap-4">
      <div>{/* Ramp times */}</div>
      <div>{/* Sun start/end times */}</div>
    </div>
    ```
  - Use Tailwind responsive classes: `flex-col lg:flex-row`
  - Test at half-screen width (~960px)

  **Must NOT do**:
  - Do NOT change the time values or logic
  - Do NOT remove any time displays
  - Do NOT change full-screen layout (lg breakpoint)
  - Do NOT touch files outside `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`

  **Parallelization**:
  - **Can Run In Parallel**: YES — with Task 2 (both layout fixes)
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: Task 1

  **References**:
  - `src/pages/Dashboard.tsx` — find section below CircularTimePicker/SetpointTimeline
  - `src/components/SetpointTimeline.tsx` — has `compact` prop

  **Acceptance Criteria**:
  - [ ] Temp times display correctly in half-screen
  - [ ] No horizontal overflow
  - [ ] Full-screen layout unchanged (lg breakpoint)
  - [ ] All time values visible

  **Commit**: YES
  - Message: `fix(frontend): responsive temp times stack in half-screen`
  - Files: `src/pages/Dashboard.tsx`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `fix(frontend): link @theme inline colors to theme variables` | `src/styles/index.css` | `npm run build` |
| 2 | `fix(frontend): responsive light names wrap in half-screen` | `src/pages/Dashboard.tsx` | Visual check |
| 3 | `fix(frontend): responsive temp times stack in half-screen` | `src/pages/Dashboard.tsx` | Visual check |

---

## Success Criteria

### Verification Commands
```bash
npm run build  # Expected: success
npm test       # Expected: 8+ tests pass
```

### Final Checklist
- [x] Theme switcher changes visual appearance
- [x] All 6 themes work correctly
- [x] Light names display correctly in half-screen
- [x] Temp times display correctly in half-screen
- [x] Full-screen layout unchanged
- [x] Build passes
- [x] Tests pass
- [x] Committed and pushed to `ui/frontend-modernization`
