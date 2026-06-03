# Frontend Completion Plan

> **IMPORTANT: Working Directory**
> All work happens in the **UI git worktree**: `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`
> This is NOT the main repo at `/home/antoine/ProjectCEA/`. The `ui/frontend-modernization` branch is checked out in the worktree.
> All relative file paths below are relative to `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`.

## TL;DR

> **Quick Summary**: Complete the `ui/frontend-modernization` branch by committing all outstanding work, fixing 26 hardcoded colors + 1 stale closure bug, configuring Grafana for iframe embedding, adding the `/grafana` vite proxy, implementing individual Grafana panel embeds on 3 monitoring pages, and safely installing shadcn/ui without breaking existing CSS.
>
> **Deliverables**:
> - All 22 uncommitted files committed in logical groups
> - Zero hardcoded Tailwind color classes remaining (26 → 0)
> - ZoneConfig stale closure bug fixed
> - Grafana server configured for iframe embedding (anonymous auth + allow_embedding)
> - `/grafana` reverse proxy in vite.config.ts
> - FlowerMonitoring: 6 individual Grafana panel iframes
> - VegetationMonitoring: 4 individual Grafana panel iframes
> - FlowerSoil: 9 individual Grafana panel iframes
> - shadcn/ui installed safely (manual method, no `npx shadcn init`)
>
> **Estimated Effort**: Medium (8 tasks, ~1-2 days)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 2 → Task 5 → Task 6/7 → Task 8

---

## Context

### Original Request
Complete all remaining work on the `ui/frontend-modernization` branch. The original 11-task modernisation plan is ~85% done. Navigation redesign (sidebar + TopRibbon) is complete but uncommitted. Grafana monitoring pages exist as scaffolds with placeholder UIDs. 26 hardcoded color classes violate the semantic token system. shadcn/ui was never installed.

### Interview Summary
**Key Decisions:**
- **Port**: Keep dev server on port 3001 (do NOT move to 3002)
- **Grafana approach**: Option 2 — individual panel iframes per monitoring page (not full dashboard embeds)
- **shadcn/ui**: Include it but via manual component addition only — MUST NOT break existing CSS/Tailwind 4 config
- **Lab pages**: Leave as scaffolds (no Grafana content needed)
- **Tests**: Skip test infrastructure for now
- **Commits**: Commit all outstanding work immediately as rollback baseline

**User Constraints (verbatim):**
- "the deploy script isnt to be used EVER WITHOUT MY DIRECT MENTION"
- "include it but dont break our past work and current config. this is of utmost importance" (re: shadcn/ui)

### Research Findings
- **Grafana UIDs verified**: Flower `7467103e-9964-4e06-9fc8-c43610129ba9` (panels 1-6), Veg `80bcfd37-f781-48da-aba9-48d3b06a6347` (panels 1,4-6), Soil `flower-sector-soil` (panels 1-4,10,20-21,30-31)
- **Grafana runs on port 3000**, needs `allow_embedding = true` and anonymous auth
- **Semantic tokens exist** in `index.css` (lines 92-171): `surface-base`, `surface-primary`, `surface-secondary`, `text-default`, `text-secondary`, `text-muted`, `text-subtle`, `accent-vivid`, `accent-hover`, `status-success-bg`, `status-success-text`, `status-danger-bg`, `status-danger-text`, `border-default`, `border-emphasis`
- **Build passes**: 5.99s, 119 modules, 211KB bundle
- **Stale closure verified**: ZoneConfig.tsx line 43-54 useEffect deps `[location, cluster, saving, success, error, roomMode]` but calls `handleSave` and `handleModeChange` which are NOT in the array — confirmed stale closure
- **All 16 untracked files verified as legitimate source files**: Layout.tsx, Sidebar.tsx, TopRibbon.tsx, ControlActionsContext.tsx, GrafanaPanel.tsx, FlowerControl/Monitoring/Overview/Soil.tsx, VegetationControl/Monitoring/Overview.tsx, LaboratoryClimate/Water/Infrastructure/Overview.tsx — zero build artifacts
- **No existing shadcn/ui components**: `src/components/ui/` directory does not exist — clean installation with zero conflict risk

