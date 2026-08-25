# T23 browser QA — retry 2

- Origin: `http://127.0.0.1:8080` (Caddy, same-origin SPA/API)
- Attempt: 2026-08-25, fresh browser profile
- Interaction policy: GET-only navigation and UI interactions; no writes outside this evidence directory.

## Results

| Check | Result | Evidence |
|---|---|---|
| Flower Room monitoring page loads | PASS (page loaded) / FAIL (data state) | `/flower/monitoring?range=live-3h` loaded, but UI showed `Range data stale` and contract validation error. |
| Veg Room monitoring page loads | PASS (page loaded) / FAIL (data state) | `/vegetation/monitoring` loaded, but UI showed `Range data stale` and contract validation error. |
| Charts render real data | FAIL | Both chart regions rendered blank/placeholder axes; no successful range data. Some live cards showed values, but chart data was unavailable. |
| Budgeted range/history requests | PASS | Range and history requests included `max_points=`; representative URLs below. |
| No separate `/stats` during range load | PASS | No `/stats` request observed in filtered network requests. |
| Chart zoom keeps fixed range and refetches resolution | FAIL | Dragging the first chart changed URL to fixed `?start=2026-08-25T17%3A29%3A16.773Z&end=2026-08-25T18%3A49%3A13.072Z` and UI to `FIXED`, but refetched range remained contract-invalid/stale; successful resolution refetch could not be confirmed. |
| Live → fixed → live transition | FAIL | Live initially; chart drag produced `FIXED`; clicking `Now` returned `?range=live-3h`. Live polling repeatedly received 404 `/tail`, so sliding live behavior was not confirmed. |
| Console clean | FAIL | Console contained repeated 404 errors and monitoring contract-validation errors. Representative verbatim errors below. |
| Screenshots | PASS | All three requested files saved. |

## Representative monitoring URLs

1. `GET http://127.0.0.1:8080/api/sensors/monitoring/range/Flower%20Room?start=2026-08-25T16%3A45%3A57.271Z&end=2026-08-25T19%3A45%3A57.271Z&max_points=2000` → 200
2. `GET http://127.0.0.1:8080/api/monitoring/control/Flower%20Room/history?start=2026-08-25T16%3A45%3A57.271Z&end=2026-08-25T19%3A45%3A57.271Z&max_points=1000` → 200
3. `GET http://127.0.0.1:8080/api/sensors/monitoring/range/Veg%20Room?start=2026-08-25T18%3A48%3A04.098Z&end=2026-08-25T19%3A48%3A04.098Z&max_points=2000` → 200
4. `GET http://127.0.0.1:8080/api/monitoring/control/Veg%20Room/history?start=2026-08-25T18%3A48%3A04.098Z&end=2026-08-25T19%3A48%3A04.098Z&max_points=1000` → 200

No `/stats` URL was observed. Live control tail requests did not carry `max_points` and returned 404.

## Console errors (verbatim representative messages)

```text
response from monitoring failed contract validation: [ { "code": "invalid_type", "expected": "number", "received": "null", "path": [ "requested_max_points" ], "message": "Expected number, received null" }, { "code": "invalid_type", "expected": "number", "received": "null", "path": [ "interval_seconds" ], "message": "Expected number, received null" } ]
Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:8080/api/monitoring/control/Veg%20Room/tail?start=2026-08-25T18%3A48%3A58.000Z&end=2026-08-25T19%3A48%3A07.200Z:0
```

## Screenshots

- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-veg.png`
- `.omo/evidence/monitoring-pipeline-radical-optimization/task-23-prod-flower-tablet.png`

## Backend tail route

- Candidate B follow-up: `GET /api/monitoring/control/{location}/tail` is now registered as a distinct bounded-poller route.
- The route resolves canonical monitoring rooms before delegating to the existing unbudgeted history read path, preserving the `ControlHistoryEnvelope`, range validation, and repository query behavior without adding SQL.
- Focused route coverage passed 3/3: OpenAPI/route registration and fake-DB envelope read, unknown-room 404, and partial-window 400.

## Contract fix

- The frontend `ControlMonitoringResponse` schema now accepts `requested_max_points` and `interval_seconds` as nullable or absent, matching the backend `ControlHistoryEnvelope` (`int | None = None`). Sensor range metadata remains unchanged and continues to require real numeric values when present.
