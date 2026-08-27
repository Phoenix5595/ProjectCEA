# monitoring-beta-production — Planning Draft

- intent: clear
- review_required: false
- status: approved
- pending_action: start work or optional high-accuracy review

## Approved outcome

Preserve the committed Grafana-backed Monitoring pages, move the dirty native uPlot implementations into new additive `Monitoring (BETA)` pages inside Flower and Vegetation, repair only genuine remaining UI/runtime defects, deploy the candidate, and then iterate in later work.

## Components ledger

| ID | Component | Outcome | Status | Evidence |
| --- | --- | --- | --- | --- |
| C1 | Room navigation | Existing Grafana `/flower/monitoring` and `/vegetation/monitoring` pages remain unchanged; each room receives a separate additive `Monitoring (BETA)` tab/route | approved | committed-vs-dirty comparison; `Infrastructure/frontend/src/App.tsx:48-56`; `Infrastructure/frontend/src/components/TopRibbon.tsx:35-47` |
| C2 | Monitoring page repairs | Fix only reproducible layout, navigation, interaction, responsive, accessibility, console, request, and runtime defects | approved | `Infrastructure/frontend/src/pages/FlowerMonitoring.tsx`; `Infrastructure/frontend/src/pages/VegetationMonitoring.tsx` |
| C3 | Data boundary | Mock-data value mismatches do not block pre-deploy; production uses existing read-only monitoring APIs backed by the real database, never browser-direct DB access | approved | User decision; `Infrastructure/frontend/src/features/monitoring/api/`; `Infrastructure/caddy/Caddyfile:52-64,101-106` |
| C4 | Candidate deployment | Build, deploy as candidate, run health and read-only beta verification, rollback on failure, finalize only after successful verification | approved | `deploy.sh:144-218,241-256`; `rollback-deploy.sh:74-145` |

## Decisions

- Do not create a unified monitoring route or new sidebar sector.
- Preserve the committed Grafana Monitoring components, current `/flower/monitoring` and `/vegetation/monitoring` URLs, and existing Monitoring tabs unchanged.
- Move the dirty native uPlot page bodies into new Flower and Vegetation beta page components and add separate `Monitoring (BETA)` room tabs/routes.
- Use `/flower/beta-monitoring` and `/vegetation/beta-monitoring`; the non-overlapping prefix preserves the existing `startsWith` tab-matching logic without another shared-navigation change.
- Use regression-first testing for each reproducible non-data defect.
- Treat mock data-value mismatches as non-blocking before deploy.
- Production beta connects through existing read-only monitoring API clients; no direct database access and no control mutation.
- User approval of the brief authorizes the worker to run the candidate deployment after every pre-deploy gate passes.
- Because `deploy.sh` copies the source working tree with `rsync --delete`, execution must assemble and deploy from a dedicated clean candidate worktree; the current dirty workspace is never used as the release source.
- The clean candidate contains only the monitoring feature, its exact backend/automation/API integration seams, the two existing room pages/routes, the BETA labels, required tests, deployment-safety seam, and deployed architecture documentation.
- Production schema/backfill/policy writes are not authorized. The candidate is rolled back if the required read-only monitoring relations or API contracts are unavailable.
- No Grafana redesign, unrelated dependency upgrade, device/control change, schema migration, backfill, or production data mutation.

## Test strategy

Raspberry-Pi-constrained verification: run one frontend production build and one owner-visible Playwright-controlled installed Chrome/Chromium session with `headless: false` for both additive beta pages. Add one focused regression only when a real UI/runtime bug is reproduced. Do not run Playwright test suites, hidden/headless browser sessions, dummy/native database harnesses, full backend/automation suites, repeated build matrices, or exact data assertions. Post-deploy verification reuses one visible GET-only browser/API check followed by finalize or rollback.

## Research ledger

- Current routes: `Infrastructure/frontend/src/App.tsx:48-56`.
- Current room tabs: `Infrastructure/frontend/src/components/TopRibbon.tsx:35-47`.
- Current room pages: `Infrastructure/frontend/src/pages/FlowerMonitoring.tsx`; `Infrastructure/frontend/src/pages/VegetationMonitoring.tsx`.
- Layout route-to-sector behavior: `Infrastructure/frontend/src/components/Layout.tsx:9-16,38-45`.
- SPA/API production routing: `Infrastructure/caddy/Caddyfile:41-64,101-106`.
- Candidate deploy and automatic rollback: `deploy.sh:144-218,241-256`.
- Manual candidate rollback: `rollback-deploy.sh:74-145`.

## Approval receipt

- User approved the corrected approach after explicitly selecting individual room pages, production-like database connectivity through the application boundary, and regression-first testing.
- User re-approved the corrected additive scope: preserve both Grafana Monitoring pages, add `/flower/beta-monitoring` and `/vegetation/beta-monitoring`, and use Raspberry-Pi-minimal verification with no broad test suites.

## Metis receipt

