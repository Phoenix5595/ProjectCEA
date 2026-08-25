# Learnings — monitoring-pipeline-radical-optimization

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

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