### Metis Gap Analysis (addressed)
1. **Stale closure verification**: Confirmed — `handleSave` and `handleModeChange` are defined as regular functions and missing from useEffect deps at line 54
2. **Color-to-token mapping completeness**: All 15 unique mappings verified against `index.css:92-171` semantic tokens — every hardcoded color has a matching token
3. **Grafana panel ID existence**: UIDs verified from Grafana JSON exports, but panel IDs (1-6, 1/4-6, 1-4/10/20-21/30-31) should be validated at runtime. If a panel ID doesn't exist, the iframe will show a Grafana error page (graceful degradation, not a crash)
4. **Build-after-each-wave guardrail**: Added `npm run build` as acceptance criterion for every task
5. **TypeScript compilation**: Added `npx tsc --noEmit` as final verification step
6. **shadcn/ui CSS safety**: md5sum pre/post check already in plan; added `git diff --stat` as additional verification that no existing files were modified
7. **Iframe loading edge case**: GrafanaPanel.tsx already has `loading="lazy"` attribute; Grafana's own error UI handles panel-not-found cases — no custom error boundary needed for MVP
8. **Scope creep lockdown**: Lab pages, test infra, additional components, and code refactoring all explicitly excluded in guardrails

---

## Work Objectives

### Core Objective
Bring the frontend modernization branch to a fully complete state: all code committed, zero hardcoded colors, working Grafana embeds, and shadcn/ui available for future component work.

### Must Have
- All 26 hardcoded colors replaced with semantic tokens
- ZoneConfig stale closure fixed with useCallback
- Individual panel iframes (not full dashboard embeds)
- shadcn/ui installed without modifying index.css or themes.css

### Must NOT Have (Guardrails)
- NO `npx shadcn init` — manual component addition only
- NO modifications to `src/styles/index.css` or `src/styles/themes.css`
- NO port changes (stay on 3001)
- NO deploy script usage
- NO `dark:` prefixed classes (theme system handles dark mode via data-theme)
- NO new dependencies beyond shadcn/ui component deps (class-variance-authority, clsx, tailwind-merge)
- NO adding shadcn/ui components beyond the 8 specified (button, card, input, label, select, badge, separator, skeleton)
- NO modifying Lab pages beyond scaffold color fixes
- NO adding test infrastructure
- NO refactoring existing code outside stated scope
- Build MUST pass after EACH task (not just final verification)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
  Task 1: Commit all outstanding work (rollback baseline)

Wave 2 (After Wave 1):
  Task 2: Fix hardcoded colors + stale closure
  Task 3: Configure Grafana server (grafana.ini)
  Task 4: Install shadcn/ui safely

Wave 3 (After Tasks 2 + 3):
  Task 5: Add /grafana vite proxy + fix GrafanaPanel.tsx
  Task 6: Implement FlowerMonitoring (6 panels)
  Task 7: Implement VegetationMonitoring (4 panels)

Wave 4 (After Wave 3):
  Task 8: Implement FlowerSoil (9 panels) + final verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 4 | None |
| 2 | 1 | 5, 6, 7 | 3, 4 |
| 3 | 1 | 5, 6, 7 | 2, 4 |
| 4 | 1 | 8 | 2, 3 |
| 5 | 2, 3 | 6, 7 | None |
| 6 | 5 | 8 | 7 |
| 7 | 5 | 8 | 6 |
| 8 | 4, 6, 7 | None | None (final) |

---

## TODOs

