# Frontend Modernisation

## TL;DR

> **Quick Summary**: Retheme the ProjectCEA dashboard with 6 switchable dark themes (Precision Void, Control Room Zero, Verdant Growth, Spectrum Analytics, Obsidian Glass, Botanical), preserving the production layout pixel-perfectly. Fresh branch from main, Tailwind 4, shadcn/ui with strict density overrides, JetBrains Mono + Inter typography. Dev server on Pi port 3002 for side-by-side comparison with production (port 3001).
>
> **Deliverables**:
> - CSS variable-based theme system with 6 complete dark palettes
> - Dev-only theme switcher dropdown in header
> - Tailwind 3→4 migration
> - shadcn/ui components with industrial-density overrides
> - Self-hosted JetBrains Mono + Inter fonts (woff2)
> - Vitest + Playwright test suite
> - Dev systemd service on port 3002
>
> **Estimated Effort**: Large (11 tasks, ~3-5 days)
> **Parallel Execution**: YES - 7 waves
> **Critical Path**: Task 1 → Task 2 → Task 5 → Task 6 → Task 9

---

## Context

### Original Request
Reimplement UI modernisation from scratch. Previous attempt (Linear/Vercel glassmorphism aesthetic) failed on:
- Too much glass/blur effects
- Felt too trendy and fragile
- Poor text readability and contrast
- Unwanted layout changes that broke "everything-at-a-glance" density

User wants fresh start from production baseline with nature-inspired themes and custom Botanical color palette. All work in git worktrees — production must not be impacted.

### Interview Summary
**Key Decisions:**
- Theme: 6 switchable themes for dev experimentation, final prod ships one winner
- Git: Reset `ui/frontend-modernization` branch to main (DONE)
- Tailwind: 3→4 upgrade (clean migration)
- Components: shadcn/ui with strict density (production density preserved)
- Typography: JetBrains Mono for data, Inter for UI
- Animations: Subtle opacity/translate only (NO scale transforms)
- Testing: Full suite (Vitest + Playwright visual QA)
- Dev server: Same Pi, port 3002 side-by-side with prod on port 3001

**User-Provided Custom Palette (Botanical theme):**
- Fern (greens): Primary surfaces
- Lemon-lime: Accents, highlights
- Soft-cyan: Data values, live readings
- Dusty-taupe: Warm neutrals
- Dusty-mauve: Warnings, error states

### Metis Review
**Identified Gaps (addressed in plan):**
1. Dashboard.tsx has ~200+ hardcoded color classes — bulk of work is mechanical conversion
2. Production cea-frontend.service runs on port 3001, dev must use 3002
3. Two incompatible styling patterns: Dashboard (hardcoded dark) vs ZoneCard (`dark:` prefixes) — both converted to semantic system
4. No test infrastructure exists despite vitest in devDeps
5. Phases 0-3 must produce ZERO visual change (infrastructure only)
6. React 18 + shadcn/ui compatibility — pin to compatible version
7. Self-host fonts as woff2, no Google Fonts CDN on RPi

---

## Work Objectives

### Core Objective
Apply 6 switchable dark theme palettes to the existing CEA dashboard via CSS custom properties, upgrading the styling infrastructure (Tailwind 4, shadcn/ui, typography) while preserving the production layout, density, and functionality pixel-perfectly.

### Concrete Deliverables
- `src/styles/themes.css` — 6 theme CSS files with complete variable sets
- `src/components/ThemeSwitcher.tsx` — Dev-only header dropdown
- `src/styles/index.css` — Semantic token system
- All components converted from hardcoded colors to semantic tokens
- Tailwind 4 `@theme inline` in CSS (replaces tailwind.config.js)
- Self-hosted `public/fonts/` (JetBrains Mono + Inter woff2)
- `src/components/ui/` — shadcn/ui components with density overrides
- Vitest config + theme switching tests
- Playwright visual regression screenshots
- `cea-frontend-dev.service` systemd unit on port 3002

### Definition of Done
- [ ] All 6 themes render correctly on all 3 pages (Dashboard, ZoneConfig, DeviceConfig)
- [ ] Production layout preserved: grid structure, spacing, density identical to main
- [ ] All existing elements functional: Pi monitoring, WebSocket data, controls, charts
- [ ] `npm run build` succeeds with 0 errors in the worktree
- [ ] Dev server accessible at `http://<pi-ip>:3002`
- [ ] Production server unaffected at `http://<pi-ip>:3001`
- [ ] No `backdrop-blur`, no `scale()` transforms anywhere

### Must Have
- All 6 theme palettes fully defined with complete CSS variable coverage
- Semantic color tokens — zero hardcoded color classes in any component
- Production layout density preserved (text-xs, text-[10px], p-2, gap-1, gap-2, h-7)
- JetBrains Mono for all numerical data values, Inter for UI text
- Self-hosted fonts with `font-display: swap`
- Dev-only theme switcher (conditional on `import.meta.env.DEV`)
- Worktree isolation — zero changes to main branch
- Side-by-side dev/prod on same Pi (port 3002/3001)
- Pi monitoring dashboard works correctly (CPU/RAM/Disk/Temp/Services)
- Snappy performance on Raspberry Pi 5

### Must NOT Have (Guardrails)
- **NO layout changes**: Grid columns, flex directions, spacing, breakpoints stay identical
- **NO Dashboard.tsx restructuring**: No extracting sub-components, no splitting the file
- **NO existing component replacement**: Don't replace CircularTimePicker/LightSlider/etc. with shadcn equivalents
- **NO `backdrop-blur`**: RPi5 GPU can't handle it smoothly
- **NO `scale()` transforms**: Causes GPU compositing text blurring (learned from v1)
- **NO complex `box-shadow` stacking**: Performance penalty on RPi5
- **NO `dark:` prefix pattern**: Eliminate entirely, use semantic tokens
- **NO light mode**: All 6 themes are dark variants
- **NO Google Fonts CDN**: Self-host everything for RPi reliability
- **NO JSX structure changes**: Only modify class strings and CSS, never change HTML structure
- **NO commit diffs exceeding ~500 lines**: Small, reviewable increments
- **NO animation proliferation**: Only theme switcher + page-level opacity transitions
- **NO responsive layout redesign**: Existing breakpoints preserved exactly

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> ALL verification is agent-executed using tools.

