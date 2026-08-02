
## 2026-08-02 learnings
- Docker not installed; local PostgreSQL 15.18 + TimescaleDB 2.28.3 available via sudo -u postgres.
- Tests using fileURLToPath(new URL(..., import.meta.url)) throw under current Vitest/jsdom.
- Manifest parity fails on table-row units like °C.
- Backend/automation T11 tooling missing entirely.
- Todo 4 now uses the local PostgreSQL Unix socket only for `postgres` administration, then gives child commands a password-authenticated `127.0.0.1` URL for a unique `monitoring_test_<UTC>_<pid>_<random>` database and dedicated disposable role.
- PostgreSQL is 15.18 and the locally available TimescaleDB extension is 2.28.3. The fixture's `by_range(...)` hypertable calls work unchanged; lifecycle compatibility accepts TimescaleDB 2.23+ within major version 2 rather than pinning exactly 2.23.1.
- The harness activates TimescaleDB as the system `postgres` user before loading fixtures as the disposable database owner. EXIT/signal cleanup drops the database with `FORCE`, then drops the role; success, child failure, and child-only URL scoping were verified with no generated databases or roles left behind.
- Both inherited connection variables and internal unsafe URLs are rejected before `psql`: `PGDATABASE=cea_sensors`, `cea_sensors_test`, and non-loopback hosts all fail. The SQL fixture/case guards independently require the `monitoring_test_` namespace.
- `schema-idempotent-and-policy-disabled` runs successfully against the fixture alone on TimescaleDB 2.28.3 and reports zero continuous-aggregate refresh policies; Todo 4 does not need a placeholder `monitoring_read_models.sql`.
- Todo 11 pins the seven shared pytest/tooling dependencies in both services; automation additionally retains its existing `safety==3.2.11` dev tool.
- Debian PEP 668 blocks user-site pip installs on this node, so Todo 11 verification used ignored service-local `.venv` environments without root or `--break-system-packages`.
- Backend `test-monitoring.sh` sources Todo 4's harness and passes its generated URL only to the pytest child; `MONITORING_DOCKER_UNAVAILABLE=1` provides a deterministic unavailable-harness failure probe.
- Scoped `.gitignore` negations expose the backend monitoring test family and the planned automation monitoring tests while leaving the global `test_*.py` safety rule intact.