- [x] 1. Commit all outstanding work (rollback baseline)

  **What to do**:
  - Stage and commit all 22 uncommitted files in 2 logical commits:
    - **Commit 1** (`feat(frontend): add navigation layout with sidebar and top ribbon`): Layout.tsx, Sidebar.tsx, TopRibbon.tsx, ControlActionsContext.tsx, App.tsx (modified routes)
    - **Commit 2** (`feat(frontend): add route pages for all sectors`): All page files (FlowerControl, FlowerMonitoring, FlowerOverview, FlowerSoil, VegetationControl, VegetationMonitoring, VegetationOverview, LaboratoryClimate, LaboratoryWater, LaboratoryInfrastructure, LaboratoryOverview), GrafanaPanel.tsx, plus remaining modified files (package-lock.json, CircularTimePicker.tsx, SetpointsTable.tsx, VerticalLightsBlock.tsx, ZoneConfig.tsx)
  - Working directory: `/home/antoine/ProjectCEA-ui/Infrastructure/frontend`

  **Must NOT do**:
  - Do NOT run deploy script
  - Do NOT modify any file content — commit as-is for rollback baseline

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]

  **Parallelization**:
  - **Blocks**: Tasks 2, 3, 4
  - **Blocked By**: None

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/Layout.tsx` — New navigation layout (untracked, 153 lines)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/Sidebar.tsx` — Collapsible sidebar (untracked, 162 lines)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/TopRibbon.tsx` — Sector tabs with actions (untracked, 158 lines)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/contexts/ControlActionsContext.tsx` — Actions context (untracked, 33 lines)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/App.tsx` — Router with lazy-loaded routes (modified)

  **Acceptance Criteria**:
  - [x] `git status` shows clean working tree after both commits
  - [x] `git log --oneline -2` shows both commits with correct messages
  - [x] `npm run build` passes before each commit

---

- [x] 2. Fix hardcoded colors (26 instances) + ZoneConfig stale closure

  **What to do**:
  - Replace all 26 hardcoded Tailwind color classes with semantic token classes across 9 files
  - Fix ZoneConfig stale closure by wrapping `handleSave` and `handleModeChange` in `useCallback` and adding them to the `useEffect` dependency array

  **Color mapping** (hardcoded to semantic):
  - `bg-gray-950` to `bg-surface-base`
  - `bg-gray-800` to `bg-surface-secondary`
  - `text-gray-100` to `text-text-default`
  - `text-gray-200` to `text-text-secondary`
  - `text-gray-400` to `text-text-muted`
  - `text-gray-500` to `text-text-subtle`
  - `text-white` to `text-text-default`
  - `border-gray-700` to `border-border-default`
  - `hover:border-gray-500` to `hover:border-border-emphasis`
  - `bg-red-900 text-red-200` to `bg-status-danger-bg text-status-danger-text`
  - `bg-green-900 text-green-200` to `bg-status-success-bg text-status-success-text`
  - `bg-cyan-700` to `bg-accent-vivid`
  - `hover:bg-cyan-600` to `hover:bg-accent-hover`
  - `text-gray-900 dark:text-gray-100` to `text-text-default` (remove dark: prefix)
  - `border-gray-200 dark:border-gray-700` to `border-border-default` (remove dark: prefix)

  **Files to modify** (with exact locations):
  1. `src/components/TopRibbon.tsx` — Lines 121, 128, 139, 149 (6 instances)
  2. `src/pages/ZoneConfig.tsx` — Lines 136, 140 (2 instances) + stale closure fix at lines 43-54
  3. `src/components/GrafanaPanel.tsx` — Lines 29, 34 (2 instances, remove dark: prefixes)
  4. `src/pages/FlowerMonitoring.tsx` — Lines 12, 14, 23 (3 instances)
  5. `src/pages/VegetationMonitoring.tsx` — Lines 12, 14, 23 (3 instances)
  6. `src/pages/FlowerSoil.tsx` — Lines 12, 14, 23 (3 instances)
  7. `src/pages/LaboratoryClimate.tsx` — (3 instances, same pattern as above)
  8. `src/pages/LaboratoryWater.tsx` — (3 instances, same pattern)
  9. `src/pages/LaboratoryInfrastructure.tsx` — (3 instances, same pattern)

  **Stale closure fix** for `src/pages/ZoneConfig.tsx`:
  - Wrap `handleSave` at ~line 68 in `useCallback` with deps `[location, cluster, roomMode, savedParams]`
  - Wrap `handleModeChange` in `useCallback` with its actual dependencies
  - Add both to the `useEffect` deps array at line 54: `[location, cluster, saving, success, error, roomMode, handleSave, handleModeChange]`

  **Must NOT do**:
  - Do NOT touch `index.css` or `themes.css`
  - Do NOT add any new CSS custom properties
  - Do NOT change component behavior, only class names

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 with Tasks 3, 4)
  - **Blocks**: Tasks 5, 6, 7
  - **Blocked By**: Task 1

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/styles/index.css:92-171` — Full semantic token definitions (source of truth for mappings). Contains `--color-surface-base`, `--color-text-default`, `--color-text-secondary`, `--color-text-muted`, `--color-text-subtle`, `--color-accent-vivid`, `--color-accent-hover`, `--color-status-success-bg`, `--color-status-success-text`, `--color-status-danger-bg`, `--color-status-danger-text`, `--color-border-default`, `--color-border-emphasis` inside a `@theme inline {}` block
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/TopRibbon.tsx:118-156` — 6 hardcoded color instances in actions area
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/ZoneConfig.tsx:43-54` — Stale closure: setActions useEffect with missing deps
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/ZoneConfig.tsx:68-100` — handleSave and handleModeChange function definitions

  **Acceptance Criteria**:
  - [x] `grep -rn "bg-gray-\|text-gray-\|border-gray-\|bg-red-\|bg-green-\|bg-cyan-\|hover:bg-cyan-\|hover:border-gray-" src/ --include="*.tsx"` returns no matches
  - [x] `grep -rn "dark:" src/ --include="*.tsx"` returns no matches
  - [x] `npm run build` passes
  - [x] `npx tsc --noEmit` passes (TypeScript compilation clean)

  **Commit**: `fix(frontend): replace 26 hardcoded colors with semantic tokens and fix stale closure`

---

- [x] 3. Configure Grafana server for iframe embedding

  **What to do**:
  - Edit `/etc/grafana/grafana.ini`:
    - Under `[security]`: set `allow_embedding = true`
    - Under `[auth.anonymous]`: set `enabled = true`, `org_name = Main Org.`, `org_role = Viewer`
  - Restart Grafana: `sudo systemctl restart grafana-server`
  - Verify Grafana accessible at `http://localhost:3000`

  **Must NOT do**:
  - Do NOT change Grafana port
  - Do NOT modify any Grafana dashboards
  - Do NOT disable other auth methods

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 with Tasks 2, 4)
  - **Blocks**: Tasks 5, 6, 7
  - **Blocked By**: Task 1

  **References**:
  - `/etc/grafana/grafana.ini` — Main Grafana config file
  - Grafana docs: https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#allow_embedding

  **Acceptance Criteria**:
  - [x] `grep "allow_embedding = true" /etc/grafana/grafana.ini` finds the setting
  - [x] `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health` returns 200
  - [x] `sudo systemctl is-active grafana-server` returns active

  **Commit**: NO (system config, not in git)

