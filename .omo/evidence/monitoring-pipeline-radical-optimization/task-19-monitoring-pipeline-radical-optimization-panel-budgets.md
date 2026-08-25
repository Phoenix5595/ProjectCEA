# T19 — Stable per-panel point budgets

## Delivered

- Added `data/pointBudget.ts`, a dependency-free pure module with:
  - `panelBudget(widthPx)`: deterministic, monotonic 250 px width buckets,
    bounded from 500 through 10,000 points.
  - `requestBudget(panelWidths)`: the maximum budget across panel widths, so a
    shared range request can serve the widest panel without per-panel fan-out.
  - `decimateSeries(points, budget)`: evenly samples each contiguous non-null
    run, retains first/last values for each run, and preserves every `null`
    separator so real gaps cannot be bridged.
- Exported the helpers from the monitoring data barrel.
- `UPlotChart` now derives `requestBudget([renderedWidth])` both at initial
  measurement and on `ResizeObserver` updates. Its optional
  `onRequestBudgetChange` callback emits only when the snapped budget changes,
  preventing sub-pixel resize churn.
- Added six pure Vitest assertions for width bounds, jitter stability,
  monotonicity, widest-panel aggregation, endpoint preservation, and gap
  preservation.

## T17/T18 seam status

T17's current `MonitoringStore.loadRangeIfChanged` still calls `sensorRange`
without a `maxPoints` parameter, so this task did not edit the T17-owned API or
store orchestration. The chart callback is a deliberately thin measurement
seam; it makes no request and cannot create fan-out.

**TODO-FOR-T18-MERGE:** Collect the two chart callback budgets at the page/container, combine them with `requestBudget`, and pass that one result through T17's `maxPoints` range-request seam; issue a new range load only when the snapped aggregate changes.

## Verification

From `Infrastructure/frontend`:

- `npx tsc --noEmit` — passed.
- `npx vitest run src/features/monitoring/data/__tests__/pointBudget.test.ts` —
  1 file, 6/6 passed.
- `npx vitest run src/features/monitoring` — 19 files, 86/86 passed.
- `npm run build` — passed (`tsc && vite build`, 1,602 modules transformed).

No API, monitoring-store orchestration, backend, dependency, deployment, or
production endpoint changes were made.

## Final size-preserving extraction

- Extracted the callback/ref reporting state to
  `charts/useRequestBudgetReporter.ts` and the element measurement fallback to
  `charts/chartSizing.ts`. `UPlotChart.tsx` remains the composition point but
  is now 247 pure lines, below the 250-line module limit.
- Focused chart plus point-budget Vitest tests passed 9/9 after extraction.
- Re-ran the complete gates after extraction: TypeScript passed, monitoring
  Vitest passed 19 files and 86/86 tests, and the production build passed with
  1,604 transformed modules.
- The language server did not return a fresh `UPlotChart.tsx` diagnostic within
  its 3-second deadline on two attempts; the successful strict TypeScript check
  and focused chart suite provide the final static and behavioral validation.

## Final verification after import/dependency cleanup

- Targeted ESLint completed with zero errors and zero warnings across all T19
  TypeScript/TSX files.
- `npx tsc --noEmit` completed successfully.
- `npx vitest run src/features/monitoring` passed 19 files and 86/86 tests.
- `npm run build` completed successfully with 1,604 transformed modules.
## T19 correction — 2026-08-25

- Corrected `panelBudget` to the plan's exact `clamp(ceil(width * 2), 500, 20000)` contract; invalid/non-positive widths remain at 500 and infinite widths clamp at 20,000.
- Added pure hysteresis: a budget report requires an integer change of at least 10%, except transitions into or out of either clamp edge (500 or 20,000), which always report.
- Retained the existing UPlotChart `ResizeObserver` as the CSS-width source and coalesced its callbacks through one `requestAnimationFrame`; no viewport or device-pixel-ratio reads were introduced.
- Added boundary, monotonicity, hysteresis, and resize-storm tests. Focused result: 2 files, 16 tests passed.
