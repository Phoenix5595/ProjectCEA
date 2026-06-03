# Relay Matrix Slim Down — Remove Hardware Chrome & Redundant Data

## TL;DR

> **Quick Summary**: Slash the relay channel matrix on the Devices page from a wide hardware-document-style panel to a compact 4-data-point per-channel grid. Remove all board-level decorations (terminal strips, bank labels, COM divider, grid background, outer header), delete 6 redundant data points per channel box (glyph, LED, CH number, pin label, location, device type), and refactor silkscreen into the text area for a clean 8×2 grid.
>
> **Deliverables**:
> - Slimmed `RelayChannelMatrix.tsx` — no more hardware chrome (panel variant only)
> - Simplified `RelayChannelBox.tsx` — 4 data points: silkscreen, status badge, device name, elapsed time
> - Trimmed `DeviceManager.tsx` — remove outer "Relay Matrix" header block
>
> **Estimated Effort**: Quick (3 files, removal-only)
> **Parallel Execution**: NO — sequential, single wave
> **Critical Path**: Box refactor → Matrix cleanup → DeviceManager trim → build

---

## Context

### Original Request
> "The matrix is ok but too wide. it can be at least 50% thinner. A lot of information is also irrlevant or duplicated. lets iterate"

### Agreed Design
Each channel box shows **4 data points**: silkscreen (K1-K16), status badge (ON/IDLE), device name, elapsed time. Keep the 8×2 bank grid. Remove all board-level chrome and hardware documentation.

### Metis Review
**Decision forced by Metis** — "hide vs remove":
- **Remove + refactor**: Delete `RelayGlyph` and `RelayStatusLed` from JSX entirely (clean DOM). Move silkscreen from its orphaned left column into the text content area.
- Also remove outer header from DeviceManager (the "Relay Matrix" + "MCP23017" block at line 818)

**Other Metis flags** (resolved):
- Tooltip: update to match 4 retained fields only
- Unassigned channels: still show "Unassigned" for device name — acceptable
- Long device names: `truncate` behavior unchanged, still works at narrower width
- `aria-hidden`: removed elements are gone from DOM entirely, no aria concerns

---

## Work Objectives

### Core Objective
Reduce the relay matrix panel width by removing all hardware documentation chrome and redundant per-channel data points. Target: visually tight, no wasted space, no duplicate information.

### Concrete Deliverables
- `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx` — panel variant stripped of board chrome
- `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` — simplified to 4 data points
- `Infrastructure/frontend/src/components/DeviceManager.tsx` — outer header block removed

### Definition of Done
- [ ] `npm run build` passes (0 errors)
- [ ] 8×2 bank grid still renders (2 columns, 8 rows)
- [ ] Each box shows: silkscreen + status badge + device name + elapsed time
- [ ] No glyph bar, no LED dot, no CH number, no pin label, no location, no device type visible
- [ ] No header, terminal strips, bank labels, COM divider, row numbers, low-level input, grid background
- [ ] Control menu (Auto/ON 5m/10m/30m/1h/Off) still functions
- [ ] Compact variant (ZoneConfig) unchanged

### Must Have
- Panel variant boxes slimmed to 4 data points
- All board-level chrome removed
- Outer header in DeviceManager removed
- Silkscreen (K1-K16) preserved as primary channel identifier
- Status badge (ON/IDLE/Unknown) preserved with dropdown menu
- Build passes

### Must NOT Have (Guardrails)
- No changes to `variant="compact"` path (ZoneConfig)
- No changes to `relayViewModel.ts`
- No changes to API contracts, types, or data flow
- No removal of the control menu dropdown
- No removal of the channel assignment editing (click-to-edit)
- No new layout components — keep it simple
- No per-channel location — already agreed to remove
- No per-channel device type — already agreed to remove

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**

### Test Decision
- **Infrastructure exists**: YES (npm build)
- **Automated tests**: NO (visual simplification — no logic changed)
- **Framework**: npm build + grep assertions

### Agent-Executed QA Scenarios

| Task | Tool | How Agent Verifies |
|------|------|-------------------|
| 1 (Box refactor) | Bash (npm build + grep) | Build passes, grep confirms removed elements absent |
| 2 (Matrix cleanup) | Bash (npm build + grep) | Build passes, grep confirms removed elements absent |
| 3 (DeviceManager trim) | Bash (npm build + grep) | Build passes, grep confirms outer header removed |

---

## Execution Strategy

### Single Wave (Sequential)

