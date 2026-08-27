# T23 Browser QA — Final Candidate B Attempt 3

Date: 2026-08-25
Base URL: http://127.0.0.1:8080
Candidate: B attempt 3 (`20260825-163148-7f8a968`)

## URLs
- Flower: http://127.0.0.1:8080/flower/monitoring
- Veg: http://127.0.0.1:8080/vegetation/monitoring

## Final checks

| Check | Result | Evidence |
|---|---|---|
| Flower monitoring page loads | PASS | Page loaded at `/flower/monitoring?range=live-3h`; charts rendered and back-cluster data was visible (23.3°C, 94.4% RH, 997.7 hPa). |
| Flower charts render real data | PASS (partial sensor availability) | Chart regions and data tables rendered; back sensor series contained real values. Front series were unavailable. |
| Veg monitoring page loads | PASS | Page loaded at `/vegetation/monitoring`; chart regions rendered. |
| Veg charts render real data | FAIL | Veg sensor cards/statistics remained `—`/unavailable during the run. |
| Budgeted range/history/tail requests | PASS | Observed Flower and Veg `/range`, `/history`, and `/tail` requests all included `max_points=` (`2000` for range, `1000` for history/tail). |
| No separate `/stats` request | PASS | No `/stats` resource appeared in captured monitoring requests. |
| Live mode tail polling/cards | PASS (Flower), FAIL (Veg data display) | Flower tail polling ran at approximately one request/second and cards updated with back-cluster values. Veg tail requests were present but displayed values remained unavailable. |
| Zoom keeps fixed range and refetches resolution | PASS | Drag selection changed Flower URL to explicit `start`/`end` with `FIXED` status; subsequent data requests were refetched. |
| Live → fixed → live | PASS | Flower transitioned `LIVE · 1h` → `PAUSED`/fixed interaction → zoom `FIXED` → `Now` + `Resume` → `LIVE · 1h`. |
| Console errors | FAIL | Captured browser error: `404 Not Found` for `http://127.0.0.1:8080/api/sensors/live/all`. |
| Flower desktop screenshot | PASS | `task-23-prod-flower.png` overwritten. |
| Veg desktop screenshot | PASS | `task-23-prod-veg.png` overwritten. |
| Flower tablet screenshot | PASS | `task-23-prod-flower-tablet.png` overwritten at 768×1024 viewport. |

## Flower `/tail` volume measurement

Method: browser Performance Resource Timing entries for Flower `/tail`, counting entries and summing `transferSize` and `encodedBodySize`. A nominal 60-second Playwright wait returned an actual browser elapsed interval of **45,646 ms**, so the exact observed interval—not an invented 60-second extrapolation—is reported here.

- Measurement start: `2026-08-25T20:45:49.208Z`
- Measurement end: `2026-08-25T20:46:34.854Z`
- Actual elapsed: **45.646 s**
- `/tail` requests: **46**
- Total transfer bytes: **183,659 bytes**
- Total encoded body bytes: **169,859 bytes**
- Request URLs carried `max_points=1000`.

This is below the requested approximately 60 requests / approximately 17 MB expectation because the browser wait ended after 45.646 seconds and the observed responses were approximately 4 KB each in this live sample, not approximately 286 KB each.

## Artifacts
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-veg.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower-tablet.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-console.txt`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-network-flower-live.txt`