- session: `ses_013db1e98ffesaRiOBVCgr5CEt`
- result: decision-complete without more owner questions
- incorporated gaps:
  - isolate release assembly from the dirty source worktree because `deploy.sh` copies the live tree;
  - specify the exact two `Monitoring (BETA)` labels and preserve existing routes;
  - classify fixture value mismatches as non-blocking while requiring structural/runtime defects to pass;
  - include candidate-state preflight, sandbox deploy verification, explicit finalize and rollback paths;
  - add monitoring-route verification beyond generic service health checks;
  - prohibit production schema writes and rollback if the live read-only contract is absent;
  - make final QA commands and scope allowlists executable and evidence-producing.

## Concision constraint

- The execution plan is limited to four implementation/deployment todos.
- Pre-deploy verification is intentionally minimal for the Raspberry Pi: one build and one browser smoke, with a focused regression only for a reproduced bug.
- No Playwright suite, database harness, broad backend suite, or repeated browser/build matrix.
- Data-value correctness is not a pre-deploy gate; only route/API availability, rendering, console/runtime safety, and read-only behavior block deployment.
- Owner explicitly declined F1-F4 final reviewer gates; Todo 4's read-only production smoke and finalize-or-rollback result are terminal.
- Owner permits Playwright only for a real visible Chrome/Chromium window; hidden/headless/virtual verification is prohibited and must never be used as fallback.

## Revision #4 supersession gate — monitoring-beta-finalization-4

- intent: clear
- review_required: false
- status: awaiting-approval
- pending_action: write `.omo/plans/monitoring-beta-finalization-4.md`
- implementation_authorized: false
- approach: Start a new isolated finalization worktree from candidate commit `3dca3b3b0f9e479a104dd9b9837ef858346f10a8`, preserve the Grafana primary routes, finish the native BETA pages and their complete monitoring dependency closure, apply the explicitly authorized additive monitoring migration under production guards, run the complete monitoring Playwright suite in a real headed browser, and deploy/finalize only through the candidate rollback boundary.

### Locked owner decisions for revision #4

- Keep `/flower/monitoring` and `/vegetation/monitoring` Grafana-backed; native pages stay at `/flower/beta-monitoring` and `/vegetation/beta-monitoring`.
- Authorize supervised additive production monitoring DDL, bounded backfill, and policy activation. Never run `DROP`, `DELETE`, `TRUNCATE`, rewrite existing source history, or use mutating production HTTP methods.
- Use one shared axis per metric family: temperature on the left, all other metric families on the right. Individual sensor/node lines retain distinct canonical colors; each family axis uses its primary line color.
- Use the final resolved control-loop photoperiod through a non-blocking observation-only hook. Tests must prove relay/DFR decisions and the one-second control cadence are unchanged.
- Run the complete monitoring Playwright suite with `headless:false`, one worker, the real accessible X display, and no headless or Xvfb fallback.

### Verified revision #4 findings

- The candidate already contains committed BETA routes/pages (`3dca3b3`), although root plan evidence stops at Todo 1.
- Candidate headed evidence shows cross-origin `localhost:3001` to `localhost:8080` failures and duplicate React keys; pre-deploy QA must use the same-origin production-build fixture preview rather than changing CORS.
- The candidate omits required projection/logger services and every monitoring database artifact while its automation route imports them; import/route/dependency-closure gates must fail before deployment until this is repaired.
- The deployed release manifest is still `20260731-065834-d346ab6`, and the deployed snapshot contains the Grafana pages but no native monitoring stack.
- Current chart rendering discards canonical manifest colors, widths, draw styles, interpolation, and per-series bounds. It also encodes uPlot axis sides backwards (`1` is right; `3` is left), omits the x-axis from the explicit axes list, starts presets at one hour, and ignores `UNKNOWN` photoperiod transitions by carrying the preceding phase across an unrecorded gap.
- Current page setup starts an initial load on subscription and then calls `setLiveRange` in an effect, causing duplicate initial loads; string-only error arrays can create duplicate React keys.
- Prior Playwright evidence can falsely pass when the web server fails and zero suites run because it checks only `stats.unexpected`; revision #4 must compare the listed and executed test IDs and reject top-level errors, zero tests, skips, and nonzero process status.

### Revision #4 scope boundaries

- IN: isolated candidate assembly; monitoring DB/backend/automation closure; exact observation-only photoperiod history; Grafana-manifest rendering parity; BETA route tests; complete local suites; headed visual/accessibility/performance QA; additive migration; candidate deploy/rollback/finalize; allowed architecture/design documentation.
- OUT: immediate native cutover; Grafana dashboard/provisioning changes; another chart library or React wrapper; ranges over seven days; prediction of sensors/device/PID state; destructive SQL; mutating production HTTP; unrelated Ruff cleanup/dependency upgrades; root dirty-worktree cleanup; hidden/headless/Xvfb browser runs.
- Historical photoperiod before the observation logger exists is never fabricated. Unrecorded intervals render as an explicit neutral `UNKNOWN` overlay; future intervals remain projected from saved configuration.
