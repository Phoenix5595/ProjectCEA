---
name: Standardize sensor naming with database field names and node_id suffix
overview: Update all sensor naming across frontend and backend to use exact database field names (temp_dry_c, temp_wet_c, co2_ppm, etc.) with node_id suffix (e.g., temp_dry_c_257, co2_ppm_258), standardizing the naming convention everywhere.
todos:
  - id: confirm-serve
    content: Confirm frontend serve host/port strategy
    status: completed
  - id: backend-api-only
    content: Remove backend static/index serving; keep API/ws; set CORS
    status: completed
    dependencies:
      - confirm-serve
  - id: frontend-config
    content: Adjust build/output and API base for external backend
    status: completed
    dependencies:
      - confirm-serve
  - id: test-end-to-end
    content: Test frontend-only access, API, ws, favicon
    status: completed
    dependencies:
      - backend-api-only
      - frontend-config
---

# Remove Backend-Served UI; Frontend-Only Access

Goal: Backend stays Python/FastAPI API-only; React+TS frontend is the sole way users access the dashboard. Remove backend serving the built UI while keeping API/CORS/websocket working.

## Plan

- Confirm serving strategy
- Serve frontend via its own host/port (Vite dev on 3000 or a static host); backend only serves APIs.
- Backend cleanup (API-only)
- Update `backend/app/main.py` to stop serving `index.html` from `backend/static` at `/` and optionally disable static mounts if not needed.
- Keep `/api/*` and `/ws/*` working; ensure CORS allows the frontend host/port.
- Frontend build & config
- Ensure `vite.config.ts` builds to a separate `dist` (not into backend) when serving independently, or keep current but do not mount in backend; adjust `API_BASE_URL` to point to backend origin in production.
- Testing
- Verify favicon and assets load via the frontend host.
- Confirm API calls and websockets succeed from the frontend; backend returns 404 for `/` to avoid UI exposure.