### Test Decision
- **Infrastructure exists**: NO (vitest in devDeps but zero config/tests)
- **Automated tests**: YES (Tests-after)
- **Framework**: Vitest + Playwright

### Agent-Executed QA

| Type | Tool | How Agent Verifies |
|------|------|-------------------|
| Visual regression | Playwright | Screenshot comparison with baseline |
| Theme switching | Playwright | Toggle themes, verify CSS variables applied |
| Build integrity | Bash | `npm run build` exit code 0 |
| Dev server | Bash (curl) | HTTP 200 on port 3002 |
| Font loading | Playwright | Verify font-family computed styles |
| Element functionality | Playwright | Interact with controls, verify WebSocket data |

---

## Color Architecture

### Semantic Token Mapping

Production hardcoded classes → semantic CSS variable classes:

**Surfaces:**

| Hardcoded | Semantic | Usage |
|-----------|----------|-------|
| `bg-gray-950` | `bg-surface-base` | Page background |
| `bg-gray-900` | `bg-surface-primary` | Card backgrounds |
| `bg-gray-800` | `bg-surface-secondary` | Elevated elements, inputs |
| `bg-gray-700` | `bg-surface-tertiary` | Hover states, active |

**Text:**

| Hardcoded | Semantic | Usage |
|-----------|----------|-------|
| `text-white` | `text-default` | Primary text |
| `text-gray-300` | `text-secondary` | Slightly dimmed |
| `text-gray-400` | `text-muted` | Labels, metadata |
| `text-gray-500` | `text-subtle` | Hints, disabled |

**Data & Status:**

| Hardcoded | Semantic | Usage |
|-----------|----------|-------|
| `text-cyan-400` / `text-cyan-300` | `text-accent-data` | Live sensor values |
| `text-amber-400` / `text-amber-300` | `text-accent-setpoint` | Setpoint values |
| `text-green-400` / `text-green-300` | `text-status-success` | Online, healthy |
| `text-red-400` / `text-red-300` | `text-status-danger` | Offline, error |
| `text-yellow-400` | `text-status-warning` | Warning states |
| `bg-green-900` | `bg-status-success` | Success backgrounds |
| `bg-red-900` | `bg-status-danger` | Error backgrounds |
| `bg-cyan-900/10` | `bg-status-info` | Info backgrounds |

**Borders:**

| Hardcoded | Semantic | Usage |
|-----------|----------|-------|
| `border-gray-700` | `border-default` | Standard borders |
| `border-gray-600` | `border-emphasis` | Emphasized borders |
| `border-gray-800` | `border-subtle` | Light borders |
| `divide-gray-700` | `divide-default` | Dividers |
| `ring-cyan-500` | `ring-accent` | Focus rings |

### 6 Theme Palettes

**Theme 1: Precision Void** (Ultra-clean minimal — Linear/Apple)
```css
[data-theme="precision-void"] {
  --surface-base: #0a0a0a; --surface-primary: #151515;
  --surface-secondary: #1a1a1a; --surface-tertiary: #262626;
  --text-default: #fafafa; --text-secondary: #a3a3a3;
  --text-muted: #737373; --text-subtle: #525252;
  --accent: #3b82f6; --accent-data: #60a5fa; --accent-setpoint: #fbbf24;
  --status-success: #4ade80; --status-danger: #f87171; --status-warning: #fbbf24;
  --border-default: #262626; --border-emphasis: #404040; --border-subtle: #1a1a1a;
}
```

**Theme 2: Control Room Zero** (Industrial HMI/SCADA)
```css
[data-theme="control-room"] {
  --surface-base: #2a2d35; --surface-primary: #363a44;
  --surface-secondary: #3f434f; --surface-tertiary: #4b5563;
  --text-default: #e5e7eb; --text-secondary: #d1d5db;
  --text-muted: #9ca3af; --text-subtle: #6b7280;
  --accent: #f59e0b; --accent-data: #67e8f9; --accent-setpoint: #fbbf24;
  --status-success: #4ade80; --status-danger: #ef4444; --status-warning: #f59e0b;
  --border-default: #4b5563; --border-emphasis: #6b7280; --border-subtle: #363a44;
}
```

**Theme 3: Verdant Growth** (Nature-tech fusion)
```css
[data-theme="verdant-growth"] {
  --surface-base: #1a1d1a; --surface-primary: #242826;
  --surface-secondary: #2d322e; --surface-tertiary: #3d4a38;
  --text-default: #f5f3ed; --text-secondary: #c8d0c4;
  --text-muted: #9fa89c; --text-subtle: #6b7a65;
  --accent: #86c232; --accent-data: #67e8f9; --accent-setpoint: #d4a574;
  --status-success: #86c232; --status-danger: #ef4444; --status-warning: #d4a574;
  --border-default: #3d4a38; --border-emphasis: #4d5c46; --border-subtle: #242826;
}
```

**Theme 4: Spectrum Analytics** (Bold modern SaaS)
```css
[data-theme="spectrum"] {
  --surface-base: #0f0f23; --surface-primary: #1a1a3e;
  --surface-secondary: #252547; --surface-tertiary: #303060;
  --text-default: #f8fafc; --text-secondary: #cbd5e1;
  --text-muted: #94a3b8; --text-subtle: #64748b;
  --accent: #6366f1; --accent-data: #67e8f9; --accent-setpoint: #fbbf24;
  --status-success: #10b981; --status-danger: #ec4899; --status-warning: #f59e0b;
  --border-default: #334155; --border-emphasis: #475569; --border-subtle: #1e293b;
}
```