## 2026-08-02 Todo 1-3 (frontend prep)
- Fixed the `import.meta.url` failure: under Vitest/jsdom `import.meta.url` is not a `file:` URL, so `fileURLToPath(new URL(...))` throws. Replaced with `path.resolve(process.cwd(), ...)` in `designTokens.test.ts`, `dependencyPolicy.test.ts`, and `canonicalManifestParity.test.ts`. Vitest runs from the frontend root, so `process.cwd()` is deterministic. Canonical dashboards resolve as `../iskra_stack/dashboards` (one `..` from `Infrastructure/frontend`).
- Manifest parity: canonical tables render standalone unit rows (`°C`, `%`, ` kPa`, ` ppm`, ` hPa`, ` mm`) that the manifests did not map. Added `units?: string[]` to `TablePanelSpec` in `manifestTypes.ts`, populated it in the flower/veg manifests, and updated the parity test to treat canonical unit rows as mapped. Also fixed the flower `Averages` table to `hasLastUpdate: true` + `Last Update` row (canonical SQL has it), and added `softMin:0/softMax:100` to the DAY/NIGHT overlay series (canonical has those bounds).
- Merged the two parity `it` blocks into one named `maps every canonical Flower and Veg field` so the fixed QA `-t` filter selects it.
- Created `Infrastructure/frontend/DESIGN.md` (tokens, axis contract, overlays, primitives, spacing/type/radius, responsive, motion, accessibility, primitive showcase spec, accepted debt, research log).
- Created `playwright.monitoring.config.ts` (Chromium only, `127.0.0.1:4173`, `reuseExistingServer:false`, webServer runs `npm run build` then `vite preview --config vite.monitoring.config.ts`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`) and a placeholder `tests/monitoring/monitoring.spec.ts`.
- Created `monitoringBrowserHarness.test.ts` (Vitest) verifying harness availability (config files exist) and the exact-origin route guard (`originGuard.ts` rejects ports 8000/8001/8003/8080 and `iskraprojectcea`).
- Verified: designTokens coverage + failure tests pass, dependencyPolicy + testEnvironment pass, canonical parity + harness pass (JSON reporter `success:true`), `tsc --noEmit` exits 0, `npm run build` exits 0, and the monitoring preview server serves fixtures/SPA/Grafana-placeholder with the restrictive CSP and request log on `127.0.0.1:4173`.
- Remaining blockers: full Playwright browser coverage (375/768/1280px, route-guard traffic assertions) is deferred to later todos; the placeholder spec is not yet run. `pytest_jsonreport` import errors in backend/automation tooling smoke tests are pre-existing (Todo 11 scope), unrelated to this frontend work.

## 2026-08-02 Todo 5 monitoring read models
- TimescaleDB 2.28.3 exposes the required adapter inputs through `_timescaledb_catalog.continuous_agg`, `continuous_aggs_hypertable_invalidation_log`, `continuous_aggs_materialization_invalidation_log`, and `hypertable`; watermark conversion uses `_timescaledb_functions.cagg_watermark(integer)` plus `_timescaledb_functions.to_timestamp(bigint)`. The schema guard pins the exact catalog column arrays and accepts only 2.23 through 2.28.x.
- Six `monitoring_` continuous aggregates are materialized-only and created `WITH NO DATA`; applying the definition twice leaves all six empty and creates zero refresh policies. Existing effective-setpoint rows retain null ingest metadata, while rows inserted after activation of the column defaults receive sequence IDs and ingestion timestamps.
- A unique partial ingest-ID index is not valid on a Timescale hypertable unless it includes the partition timestamp. The required partial ingest-ID index is therefore non-unique; the dedicated sequence still provides future-row identity without rewriting history.
- `monitoring_cagg_watermark` emits one row per CAGG when no invalidations exist and one row per pending materialization/raw-hypertable invalidation otherwise. The fixture produces six pending raw invalidation rows before first refresh, which proves the adapter exposes both watermark and pending-range data.
- Policy activation is isolated in `monitoring_read_models_activate_policies.sql`. It fails without six explicit supervised markers and checks each marker against both the CAGG watermark and latest source observation before adding 7-day-window policies. A disposable activation probe refreshed all six CAGGs, inserted explicit markers, and created exactly six policies.
- Required harness evidence passed on unique loopback-only `monitoring_test_*` databases: `05-idempotent-schema.txt` reports 6 materialized-only CAGGs, 6 adapter rows, 6 pending invalidations, and 0 policies; `05-destructive-guard.txt` reports incompatible-object rejection, forbidden-statement guard, and unmarked-policy rejection.

## 2026-08-02 Todo 6 additive statistics equivalence
- TimescaleDB continuous aggregates reject PostgreSQL `REFRESH MATERIALIZED VIEW`; deterministic tests must use bounded `CALL refresh_continuous_aggregate(...)`. Bounded refreshes also avoid converting an infinite full-refresh watermark through the Todo 5 catalog adapter.
- The statistics proof evaluates raw <=1h, 1-minute for 6h, and 5-minute for 24h/7d inside one read-only `REPEATABLE READ` transaction. Only complete buckets below the CAGG watermark and disjoint from every pending invalidation are eligible; partial boundaries, invalidated buckets, and the watermark tail are replaced by raw rows.
- A sensor-1 row at `2026-01-01T01:00:30Z` is inserted into a fully eligible bucket after both CAGGs are materialized and never refreshed. The pending invalidation makes hybrid AVG/MIN/MAX/STDDEV_SAMP exactly match direct raw truth before refresh, with all 30 range/sensor comparisons at zero error (tolerance `1e-9`). The same case proves `n=1` returns deviation 0 and `n=0` emits no statistics row.
- The uneven 3-sample/1-sample one-minute fixture yields raw average 22 versus an intentionally wrong unweighted average-of-averages of 20.6666666666666667, proving the weighting error exceeds `1e-9`.
- Evidence: `06-statistics-equivalence.json` and `06-weighting-failure.txt`; both harness cases exited 0 and their disposable roles/databases were dropped.

## 2026-08-02 Todo 7 monitoring sensor contracts
- `app.monitoring_models` now owns frozen/strict Pydantic v2 response contracts, canonical unit-family enums (`celsius`, `percent`, `kpa`, `ppm`, `hpa`, `mm`), and finite numeric series/statistics fields.
- `MonitoringRange.from_absolute` accepts ISO strings or datetimes through an explicit typed-error boundary, rejects naïve/malformed/reversed/<5m/>7d input with status 400, normalizes offsets to UTC, and serializes UTC timestamps with `Z`; `from_now` uses aware UTC.
- Tier selection is derived only from validated duration: raw through 1h, 1min through 6h, and 5min through 7d. `MonitoringMetadata` rejects a tier that does not match its range.
- `resolve_room_metadata` reads canonical topology: Flower resolves ordered `front`/`back`, Veg resolves `main`, Lab/Outside raise typed 400 errors, and unknown rooms raise typed 404 errors. Route handlers can catch `MonitoringError` and use its `status_code` and `detail` fields.
- Todo 7 evidence is `07-range-contract.txt` (10 passed) and `07-range-rejections.json` (13 passed); the complete focused file passes 23 tests. Changed files pass Ruff, format, compile, diagnostics, and the strict no-excuse audit.
- Whole-backend `ruff check .` remains blocked by two unchanged tracked baseline `UP038` findings in `app/repositories/sensor_repository.py:209` and `app/routes/sensor_data.py:127`; Todo 7 did not alter them because existing routes/models are explicitly out of scope.

## 2026-08-02 Todo 9 monitoring statistics repository
- `MonitoringStatisticsRepository` performs a read-only `REPEATABLE READ` query, accepting only complete CAGG buckets below the catalog watermark and outside pending invalidation ranges; raw measurements replace every other bucket so stale tails and late rows remain exact.
- Aggregated `NUMERIC` sum and sum-of-squares values are retained through the Python boundary. The repository reconstructs sample standard deviation with a non-negative Decimal variance clamp, then explicitly verifies both Decimal and float finiteness before creating `SensorStatistics`.
- The disposable integration fixture materializes a 5-minute interior row and inserts a later unrefreshed row. The repository returns the raw-equivalent count/min/max/mean/stddev to `1e-9`, and its harness removes the generated database and role afterward. Missing CAGGs raise the typed HTTP-503-ready error instead of falling back to a multi-day raw scan.

## 2026-08-02 Todo 8 monitoring sensor series
- `MonitoringSeriesRepository` validates monitoring-room topology before acquiring a database connection, returns a typed 503 when the selected 1-minute/5-minute CAGG has no catalog row or has not reached the requested range start, and uses a read-only `REPEATABLE READ` snapshot for all node reads.
- Aggregate interiors require a whole requested bucket below the materialization watermark and outside pending invalidation intervals; CAGG edges, stale tails, and invalidated buckets are replaced from `measurement` using the same UTC `time_bucket` interval, avoiding overlap and index-step downsampling.
- The disposable `sensor-tier-edges-tail-invalidation` harness case and focused topology/missing-CAGG pytest selection passed; evidence is recorded as `08-series-tier-results.json` and `08-topology-failure.txt`.

## 2026-08-02 Todo 8/9 production schema compatibility
- Production sensor ownership is `room → rack → device → sensor → measurement`; neither `device.node` nor `device.room_id` exists. Series and statistics now use the canonical `sensor → device → LEFT JOIN rack → room` join and resolve Flower/Veg nodes solely via `sensor_name_like_pattern` (`%_f`, `%_b`, `%_v`).
- The disposable fixture now mirrors that rack chain while preserving its room, device, sensor, and measurement identities. All Todo 5/6 SQL cases still pass, and the statistics repository integration succeeds against the production-shaped fixture.
- The direct repository pytest command skips the explicitly integration-marked statistics test when no disposable `MONITORING_TEST_DATABASE_URL` is supplied; `scripts/test-monitoring.sh --integration` runs that test and passed.

## 2026-08-02 Todo 10 sensor monitoring routes
- The monitoring router is deliberately mounted only under `/api/sensors/monitoring/*`, which is the backend prefix Caddy forwards to port 8000; range, live, and stats therefore remain same-origin and do not alter the legacy dynamic sensor routes.
- Range and stats parse string query boundaries through `MonitoringRange`, returning typed 400/404/503 monitoring errors without FastAPI's default 422 path or generic traceback. Missing `start` and `end` uses the existing one-hour `from_now` constructor.
- Live values scan the node-specific `sensor_name_like_pattern` translated to a Redis glob, batch-read state plus millisecond timestamps, and serialize `datetime.fromtimestamp(..., tz=UTC)` as `Z` timestamps. Route tests use FastAPI dependency overrides for both repositories; happy and failure evidence each records 3 passing cases.

## 2026-08-02 Monitoring statistics lazy acquisition refactor
- `MonitoringStatisticsRepository` now accepts either a `DatabaseManager` for production lazy pool acquisition or an explicit asyncpg pool for the disposable integration test. `_acquire()` owns the single connection-acquisition seam, matching the series repository while leaving SQL and transaction behavior unchanged.
- The FastAPI statistics dependency is synchronous and caches a repository holding the database manager rather than awaiting and caching a pool. Route tests, focused statistics tests, and the disposable `statistics-repository` integration case all pass after the lifecycle-only change.

## 2026-08-02 Todo 12 automation monitoring contracts
- Monitoring contracts are split into `schemas/monitoring_models.py` (strict UTC range/provenance primitives) and `schemas/monitoring.py` (climate/light/device/PID/photoperiod timelines) to stay below the 250-line pure-module ceiling.
- Origin and quality remain independent: projected climate/light series require immutable projection revision, anchor fingerprint, observed-at, quality, and validity metadata; device and PID history reject projected provenance at construction.
- `Phase.UNKNOWN` is explicit and requires `quality=unavailable`; known SUN/MOON phases cannot claim unavailable quality. Per-source opaque cursors, dropped-row flush health, and a distinct `RuntimeSnapshotVersion` are carried without comparing process-local runtime state to projection tokens.
- Focused evidence passed: `12-control-contracts.json` has 5 selected passing tests and `12-illegal-projection.txt` has 2 selected passing tests; both monitoring schema modules pass Ruff and LSP diagnostics.

## 2026-08-02 Todo 13
- `MonitoringSnapshotRepository` acquires one lazy pool connection and reads all projection inputs and source high-water marks in a read-only `REPEATABLE READ` transaction. It then separately observes only semantic Redis ramp/light anchors, preserving observed/validity timestamps outside the anchor fingerprint.
- Immutable tuple-backed records protect loaded inputs. Stable sorted JSON hashing includes range, room/cluster, active mode, calendar/configuration inputs, registry expectations, and source cursors; runtime snapshot version remains a separate branded process-local value.
- Fake-only focused evidence passed: `13-projection-snapshot.json` records 3 selected passing tests; `13-mixed-revision-failure.txt` records 2 selected passing tests. Ruff check and format pass for the repository, helper, and tests.

## 2026-08-02 Todo 15
- Added a pure, injected-clock light projection split between timeline assembly and stateless scheduler-parity evaluation. It uses Toronto-local windows, preserves the runtime's current-local-weekday overnight program behavior and priority/created-at ordering, and intentionally does not use `program_type` to select a match.
- Ordinary photoperiod ramps reproduce the scheduler's 10%-to-target and target-to-minimum math. Missing mode parameters remain a visualization-only SUN failsafe with unavailable quality; missing anchors and manual/non-matching scheduler metadata cannot claim exact ramp parity.
- Seven-day one-second cycles are bounded into deterministic aggregate buckets with average/min/max metadata and aggregated provenance. Focused happy/failure selectors, Ruff, format, and compile checks passed.

## 2026-08-02 Todo 17
- `PhotoperiodHistoryLogger` owns a fixed 256-entry `asyncio.Queue`, records only transition/60-second heartbeats through `put_nowait`, and flushes at most 64 append-only rows every 100ms; drop, oldest-pending, and last-success metadata remain available without touching scheduler or device state.
- `ControlEngine` reuses the one active-mode row read for moon authority to preserve mode/submode provenance, and the container starts the logger before control background tasks then stops/flushed it before closing the database. Fake-only tests cover ordering, transition provenance, cadence, overflow, no-op empty registry, and throttled persistence failure.

## 2026-08-02 Todo 14
- `climate_projection.py` is a clock-injected, I/O-free projection engine. It evaluates climate periods in `America/Toronto`, emits UTC timeline points, uses the runtime linear-ramp equation and canonical per-metric skip thresholds, and never creates a `RampManager` or `SetpointManager`.
- The immutable monitoring snapshot now captures semantic `ramp_anchors` once at repository load, allowing only a ramp crossing `now` to seed the current boundary. Missing future-mode configuration emits `quality=unavailable`; nonexact anchor metadata downgrades the current resolved point to estimated.
- Flower calendar events resolve by highest `phase_order`, respect disabled and same-mode transitions, warn about the 60-second scheduler cadence, and fall back from drying to veg after the final plan. Veg retains its active mode. Focused happy and failure pytest selections, Ruff, formatting, and compilation passed.

## 2026-08-02 Todo 16
- `MonitoringHistoryRepository` keeps the Todo 13 lazy pool seam and reads initial effective/automation history in one read-only `REPEATABLE READ` transaction. Complete 1/5-minute CAGG buckets below the watermark and outside pending invalidations are used; raw rows replace every remaining bucket.
- Tail pages retain immutable source IDs (`monitoring_ingest_id`/`id`), fetch one sentinel row to report `has_more`, expose at most 1,000 observations, and never collapse same-timestamp events. Nullable automation PID values remain null.
- Returned `ControlMonitoringResponse` values use recorded provenance, optional flush-health metadata, source cursors, and UNKNOWN/unavailable photoperiod semantics for conflict, missing expected lights, or heartbeat gaps. Required disposable harness and focused failure selector passed.

## 2026-08-02 Todo 18
- Added GET-only control monitoring range, projection, and tail routes. Range validation uses the shared UTC-aware `MonitoringRange`; initial history and source cursors remain repository-atomic.
- Projection responses contain future climate/light/photoperiod data only, with snapshot-derived projection metadata and injected photoperiod logger health. Tail outages, invalid cursors, and dropped logger rows invoke one injected bounded-range reconciliation.
- Fake-backed route selectors pass (6 happy and 4 failure cases), and the frontend OpenAPI generator was run through `npm run api:check`.
