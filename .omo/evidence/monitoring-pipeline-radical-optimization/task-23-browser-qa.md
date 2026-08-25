# T23 browser QA - attempt 3

Date: 2026-08-25
Base: http://127.0.0.1:8080

## Result

**FAIL** - both pages render historical chart canvases and range requests are budgeted, but the deployed browser surface still returns 404 for every `/tail` request, causing console errors and preventing live card updates.

## URLs

- Flower: http://127.0.0.1:8080/flower/monitoring?range=live-3h
- Veg: http://127.0.0.1:8080/vegetation/monitoring
- Tablet screenshot: Flower URL at viewport 1024x768

## Checks

- [PASS] Flower loads; climate and atmosphere chart canvases render. Historical data is visible (Back cluster: 22.7 C, RH 96.2%, VPD 0.11 kPa; statistics populated).
- [PASS] Veg loads; chart regions render and `/history` returns 200 with `max_points=1000`.
- [PASS] Range/history budgeting: observed GET `/history?...&max_points=1000` for Flower and Veg; no `/stats` request observed.
- [FAIL] Flower tail: repeated GET `/api/monitoring/control/Flower%20Room/tail?...` returned HTTP 404, not 200.
- [FAIL] Veg tail: repeated GET `/api/monitoring/control/Veg%20Room/tail?...` returned HTTP 404, not 200.
- [PASS] Zoom changes Flower URL to fixed `start=...&end=...` and refetches `/history?...&max_points=1000` plus `/projection`.
- [FAIL] Fixed-to-live and live-value update: Resume returns `LIVE · 3h`, but tail 404 responses prevent confirming live updates.
- [FAIL] Console errors: Flower final collection had 34 errors; Veg had 17 errors. Repeated tail 404s and initial `/api/sensors/live/all` 404.

## Tail-volume measurement (Flower)

Playwright `page.on('response')` captured matching `/tail` responses and read each response body byte length while Flower was LIVE. The 60-second MCP call timed out, so measurement used 50.002 s + 10.002 s = 60.004 s instrumented time, with the listener reattached between windows.

- Requests: **60** (50 + 10)
- Statuses: **60 x 404**
- Total response bytes: **1,260 bytes**
- Total: **0.001260 MB** (decimal)
- Average: **21 bytes/request = 0.021 KB/request** (decimal)
- This is not the expected ~2.9 MB/poll payload; the route is unavailable at this URL.

## Verbatim console errors

- `Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:8080/api/monitoring/control/Flower%20Room/tail?...`
- `Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:8080/api/sensors/live/all:0`

## Screenshots

- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-veg.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower-tablet.png`

No fixes, service changes, non-GET requests, or git commands were performed.

## Tail budgeting

- `GET /api/monitoring/control/{location}/tail` now accepts the same optional
  `max_points` contract as `/history` (integer 10–100000; invalid requests return
  400) and forwards it to the shared history handler.
- Omitting `max_points` still forwards `None`, retaining the legacy envelope
  shape and null budget metadata.
- The tail route test uses a fake database with 1,001 raw setpoint rows; with
  `max_points=1000`, each returned semantic series is capped at 1,000 points.
- The frontend `controlTail()` request now sends the exported
  `CONTROL_HISTORY_MAX_POINTS` value (1,000), including on the 1 Hz live poller
  path while retaining its existing request-options forwarding.
- Focused verification: monitoring-service tail/contract tests **12/12 passed**;
  frontend monitoring API tests **11/11 passed**.
- Full verification: monitoring-service **109/109 passed**, Ruff check and
  format check passed, and compileall passed. Frontend TypeScript check passed,
  monitoring Vitest **110/110 passed**, and the production build passed
  (1,606 transformed modules). Production module sizes remain within the limit:
  `control_routes.py` 81 pure LOC and `monitoringApi.ts` 93 pure LOC.
