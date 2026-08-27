---
slug: monitoring-pipeline-radical-optimization
status: complete
intent: clear
review_required: false
pending-action: write .omo/plans/monitoring-pipeline-radical-optimization.md
approach: Measure first; define a width-aware query contract; move bounded semantic aggregation to the monitoring service; separate low-frequency React state from the chart data path; preserve zoom re-query, provenance, gaps, and read-only safety; verify with deterministic benchmarks and candidate deployment evidence.
---

# Draft: monitoring-pipeline-radical-optimization

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Reproducible performance baseline and regression harness quantify server latency, payload size, client alignment/draw cost, and long-running stability. | active | `.omo/evidence/botanical-color-spectrum-monitoring-recovery/T28/runtime/cadence_probe.py`; `Infrastructure/frontend/src/features/monitoring/**/__tests__` |
| C2 | Monitoring-service queries honor a bounded point budget while preserving sensor envelopes, step-state semantics, gaps, CAGG coverage, and read-only guarantees. | active | `Infrastructure/monitoring-service/monitoring_service/{sensor_routes.py,sensor_repository.py,control_routes.py,control_repository.py,database.py}` |
| C3 | Live chart updates avoid full React/page realignment when only tail values change, while tables/status remain React-consistent. | active | `Infrastructure/frontend/src/features/monitoring/state/`; `Infrastructure/frontend/src/features/monitoring/data/`; `Infrastructure/frontend/src/features/monitoring/charts/UPlotChart.tsx` |
| C4 | Zoom and resize derive query resolution from visible range and chart width, cancel/supersede stale loads, and return finer data on zoom-in. | active | `Infrastructure/frontend/src/pages/{FlowerMonitoring.tsx,VegetationMonitoring.tsx}`; `monitoringStore.ts:89-214`; Grafana query-runner references in Findings |
| C5 | Candidate deployment, rollback, soak, and evidence gates prove no production-control or data-write regressions. | active | `deploy.sh`; `finalize-deploy.sh`; `rollback-deploy.sh`; `Infrastructure/scripts/tests/test-deploy-candidate.sh`; root `AGENTS.md` |
| C6 | Current/projection publication composition is either included explicitly or remains a separately tracked functional gap. | active | `Infrastructure/monitoring-service/monitoring_service/control_repository.py:264-341`; prior F4 evidence |

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

None. The user explicitly requested questions and no assumptions; unresolved cross-cutting choices remain in Open questions.

## Findings (cited - path:lines)

