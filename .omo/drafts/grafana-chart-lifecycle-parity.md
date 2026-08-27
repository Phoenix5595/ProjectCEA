---
slug: grafana-chart-lifecycle-parity
status: plan-complete
intent: clear
review_required: false
pending-action: hand off .omo/plans/grafana-chart-lifecycle-parity.md for optional high-accuracy review or separate $start-work execution
approach: Reproduce both defects first; isolate chart measurement from uPlot intrinsic size like Grafana; make requested and fulfilled time ranges explicit; retain last-good data through loading/errors; connect a one-point-per-pixel width budget to the next range query without querying on resize; verify the shared behavior in targeted Firefox and existing Chromium coverage without changing ProjectCEA styling.
---

# Draft: grafana-chart-lifecycle-parity

## Components (topology ledger)

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Snap-half to maximize is a pure layout change: both uPlot charts retain data, scales, visibility, and instance continuity while adopting the final width. | planned | `Infrastructure/frontend/src/features/monitoring/charts/UPlotChart.tsx`; `Infrastructure/frontend/src/features/monitoring/styles/monitoring.css`; Grafana `VizPanelRenderer.tsx` absolute-wrapper pattern |
| C2 | Every 3h→1h range change issues one latest-wins range load and displays the exact fulfilled 1h viewport, with one hour filling the chart. | planned | `Infrastructure/frontend/src/features/monitoring/state/monitoringStore.ts`; `charts/MonitoringChartFeed.ts`; `charts/UPlotChart.tsx` |
| C3 | The next range query uses the latest panel width at exactly one point per CSS pixel; resize alone never reloads history. | planned | `data/pointBudget.ts`; `charts/useRequestBudgetReporter.ts`; `state/monitoringStore.ts`; Grafana `SceneQueryRunner.ts` |
| C4 | TDD regressions prove both behaviors in focused tests and targeted Playwright Firefox, while existing Chromium monitoring behavior remains intact. | planned | `charts/__tests__/UPlotChart.test.tsx`; `state/__tests__/monitoringStore.test.ts`; `tests/monitoring/`; `playwright.monitoring.config.ts` |

## Open assumptions (announced defaults)

None. The user explicitly requested questions; all product and test-strategy forks were resolved.

## Findings (cited)