```
Task 1: RelayChannelBox.tsx — remove 6 data points, refactor silkscreen
  ↓
Task 2: RelayChannelMatrix.tsx — strip panel chrome
  ↓
Task 3: DeviceManager.tsx — remove outer header
  ↓
Task 4: Build verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 4 | None |
| 2 | None | 4 | 1 (different file) |
| 3 | None | 4 | 1, 2 (different file) |
| 4 | 1, 2, 3 | None | None (final) |

### Agent Dispatch Summary

| Task | Recommended Agent |
|------|-------------------|
| 1-3 | `category="visual-engineering", skills=["frontend-ui-ux"]` — single agent for all 3 files |
| 4 | Built into Task 1-3 agent (run build at end) |

---

## TODOs

- [ ] 1. **Simplify RelayChannelBox to 4 data points**

  **What to do**:
  1. Remove `RelayGlyph` function (lines 29-47) and its JSX rendering
  2. Remove `RelayStatusLed` function (lines 49-58) and its JSX rendering
  3. Remove CH number display (`<span className="text-[10px] font-semibold">CH {channel.channel}</span>` at line 138)
  4. Remove pin label badge (`<span className="rounded-sm ...">` at line 139-141)
  5. Remove location label (`<div className="truncate text-[9px] text-text-muted">{locationLabel}</div>` at line 143)
  6. Remove device type label (`<span className="truncate">{typeLabel}</span>` at line 167)
  7. Move silkscreen from the left `flex-col` column into the main text area (alongside status badge in the header row)
  8. Updated tooltip: `tooltipTitle` simplifies to `${silkscreen} · ${deviceLabel} · ${resolvedText} · ${elapsedLabel}`
  9. After refactoring, the JSX structure becomes: `flex items-stretch` → single text block with silkscreen+statusBadge on top row, deviceName below, elapsedTime bottom

  **Target JSX structure**:
  ```tsx
  <div className="flex items-stretch gap-1">
    <div className="min-w-0 flex-1">
      {/* Top row: silkscreen + status badge */}
      <div className="flex items-center justify-between gap-1">
        <span className="font-mono text-[10px] font-bold uppercase tracking-tight text-text-input">{silkscreen}</span>
        <button ... className={stateBadgeClasses(resolvedTone)}>{resolvedText}</button>
      </div>
      {/* Device name */}
      <div className="mt-0.5 truncate text-[10px] font-medium text-text-default">{deviceLabel}</div>
      {/* Elapsed time */}
      <div className="mt-0.5 text-[9px] font-mono text-text-muted">{elapsedLabel}</div>
    </div>
  </div>
  ```

  **Must NOT do**:
  - Do NOT remove the status badge button or its dropdown menu
  - Do NOT remove `isEditing` ring indicator or `onSelect` click handler
  - Do NOT change the `variant` prop or conditional rendering
  - Do NOT change silkscreen label text — keep `K1`-`K16`

  **References**:
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:29-47` — RelayGlyph (to remove)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:49-58` — RelayStatusLed (to remove)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:60-183` — current component body
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:40-42` — `getRelaySilkscreenLabel()` (still used)

  **Acceptance Criteria**:
  - [ ] `grep "RelayGlyph" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "RelayStatusLed" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "CH {channel.channel}" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "pinLabel" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "locationLabel" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "typeLabel" RelayChannelBox.tsx` → 0 matches
  - [ ] `grep "silkscreen" RelayChannelBox.tsx` → ≥ 1 match (still present, moved to text area)
  - [ ] `grep "stateBadgeClasses" RelayChannelBox.tsx` → ≥ 1 match (still present)
  - [ ] `grep "deviceLabel" RelayChannelBox.tsx` → ≥ 1 match (still present)
  - [ ] `grep "elapsedLabel" RelayChannelBox.tsx` → ≥ 1 match (still present)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: RelayChannelBox exports only 4 data points
    Tool: Bash (grep + npm build)
    Preconditions: Working directory = Infrastructure/frontend
    Steps:
      1. grep -c "RelayGlyph\|RelayStatusLed" src/components/devices/RelayChannelBox.tsx
      2. Assert: output is "0" (both removed)
      3. grep -c "pinLabel\|locationLabel\|typeLabel" src/components/devices/RelayChannelBox.tsx
      4. Assert: output is "0" (all removed)
      5. grep -c "silkscreen" src/components/devices/RelayChannelBox.tsx
      6. Assert: output ≥ 1 (silkscreen still present)
      7. npm run build 2>&1 | tail -1
      8. Assert: contains "built in" (build succeeded)
    Expected Result: All removed elements gone, retained elements present, build passes
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `refactor(ui): slim RelayChannelBox to 4 data points — remove glyph, LED, CH#, pin, location, type`
  - Files: `RelayChannelBox.tsx`

---