- `MonitoringStore` ticks every 1 second and the poller requests live sensors plus a bounded control tail (`Infrastructure/frontend/src/features/monitoring/state/monitoringStore.ts:8,133-170`; `monitoringStore.poller.ts:27,42-47,83-137`).
- Every successful live response creates a new `data` object; both monitoring pages key full `alignSeries()` recomputation on `[snapshot.data, snapshot.range]` (`monitoringStore.poller.ts:83-94`; `FlowerMonitoring.tsx:62-67`; `VegetationMonitoring.tsx:62-67`).
- `alignSeries()` currently unions/sorts all source timestamps, conditionally builds a 5000-slot grid, realigns every sensor/control/device/PID series, and injects live points before every draw (`alignSeries.ts:27-70`; `alignSeries.grid.ts`; `alignSeries.series.ts`).
- uPlot itself already follows the correct data-only update path: `setData(..., false)` preserves zoom and avoids chart recreation (`Infrastructure/frontend/src/features/monitoring/charts/UPlotChart.tsx:226-236`; `UPlotChart.test.tsx`; `chartInteractions.test.tsx`).
- User zoom already re-queries: chart `onZoom` calls `setFixedRange()`, which starts a four-request range load (`FlowerMonitoring.tsx:157-180`; `monitoringStore.ts:89-93,172-214`).
- A range load concurrently requests sensor range, sensor statistics, control history, and projection, but the monitoring-service range handler then reads series and statistics sequentially (`monitoringStore.ts:179-184`; `Infrastructure/monitoring-service/monitoring_service/sensor_routes.py:53-71`).
- Sensor repository node reads and control-history setpoint/state/photoperiod reads are sequential (`sensor_repository.py:60-115`; `control_repository.py:55-68`).
- The source tree has pool hardening (min 1/max 8, 10-second acquire bound) and >48-hour 5-minute-CAGG statistics; the locally readable deployed snapshot does not. This source/deploy drift must be reconciled before benchmarking or rollout (`database.py`; `config.py`; `resources.py`; `sensor_repository.py:33,96-115`; `/opt/projectcea/current/deploy_manifest.json`).
- Monitoring queries have tier-based 1-second/1-minute/5-minute aggregation but no width-aware `max_points` contract or hard per-series response budget (`sensor_models.py:125-132`; `sensor_repository.py:60-83`).
- Grafana derives `maxDataPoints` from panel width, computes interval approximately as range/resolution, requeries on zoom, outer-joins DataFrames, and uses uPlot `setData()` for value-only changes ([Grafana query docs](https://grafana.com/docs/grafana/latest/panels/query-a-data-source/); [rangeutil source](https://github.com/grafana/grafana/blob/4dd65aab218b71df429ad0ef08da71592fe291db/packages/grafana-data/src/datetime/rangeutil.ts#L580-L594); [Plot update path](https://github.com/grafana/grafana/blob/4dd65aab218b71df429ad0ef08da71592fe291db/packages/grafana-ui/src/components/uPlot/Plot.tsx#L80-L94)).
- Grafana does not downsample generically in OSS core; the datasource must honor the supplied budget/interval. Therefore ProjectCEA must implement width-to-budget in its frontend and semantic bucketing in monitoring-service.
- TimescaleDB supports `time_bucket` and continuous aggregates, but aggregation must match series semantics: analog sensors need mean/min/max; step/state series need last-value/LOCF behavior; gaps and trailing incomplete CAGG buckets must remain explicit ([Timescale `time_bucket`](https://docs.timescale.com/api/latest/hyperfunctions/time_bucket/); [continuous aggregates](https://docs.timescale.com/using-timescaledb/continuous-aggregates/); [last](https://github.com/timescale/docs/blob/latest/api/last.md); [locf](https://www.tigerdata.com/docs/reference/timescaledb/hyperfunctions/time_bucket_gapfill/locf)).
- uPlot accepts full aligned buffers rather than incremental patches; the optimization opportunity is upstream buffer construction, update gating, and allocation reuse—not inventing an unsupported append API ([uPlot issue 720](https://github.com/leeoniya/uPlot/issues/720); current `UPlotChart.tsx:226-236`).
- Existing tests cover store races, bounded control windows, alignment semantics, zoom preservation, and chart non-recreation, but no committed end-to-end performance budget or browser/store/alignment integration benchmark exists (`monitoringStore.test.ts`; `alignSeries.test.ts`; `UPlotChart.test.tsx`; `.omo/evidence/.../T28/runtime/cadence_probe.py`).
- Adversarial verification found the monitoring-service, publication modules, shared monitoring contracts, and parts of frontend monitoring state are untracked while deployed; the release manifest's Git SHA therefore cannot reproduce all deployed bytes. The plan must either repair this foundation or explicitly stop before production rollout (`deploy.sh`; `/opt/projectcea/current/deploy_manifest.json`; verifier evidence recorded in this draft).
- Adversarial verification confirmed `deploy.sh` restarts all managed services, so “staged candidates” means two backward-compatible repository/release states—not component-only service deploys. Backend capability must ship without requiring the new frontend; frontend activation ships only after the first candidate is finalized.
- Backend design verification recommends an optional public point-budget contract whose default preserves old clients, with server-derived bucket width and semantic aggregation. It rejects average-of-averages, unbounded query parallelism, removal of CAGG watermark completeness checks, and any response that silently mislabels approximate statistics.
- Frontend design verification recommends measuring panel width, sending the point budget on range/zoom/resize requests, prefiltering/alignment per panel, coalescing same-tick store emissions, and retaining uPlot full-buffer `setData(..., false)`. It rejects unsupported append APIs and wholesale React bypass.
- Publication design verification found the concrete current writer exists, but no concrete future projection store/factory or production composition exists. The safe handoff is synchronous latest-only enqueue from an immutable snapshot seam; all Redis I/O and projection work must stay in independent workers.

## Decisions (with rationale)

- Intent is CLEAR because the user explicitly requested an interview and no assumptions.
- Classification is Architecture: the work spans monitoring-service contracts/SQL, frontend state/alignment/rendering, browser interactions, deployment, and production evidence.
- No implementation or implementer subagent will run in this session; approval authorizes writing the plan only.
- Dynamic verification found that replacing uPlot `setData()` is not a valid objective: it is already the library-supported fast path. The plan must optimize data production and React subscription boundaries instead.
- Dynamic verification found zoom re-query already exists. The plan should preserve and benchmark it, then add width-aware resolution and cancellation—not recreate the feature.
- The owner selected aggressive hard SLOs: 7-day warm P95 ≤1 second, live visual age ≤1 second, client processing P95 ≤8 milliseconds, no 500/503 during an eight-viewer soak, and ≥85% payload reduction wherever bucketing applies.
- The owner selected semantic envelopes: analog series retain average/minimum/maximum/sample count; step and device-state series retain last-value semantics with carry-forward; real gaps remain gaps.
- The owner selected hybrid contract-first development: behavioral, SQL-shape, and store regression tests precede each implementation; deterministic performance benchmarks run after each implementation wave.
- The owner selected staged candidate rollout: deploy and verify the backward-compatible monitoring-service contract first, then deploy the frontend fast path with an independent rollback boundary.
- The owner selected full automation current/projection publication composition as a later independent wave and candidate, after query and frontend optimization pass their gates.
- Staged rollout is interpreted as two backward-compatible full-service candidates because current deploy tooling restarts all managed units: candidate A adds/reconciles backend capability with the old frontend unchanged; candidate B activates the optimized frontend after candidate A is finalized.
- The owner requires release reproducibility first: bring the complete monitoring stack under version control, reject dirty/unreproducible candidates, and record content identity before optimization benchmarking or production rollout.
- The owner selected `max_points` as the sole public resolution input. Monitoring-service derives and echoes a quantized interval; there is no public `interval_ms` override or conflicting precedence rule.
- The owner accepts CAGG-derived standard deviation for windows over 48 hours only when the response contract and UI explicitly label it approximate; minimum, maximum, mean, and sample count remain exact.
- The owner selected a 24-hour publication horizon covering projected heating, cooling, VPD, and CO₂ targets, per-light intensity, and photoperiod. Future relay/device/PID state must not be fabricated.
- The owner delegated the deploy-reproducibility mechanism. Decision: leave `deploy.sh`, `finalize-deploy.sh`, and `rollback-deploy.sh` unchanged; enforce committed task-owned worktree input, non-mutating preflight, pre/post source identity, and independent deployed-content evidence around the approved scripts.

## Scope IN

- Deterministic baseline and post-change performance measurements for 1h/3h/24h/7d, zoom, live ticks, payload size, query count, DB pool pressure, client alignment/draw duration, memory/GC trend, and error recovery.
- Monitoring-service query budgeting and semantic aggregation for sensor and control history surfaces used by Flower and Vegetation monitoring.
- Frontend chart-width measurement, request contract, range/zoom/resize orchestration, stale-request handling, and chart hot-path decoupling from unrelated React renders.
- Preservation of live staleness behavior, source/provenance metadata, gaps, step semantics, min/max bands, fixed zoom, accessibility tables, pause/resume, and last-good-on-error behavior.
- Test, browser QA, candidate deploy, rollback, soak, and evidence updates.

## Scope OUT (Must NOT have)

- No production POST/PUT/PATCH/DELETE, destructive SQL, Redis mutation, hardware access, service reset, or direct deploy outside approved scripts.
- No replacement of uPlot, React, TimescaleDB, Redis, or the monitoring-service architecture unless the user explicitly changes scope.
- No silent approximation of states/setpoints, no averaging boolean/discrete values, no bridging stale/gapped sensors, and no removal of provenance/quality metadata.
- No unbounded query parallelism, client buffer, response payload, or DB connection growth.
- No implementation before the approval gate and completed plan artifact.

## Open questions

None. Objective, full scope, architecture, performance targets, fidelity, publication semantics, test strategy, and rollout policy are resolved.

## Approval gate
status: approved
approved-by-user: true
approach: First make the monitoring release reproducible and reconcile source/deploy drift. Then establish committed baseline/SLO harnesses; add a backward-compatible `max_points` monitoring contract with semantic server aggregation and explicit statistics approximation metadata; deploy/finalize that backend capability; refactor the frontend to width-budgeted per-panel alignment with coalesced live updates while retaining uPlot full-buffer `setData`; deploy/finalize the frontend activation; finally compose 24-hour current/projection publication workers in an isolated automation candidate with proof that control cadence and hardware authority are untouched.
next-action: On explicit approval, create `.omo/plans/monitoring-pipeline-radical-optimization.md`, run mandatory Metis gap analysis, append decision-complete todos and final verification tasks, and stop for worker handoff. Approval does not authorize implementation.
plan-path: .omo/plans/monitoring-pipeline-radical-optimization.md
plan-structure: 31 implementation todos plus F1-F4 final verification; six gated waves
metis: completed; blockers incorporated (reproducibility, minimum-range ownership, aggregation metadata, tier/budget interaction, no schema migrations, request cancellation, publication timeline/observer safety)
pending-action: user chooses `$start-work monitoring-pipeline-radical-optimization` or requests dual high-accuracy review
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