- The page crosses from one column to `340px + minmax(0, 1fr)` at 1100px, so maximizing a snapped window can make the chart column narrower despite increasing total window width (`Infrastructure/frontend/src/features/monitoring/styles/monitoring.css:319-348`).
- The observed element contains uPlot's fixed-width root, so child intrinsic width can influence the dimension being measured. Grafana deliberately uses a relative outer wrapper plus a 100%-sized absolute measured wrapper because an in-flow measured panel otherwise cannot adopt a smaller width during browser/menu layout changes ([Grafana Scenes `VizPanelRenderer.tsx`](https://github.com/grafana/scenes/blob/13a24b1f85e150f6a03426b68b5eb2dda4af6745/packages/scenes/src/components/VizPanel/VizPanelRenderer.tsx#L535-L560)).
- uPlot 1.6.32 requires numeric dimensions; its supported responsive path is `ResizeObserver`/window resize followed by `setSize({width,height})`. `setSize` preserves data and current scales; CSS transforms are unsupported for responsive sizing ([uPlot resize demo](https://github.com/leeoniya/uPlot/blob/e995b061e9fc5476a6d862cd2fb2ebc7452ca012/demos/resize.html); [uPlot API](https://github.com/leeoniya/uPlot/blob/e995b061e9fc5476a6d862cd2fb2ebc7452ca012/dist/uPlot.d.ts#L108); [issue 1056](https://github.com/leeoniya/uPlot/issues/1056)).
- The current chart constructs immediately using a `600x300` fallback, observes the same element that contains uPlot, and calls `setSize` on positive ResizeObserver measurements (`UPlotChart.tsx:133-201`; `chartSizing.ts:6-9`). The prior zero-size-only regression therefore did not test intrinsic-width feedback or the 1100px layout transition.
- The 3h→1h failure seam is source-grounded: requested range state emits before the asynchronous response; feed subscribers can therefore align/publish old history under the new range, while `UPlotChart.updateData()` keys its one-time scale correction to range identity and later uses `setData(..., false)`. The plan removes this ordering ambiguity by publishing an explicit fulfilled transaction/revision only with the settled data (`monitoringStore.ts:120-127,219-260`; `MonitoringChartFeed.ts:70-80,110-124`; `UPlotChart.tsx:203-224`).
- Grafana treats a time-range change as a new query transaction, passes the selected range into the request, and renders panels using the query result's time range; unchanged raw bounds do not requery ([SceneTimeRange](https://github.com/grafana/scenes/blob/13a24b1f85e150f6a03426b68b5eb2dda4af6745/packages/scenes/src/core/SceneTimeRange.tsx#L142-L174); [SceneQueryRunner](https://github.com/grafana/scenes/blob/13a24b1f85e150f6a03426b68b5eb2dda4af6745/packages/scenes/src/querying/SceneQueryRunner.ts#L579-L595)).
- Grafana records positive panel width for the next query but does not requery on ordinary resize; the width becomes `maxDataPoints` only on a subsequent query when width-based resolution is enabled (`SceneQueryRunner.ts:553-573,607-613`).
- ProjectCEA already has a width budget, API `max_points`, supersession/abort logic, and tests, but the chart callback is not wired by either monitoring page. The existing four-points-per-pixel rule conflicts with the user's selected exact Grafana density and must become one point per pixel (`pointBudget.ts:1-36`; `monitoringApi.ts:27-39,66-78`; `monitoringStore.ts:129-136`; page UPlotChart call sites).
- Browser tests currently use local Chromium only. The user reproduces on Firefox/Windows and selected targeted local Playwright Firefox coverage for these regressions rather than a Windows browser-service dependency (`playwright.monitoring.config.ts:11-45`).

## Decisions (with rationale)

- Intent is CLEAR because the user specified the failures, requested questions, and resolved the surviving behavior choices.
- Classification is Architecture: the requested lifecycle parity spans responsive layout, chart adapter state, feed/store query transactions, width-derived API budgets, and cross-browser regression infrastructure.
- Preserve all current ProjectCEA visual styling; copy Grafana behavior and lifecycle boundaries, not Grafana chrome or CSS appearance.
- Include Grafana-like lifecycle/query continuity and resolution parity: stable chart instance across resize, exact fulfilled range, previous data retained while loading, latest request wins, last-good data retained on error, and resize does not query.
- Use exactly one requested point per CSS pixel, selected by the user, replacing the dormant four-points-per-pixel rule.
- Exclude in-app panel view/expand mode and synchronized cross-chart cursor/tooltips; the user selected neither.
- Use TDD: failing focused and browser regressions precede implementation.
- Add targeted local Playwright Firefox coverage for resize/range regressions only; retain Chromium as the complete suite and do not add a Windows cloud-browser service.
- No production deployment or production endpoint access is part of this plan.

## Scope IN

- Shared Flower and Vegetation uPlot lifecycle, responsive measurement boundary, positive-size mount/resize behavior, and state preservation.
- Requested/fulfilled range transaction semantics, exact x-axis bounds after fresh data, loading/error continuity, cancellation, and URL/preset behavior preservation.
- One-point-per-pixel width budgeting wired to the next sensor/control range request, with no range request caused solely by resize.
- Focused Vitest tests, shared fixture behavior where needed, targeted Firefox Playwright regressions, existing Chromium monitoring regression checks, typecheck, and production build.

## Scope OUT (Must NOT have)

- No ProjectCEA visual redesign, Grafana CSS/chrome copy, iframe, Grafana runtime dependency, or replacement of uPlot.
- No panel expand/view mode and no synchronized cross-chart cursor/tooltips.
- No backend/database schema change, production deploy, production read/write, Redis access, hardware access, or service restart.
- No query on every resize, no clearing last-good data during range loading, no stale response overwrite, and no remount for ordinary size/range/data changes.
- No Windows browser-cloud provider or credentials.

## Open questions

None. Objective, scope, parity boundary, browser target, point density, and TDD strategy are resolved.

## Approval gate

status: approved
approved-by-user: true
approach: First add failing unit and Firefox browser reproductions for the 1100px snap/maximize transition and stale 3h→1h scale. Then isolate panel measurement from uPlot intrinsic dimensions using Grafana's wrapper principle and positive-size rendering; model requested versus fulfilled range/bounds so fresh query data atomically updates data and viewport while preserving old data through loading/errors; wire the latest positive panel width as a one-point-per-pixel budget for the next range query only; finish with focused Firefox and full existing Chromium/TypeScript/build verification.
next-action: The decision-complete plan is written and structurally checked. Offer the user separate `$start-work grafana-chart-lifecycle-parity` execution or the optional dual high-accuracy plan review; do not implement in this planning session.
plan-path: .omo/plans/grafana-chart-lifecycle-parity.md