**Theme 5: Obsidian Glass** (Premium dark — NO blur effects, solid bgs only)
```css
[data-theme="obsidian"] {
  --surface-base: #0a0a0f; --surface-primary: #141419;
  --surface-secondary: #1e1e28; --surface-tertiary: #2a2a3a;
  --text-default: #ffffff; --text-secondary: #d0d0e0;
  --text-muted: #a0a0b0; --text-subtle: #6b6b80;
  --accent: #a78bfa; --accent-data: #60a5fa; --accent-setpoint: #fbbf24;
  --status-success: #4ade80; --status-danger: #f87171; --status-warning: #fbbf24;
  --border-default: #2a2a3a; --border-emphasis: #3a3a4f; --border-subtle: #1a1a25;
}
```

**Theme 6: Botanical** (User's custom palette)
```css
[data-theme="botanical"] {
  --surface-base: #10150e; --surface-primary: #181e15;
  --surface-secondary: #2f3d29; --surface-tertiary: #475b3e;
  --text-default: #f1f5f0; --text-secondary: #c8d6c2;
  --text-muted: #b18981; --text-subtle: #7e564e;
  --accent: #b0d52a; --accent-data: #00fff2; --accent-setpoint: #c0dd55;
  --status-success: #769867; --status-danger: #bd4253; --status-warning: #d78e98;
  --border-default: #475b3e; --border-emphasis: #5e7953; --border-subtle: #2f3d29;

  /* Full Botanical color scales for utility classes */
  --color-fern-50: #f1f5f0; --color-fern-100: #e4eae1;
  --color-fern-200: #c8d6c2; --color-fern-300: #adc1a4;
  --color-fern-400: #91ac86; --color-fern-500: #769867;
  --color-fern-600: #5e7953; --color-fern-700: #475b3e;
  --color-fern-800: #2f3d29; --color-fern-900: #181e15;
  --color-fern-950: #10150e;

  --color-lemon-lime-50: #f7fbea; --color-lemon-lime-100: #eff7d4;
  --color-lemon-lime-200: #dfeeaa; --color-lemon-lime-300: #d0e67f;
  --color-lemon-lime-400: #c0dd55; --color-lemon-lime-500: #b0d52a;
  --color-lemon-lime-600: #8daa22; --color-lemon-lime-700: #6a8019;
  --color-lemon-lime-800: #465511; --color-lemon-lime-900: #232b08;
  --color-lemon-lime-950: #191e06;

  --color-soft-cyan-50: #e5fffe; --color-soft-cyan-100: #ccfffc;
  --color-soft-cyan-200: #99fffa; --color-soft-cyan-300: #66fff7;
  --color-soft-cyan-400: #33fff5; --color-soft-cyan-500: #00fff2;
  --color-soft-cyan-600: #00ccc2; --color-soft-cyan-700: #009991;
  --color-soft-cyan-800: #006661; --color-soft-cyan-900: #003330;
  --color-soft-cyan-950: #002422;

  --color-dusty-taupe-50: #f5f0ef; --color-dusty-taupe-100: #ece1df;
  --color-dusty-taupe-200: #d8c4c0; --color-dusty-taupe-300: #c5a6a0;
  --color-dusty-taupe-400: #b18981; --color-dusty-taupe-500: #9e6b61;
  --color-dusty-taupe-600: #7e564e; --color-dusty-taupe-700: #5f403a;
  --color-dusty-taupe-800: #3f2b27; --color-dusty-taupe-900: #201513;
  --color-dusty-taupe-950: #160f0e;

  --color-dusty-mauve-50: #f8ecee; --color-dusty-mauve-100: #f2d9dd;
  --color-dusty-mauve-200: #e4b4ba; --color-dusty-mauve-300: #d78e98;
  --color-dusty-mauve-400: #ca6875; --color-dusty-mauve-500: #bd4253;
  --color-dusty-mauve-600: #973542; --color-dusty-mauve-700: #712832;
  --color-dusty-mauve-800: #4b1b21; --color-dusty-mauve-900: #260d11;
  --color-dusty-mauve-950: #1a090c;
}
```

---

## UI Elements Inventory (Preservation Checklist)

Every element below MUST work correctly after modernisation. Only class strings change — never JSX structure.

| # | Element | Location | Styling | Verify |
|---|---------|----------|---------|--------|
| 1 | **Pi Monitoring** | Dashboard.tsx ~L890-1050 | Progress bars (green/blue/amber), service dots, CPU temp | Progress bar colors map to semantic tokens |
| 2 | **Real-time Sensors** | Dashboard.tsx ~L524-683, L707-866, L1074-1198 | font-mono numerics, text-white values, text-amber-400 setpoints | WebSocket 1Hz still updating, values readable |
| 3 | **Status Indicators** | Dashboard.tsx | green-400 animate-pulse for WS, sun/moon emoji, service dots | Pulse animation preserved, colors via tokens |
| 4 | **CircularTimePicker** | CircularTimePicker.tsx | Canvas 24h clock, draggable handles | Canvas colors via getComputedStyle() + CSS vars |
| 5 | **LightSlider** | LightSlider.tsx | Vertical slider, amber gradient, white overlay | Gradient uses CSS vars, overlay visible |
| 6 | **RoomModeSelector** | RoomModeSelector.tsx | Pill buttons VEG/FLOWER/SHUTDOWN + submodes | Button colors from semantic tokens |
| 7 | **SetpointTimeline** | SetpointTimeline.tsx | Custom SVG, mode bg colors, diagonal ramp, hatch patterns | SVG fill/stroke from CSS vars |
| 8 | **SetpointsTable** | SetpointsTable.tsx | 2x2 grid, cyan-500 highlight on changes | Highlight color via accent-data token |
| 9 | **Device Logs** | Dashboard.tsx ~L672-682 | Terminal-style mono, HH:MM:SS timestamps | font-mono preserved, colors semantic |
| 10 | **Toast Notifications** | ToastContext.tsx → migrate to Sonner | Fixed top-right, green/red/yellow/blue | Install sonner, replace ToastContext |
| 11 | **PID Dialog** | PIDChangeDialog.tsx | Modal, backdrop-blur-sm | Replace backdrop-blur-sm → bg-black/60 |
| 12 | **Navigation** | App.tsx | 3 routes, sticky header "Siberian Jungle" | Header uses surface-primary bg |

---

## Performance Requirements (RPi5)

| Constraint | Reason | Alternative |
|------------|--------|-------------|
| No `backdrop-blur` | GPU-intensive on ARM | Use solid `bg-black/60` overlays |
| No `scale()` transforms | GPU compositing causes text blur | Use `opacity` + `translate` only |
| No complex `box-shadow` stacking | Repaint cost | Single subtle shadow max |
| Self-host fonts | No CDN dependency, offline-capable | `public/fonts/*.woff2` |
| `font-display: swap` | Prevent FOIT on slow load | Applied in @font-face |
| Preload critical fonts | Faster initial paint | `<link rel="preload">` in index.html |
| Limit transitions | 150-200ms max, `opacity`+`transform` only | GPU-accelerated properties only |
| Code splitting | Smaller initial bundle | React.lazy for ZoneConfig, DeviceConfig |
| `font-variant-numeric: tabular-nums` | Prevent layout shift on number changes | Applied globally to data displays |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (DONE):
└── Task 1: Git Reset + Baseline ✅

Wave 2 (Sequential):
└── Task 2: Tailwind 3→4 Migration

Wave 3 (Parallel):
├── Task 3: Font Installation (woff2)
└── Task 4: shadcn/ui + Density Overrides

Wave 4 (Sequential — BIGGEST TASK):
└── Task 5: Semantic Color Token System

Wave 5 (Parallel):
├── Task 6: 6 Theme Palettes
└── Task 7: Theme Switcher Component

Wave 6 (Parallel):
├── Task 8: Typography Application
└── Task 9: Element Enhancement + Performance Audit

Wave 7 (Parallel):
├── Task 10: Test Infrastructure + Visual QA
└── Task 11: Dev Server Deployment (port 3002)
```

### Dependency Matrix

| Task | Depends On | Blocks | Parallel With |
|------|-----------|--------|---------------|
| 1 | None | 2 | None |
| 2 | 1 | 3, 4 | None |
| 3 | 2 | 5 | 4 |
| 4 | 2 | 5 | 3 |
| 5 | 3, 4 | 6, 7 | None |
| 6 | 5 | 8, 9 | 7 |
| 7 | 5 | 8, 9 | 6 |
| 8 | 6, 7 | 10, 11 | 9 |
| 9 | 6, 7 | 10, 11 | 8 |
| 10 | 8, 9 | None | 11 |
| 11 | 8, 9 | None | 10 |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|--------------------|
| 1 | 1 ✅ | quick + git-master + playwright |
| 2 | 2 | unspecified-high |
| 3 | 3, 4 | quick (parallel) |
| 4 | 5 | ultrabrain (biggest task) |
| 5 | 6, 7 | quick (parallel) |
| 6 | 8, 9 | unspecified-high + playwright |
| 7 | 10, 11 | unspecified-high (parallel) |

---

## TODOs

> ALL work happens in the UI worktree at `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`

- [x] 1. Git Reset + Visual Baseline

  **Status**: COMPLETED
  Branch `ui/frontend-modernization` reset to main. npm install done. Build verified. Commit: `d790645 chore(frontend): reset branch to main baseline`.

---

- [ ] 2. Tailwind 3→4 Migration

  **What to do**:
  - Run `npx @tailwindcss/upgrade` in `Infrastructure/frontend/`
  - Install `@tailwindcss/vite`: `npm install -D @tailwindcss/vite @tailwindcss/postcss`
  - Update `vite.config.ts` to use `@tailwindcss/vite` plugin
  - Remove old `tailwind.config.js` and `postcss.config.js` if the upgrade tool converts them
  - Verify no `@tailwind base/components/utilities` directives remain in CSS (TW4 uses `@import "tailwindcss"`)
  - Handle breaking changes: `shadow-sm`→`shadow-xs`, `ring`→`ring-3`, `outline-none`→`outline-hidden`
  - Run `npm run build` — must exit 0

  **Must NOT do**:
  - Change any layout or component structure
  - Add new Tailwind utilities not already in use
  - Modify any JSX — only config and CSS files

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: None needed beyond default

  **Parallelization**:
  - Sequential (Wave 2)
  - **Blocked By**: Task 1
  - **Blocks**: Tasks 3, 4

  **References**:
  - `Infrastructure/frontend/tailwind.config.js` — current TW3 config to be converted
  - `Infrastructure/frontend/postcss.config.js` — current PostCSS config
  - `Infrastructure/frontend/src/styles/index.css` — main CSS entry point
  - `Infrastructure/frontend/vite.config.ts` — Vite config to update
  - TW4 migration guide: https://tailwindcss.com/docs/upgrade-guide

  **Acceptance Criteria**:
  - [ ] `tailwind.config.js` removed or converted to `@theme` in CSS
  - [ ] `postcss.config.js` updated or removed
  - [ ] `vite.config.ts` includes `@tailwindcss/vite` plugin
  - [ ] `npm run build` exits 0 with no errors
  - [ ] All existing Tailwind classes still work (visual output unchanged)
  - [ ] No `@tailwind` directives in CSS files

  **Commit**: `feat(frontend): migrate Tailwind CSS 3 to 4`

---

- [ ] 3. Font Installation (JetBrains Mono + Inter)

  **What to do**:
  - Download JetBrains Mono (Regular, Medium, Bold) woff2 files from Google Fonts or JetBrains CDN
  - Download Inter (Regular 400, Medium 500, Semibold 600) woff2 files
  - Place in `public/fonts/jetbrains-mono/` and `public/fonts/inter/`
  - Add `@font-face` declarations in `src/styles/index.css`:
    ```css
    @font-face {
      font-family: 'JetBrains Mono';
      src: url('/fonts/jetbrains-mono/JetBrainsMono-Regular.woff2') format('woff2');
      font-weight: 400; font-style: normal; font-display: swap;
    }
    /* ... Medium 500, Bold 700 ... */
    @font-face {
      font-family: 'Inter';
      src: url('/fonts/inter/Inter-Regular.woff2') format('woff2');
      font-weight: 400; font-style: normal; font-display: swap;
    }
    /* ... Medium 500, Semibold 600 ... */
    ```
  - Add `<link rel="preload">` tags in `index.html` for Regular weights
  - Register fonts in Tailwind 4 `@theme`:
    ```css
    @theme inline {
      --font-sans: 'Inter', system-ui, sans-serif;
      --font-mono: 'JetBrains Mono', ui-monospace, monospace;
    }
    ```

  **Must NOT do**:
  - Use Google Fonts CDN (must be self-hosted for RPi offline use)
  - Change any component files

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - Wave 3 — parallel with Task 4
  - **Blocked By**: Task 2
  - **Blocks**: Task 5

  **Acceptance Criteria**:
  - [ ] woff2 files exist in `public/fonts/`
  - [ ] `@font-face` declarations in index.css
  - [ ] `<link rel="preload">` in index.html
  - [ ] `@theme` font registration in CSS
  - [ ] `npm run build` exits 0

  **Commit**: `feat(frontend): add self-hosted JetBrains Mono and Inter fonts`

---

- [ ] 4. shadcn/ui Installation + Strict Density Overrides

  **What to do**:
  - Run `npx shadcn@latest init` with Tailwind 4 / New York style / Neutral base
  - Install 8 components: `npx shadcn@latest add button card input label select separator skeleton badge`
  - **IMMEDIATELY** apply density overrides to each component in `src/components/ui/`:
    - `button.tsx`: Default variant uses `h-7 px-2 text-xs` (not h-10 px-4 text-sm)
    - `card.tsx`: CardContent uses `p-2` (not p-6)
    - `input.tsx`: Uses `h-7 px-2 text-xs` (not h-10 px-3 text-sm)
    - `select.tsx`: Trigger uses `h-7 text-xs` (not h-10 text-sm)
    - `badge.tsx`: Uses `px-1.5 py-0 text-[10px]` (not px-2.5 py-0.5 text-xs)
    - `separator.tsx`: Uses `my-1` (not my-4)
  - Install utility dependencies: `npm install class-variance-authority clsx tailwind-merge` (if not already present)
  - Ensure `src/lib/utils.ts` has the `cn()` helper function
  - Verify React 18 compatibility — if shadcn errors on React 19 patterns, pin to compatible version

  **Must NOT do**:
  - Replace any existing custom components with shadcn equivalents
  - Use shadcn components anywhere yet (just install + configure)
  - Change production density values

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - Wave 3 — parallel with Task 3
  - **Blocked By**: Task 2
  - **Blocks**: Task 5

  **Acceptance Criteria**:
  - [ ] `components.json` exists at frontend root
  - [ ] 8 shadcn components in `src/components/ui/`
  - [ ] All density overrides applied (grep for `h-7` in button.tsx, `p-2` in card.tsx)
  - [ ] `cn()` helper in `src/lib/utils.ts`
  - [ ] `npm run build` exits 0

  **Commit**: `feat(frontend): install shadcn/ui with production-density overrides`

---

- [ ] 5. Semantic Color Token System

  **What to do**:
  This is the BIGGEST task. Convert ~200+ hardcoded Tailwind color classes across all component files to semantic CSS custom properties.

  **Step 1: Define CSS variables in `src/styles/index.css`**
  Add to `:root` (default values matching current production appearance):
  ```css
  :root {
    --surface-base: #030712;    /* was bg-gray-950 */
    --surface-primary: #111827; /* was bg-gray-900 */
    --surface-secondary: #1f2937; /* was bg-gray-800 */
    --surface-tertiary: #374151; /* was bg-gray-700 */
    --text-default: #ffffff;
    --text-secondary: #d1d5db;  /* was text-gray-300 */
    --text-muted: #9ca3af;      /* was text-gray-400 */
    --text-subtle: #6b7280;     /* was text-gray-500 */
    --accent: #06b6d4;
    --accent-data: #22d3ee;     /* was text-cyan-400 */
    --accent-setpoint: #fbbf24; /* was text-amber-400 */
    --status-success: #4ade80;  /* was text-green-400 */
    --status-danger: #f87171;   /* was text-red-400 */
    --status-warning: #facc15;  /* was text-yellow-400 */
    --border-default: #374151;  /* was border-gray-700 */
    --border-emphasis: #4b5563; /* was border-gray-600 */
    --border-subtle: #1f2937;   /* was border-gray-800 */
    --ring-accent: #06b6d4;
  }
  ```

  **Step 2: Register in Tailwind 4 `@theme`**
  ```css
  @theme inline {
    --color-surface-base: var(--surface-base);
    --color-surface-primary: var(--surface-primary);
    --color-surface-secondary: var(--surface-secondary);
    --color-surface-tertiary: var(--surface-tertiary);
    --color-text-default: var(--text-default);
    --color-text-secondary: var(--text-secondary);
    --color-text-muted: var(--text-muted);
    --color-text-subtle: var(--text-subtle);
    --color-accent: var(--accent);
    --color-accent-data: var(--accent-data);
    --color-accent-setpoint: var(--accent-setpoint);
    --color-status-success: var(--status-success);
    --color-status-danger: var(--status-danger);
    --color-status-warning: var(--status-warning);
    --color-border-default: var(--border-default);
    --color-border-emphasis: var(--border-emphasis);
    --color-border-subtle: var(--border-subtle);
  }
  ```

  **Step 3: Mechanical class replacement**
  Process files ONE AT A TIME. Use search-and-replace (ast_grep_replace where possible):

  Surface replacements:
  - `bg-gray-950` → `bg-surface-base`
  - `bg-gray-900` → `bg-surface-primary`
  - `bg-gray-800` → `bg-surface-secondary`
  - `bg-gray-700` → `bg-surface-tertiary`

  Text replacements:
  - `text-white` → `text-text-default`
  - `text-gray-300` → `text-text-secondary`
  - `text-gray-400` → `text-text-muted`
  - `text-gray-500` → `text-text-subtle`

  Accent/Status replacements:
  - `text-cyan-400` → `text-accent-data`
  - `text-cyan-300` → `text-accent-data`
  - `text-amber-400` → `text-accent-setpoint`
  - `text-amber-300` → `text-accent-setpoint`
  - `text-green-400` → `text-status-success`
  - `text-green-300` → `text-status-success`
  - `text-red-400` → `text-status-danger`
  - `text-red-300` → `text-status-danger`
  - `text-yellow-400` → `text-status-warning`
  - `bg-green-900` → `bg-status-success`
  - `bg-red-900` → `bg-status-danger`

  Border replacements:
  - `border-gray-700` → `border-border-default`
  - `border-gray-600` → `border-border-emphasis`
  - `border-gray-800` → `border-border-subtle`
  - `divide-gray-700` → `divide-border-default`
  - `ring-cyan-500` → `ring-accent`

  **Step 4: Handle special cases**
  - `dark:` prefix classes: Remove entirely, the semantic tokens handle dark mode
  - Canvas components (CircularTimePicker): Use `getComputedStyle()` to read CSS vars
  - SVG components (SetpointTimeline): Use `var()` in inline style attributes
  - PIDChangeDialog: Replace `backdrop-blur-sm` → `bg-black/60`
  - Gradient colors: Convert amber/cyan gradients to use CSS vars via arbitrary values

  **Step 5: Verify zero hardcoded colors remain**
  Run: `grep -rn "text-gray-\|bg-gray-\|border-gray-\|text-white\|text-cyan-\|text-amber-\|text-green-\|text-red-\|text-yellow-\|bg-green-\|bg-red-\|bg-cyan-\|divide-gray-\|ring-cyan-" src/ --include="*.tsx" --include="*.ts"`
  Must return 0 results (excluding theme definition files).

  **Must NOT do**:
  - Change any JSX structure — ONLY class string values
  - Change any layout classes (flex, grid, gap, p-, m-, w-, h-)
  - Modify any JavaScript logic
  - Touch non-color Tailwind classes

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: None (pure mechanical + careful work)

  **Parallelization**:
  - Sequential (Wave 4) — this is the critical path
  - **Blocked By**: Tasks 3, 4
  - **Blocks**: Tasks 6, 7

  **References**:
  - `Infrastructure/frontend/src/pages/Dashboard.tsx` — ~200+ color classes, process first
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` — uses `dark:` prefix pattern
  - `Infrastructure/frontend/src/pages/DeviceConfig.tsx` — smaller file
  - `Infrastructure/frontend/src/components/SetpointTimeline.tsx` — SVG colors
  - `Infrastructure/frontend/src/components/CircularTimePicker.tsx` — Canvas colors
  - `Infrastructure/frontend/src/components/LightSlider.tsx` — gradient colors
  - `Infrastructure/frontend/src/components/RoomModeSelector.tsx` — pill button colors
  - `Infrastructure/frontend/src/components/SetpointsTable.tsx` — highlight colors
  - `Infrastructure/frontend/src/components/PIDChangeDialog.tsx` — modal backdrop
  - Color mapping table: See "Semantic Token Mapping" section above

  **Acceptance Criteria**:
  - [ ] `:root` CSS variables defined in index.css
  - [ ] `@theme inline` registers all colors for Tailwind
  - [ ] Zero hardcoded gray/cyan/amber/green/red color classes in src/ (verified by grep)
  - [ ] All `dark:` prefixes eliminated
  - [ ] `npm run build` exits 0
  - [ ] Visual output IDENTICAL to production (same colors, just via variables now)
  - [ ] Canvas/SVG components use getComputedStyle/var() for colors
  - [ ] PIDChangeDialog uses bg-black/60 instead of backdrop-blur

  **Commit**: `feat(frontend): convert all hardcoded colors to semantic CSS tokens`
  Files: All component .tsx files + src/styles/index.css

---

- [ ] 6. Theme Palettes (6 Themes)

  **What to do**:
  - Create `src/styles/themes.css` with all 6 `[data-theme]` blocks (see Color Architecture section above for exact values)
  - Import in `src/styles/index.css`: `@import './themes.css';`
  - Set default theme in `index.html`: `<html data-theme="botanical">`
  - Add localStorage persistence logic in a `src/hooks/useTheme.ts` hook:
    ```typescript
    export function useTheme() {
      const [theme, setTheme] = useState(() => localStorage.getItem('cea-theme') || 'botanical');
      useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('cea-theme', theme);
      }, [theme]);
      return { theme, setTheme };
    }
    ```

  **Must NOT do**:
  - Use any `backdrop-blur` in Obsidian Glass theme (solid backgrounds only)
  - Modify any component files (only CSS and new hook file)

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - Wave 5 — parallel with Task 7
  - **Blocked By**: Task 5
  - **Blocks**: Tasks 8, 9

  **Acceptance Criteria**:
  - [ ] `src/styles/themes.css` exists with 6 `[data-theme]` blocks
  - [ ] All 6 themes have complete variable coverage (all --surface-*, --text-*, --accent-*, --status-*, --border-* vars)
  - [ ] Botanical theme includes full color scale variables (fern, lemon-lime, soft-cyan, dusty-taupe, dusty-mauve)
  - [ ] Default theme is `botanical` in index.html
  - [ ] localStorage persistence works (theme survives page reload)
  - [ ] `npm run build` exits 0

  **Commit**: `feat(frontend): add 6 switchable dark theme palettes`

---

- [ ] 7. Theme Switcher Component

  **What to do**:
  - Create `src/components/ThemeSwitcher.tsx`:
    - Dropdown/select with 6 theme options
    - Gated on `import.meta.env.DEV` (not rendered in production builds)
    - Uses the `useTheme()` hook from Task 6
    - Compact styling to fit in header without disrupting layout
  - Add `<ThemeSwitcher />` to the header area in `App.tsx`
  - Only shows in dev mode (Vite strips it from production builds)

  **Must NOT do**:
  - Change header layout or structure
  - Make it visible in production builds
  - Add complex animations to theme transitions

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - Wave 5 — parallel with Task 6
  - **Blocked By**: Task 5
  - **Blocks**: Tasks 8, 9

  **References**:
  - `Infrastructure/frontend/src/App.tsx` — header area where switcher goes
  - `Infrastructure/frontend/src/hooks/useTheme.ts` — from Task 6

  **Acceptance Criteria**:
  - [ ] `ThemeSwitcher.tsx` created with 6 theme options
  - [ ] Renders in dev mode, hidden in production build
  - [ ] Changing selection switches `data-theme` attribute on `<html>`
  - [ ] Theme persists across page reloads via localStorage
  - [ ] Does not break header layout
  - [ ] `npm run build` exits 0 (component tree-shaken in prod)

  **Commit**: `feat(frontend): add dev-only theme switcher dropdown`

---

- [ ] 8. Typography Application

  **What to do**:
  - Verify `font-mono` → JetBrains Mono mapping works (from Task 3 `@theme` config)
  - Verify `font-sans` → Inter mapping works
  - Add `font-variant-numeric: tabular-nums` to all data display elements:
    - Add a `.tabular-nums` utility or apply via Tailwind's `tabular-nums` class
    - Apply to: sensor values, setpoint values, PID values, timestamps, percentages
  - Verify text rendering at all production sizes (text-[8px], text-[10px], text-xs, text-sm)
  - Check that JetBrains Mono renders correctly in CircularTimePicker (Canvas)

  **Must NOT do**:
  - Change font sizes
  - Change font weights beyond what's defined
  - Modify layout spacing

  **Recommended Agent Profile**:
  - **Category**: `quick`

  **Parallelization**:
  - Wave 6 — parallel with Task 9
  - **Blocked By**: Tasks 6, 7
  - **Blocks**: Tasks 10, 11

  **Acceptance Criteria**:
  - [ ] `font-mono` elements render in JetBrains Mono
  - [ ] `font-sans` elements render in Inter
  - [ ] `tabular-nums` applied to numeric data displays
  - [ ] All text sizes render without clipping or overflow
  - [ ] `npm run build` exits 0

  **Commit**: `feat(frontend): apply JetBrains Mono + Inter typography with tabular nums`

---

- [ ] 9. Element Enhancement + Performance Audit

  **What to do**:
  - **Verify all 12 UI elements** from the inventory table work correctly with themed colors
  - **Migrate ToastContext → Sonner**:
    - `npm install sonner`
    - Replace `ToastContext.tsx` usage with Sonner's `<Toaster />` component
    - Update all `toast()` call sites
    - Style Sonner to match current theme (uses CSS vars)
  - **Add React.lazy code splitting**:
    ```typescript
    const ZoneConfig = React.lazy(() => import('./pages/ZoneConfig'));
    const DeviceConfig = React.lazy(() => import('./pages/DeviceConfig'));
    ```
    Wrap with `<Suspense fallback={<div>Loading...</div>}>` (or skeleton)
  - **Performance audit**:
    - `grep -rn "backdrop-blur\|scale(" src/` — must return 0 results
    - `grep -rn "box-shadow.*box-shadow" src/` — no stacked shadows
    - Check bundle size: `npm run build` output
    - Verify no unnecessary re-renders in React DevTools

  **Must NOT do**:
  - Change any layout
  - Add new components
  - Modify any business logic

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `playwright` (for element verification)

  **Parallelization**:
  - Wave 6 — parallel with Task 8
  - **Blocked By**: Tasks 6, 7
  - **Blocks**: Tasks 10, 11

  **References**:
  - UI Elements Inventory table above
  - `Infrastructure/frontend/src/contexts/ToastContext.tsx` — to be replaced
  - `Infrastructure/frontend/src/App.tsx` — routes for code splitting

  **Acceptance Criteria**:
  - [ ] All 12 UI elements functional with themed colors
  - [ ] Sonner installed and working (toast notifications appear)
  - [ ] ToastContext.tsx deprecated/removed
  - [ ] React.lazy applied to ZoneConfig and DeviceConfig routes
  - [ ] Zero `backdrop-blur` or `scale(` in codebase
  - [ ] `npm run build` exits 0, bundle size reasonable

  **Commit**: `feat(frontend): migrate toasts to Sonner, add code splitting, verify elements`

---

- [ ] 10. Test Infrastructure + Visual QA

  **What to do**:
  - Create `vitest.config.ts`:
    ```typescript
    import { defineConfig } from 'vitest/config';
    export default defineConfig({
      test: { environment: 'jsdom', globals: true }
    });
    ```
  - Install test deps: `npm install -D @testing-library/react @testing-library/jest-dom jsdom`
  - Create theme switching test: `src/__tests__/theme.test.ts`
    - Test that `useTheme()` updates `data-theme` attribute
    - Test localStorage persistence
    - Test all 6 theme names are valid
  - Create Playwright visual regression tests:
    - Screenshot each page with each theme
    - Compare screenshots between themes (they should differ)
    - Store reference screenshots in `.sisyphus/evidence/themes/`
  - Add `test` script to `package.json`: `"test": "vitest run"`

  **Must NOT do**:
  - Write tests for non-styling functionality
  - Modify any source code

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `playwright`

  **Parallelization**:
  - Wave 7 — parallel with Task 11
  - **Blocked By**: Tasks 8, 9
  - **Blocks**: None

  **Acceptance Criteria**:
  - [ ] `vitest.config.ts` exists
  - [ ] Theme unit tests pass: `npm test`
  - [ ] Playwright screenshots captured for all themes
  - [ ] `npm test` exits 0

  **Commit**: `test(frontend): add theme switching tests and visual regression suite`

---

- [ ] 11. Dev Server Deployment (port 3002)

  **What to do**:
  - Update `vite.config.ts` in the UI worktree:
    ```typescript
    server: {
      port: 3002,
      host: '0.0.0.0',
      proxy: {
        '/api': 'http://localhost:8000',
        '/ws': { target: 'ws://localhost:8000', ws: true },
        '/automation': 'http://localhost:8001'
      }
    }
    ```
  - Create systemd service file `cea-frontend-dev.service`:
    ```ini
    [Unit]
    Description=CEA Frontend Dev Server
    After=network.target

    [Service]
    Type=simple
    User=antoine
    WorkingDirectory=/home/antoine/ProjectCEA-ui/Infrastructure/frontend
    ExecStart=/usr/bin/npm run dev
    Restart=on-failure
    Environment=NODE_ENV=development

    [Install]
    WantedBy=multi-user.target
    ```
  - Install and start the service:
    ```bash
    sudo cp cea-frontend-dev.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable cea-frontend-dev
    sudo systemctl start cea-frontend-dev
    ```
  - Verify: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3002` → 200
  - Verify prod still works: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3001` → 200

  **Must NOT do**:
  - Modify the production frontend service
  - Change any production configuration
  - Use port 3001 (that's production)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`

  **Parallelization**:
  - Wave 7 — parallel with Task 10
  - **Blocked By**: Tasks 8, 9
  - **Blocks**: None

  **References**:
  - `Infrastructure/frontend/vite.config.ts` — dev server config
  - Production service at `/etc/systemd/system/cea-frontend.service` — DO NOT TOUCH

  **Acceptance Criteria**:
  - [ ] Vite dev server listens on port 3002
  - [ ] Systemd service `cea-frontend-dev` is active and running
  - [ ] `curl http://localhost:3002` returns 200
  - [ ] `curl http://localhost:3001` still returns 200 (prod unaffected)
  - [ ] API proxy works (sensor data loads via WebSocket)
  - [ ] Both servers accessible from network (host: '0.0.0.0')

  **Commit**: `ops(frontend): deploy dev server on port 3002 with systemd service`

---

## Commit Strategy

| After Task | Message | Key Files |
|------------|---------|-----------|
| 1 ✅ | `chore(frontend): reset branch to main baseline` | All |
| 2 | `feat(frontend): migrate Tailwind CSS 3 to 4` | Config files, CSS |
| 3 | `feat(frontend): add self-hosted JetBrains Mono and Inter fonts` | public/fonts/, index.css, index.html |
| 4 | `feat(frontend): install shadcn/ui with production-density overrides` | components/ui/, components.json |
| 5 | `feat(frontend): convert all hardcoded colors to semantic CSS tokens` | All .tsx files, index.css |
| 6 | `feat(frontend): add 6 switchable dark theme palettes` | themes.css, index.css, useTheme.ts |
| 7 | `feat(frontend): add dev-only theme switcher dropdown` | ThemeSwitcher.tsx, App.tsx |
| 8 | `feat(frontend): apply JetBrains Mono + Inter typography` | Component .tsx files |
| 9 | `feat(frontend): migrate toasts to Sonner, add code splitting` | App.tsx, ToastContext removal |
| 10 | `test(frontend): add theme tests and visual regression` | vitest.config.ts, tests/ |
| 11 | `ops(frontend): deploy dev server on port 3002` | vite.config.ts, systemd service |

---

## Success Criteria

### Verification Commands
```bash
# Build must pass
cd /home/antoine/ProjectCEA-ui/Infrastructure/frontend && npm run build

# Zero hardcoded colors
grep -rn "text-gray-\|bg-gray-\|border-gray-\|text-white\|text-cyan-\|text-amber-\|text-green-\|text-red-\|text-yellow-" src/ --include="*.tsx" | grep -v themes.css | wc -l
# Expected: 0

# Zero performance violations
grep -rn "backdrop-blur\|scale(" src/ --include="*.tsx" | wc -l
# Expected: 0

# Tests pass
npm test

# Dev server responds
curl -s -o /dev/null -w "%{http_code}" http://localhost:3002
# Expected: 200

# Prod server unaffected
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001
# Expected: 200
```

### Final Checklist
- [ ] All 6 themes render correctly on Dashboard, ZoneConfig, DeviceConfig
- [ ] Production layout density identical to main branch
- [ ] All 12 UI elements functional (Pi monitoring, sensors, controls, charts, toasts)
- [ ] JetBrains Mono renders for data, Inter for UI text
- [ ] Theme switcher works in dev, hidden in prod build
- [ ] Zero hardcoded color classes (verified by grep)
- [ ] Zero `backdrop-blur` or `scale()` transforms
- [ ] Dev server on port 3002, prod on port 3001, both working
- [ ] All tests pass
- [ ] Bundle size reasonable for RPi5