---

- [x] 4. Install shadcn/ui safely (manual method)

  **What to do**:
  - **CRITICAL SAFETY STEP**: Before ANYTHING, capture `md5sum src/styles/index.css` and `md5sum src/styles/themes.css`. After every operation, verify hashes match. If changed, revert immediately.
  - Install utility deps: `npm install class-variance-authority clsx tailwind-merge`
  - Create `src/lib/utils.ts` with `cn()` helper:
    ```typescript
    import { type ClassValue, clsx } from "clsx"
    import { twMerge } from "tailwind-merge"
    export function cn(...inputs: ClassValue[]) {
      return twMerge(clsx(inputs))
    }
    ```
  - Create `src/components/ui/` directory
  - Manually add 8 core shadcn/ui components by copying from the shadcn/ui GitHub source (NOT using `npx shadcn add`):
    - `button.tsx`, `card.tsx`, `input.tsx`, `label.tsx`, `select.tsx`, `badge.tsx`, `separator.tsx`, `skeleton.tsx`
  - For each component: adapt imports for `@/lib/utils`, apply density overrides (reduce padding ~25%: `h-10` to `h-8`, `px-4` to `px-3`)
  - **FINAL CHECK**: Verify index.css and themes.css md5sums are identical to pre-task values

  **Must NOT do**:
  - NEVER run `npx shadcn init` or `npx shadcn add` — these modify CSS config
  - NEVER modify `src/styles/index.css` or `src/styles/themes.css`
  - NEVER add `tailwind.config.js` or `tailwind.config.ts` (Tailwind 4 doesn't use them)
  - NEVER modify `vite.config.ts` for shadcn

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2 with Tasks 2, 3)
  - **Blocks**: Task 8
  - **Blocked By**: Task 1

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/styles/index.css:68-90` — `@theme inline` block with shadcn-compatible variables (background, foreground, card, primary, secondary, etc.) — these are ALREADY defined, which is why shadcn components will work without init
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/styles/themes.css` — Theme variables that feed into the @theme inline block — DO NOT MODIFY
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/vite.config.ts:8-11` — Path alias `@/` to `./src` (already configured for shadcn import paths)
  - shadcn/ui GitHub source: https://github.com/shadcn-ui/ui/tree/main/apps/www/registry/default/ui/
  - Manual install guide: https://ui.shadcn.com/docs/installation/manual

  **Acceptance Criteria**:
  - [x] `ls src/components/ui/` shows button.tsx, card.tsx, input.tsx, label.tsx, select.tsx, badge.tsx, separator.tsx, skeleton.tsx
  - [x] `ls src/lib/utils.ts` exists
  - [x] `npm run build` passes
  - [x] `md5sum src/styles/index.css` matches pre-task hash (CSS unchanged)
  - [x] `md5sum src/styles/themes.css` matches pre-task hash (CSS unchanged)
  - [x] `git diff --name-only` shows ONLY new files (utils.ts, ui/*.tsx) and package.json/lock — zero modifications to existing source files

  **Commit**: `feat(frontend): add shadcn/ui components via manual installation`

---

- [x] 5. Add /grafana vite proxy + verify GrafanaPanel.tsx colors

  **What to do**:
  - Add `/grafana` proxy entry to `vite.config.ts`:
    ```typescript
    '/grafana': {
      target: 'http://localhost:3000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/grafana/, ''),
    }
    ```
  - Verify GrafanaPanel.tsx has semantic colors (should already be done by Task 2, but verify)
  - Verify the embed URL format is correct: `/grafana/d-solo/{uid}?orgId=1&panelId={id}&theme=dark`

  **Must NOT do**:
  - Do NOT change existing `/api`, `/ws`, `/automation` proxies
  - Do NOT change the dev server port

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Tasks 6, 7
  - **Blocked By**: Tasks 2, 3

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/vite.config.ts:17-29` — Existing proxy configuration pattern (follow same structure)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/GrafanaPanel.tsx:22-24` — Existing embed URL construction (already uses `/grafana` base path)

  **Acceptance Criteria**:
  - [x] `grep "/grafana" vite.config.ts` finds the proxy config
  - [x] `npm run build` passes

  **Commit**: `feat(frontend): add grafana reverse proxy to vite config`

---

- [x] 6. Implement FlowerMonitoring with 6 individual panels

  **What to do**:
  - Rewrite `src/pages/FlowerMonitoring.tsx` to show 6 individual Grafana panel iframes in a responsive grid
  - Dashboard UID: `7467103e-9964-4e06-9fc8-c43610129ba9`
  - Panels to embed:
    1. Panel 1: "Averages" (table) — full width
    2. Panel 2: "Front Cluster" (table) — half width
    3. Panel 3: "Back Cluster" (table) — half width
    4. Panel 4: "Temperature, RH and VPD" (timeseries) — full width, height 400
    5. Panel 5: "CO2 and Pressure" (timeseries) — full width, height 400
    6. Panel 6: "Statistics - All Sensors" (table) — full width
  - Use semantic token classes (bg-surface-base, text-text-default, etc.)
  - Remove the placeholder UID check / "Dashboard not configured" message

  **Must NOT do**:
  - Do NOT use single full-dashboard embed
  - Do NOT use hardcoded colors
  - Do NOT modify GrafanaPanel.tsx component

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 with Task 7)
  - **Blocks**: Task 8
  - **Blocked By**: Task 5

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/GrafanaPanel.tsx` — Reusable component props: `dashboardUid`, `panelId`, `title`, `width`, `height`
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/FlowerMonitoring.tsx` — Current placeholder to replace (30 lines)

  **Acceptance Criteria**:
  - [x] `grep -c "panelId=" src/pages/FlowerMonitoring.tsx` returns 6
  - [x] `grep "7467103e-9964-4e06-9fc8-c43610129ba9" src/pages/FlowerMonitoring.tsx` finds UID
  - [x] Zero hardcoded color classes in file
  - [x] `npm run build` passes

  **Commit**: Groups with Task 7 as `feat(frontend): implement grafana panel embeds for monitoring pages`

---

- [x] 7. Implement VegetationMonitoring with 4 individual panels

  **What to do**:
  - Rewrite `src/pages/VegetationMonitoring.tsx` to show 4 individual Grafana panel iframes
  - Dashboard UID: `80bcfd37-f781-48da-aba9-48d3b06a6347`
  - Panels to embed:
    1. Panel 1: "Sensor Values" (table) — full width
    2. Panel 4: "Temperature/RH/VPD" (timeseries) — full width, height 400
    3. Panel 5: "Pressure and Devices" (timeseries) — full width, height 400
    4. Panel 6: "Statistics" (table) — full width
  - Use semantic token classes
  - Remove placeholder UID check

  **Must NOT do**:
  - Do NOT use hardcoded colors
  - Do NOT modify GrafanaPanel.tsx component

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 3 with Task 6)
  - **Blocks**: Task 8
  - **Blocked By**: Task 5

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/GrafanaPanel.tsx` — Reusable component (same as Task 6)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/VegetationMonitoring.tsx` — Current placeholder to replace (30 lines)

  **Acceptance Criteria**:
  - [x] `grep -c "panelId=" src/pages/VegetationMonitoring.tsx` returns 4
  - [x] `grep "80bcfd37-f781-48da-aba9-48d3b06a6347" src/pages/VegetationMonitoring.tsx` finds UID
  - [x] Zero hardcoded color classes in file
  - [x] `npm run build` passes

  **Commit**: Groups with Task 6

---

- [x] 8. Implement FlowerSoil with 9 panels + final verification

  **What to do**:
  - Rewrite `src/pages/FlowerSoil.tsx` to show 9 individual Grafana panel iframes in a responsive grid
  - Dashboard UID: `flower-sector-soil`
  - Panels to embed (in a logical grid layout):
    1. Panel 1 — full width
    2. Panel 2 — half width
    3. Panel 3 — half width
    4. Panel 4 — full width
    5. Panel 10 — full width
    6. Panel 20 — half width
    7. Panel 21 — half width
    8. Panel 30 — half width
    9. Panel 31 — half width
  - Use semantic token classes
  - Remove placeholder UID check
  - **Final verification**: Run complete project checks

  **Must NOT do**:
  - Do NOT use hardcoded colors
  - Do NOT modify GrafanaPanel.tsx component

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (Wave 4, final)
  - **Blocks**: None (final task)
  - **Blocked By**: Tasks 4, 6, 7

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/components/GrafanaPanel.tsx` — Reusable component
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/FlowerSoil.tsx` — Current placeholder to replace (30 lines)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/FlowerMonitoring.tsx` — Follow the grid pattern established in Task 6

  **Acceptance Criteria**:
  - [x] `grep -c "panelId=" src/pages/FlowerSoil.tsx` returns 9
  - [x] `grep "flower-sector-soil" src/pages/FlowerSoil.tsx` finds UID
  - [x] `npm run build` passes with zero errors
  - [x] `grep -rn "bg-gray-\|text-gray-\|border-gray-\|bg-red-\|bg-green-\|bg-cyan-\|dark:" src/ --include="*.tsx"` returns zero matches across entire project
  - [x] `ls src/components/ui/button.tsx src/lib/utils.ts` both exist
  - [x] `git status` shows clean working tree
  - [x] `git log --oneline -7` shows all task commits

  **Commit**: `feat(frontend): implement grafana panel embeds for flower soil monitoring`

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(frontend): add navigation layout with sidebar and top ribbon` | Layout, Sidebar, TopRibbon, ControlActionsContext, App.tsx | `npm run build` |
| 1 | `feat(frontend): add route pages for all sectors` | All page files, GrafanaPanel, other modified | `npm run build` |
| 2 | `fix(frontend): replace 26 hardcoded colors with semantic tokens and fix stale closure` | 9 .tsx files | `npm run build` |
| 4 | `feat(frontend): add shadcn/ui components via manual installation` | utils.ts, ui/*.tsx, package.json | `npm run build` |
| 5 | `feat(frontend): add grafana reverse proxy to vite config` | vite.config.ts | `npm run build` |
| 6+7 | `feat(frontend): implement grafana panel embeds for monitoring pages` | FlowerMonitoring.tsx, VegetationMonitoring.tsx | `npm run build` |
| 8 | `feat(frontend): implement grafana panel embeds for flower soil monitoring` | FlowerSoil.tsx | `npm run build` |

---

## Success Criteria

### Verification Commands
```bash
npm run build                    # Expected: exit 0, "built in"
npx tsc --noEmit                 # Expected: exit 0, no TypeScript errors
git status                       # Expected: clean working tree
grep -rn "bg-gray-\|text-gray-\|border-gray-\|bg-red-\|bg-green-\|bg-cyan-\|dark:" src/ --include="*.tsx"  # Expected: no matches
ls src/components/ui/            # Expected: 8 component files
ls src/lib/utils.ts              # Expected: file exists
curl -s http://localhost:3001/grafana/api/health  # Expected: {"database":"ok"}
```

### Final Checklist
- [x] All "Must Have" present (26 colors fixed, stale closure fixed, Grafana panels, shadcn/ui)
- [x] All "Must NOT Have" absent (no init command, no CSS changes, no port changes, no deploy)
- [x] Build passes with zero errors
- [x] Git log shows 7 logical commits
- [x] Working tree is clean
