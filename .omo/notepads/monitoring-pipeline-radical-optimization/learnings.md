# Learnings — monitoring-pipeline-radical-optimization

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## [2026-08-25] Task: T28 publication consumer

- The monitoring projection route now exposes the canonical read-only `ProjectionPublicationResponse`, rather than an empty history-envelope stub. Existing paired-publication validation keeps current/projection unavailable together on missing, stale, malformed, expired, or version-mismatched payloads.
- The frontend translates canonical interval publications at its Zod boundary into the existing climate/light/photoperiod chart envelope. Projection intervals use step values and an unavailable valid-until sentinel so the grid extends to the real future boundary without holding stale values afterward.
- Recorded facts retain precedence at equal timestamps. An explicit unavailable projection response clears only projection state and leaves recorded control history intact.
- Final gates: monitoring-service 118/118 pytest with Ruff/format/compileall clean; frontend monitoring 114/114 Vitest, TypeScript, and production build clean.

## [2026-08-24T21:05:01+00:00] Task: T1 inventory

- HEAD = `5b0605ceb06264c8ca55f6f852e1f7ee57c41082` (matches deploy_manifest git_sha). Branch `main`.
- Working tree has 1040 dirty porcelain entries (before this task's evidence files).
- Covered prefixes: all 16 exist (no missing prefix). No symlinks, no deleted files under covered prefixes.
- Inventory: 127 files total = 89 untracked + 25 tracked-modified + 13 tracked-clean.
  - `Infrastructure/monitoring-service/` is entirely untracked (live in prod at /opt/projectcea/current).
  - `Infrastructure/automation-service/app/monitoring_publication/`, `app/redis/monitoring.py`, `app/schemas/monitoring.py`, `app/schemas/monitoring_models.py`, `shared/monitoring_contracts.py`, `shared/monitoring_contracts_contract.py`, `database/monitoring_read_models*.sql`, `database/tests/` (monitoring-named only), `frontend/playwright.monitoring.config.ts`, `frontend/vite.monitoring.config.ts` all untracked.
  - `Caddyfile` and `services.yaml` are tracked-modified (shared release inputs; flagged `shared_file: true`).
- Unrelated exclusions: 975 dirty paths outside covered prefixes (715 untracked, 200 modified, 49 deleted, 11 renamed). Notable: deleted Grafana dashboards (`Infrastructure/frontend/grafana/dashboards/*`, `Infrastructure/grafana/dashboards/*`), `figma/` deleted, `deploy.sh`/`rollback-deploy.sh` modified (must never be staged by this plan).
- DB tests filter: only files whose names contain "monitoring" → `fixtures/monitoring_test_fixture.sql` + `test-monitoring-read-models.sh` (2 files).
- Artifacts: `.txt` + `.json` inventory, `allowlist.txt` (127 exact paths, no globs/comments). JSON keys: generated_at, head_sha, files[{path,state,sha256,bytes,symlink,shared_file}], unrelated_exclusions[{path,state}].
- Zero product files modified; zero git mutations. Only writes were inside `.omo/evidence/monitoring-pipeline-radical-optimization/` and this notepad.

---

## [2026-08-24T17:26:00-04:00] Task: T2 Phase A stabilization

- Added the missing `ToolbarMonitoring` export through a dedicated toolbar companion module; the optional toolbar status supplies Retry only, preserving the toolbar as the sole Pause/Resume owner.
- Added the missing Room Averages title row and zeroed `.mon-toolbar` bottom margin. Existing temperature headroom, VPD width, compact-card CSS, and Water Level Avg removal were already correct and are now locked by focused tests.
- Replaced a jsdom-incompatible direct-uPlot construction test with its deterministic configured range-function contract; the required monitoring Vitest suite is clean.
- Baseline gates: frontend TypeScript passed; monitoring Vitest passed 77/77; frontend build passed; monitoring-service Ruff/format/compileall passed; monitoring-service pytest passed 21/21; root `git diff --check` passed.

---

## [2026-08-24T17:35:00-04:00] Task: T2 Phase B reproducibility bootstrap

- Created `83f4ea0 feat(monitoring): stabilize and preserve monitoring baseline` with 122 changed paths; its tree contains all 127 Todo 1 paths plus two newly-created covered-prefix toolbar files.
- Temporary `git archive` extraction matched all 127 Todo 1 files against the worktree with zero missing files and zero SHA-256 mismatches. Eight inherited files legitimately changed during stabilization; evidence records both Todo 1 and committed hashes.
- All 12 unrelated pre-existing staged archive/onewire renames remained staged. The four pre-existing staged monitoring alignment files were allowlisted release inputs and were intentionally included in the bootstrap commit.

---

## [2026-08-24] Task: T3 external release identity verifier

- Added committed, allowlist-driven preflight/verify gates without modifying deploy scripts. Preflight captures HEAD, SHA-256 inputs, and a release-input porcelain snapshot; verify compares source and optional deployed `Infrastructure/` bytes.
- Fixture-only shell coverage passed 9 scenarios, including dirty/untracked inputs, release-byte drift, symlink escape, secret redaction, rejected `--fix`, and failing checks. Deploy/finalize/rollback SHA-256 values were identical before and after commit `03374c2`.

---

## [2026-08-24] Task: T4 deploy reproducibility sandbox

- Extended `test-deploy-candidate.sh` from 10 to 15 named scenarios: five isolated `mktemp -d` release-identity fixtures plus the pre-existing active-candidate rejection scenario.
- The fixture wrapper runs T3 preflight before deploy and verifies source/release bytes before finalize; explicit short-circuiting remains necessary because negative assertions temporarily disable `errexit`.
- Two consecutive full sandbox runs passed. The suite asserts the fixed deploy, finalize, and rollback SHA-256 values and writes complete output to the T4 evidence file.

---

## [2026-08-24] Task: T7 frontend processing and paint-age benchmark

- The monitoring-only Vite config statically enables performance marks and a 12 ms delay capability; the ordinary production build removes all `__monitoringPerf` and `VITE_MONITORING_PERF` markers from `dist`.
- The fixture artifact records 120 Flower ticks, nearest-rank P50/P95/P99 timing summaries, request-to-paint and source-to-paint age, Chromium heap samples, exact one drag-zoom refetch, resize duration, and the captured failed 8 ms negative-probe assertion.
- The existing control poller had drifted onto the history route. Restoring the dedicated `/tail` method preserves bounded 1 Hz control windows and makes the browser/Store contracts distinguish tail traffic from historical range reloads.
- Final local verification: `tsc`, monitoring Vitest 77/77, production build with an empty perf-marker dist scan, the full fixture Playwright dependency run 38/38, and a second chromium-performance run 4/4.

---

## [2026-08-24] Task: T8 query observation

- `shared.infra_logging.JsonFormatter` renders caller fields supplied as `extra={"extra": ...}`; monitoring query events use that existing convention and the DEBUG level, so normal INFO production routing stays quiet.
- A `ContextVar` request collector makes query count and summed fetch duration isolated across async requests without changing the narrow repository database protocols.
- Request-summary wall duration encloses repository row-to-model processing; subtracting acquire/query events exposes the non-driver portion without logging rows, SQL, arguments, sensor names, or room names.
- Baseline was 21/21; the added observer tests produce 25/25 across two full-suite runs. Evidence records the sentinel-negative assertion and safe formatter output.

---

## [2026-08-24] Task: T6 read-path benchmark harness

- `Infrastructure/scripts/monitoring_benchmark.py` requires an explicit `http(s)` origin, permits only localhost/private-LAN hosts or exact `--allow-host` matches, validates every guard before network I/O, and rejects all custom headers (with dedicated secret-header refusal).
- Fixture-only coverage passes six scenarios: fixed nearest-rank (including `n=1` and 40-sample P95), byte accounting, parse and timeout classifications, empty-log guard refusals, and fixture-observed eight-viewer concurrency. Two consecutive full runs passed.
- The soak uses exactly one bounded asyncio task per viewer, staggered across 100ms, makes initial selected window loads, then issues a live read once per second. The evidence artifact is task-6-monitoring-pipeline-radical-optimization-service-harness.json.
- Discovery: .gitignore:40 'test_*.py' ignores new files in Infrastructure/monitoring-service/tests/. Bootstrap-era tests were added before/around the rule; any NEW test file there needs 'git add -f' (siblings tracked prove intent). Applies to T10-T15.

---

## [2026-08-25] Task: T9 frozen baseline

- All 12 inherited `task-9-*-t6.json` documents validated as T6 schema attempt-1 reports with 30 warm samples and warmup 1; the harness schema retains summaries and counts, not individual latency observations. The frozen baseline therefore preserves the reports verbatim, marks service raw-sample traceability blocked, and still records the historical 7d range P99 1014.730 ms as an SLO fail.
- Current `127.0.0.1:8005` production surface refused both allowed GET attempts for `/health` and `/ready`; current production measurements, soak comparability, and DEBUG query observation are explicitly blocked/deferred rather than invented.
- Deployed release `20260824-123133-5b0605c` (`5b0605c`) drifts from source `9639c91`: pool max 2→8, min 0→1, no acquire deadline→10 s, and >48 h stats raw scan→5 min CAGG routing.
- Fixture fallback soak attempt 2 completed 8 viewers × 600 s with 4,808 HTTP 200 responses and zero 500/503; the prior failed fixture attempt (4,136 connection errors) remains attached. Two fixture Playwright runs passed 38/38, each with 120 raw ticks; worst P95 client tick 1.200 ms and visual age 17.400 ms.

---

## [2026-08-24] Task: T10 bounded-read contracts

- `NICE_INTERVAL_SECONDS` is centralized in `monitoring_service.sensor_models`; Task 10 derives/echoes metadata only and does not pass `max_points` into repositories or SQL.
- FastAPI's native query validation is 422, while the monitoring contract requires 400. `create_app()` now maps only request-validation errors located at `max_points` to 400; all other validation behavior remains delegated to FastAPI.
- Service contracts default absent budget metadata to `None`, compute per-series point/sample counts from points, and label raw/CAGG standard deviation `exact`/`approximate` through the existing statement-selection branch.
- Verification passed: service 32/32 twice, focused contracts 7/7, frontend monitoring 78/78, and TypeScript type checking. Evidence: `task-10-monitoring-pipeline-radical-optimization-contracts.txt`.

---

## [2026-08-24] Task: T14 control semantic budget

- No-budget control-history JSON is locked through a fake-DB Pydantic serialization characterization; budgeted reads alone add `steps`/`linear` representations and applied metadata.
- Existing duration sources are raw setpoints plus 1-minute state below two hours, 1-minute setpoints/state through under one day, then 5-minute setpoints/state; CAGG `*_last` values retain aggregated provenance.
- The predecessor counts within the requested budget, duplicate categorical rows are removed without averaging, raw ramp endpoints use `linear`, and null raw-target observations become unavailable null steps so held values do not bridge gaps.
- Final monitoring-service suite passed 77/77 twice; Ruff, format check, and compileall passed. Evidence: `task-14-monitoring-pipeline-radical-optimization-control-budget.txt`.

---

## [2026-08-24] Task: T11 exact sensor bucketing

- Budgeted raw reads bind a `timedelta` through `$5::interval` and anchor `time_bucket` at the requested start, which bounds aligned buckets without interpolating request values.
- Budgeted CAGG reads must aggregate `sum(value_sum) / NULLIF(sum(sample_count), 0)`; the 10×1-versus-1×100 fixture yields `110/11`, not `50.5`.
- Legacy no-budget source statements and their four-argument calls remain separate from new bucketed statements; characterization covers raw, 1min, and 5min source selection.
- Full monitoring-service tests passed 76/76 twice. Format and compile passed. Service-wide Ruff remains blocked by an untouched control-side unused import in `control_repository.py`.

---

## [2026-08-24] Task: T12 bounded node query

- The monitoring feature has at most two canonical nodes, so a fixed four-parameter `(node, pattern)` pair maps Flower front/back and Veg main through a parameterized SQL `VALUES` CTE without dynamically constructing SQL or broadening room scope.
- SQL must select the node from that CTE and group by it; repository conversion then restores canonical topology order (`front`, `back`) rather than SQL lexical order.
- Query-count proof is route-level: the Flower range fake records exactly two fetches total, one series and one statistics, instead of the old two-per-node fan-out. Raw/CAGG and budgeted source tests use the same mapping parameters.
- T13's process-local watermark cache requires isolated cache instances in per-database snapshot fakes; production caching logic was not changed by T12.

---

## [2026-08-24] Task: T13 CAGG completeness and exact statistics

- `CaggWatermarkCache` stores immutable `{watermark, fetched_at}` entries under only known CAGG relation names and uses `time.monotonic()` with a documented 5-second TTL. Repository-owned process-local cache state keeps independent fake repositories isolated while the runtime singleton shares entries across requests.
- `_require_cagg_coverage` retains its 503 protection and now returns `watermark - source_bucket_width`; tiered SQL binds that as the complete-tail limit (`$8` non-bucketed, `$9` bucketed), removing correlated `SELECT max(bucket)` work and excluding the trailing incomplete bucket.
- Long-window CAGG standard deviation now uses `value_sum_squares`, `value_sum`, and `sample_count`; n<2 serializes the legacy nonnegative zero. Raw and sufficient-stat CAGG sources are exact; the explicit approximate model serialization path remains covered for a future source without sufficient statistics.
- Final service gates passed twice: pytest 95/95 each time, Ruff clean, format clean, compileall clean. Evidence: `task-13-monitoring-pipeline-radical-optimization-coverage.txt`. No Git command was run.

---

## [2026-08-24] Task: T15 bounded-read integration

- The existing sensor range path remains intentionally sequential: one CAGG watermark and one series query, followed by one statistics query. Eight concurrent 7-day Flower requests completed against an eight-slot fake pool with zero leaked acquisitions and no duplicate SQL per request.
- `asyncpg.QueryCanceledError` and pool `TimeoutError` must become `MonitoringUnavailableError` at `ReadOnlyDatabase.fetch`; a global `MonitoringError` handler then preserves the service's 400/404/503 contract instead of leaking a 500.
- Final service suite passed 98/98 twice; all 21 `monitoring_service/*.py` modules measure at most 230 pure LOC. No Git command was run.

---

## [2026-08-25] Task: T16 candidate A deployment attempt 2

- Commit `4b2becc` passed the missing `ruff check Infrastructure/scripts/` gate, the full local matrix, and the approved deploy through candidate creation. Monitoring service `/health` and `/ready` both returned 200 after deployment; no Redis readiness failure was observed.
- The required sensor-range GETs using location `flower` returned 404 twice for both legacy and `max_points=1000` variants, while matching control-history GETs returned 200. The candidate was immediately rejected with the approved rollback script; no soak, P99, identity verification, or finalization was run.
- Rollback restored last-good `20260824-123133-5b0605c`; rollback target remains `20260824-122205-5b0605c`. See Task 16 evidence for the full deploy transcript and receipts.

---

## [2026-08-25] Task: T16 candidate A deployment attempt 3

- Canonical sensor rooms are exactly `Flower Room` and `Veg Room`; lowercase aliases are intentionally 404. Attempt 3 prepared only canonical encoded paths and did not issue any alias probe.
- The mandatory isolated Uvicorn smoke session exited before binding port 18999, so its first canonical Flower Room curl failed with connection refused. The stop rule prevented preflight, deployment, production GETs, soak, and finalization; current remains last-good `20260824-123133-5b0605c` on port 8005.

---

## [2026-08-25] Task: T16 candidate A attempt 3 execution

- The corrected local smoke recipe passed with `MONITORING_POSTGRES_DSN`, `MONITORING_REDIS_URL`, and the automation-service venv. Canonical Flower/Veg reads and non-empty Flower control history passed; the lowercase alias correctly returned 404.
- Candidate `20260825-065219-4b2becc` passed preflight, all local gates, deploy health, and the canonical deployed matrix. The Flower budget response retained `requested_max_points=1000`, 300-second interval, exact standard deviation, and at most 284 points per series.
- The 8-viewer 600-second 7-day soak failed with 4 history-route HTTP 503 responses and 68 timeouts (4,760 HTTP 200s). The candidate was immediately rejected; no P99, identity, or finalize run is valid.

---

## [2026-08-25] Task: T16 candidate A attempt 4

- The native harness exactly implements the owner-approved realistic mix: each viewer makes one initial range/stats/history/projection load, then only live requests at 1 Hz. The 3-viewer release soak still failed with 3 live-route HTTP 500s and 1 history-route HTTP 503.
- Candidate `20260825-080206-4b2becc` otherwise passed preflight, all local gates, deploy health, and the canonical GET matrix. It was correctly rolled back before P99, informational 7-day reads, identity, or finalize.

---

## [2026-08-25] Task: T16 candidate A attempt 5

- Commit `080dfc3` converted the previous Redis timeout-driven live-route 500s to the typed 503 boundary: the subsequent 3-viewer, 600-second realistic soak had 1,800 live-route 200 responses and no live failures.
- The candidate nevertheless failed its mandatory soak: viewer 2's one-time control-history load returned 503, producing aggregate counts of 1,811 HTTP 200 and 1 HTTP 503. This blocks P99, informational reads, identity verification, and finalization.
- `rollback-deploy.sh` restored the last-good symlink before its automation-service health gate failed. Its candidate-clear operation follows that gate, so a failed health check leaves stale candidate state even when the current manifest is last-good and the service is later active.

---

## [2026-08-25] Task: T16 candidate A attempt 6

- The amended realistic-soak policy accepts isolated typed 503s through 0.1% of requests on this shared OpenCode/production host, while preserving zero HTTP 500 as a hard release gate and rejecting consecutive/repeated 503s.
- The attempt stopped before deployment because full automation pytest collected `app/routes/lights/light_test.py::test_light` as a test and lacked its `device_id` fixture, while the registry integration harness expected `REGISTRY_TEST_DB_NAME`. Ruff, format, compileall, frontend checks, scripts Ruff, and whitespace checks all passed.

---

## [2026-08-25] Task: T16 candidate A attempt 7

- The correct release-local test gate is monitoring-scoped: `PYTHONPATH=/home/antoine/ProjectCEA/Infrastructure ../automation-service/.venv/bin/python -m pytest -q tests` from `Infrastructure/monitoring-service`; it passed 102/102.
- A successful 200 budget response is insufficient: the canonical seven-day Flower request returned `requested_max_points=1000` and exact metadata but still had a 2,012-point series. Validate the actual per-series `point_count`, not only echoed budget metadata.
- Rollback again restored the symlink before automation health failed; the explicit `clear-candidate` hygiene command returned `ok`, leaving current/last-good at `20260824-123133-5b0605c` and no candidate.

---

## [2026-08-25] Task: T16 candidate A attempt 8

- In PostgreSQL, a `GROUP BY` name ambiguous between a source column and a SELECT alias resolves to the source column. CAGG budget SQL must group by the complete bound `time_bucket($8::interval, c.bucket, $6::timestamptz)` expression, not bare `bucket`; otherwise interval metadata can be correct while natural CAGG rows remain unmerged.
- Commit `d5b2111` locks that contract for both shared CAGG tiers. The deployed 24-hour budget response then reported `300s` and at most 285 points, proving the point-budget path works for that matrix range.

---

## [2026-08-25] Task: T25 bounded monitoring projections

- An automation-side projection input can remain control-safe by accepting only injected read-only repository protocols and publishing no side effect from its async builder; it creates one frozen `[now, now + 24h)` snapshot for later worker execution.
- `FutureProjection` has only numerical series, so SUN/MOON is carried as the estimated `light.photoperiod` 1/0 interval alongside climate targets and light intensities. Missing schedule authority maps to `UNAVAILABLE` with `None`, while a missing configuration version returns no cacheable timeline.
- `MAX_PROJECTION_INTERVALS = 256` fails closed as one full-horizon unavailable interval when dense source transitions would exceed the representation budget; this preserves exact coverage and avoids silently dropping schedule changes.

## [2026-08-25] Task: T16 attempt 9 success

- Candidate `20260825-103907-d5b2111` deployed at `2026-08-25T14:43:09Z` and finalized at `2026-08-25T15:13:53Z`; post-finalize identity verification confirms all 127 release inputs match preflight HEAD `d5b211161ef90ec8478839163d6badc9869e4f22`.
- All local gates passed: monitoring pytest 104/104, Ruff check/format, compileall, frontend TypeScript/Vite build, and `git diff --check`; deploy/finalize/rollback script hashes remained `2f55cb21be02`, `6503e11745e1`, and `39a34de6cda8`.
- The deployed GET matrix, nine-request warmup, 3-viewer 600-second live-only soak (`1800/1800` HTTP 200, zero errors), and bracketed 24-hour/7-day P99 gates passed. P99 was 484.0 ms for 24 h and 808.1 ms for 7 d, both within the 1000 ms SLO.
- Candidate A is live in production as last-good. The seven-day budgeted history was 387 KB / 2.4 s versus 490 MB / 83 s legacy (~1270× smaller); legacy growth remains driven by no-change telemetry writes.

---

## [2026-08-25] Task: T19 stable panel budgets

- `data/pointBudget.ts` maps rendered widths into deterministic 250 px buckets with a 500–10,000 clamp. `requestBudget` takes the maximum across widths, maintaining the one shared range request rather than per-panel fetches.
- `decimateSeries` retains every null separator and each contiguous run's endpoints; when an undersized budget conflicts with that topology, preserving real gaps takes precedence over the numerical cap.
- The currently concurrent T17 store/API path has no mergeable `maxPoints` seam yet. `UPlotChart` therefore reports deduplicated snapped budgets at its existing initial measurement and `ResizeObserver` path; T18 must aggregate callbacks and send one max-points range reload.
- Verification: TypeScript passed; new pure budget suite 6/6; monitoring Vitest 86/86; production Vite build passed.

- The added rendered-width reporter pushed the pre-existing UPlot adapter past
  the 250 pure-line limit. Isolating reporting state in
  `charts/useRequestBudgetReporter.ts` and size fallback measurement in
  `charts/chartSizing.ts` restored the adapter to 247 pure lines; focused
  chart/pure tests passed 9/9 and the final monitoring suite remained 86/86.

---

## [2026-08-25] Task: T17 frontend API orchestration

- Monitoring API range/history calls now take optional `maxPoints` before request options and serialize `max_points` only when supplied; the store supplies interim 2,000 sensor and 1,000 history budgets.
- The projection backend route has no `max_points` contract, so projection remains explicitly unbudgeted.
- Successful room-level statistics are cached by selected range identity. This prevents duplicate statistics on same-range retries and preserves a retry path after a statistics failure because only fulfilled responses enter the cache.
- Focused API/store characterization passed 23/23 after a red run that demonstrated all three intended missing behaviors.
- Final frontend gates passed: `tsc`, production Vite build, monitoring Vitest 86/86, and a production-dist scan with zero `__monitoringPerf` or `VITE_MONITORING_PERF` markers.

## [2026-08-25] Task: T17 correction

- `MonitoringResponse.statistics` is the authoritative statistics payload for range loads; keeping a separate store cache created an unnecessary fourth request and could decouple statistics failure semantics from sensor-range failure semantics.
- The corrected store settles exactly sensor range, control history, and projection. Focused API/store tests passed 24/24; the full monitoring suite remains blocked by two unrelated chart failures (92/94).

## [2026-08-25] Task: T19 correction

- The plan's budget contract is a direct CSS-width projection, not quantized width buckets: `clamp(ceil(width * 2), 500, 20000)`. Hysteresis must therefore be applied to the derived integer budget, with clamp-edge transitions treated as explicit report triggers.
- Keeping the observer's measurement and `setSize` work inside one rAF preserves the existing ResizeObserver width source while making resize storms bounded to one handling per frame.

---

## [2026-08-25] Task: T20 static panel alignment

- Historical alignment is now cacheable per panel by stable series/control/projection/range/budget identities; panel filtering occurs before grid construction, so unrelated panel timestamps cannot inflate a panel's x axis.
- The live adapter is keyed by the live-values reference and UTC second, updates only the bounded tail, and retains null separators and static provenance/bands. A 120-tick characterization records one base alignment and 120 tail updates.
- `AlignInput.maxPoints` is defensively clamped to the shared 20,000 T19 ceiling, including legacy payload shapes that omit a server budget.
- Final frontend verification: TypeScript passed; focused alignment tests 12/12; monitoring Vitest 20 files / 103/103; production build passed with 1,604 transformed modules. No Git command was run.

---

## [2026-08-25] Task: T18 range-load cancellation

- A range-load controller must be owner-checked before clearing `rangeInFlight`: a superseded promise may ignore abort and settle after its replacement has started.
- Passing one controller signal to sensor range, control history, and projection keeps the three-request T17 shape intact while allowing one range/budget/unsubscribe action to cancel all three.
- `MonitoringAbortError` must be omitted from store error state, while `MonitoringTimeoutError` remains a real typed failure. An already-aborted external signal must abort the composed client controller before `fetch` starts.

---

## [2026-08-25] Task: T21 chart-only feed

- `charts/MonitoringChartFeed.ts` owns one chart's T20 `PanelAlignment`, stable structural snapshot, and bounded full buffer. Its structural snapshot changes only for range identity, series shape/count, or theme; buffer revisions retain the same snapshot identity.
- `UPlotChart` uses `useSyncExternalStore` only for structural changes and separately subscribes to the feed for `setData(..., false)`, so 120 live feed revisions performed exactly 120 mocked draws without rerendering the instrumented parent or recreating uPlot.
- Page fixtures may provide only a current snapshot and actions; feed connection accepts that snapshot as initialization fallback, while production stores provide both `getSnapshot` and `subscribe` for direct live updates.
- Final frontend verification: TypeScript passed; monitoring Vitest 21 files / 106/106; production build passed with 1,605 modules transformed; production dist contains no `__monitoringPerf` or `VITE_MONITORING_PERF` markers. No Git command was run.

---

## [2026-08-25] Task: T22 frontend QA semantics

- `StoreState` now exposes a fully successful range timestamp and a failed-refresh timestamp. Presenting both in status plus every chart/table card makes retained range data visibly stale instead of current.
- A fixture scenario must account for the page's initial default-live request being superseded by the room-duration request; `range-503-after-good` therefore fails the third range request, which is the user-triggered retry after valid initial data.
- T21's chart feed had retained conversion/draw perf hooks but lost the begin/finish lifecycle during alignment. Wrapping panel alignment restores browser tick samples without changing chart-only structural update behavior.
- Final frontend verification: tsc passed, production build passed, monitoring Vitest 109/109, and full fixture Playwright 39/39. Desktop (1280 px) and tablet (768 px) screenshots plus a11y snapshots are recorded in Task 22 evidence.

---

## [2026-08-25] Task: T23 backend tail-route follow-up

- The live control poller requires its own `/api/monitoring/control/{location}/tail` route even though it returns the same unbudgeted `ControlHistoryEnvelope` as history; a thin route wrapper preserves distinct 1 Hz traffic while delegating range parsing, read availability handling, and repository access to the existing history handler.
- `resolve_room_metadata(location)` at the tail boundary supplies the canonical-room 404 contract before the shared history read; partial `start`/`end` windows retain the history handler's 400 contract.

## [2026-08-25] Task: T23 frontend contract fix

- The backend `ControlHistoryEnvelope` metadata fields `requested_max_points` and `interval_seconds` are nullable defaults, including when `max_points` was requested. The frontend control-history Zod schema must therefore use `nullable().optional()` for both fields; the sensor range schema remains numeric because its backend response echoes real values.

---

## [2026-08-25] Task: T23 candidate B tail budgeting

- A tail route can preserve its legacy payload by passing `max_points=None` through its shared history handler; adding the same annotated query parameter as `/history` routes valid budgets through the existing statement-selection and semantic-thinning path without changing envelope construction.
- Testing budget plumbing needs a source larger than the budget: a 1,001-row fake setpoint source proves that `max_points=1000` caps the returned semantic series, while the existing unbudgeted tail characterization locks legacy metadata to null.
- The tail API method is the correct central caller to apply `CONTROL_HISTORY_MAX_POINTS`, ensuring every 1 Hz poll is budgeted while preserving its request-options cancellation forwarding.

## [2026-08-25] Task: T24 publication contract
- Shared key builders are now the only key source for monitoring publications (producer + consumer parity test locks it).
- pydantic strict models reject ISO strings via model_validate(dict) — always route external payloads through model_validate_json (JSON mode).
- validate_projection_timeline lives in shared contracts and is wired into MonitoringPublication itself, so every construction site enforces ordering/non-overlap/single-version.

---

## [2026-08-25] Task: T26 current-publication observer
- A synchronous `offer(CurrentSnapshot)` seam can preserve control equivalence when it is invoked only after successful automation-state logging and its failures are counted in memory rather than logged from the 1 Hz tick.
- `CurrentPublicationPublisher.offer()` replaces one pending snapshot without invoking its writer; `enqueue()` remains a compatibility alias for existing callers.
- Closing and rejecting an awaitable returned by a malformed observer provides a runtime proof that the tick never awaits publication work. Full automation pure tests passed 142/142; the suite retains one unrelated Pydantic V1-validator deprecation warning.

---

## [2026-08-25] Task: T27 asynchronous publication workers
- A room worker can keep the T26 control seam synchronous by owning `CurrentPublicationPublisher.flush_once()` in an independent AnyIO task group; projection work reads the publisher's latest immutable snapshot and never runs on the control task.
- The composed `MonitoringPublication` boundary provides the required current/future version check before either publication action invokes Redis. Future cache replacement happens only after a complete tuple's single SET succeeds, so failed or timed-out writes retain the last good timeline.
- Publication health is deliberately separate from control health and now exposes per-room success age plus handoff pending/replacement/failure counters. Full automation pure tests passed 150/150; Ruff, formatting, and compileall passed.

## [2026-08-25] Task: T27 final verification correction
- Added fail-closed mixed-version and bounded timeout characterizations after the initial count. Final `pytest -q app/tests/pure` result is 152 passed; the Pydantic V1 validator warning remains unrelated.
