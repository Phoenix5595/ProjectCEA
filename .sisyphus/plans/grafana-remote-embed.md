# Grafana Remote Optimization Plan

## TL;DR
Switch Grafana embedding from individual panels to full-dashboard kiosk mode (to preserve the time picker), and point the proxy to the powerful `iskradocker:3000` instance instead of the local Pi to improve rendering performance. 

## Context
- Individual panel embeds (`/d-solo/`) hide the top navbar, meaning the user cannot change the time range.
- Embedding the full dashboard (`/d/`) with `kiosk=tv` hides the sidebar but keeps the top navbar and time picker.
- The user wants to use `iskradocker:3000` (which has a synced TimescaleDB and more compute power) instead of the local Grafana instance.
- The user confirmed database permissions are handled locally on `iskradocker`, so the only Grafana permissions needed are Viewer access for the Anonymous user on the Dashboard Folders.
- `FlowerMonitoring.tsx` and `GrafanaPanel.tsx` were already updated to support `fullDashboard={true}` in the previous session.

---

## TODOs

- [ ] 1. Update VegetationMonitoring for Full Dashboard
  **What to do:**
  - Replace the multi-panel grid layout with a single `GrafanaPanel` call.
  - Use `fullDashboard={true}` and `height="calc(100vh - 120px)"`.
  **File:** `src/pages/VegetationMonitoring.tsx`

- [ ] 2. Point Vite Proxy to Iskradocker
  **What to do:**
  - Change the `/grafana` proxy target from `http://localhost:3000` to `http://iskradocker:3000`.
  **File:** `vite.config.ts`

- [ ] 3. Update AGENTS.md documentation
  **What to do:**
  - Update `Infrastructure/frontend/AGENTS.md` and the root `AGENTS.md` to document the new architecture:
    - Frontend now proxies to `iskradocker:3000` via Tailscale instead of `localhost:3000`.
    - We embed full dashboards (`/d/`) with `kiosk=tv` parameter to keep the time picker instead of individual panels (`/d-solo/`).
    - Note the `grafana.ini` requirements (allow_embedding, cookie_samesite, root_url).
  **Files:** 
  - `Infrastructure/frontend/AGENTS.md`
  - `AGENTS.md`

- [ ] 4. Commit the changes
  **What to do:**
  - Stage `VegetationMonitoring.tsx`, `vite.config.ts`, and both `AGENTS.md` files.
  - Commit message: `feat(frontend): switch grafana embeds to full dashboard kiosk mode and point to iskradocker`

---

## MANUAL STEPS FOR THE USER (Iskradocker Configuration)

Once the agent completes the above, **YOU** must perform these steps on the `iskradocker` machine:

### Step 1: Update `grafana.ini` on iskradocker
On the `iskradocker` machine, edit your Grafana configuration (usually `/etc/grafana/grafana.ini` or passed via docker-compose env vars). 

You MUST have these exact settings:

```ini
[server]
# This tells Grafana it is being served under a subpath
root_url = %(protocol)s://%(domain)s:%(http_port)s/grafana/
serve_from_sub_path = true

[security]
# This is REQUIRED for iframes to work across domains/proxies
allow_embedding = true
cookie_samesite = none
cookie_secure = true

[auth.anonymous]
# This allows viewing without logging in
enabled = true
org_name = Main Org.  # Make sure this matches your exact Org name in Grafana!
org_role = Viewer
hide_version = true
```

### Step 2: Grant Anonymous User Dashboard Permissions
Even with anonymous auth enabled, the Viewer role might not have access to the specific folders containing your dashboards.
1. Log into the `iskradocker:3000` Grafana as Admin.
2. Go to **Dashboards**.
3. Hover over the Folder containing your CEA dashboards (e.g., "General" or "CEA").
4. Click **Manage permissions**.
5. Add a permission: Set **Role** -> **Viewer** to **View**.
*(Note: Database permissions are not the issue here as they are handled locally; only folder/dashboard permissions in Grafana are needed).*

### Step 3: Restart Grafana on Iskradocker
If running in Docker:
`docker restart <grafana-container-name>`
If running natively:
`sudo systemctl restart grafana-server`

---

## Success Criteria
- Frontend proxies `/grafana` requests to `iskradocker:3000`.
- Flower and Veg monitoring pages load a single iframe showing the full dashboard.
- The Grafana top navbar (with time picker) is visible, but the left sidebar is hidden (`kiosk=tv`).
- Data loads successfully without "token not found" errors because `iskradocker` is configured correctly.