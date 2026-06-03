# Grafana Layout Fix Plan

## TL;DR
Fix Grafana panel embedding by updating configuration and adjusting monitoring page layouts to match original dashboard proportions. Keep overview pages empty (no redirect).

## Context

### What's Done
- Grafana `root_url` set to include `/grafana` subpath
- `serve_from_sub_path = true` enabled
- Grafana restarted and serving on port 3000

### What's Wrong
- FlowerMonitoring and VegetationMonitoring don't match original Grafana dashboard layouts
- Overview pages redirect to monitoring (should stay empty)

### Original Dashboard Layouts (24-column grid)

**Flower Sector UID:** `7467103e-9964-4e06-9fc8-c43610129ba9`
| Panel ID | Title | gridPos (x, y, w, h) |
|----------|-------|------------------------|
| 1 | Averages | x=0, y=0, w=4, h=10 |
| 4 | Temperature, RH & VPD - Main Graph | x=4, y=0, w=20, h=24 |
| 5 | CO2 & Pressure | x=4, y=24, w=20, h=8 |
| 6 | Statistics - All Available Sensors | x=0, y=32, w=24, h=10 |

**Veg Sector UID:** `80bcfd37-f781-48da-aba9-48d3b06a6347`
| Panel ID | Title | gridPos (x, y, w, h) |
|----------|-------|------------------------|
| 1 | Sensor Values | x=0, y=0, w=4, h=24 | ← Changed from w=3 to match Flower (4/24 = 17%)
| 4 | Temperature, RH & VPD - Main Graph | x=4, y=0, w=20, h=24 | ← Changed from w=21 to match Flower (20/24 = 83%)
| 5 | Pressure & Devices | x=4, y=24, w=20, h=8 | ← Changed from w=21 to match Flower proportions
| 6 | Statistics - All Available Sensors | x=0, y=32, w=24, h=10 |

**Note:** Veg Sector panels adjusted to match Flower Sector proportions (4/20 split instead of original 3/21) for visual consistency across the app.

---

## TODOs

- [x] 0. Update Veg Sector Grafana dashboard JSON to match Flower proportions

  **What to do**:
  - Edit the Veg Sector dashboard JSON to change panel widths:
    - Panel 1: change w from 3 to 4, x stays 0
    - Panel 4: change w from 21 to 20, x from 3 to 4
    - Panel 5: change w from 21 to 20, x from 3 to 4
  - This makes Veg match Flower's 4/20/20/24 proportions exactly

  **Files**:
  - `/var/lib/grafana/dashboards/veg_sector/veg_sector.json`
  - (Also update repo copy: `/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/dashboards/veg_sector/veg_sector.json`)

  **Acceptance Criteria**:
  - [ ] Panel 1: gridPos x=0, w=4
  - [ ] Panel 4: gridPos x=4, w=20
  - [ ] Panel 5: gridPos x=4, w=20
  - [ ] Reload Grafana dashboard or restart Grafana to apply changes

- [x] 1. Make FlowerOverview empty (remove redirect to monitoring)
- [x] 2. Make VegetationOverview empty (remove redirect to monitoring)

- [x] 3. Fix FlowerMonitoring layout to match original dashboard

  **What to do**:
  - Use CSS Grid with 24-column system
  - Left column (~16.7%): Panels 1, 2, 3 stacked
  - Right column (~83.3%): Panels 4, 5 stacked
  - Bottom: Panel 6 full width
  - Use semantic token classes (bg-surface-base, etc.)

  **File**: `src/pages/FlowerMonitoring.tsx`

  **Layout Structure**:
  ```tsx
  // Row 1: Panel 1 (left, 4/24) + Panel 4 (right, 20/24)
  // Row 2: Panels 2+3 (left column below Panel 1) + Panel 5 (right column below Panel 4)
  // Row 3: Panel 6 (full width)
  ```

  **Acceptance Criteria**:
  - [ ] Panel 1 ~17% width on left
  - [ ] Panel 4 ~83% width on right (same row as Panel 1)
  - [ ] Panel 2 below Panel 1 (left column)
  - [ ] Panel 3 below Panel 2 (left column)
  - [ ] Panel 5 below Panel 4 (right column)
  - [ ] Panel 6 full width at bottom
  - [ ] All panels use semantic token classes

- [x] 4. Fix VegetationMonitoring layout to match original dashboard

  **What to do**:
  - Use CSS Grid with 24-column system
  - Left column (~12.5%): Panel 1 tall
  - Right column (~87.5%): Panels 4, 5 stacked
  - Bottom: Panel 6 full width

  **File**: `src/pages/VegetationMonitoring.tsx`

  **Layout Structure**:
  ```tsx
  // Row 1: Panel 1 (left, 3/24) + Panel 4 (right, 21/24)
  // Row 2: Panel 5 (right column below Panel 4)
  // Row 3: Panel 6 (full width)
  ```

  **Acceptance Criteria**:
  - [ ] Panel 1 ~12.5% width on left, tall (spans rows)
  - [ ] Panel 4 ~87.5% width on right (same row as Panel 1)
  - [ ] Panel 5 below Panel 4 (right column)
  - [ ] Panel 6 full width at bottom
  - [ ] All panels use semantic token classes

- [x] 5. Verify all Grafana panels load correctly

  **What to do**:
  - Test each panel embed URL returns 200
  - Flower panels: 1, 2, 3, 4, 5, 6
  - Veg panels: 1, 4, 5, 6

  **Test Commands**:
  ```bash
  # Flower
  curl -s -o /dev/null -w "%{http_code}" "http://localhost:3001/grafana/d-solo/7467103e-9964-4e06-9fc8-c43610129ba9?orgId=1&panelId=1&theme=dark"
  # ... repeat for panels 2-6

  # Veg
  curl -s -o /dev/null -w "%{http_code}" "http://localhost:3001/grafana/d-solo/80bcfd37-f781-48da-aba9-48d3b06a6347?orgId=1&panelId=1&theme=dark"
  # ... repeat for panels 4-6
  ```

  **Acceptance Criteria**:
  - [ ] All 10 panel URLs return 200

- [x] 6. Commit changes

  **What to do**:
  - Stage and commit all modified files
  - Message: `fix(frontend): make overview pages empty and fix monitoring page layouts`

  **Files**:
  - FlowerOverview.tsx
  - VegetationOverview.tsx
  - FlowerMonitoring.tsx
  - VegetationMonitoring.tsx

---

## Parallel Execution

Task 1 and 2 can run in parallel (both are simple overview page edits)
Tasks 3 and 4 should be sequential (monitoring page fixes)
Task 5 depends on 3 and 4
Task 6 depends on all

---

## Success Criteria
- Overview pages show title only (no redirect, no Grafana panels)
- Monitoring pages match original Grafana dashboard proportions
- All 10 Grafana panel iframes load correctly (200 status)
- Build passes
- Git working tree clean