- [ ] 2. **Strip board-level chrome from RelayChannelMatrix panel variant**

  **What to do**:
  1. Remove header block (lines 121-128): "16-CH Relay Module" + "MCP23017 · SainSmart layout"
  2. Remove `TerminalStrip` function (lines 21-34) and its JSX rendering (lines 130-137)
  3. Remove Bank A/B headers (lines 139-149): "Bank A (CH 0–7)" / "Bank B (CH 8–15)"
  4. Remove COM vertical divider (lines 159-169): the center column with rotated "COM" text
  5. Remove row numbers (lines 180-187): the `{rowIndex + 1}` labels in column 1
  6. Remove `LowLevelInputStrip` function (lines 36-49) and its JSX rendering (lines 199-206)
  7. Remove `GRID_BACKGROUND_STYLE` constant (lines 80-86) and its usage (line 154)
  8. Simplify grid template columns: since we removed COM divider and row numbers, the 4-column grid (`auto 1fr auto 1fr`) collapses to 2 columns (`1fr 1fr`)
  9. Remove unnecessary wrapper divs that only existed to hold removed elements

  **Simplified grid**: `gridTemplateColumns: '1fr 1fr'` with only the 8×2 channel boxes.

  **Must NOT do**:
  - Do NOT remove the `variant` prop or the `isPanel` / `isCompact` branching
  - Do NOT change the 8×2 bank layout (8 rows, 2 columns of channel boxes)
  - Do NOT remove `renderChannelBox` or `ChannelBoxRenderProps`
  - Do NOT change `splitRelayBanks` call or bank iteration logic
  - Do NOT change `RelayChannelBox` props — the interface stays the same even though some props are no longer used

  **References**:
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:21-34` — TerminalStrip (to remove)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:36-49` — LowLevelInputStrip (to remove)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:80-86` — GRID_BACKGROUND_STYLE (to remove)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:88-209` — full component (panel variant)
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:100-101` — `variant` resolution (keep)

  **Acceptance Criteria**:
  - [ ] `grep "TerminalStrip" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "LowLevelInputStrip" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "GRID_BACKGROUND_STYLE" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "16-CH Relay Module" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "Bank A" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "Bank B" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "SainSmart" RelayChannelMatrix.tsx` → 0 matches
  - [ ] `grep "writing-mode:vertical-rl" RelayChannelMatrix.tsx` → 0 matches (COM divider gone)
  - [ ] `grep "gridTemplateColumns" RelayChannelMatrix.tsx` → `'1fr 1fr'` (simplified grid)
  - [ ] `grep "splitRelayBanks" RelayChannelMatrix.tsx` → ≥ 1 match (still used)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Panel variant stripped of all hardware chrome
    Tool: Bash (grep + npm build)
    Preconditions: Task 1 complete
    Steps:
      1. grep -c "TerminalStrip\|LowLevelInputStrip\|GRID_BACKGROUND_STYLE" src/components/devices/RelayChannelMatrix.tsx
      2. Assert: output is "0" (all removed)
      3. grep -c "Bank A\|Bank B\|SainSmart\|16-CH Relay" src/components/devices/RelayChannelMatrix.tsx
      4. Assert: output is "0" (all removed)
      5. grep "gridTemplateColumns" src/components/devices/RelayChannelMatrix.tsx
      6. Assert: contains "1fr 1fr" (simplified to 2-column grid)
      7. grep -c "splitRelayBanks" src/components/devices/RelayChannelMatrix.tsx
      8. Assert: output ≥ 1 (bank logic preserved)
      9. npm run build 2>&1 | tail -1
      10. Assert: contains "built in" (build succeeded)
    Expected Result: All chrome removed, 2-column grid, build passes
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `refactor(ui): strip hardware chrome from relay matrix panel variant`
  - Files: `RelayChannelMatrix.tsx`

---

- [ ] 3. **Remove outer header block from DeviceManager**

  **What to do**:
  In `Infrastructure/frontend/src/components/DeviceManager.tsx`, remove lines 816-821:
  ```tsx
  <div className="mb-2 flex items-start justify-between gap-1">
    <div>
      <h3 className="text-lg font-semibold text-text-default">Relay Matrix</h3>
      <p className="font-mono text-[10px] text-text-muted">16-CH module · MCP23017</p>
    </div>
  </div>
  ```

  **Must NOT do**:
  - Do NOT remove the `RelayChannelMatrix` component itself (lines 823-835)
  - Do NOT remove the `matrixPanelRef` div wrapper (line 812-814)
  - Do NOT change any props passed to `RelayChannelMatrix`

  **References**:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:816-821` — header block to remove

  **Acceptance Criteria**:
  - [ ] `grep "Relay Matrix" DeviceManager.tsx` → 0 matches (outer header gone)
  - [ ] `grep "16-CH module" DeviceManager.tsx` → 0 matches (outer subtitle gone)
  - [ ] `grep "MCP23017" DeviceManager.tsx` → 0 matches
  - [ ] `grep "RelayChannelMatrix" DeviceManager.tsx` → ≥ 1 match (component still rendered)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Outer header removed, matrix still rendered
    Tool: Bash (grep + npm build)
    Preconditions: Tasks 1, 2 complete
    Steps:
      1. grep -c "Relay Matrix\|16-CH module\|MCP23017" src/components/DeviceManager.tsx
      2. Assert: output is "0" (header block removed)
      3. grep -c "RelayChannelMatrix" src/components/DeviceManager.tsx
      4. Assert: output ≥ 1 (component still rendered)
      5. npm run build 2>&1 | tail -1
      6. Assert: contains "built in" (build succeeded)
    Expected Result: Header gone, matrix still renders, build passes
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `refactor(ui): remove outer relay matrix header from DeviceManager`
  - Files: `DeviceManager.tsx`

