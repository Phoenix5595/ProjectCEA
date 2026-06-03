# Learnings - Dashboard Fixes Deployment

## Deployment Workflow
- Frontend build with `npm run build` in `Infrastructure/frontend/`.
- Deployment requires replacing `/opt/projectcea/current/Infrastructure/frontend/dist`.
- `automation-service` must be restarted after frontend deployment as it serves the static files.

## Verification Patterns
- When Playwright is unavailable (e.g., on Arm64/missing dependencies), `curl` can verify API responses.
- `grep` on minified JS bundles can sometimes verify source changes, but it's brittle due to minification.
- Directly checking the source code of the deployed version (if available) or the build source is more reliable for UI structure verification if visual tools fail.

## Dashboard Architecture
- Dashboard layout uses responsive Tailwind classes: `lg:w-[37%]`, `lg:w-[26%]`.
- Flower Room cluster separation is achieved by separate API calls to `clusterA` and `clusterB` and mapping to specific suffixes in the UI (`_f` for Front, `_b` for Back).
- Sensor data keys in the backend/Redis use suffixes like `_f` and `_b` to distinguish clusters within a room.
## Dashboard Final Verification - Tue 03 Mar 2026 11:16:51 AM EST
- Dashboard loaded successfully at http://localhost:8001
- Verified 37/37/26 column layout via CSS classes 'lg:w-[37%]' and 'lg:w-[26%]'.
- Verified 'Front Cluster' and 'Back Cluster' are displayed side-by-side in Flower Room section.
- Verified absence of 'Device Config' button in the dashboard.
- Successfully used Playwright on Arm64 by symlinking system chromium to /opt/google/chrome/chrome.
## Build and Deployment - 2026-03-03
- Frontend built successfully in ProjectCEA-ui directory.
- Built artifacts deployed to /opt/projectcea/current/Infrastructure/frontend/dist/.
- Destination directory was cleaned before deployment to avoid stale assets.
- automation-service restarted and is serving the new frontend.
- High CPU load (97%) observed on the system, causing slow initial page loads (up to 60s).

## Visual Verification
- Verified 3-column layout (37/37/26 ratio).
- Verified footer 'Device Config' button removal.
- Verified 'Flower Room' shows 'Front' and 'Back' clusters separately.

## Backend Verification
- Confirmed that /api/sensors/{location}/{cluster}/live returns keys with correct suffixes:
  - Flower Room Front -> _f
  - Flower Room Back -> _b
- Confirmed that Redis contains sensor keys with _f and _b suffixes.