---

- [ ] 4. **Final build verification**

  **What to do**: Run `npm run build` from `Infrastructure/frontend` and confirm 0 errors. Run targeted grep checks across all 3 files to ensure no remnant of removed elements.

  **Acceptance Criteria**:
  - [ ] `npm run build` exits 0
  - [ ] No "RelayGlyph" anywhere in the frontend src
  - [ ] No "RelayStatusLed" anywhere in the frontend src
  - [ ] No "TerminalStrip" anywhere in the frontend src
  - [ ] No "LowLevelInputStrip" anywhere in the frontend src
  - [ ] No "GRID_BACKGROUND_STYLE" anywhere in the frontend src
  - [ ] No "16-CH Relay" or "MCP23017" in DeviceManager or RelayChannelMatrix

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Full cleanup verification
    Tool: Bash (grep + npm build)
    Preconditions: All tasks complete
    Steps:
      1. npm run build 2>&1
      2. Assert: exit code 0, contains "built in"
      3. grep -r "RelayGlyph\|RelayStatusLed" src/components/devices/
      4. Assert: exit code 1 (no matches found)
      5. grep -r "TerminalStrip\|LowLevelInputStrip\|GRID_BACKGROUND_STYLE" src/components/devices/
      6. Assert: exit code 1 (no matches found)
      7. grep -r "16-CH Relay\|MCP23017\|SainSmart" src/components/
      8. Assert: exit code 1 (no matches found)
    Expected Result: Clean build, zero remnants
    Evidence: Terminal output captured
  ```

  **Commit**: NO (verification only, no code changes)

---

## Commit Strategy

| After Task | Message | Files |
|-----------|---------|-------|
| 1 | `refactor(ui): slim RelayChannelBox to 4 data points — remove glyph, LED, CH#, pin, location, type` | `RelayChannelBox.tsx` |
| 2 | `refactor(ui): strip hardware chrome from relay matrix panel variant` | `RelayChannelMatrix.tsx` |
| 3 | `refactor(ui): remove outer relay matrix header from DeviceManager` | `DeviceManager.tsx` |

---

## Success Criteria

### Verification Commands
```bash
cd Infrastructure/frontend

# Build must pass
npm run build

# Removed elements must be gone
grep -r "RelayGlyph\|RelayStatusLed" src/components/devices/
# Expected: exit 1 (no matches)

grep -r "TerminalStrip\|LowLevelInputStrip\|GRID_BACKGROUND_STYLE" src/components/devices/
# Expected: exit 1 (no matches)

grep -r "16-CH Relay\|MCP23017\|SainSmart" src/components/
# Expected: exit 1 (no matches)

# Retained elements must be present
grep "silkscreen" src/components/devices/RelayChannelBox.tsx
# Expected: ≥ 1 match

grep "stateBadgeClasses" src/components/devices/RelayChannelBox.tsx
# Expected: ≥ 1 match

grep "splitRelayBanks" src/components/devices/RelayChannelMatrix.tsx
# Expected: ≥ 1 match
```

### Final Checklist
- [ ] `npm run build` passes (0 errors)
- [ ] 8×2 bank grid preserved (2 columns, 8 rows of boxes)
- [ ] Each box: silkscreen + status badge + device name + elapsed time
- [ ] All hardware chrome removed (no terminal strips, bank labels, COM divider, grid bg, outer header)
- [ ] All redundant data removed (no glyph, LED, CH#, pin, location, type)
- [ ] Control menu dropdown still functional
- [ ] Compact variant (ZoneConfig) unchanged
- [ ] No changes to relayViewModel.ts, API contracts, types
