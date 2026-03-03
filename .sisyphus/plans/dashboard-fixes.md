# Dashboard Performance, Layout & Data Fixes

## TL;DR

> **Quick Summary**: Fix all remaining Dashboard issues — backend sensor suffix bug causing wrong Flower Room data, slow loading from missing timeouts, broken column widths, orphaned Device Config button, and Lab sensor data not populating.
> 
> **Deliverables**:
> - Backend `get_sensor_suffix()` fixed in 2 files (sensors.py + database.py)
> - Frontend `backendClient` timeout added + `getAllDevices()` error handling
> - Column widths changed to 37%/37%/26%
> - Device Config footer button removed
> - System section overflow scroll added
> - Broken clusterA/B API calls removed until backend is fixed, then re-added
> - Lab temp/water temp operational diagnosis
> - Frontend rebuild + deploy
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves (backend + frontend in parallel, then build/deploy)
> **Critical Path**: Backend suffix fix → Re-enable clusterA/B calls → Build → Deploy

---

## Context

### Original Request
User wants the Dashboard fixed comprehensively: it's slow, column widths are wrong, Flower Room Back cluster shows duplicate data, Lab temp/water temp aren't populated, Device Config button is redundant (now in sidebar), and the overall layout needs cleanup.

### Interview Summary
**Key Discussions**:
- Flower Room should show Front (clusterA) and Back (clusterB) side-by-side — this JSX work is already done
- Effective Setpoints must remain a single zone (not split)
- Column widths should be Veg 37%, Flower 37%, System/Lab 26%
- Device Config is already in sidebar → remove Dashboard footer button
- Light names in Flower Room should use 2-row display — already done

**Research Findings**:
- `get_sensor_suffix()` is broken in `sensors.py:322-330` AND `database.py:322-330` — only checks `cluster == "front"`, never handles `clusterA`/`clusterB`
- Correct mapping exists in `stream_processor.py:24-33` and `stream_processor.py:82-97`
- `backendClient` has NO timeout (automationClient has 30s, weatherClient has 10s)
- `getAllDevices()` is the ONLY Promise.all member without `.catch()`
- Lab temp/water temp frontend code is correct — issue is operational (onewire-worker service or Redis TTL expiry)

---

## Work Objectives

### Core Objective
Fix all Dashboard data, performance, and layout issues to deliver a fast, accurate, properly-laid-out monitoring dashboard.

### Concrete Deliverables
- 2 Python files fixed (sensors.py, database.py)
- 2 TypeScript files fixed (api.ts, Dashboard.tsx)
- Lab temp operational diagnosis report
- Production build + deployment

### Definition of Done
- [x] Flower Room clusterA returns front sensor data (`_f` suffix), clusterB returns back data (`_b` suffix)
- [x] Dashboard loads within 10 seconds even if backend is slow
- [x] Column widths visually match 37/37/26 ratio
- [x] No Device Config button in Dashboard footer
- [x] `npm run build` succeeds with 0 errors

### Must Have
- Backend suffix fix for BOTH sensors.py and database.py
- Timeout on backendClient
- Error handling on getAllDevices()
- Column width change
- Device Config button removal

### Must NOT Have (Guardrails)
- Do NOT change Effective Setpoints layout (must remain single zone)
- Do NOT modify the Front/Back JSX structure (already correct)
- Do NOT change light name 2-row display (already correct)
- Do NOT change WebSocket connection code
- Do NOT add new dependencies
- Do NOT change API endpoint signatures
- Do NOT use `replaceAll` on gap classes — only target specific elements

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest for Python, npm run build for frontend)
- **Automated tests**: Tests-after (verify existing tests still pass)
- **Framework**: pytest (backend), vite build (frontend)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — parallel):
├── Task 1: Backend suffix fix (sensors.py + database.py)
└── Task 2: Frontend fixes (api.ts timeout + Dashboard.tsx layout/cleanup)

Wave 2 (After Wave 1):
├── Task 3: Re-enable clusterA/B API calls in Dashboard.tsx (depends on Task 1)
└── Task 4: Lab temp operational diagnosis (independent but logically after backend fix)

Wave 3 (After Wave 2):
└── Task 5: Build, verify, and deploy
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 3, 5 | 2 |
| 2 | None | 5 | 1 |
| 3 | 1 | 5 | 4 |
| 4 | None | 5 | 3 |
| 5 | 1, 2, 3 | None | None (final) |

---

## TODOs

- [x] 1. Fix `get_sensor_suffix()` in backend (2 files)

  **What to do**:
  - Fix `Infrastructure/backend/app/routes/sensors.py` lines 322-330: Replace the broken function with one that handles both naming conventions (`front`/`back` AND `clusterA`/`clusterB`)
  - Fix `Infrastructure/backend/app/database.py` lines 322-330: IDENTICAL fix — same broken function exists here
  - The correct mapping is:
    - `("Flower Room", "front")` OR `("Flower Room", "clusterA")` → `"f"`
    - `("Flower Room", "back")` OR `("Flower Room", "clusterB")` → `"b"`
    - `("Veg Room", *)` → `"v"`
    - `("Lab", *)` → `""`
  - The fix for BOTH files should be:
    ```python
    def get_sensor_suffix(location: str, cluster: str) -> str:
        """Get sensor name suffix based on location and cluster."""
        if location == "Flower Room":
            if cluster in ("front", "clusterA"):
                return "f"
            return "b"
        elif location == "Veg Room":
            return "v"
        elif location == "Lab":
            return ""
        return ""
    ```
  - For `database.py`, the function is named `_get_sensor_suffix` (with underscore prefix as it's a method). Apply same logic.
  - After fixing, restart the backend service: `sudo systemctl restart cea-backend`
  - Run ruff before committing: `cd Infrastructure && ruff check --fix . && ruff format .`

  **Must NOT do**:
  - Do NOT change the function signature
  - Do NOT modify `stream_processor.py` (it already has the correct implementation)
  - Do NOT change how the function is called at line 205 of sensors.py or line 268 of database.py

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Targeted fix to 2 functions with known exact code — minimal complexity
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 3, Task 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `Infrastructure/backend/app/stream_processor.py:24-33` — The CORRECT suffix_map showing `("Flower Room", "clusterA") → "_f"` and `("Flower Room", "clusterB") → "_b"`. This is the canonical mapping to follow.
  - `Infrastructure/backend/app/stream_processor.py:82-97` — Shows how the stream processor adapts `"front"/"back"` to `"clusterA"/"clusterB"` before looking up suffix.

  **Files to modify**:
  - `Infrastructure/backend/app/routes/sensors.py:322-330` — First copy of the broken function. Called at line 205.
  - `Infrastructure/backend/app/database.py:322-330` — Second copy of the broken function. Called at line 268.

  **Acceptance Criteria**:
  - [x] `sensors.py:get_sensor_suffix("Flower Room", "clusterA")` returns `"f"`
  - [x] `sensors.py:get_sensor_suffix("Flower Room", "clusterB")` returns `"b"`
  - [x] `sensors.py:get_sensor_suffix("Flower Room", "front")` returns `"f"` (backward compat)
  - [x] `sensors.py:get_sensor_suffix("Flower Room", "back")` returns `"b"` (backward compat)
  - [x] `database.py:_get_sensor_suffix` has identical logic
  - [x] `sudo systemctl restart cea-backend` succeeds

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: ClusterA returns front sensor data
    Tool: Bash (curl)
    Preconditions: cea-backend service running on localhost:8000
    Steps:
      1. curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterA/live
      2. Parse JSON response
      3. Assert: response keys contain sensor types with "_f" suffix (e.g., "dry_bulb_f")
      4. Save response body
    Expected Result: All sensor keys use "_f" suffix
    Evidence: Response body captured

  Scenario: ClusterB returns back sensor data (different from clusterA)
    Tool: Bash (curl)
    Preconditions: cea-backend service running
    Steps:
      1. curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterA/live > /tmp/clusterA.json
      2. curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterB/live > /tmp/clusterB.json
      3. Compare: clusterA should have "_f" suffixed keys, clusterB should have "_b" suffixed keys
      4. Assert: The two responses contain DIFFERENT sensor type keys
    Expected Result: clusterA has "_f" sensors, clusterB has "_b" sensors
    Evidence: Both response files saved

  Scenario: Backward compatibility with front/back naming
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8000/api/sensors/Flower%20Room/front/live
      2. Assert: response contains "_f" suffixed keys
      3. curl -s http://localhost:8000/api/sensors/Flower%20Room/back/live
      4. Assert: response contains "_b" suffixed keys
    Expected Result: Old front/back names still work
    Evidence: Response bodies captured
  ```

  **Commit**: YES
  - Message: `fix(backend): correct get_sensor_suffix to handle clusterA/clusterB naming`
  - Files: `Infrastructure/backend/app/routes/sensors.py`, `Infrastructure/backend/app/database.py`

---

- [x] 2. Frontend performance + layout + cleanup fixes

  **What to do**:
  
  **2a. Add timeout to backendClient** (`/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/services/api.ts`):
  - At lines 32-37, the `backendClient` axios instance has NO timeout
  - Add `timeout: 10000` (10 seconds) to match the pattern of weatherClient
  - Change:
    ```typescript
    this.backendClient = axios.create({
      baseURL: BACKEND_API_URL,
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: 10000, // 10 second timeout
    });
    ```

  **2b. Add .catch() to getAllDevices()** (`/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/Dashboard.tsx`):
  - At line 137, `apiClient.getAllDevices()` is the ONLY Promise.all member without `.catch()`
  - If this call fails, the entire `Promise.all` rejects and NO data loads
  - Change line 137 from `apiClient.getAllDevices()` to `apiClient.getAllDevices().catch(() => [])`

  **2c. Change column widths to 37%/37%/26%** (Dashboard.tsx):
  - At line 504, change from `grid grid-cols-1 lg:grid-cols-3 gap-2` to a flex layout
  - Replace the className with: `flex-1 flex flex-col lg:flex-row gap-2 min-h-0`
  - Then add explicit width classes to each of the 3 column divs:
    - Column 1 (Veg Room, line 507): Add `lg:w-[37%]` to its className
    - Column 2 (Flower Room): Add `lg:w-[37%]` to its className
    - Column 3 (System/Lab, line 926): Add `lg:w-[26%]` to its className

  **2d. Remove Device Config footer button** (Dashboard.tsx):
  - Delete lines 1259-1273 entirely (the `{/* Settings Section */}` div containing the button)
  - The Device Config is already accessible via sidebar nav item (Devices → `/devices`)

  **2e. Add overflow-y-auto to System section** (Dashboard.tsx):
  - At line 929, the System section div has `flex-1 border-b border-border-subtle pb-2 mb-2`
  - Add `overflow-y-auto`: `flex-1 overflow-y-auto border-b border-border-subtle pb-2 mb-2`

  **2f. Remove broken clusterA/B API calls** (Dashboard.tsx):
  - At lines 149-150, remove the `getLiveSensorData('Flower Room', 'clusterA/clusterB')` calls from Promise.all
  - Remove `clusterAData` and `clusterBData` from the destructuring at lines 129-130
  - Remove the clusterA/B processing code at lines 171-185 (clusterAFlat/clusterBFlat)
  - These will be re-added in Task 3 after the backend is fixed

  **Must NOT do**:
  - Do NOT change the Front/Back JSX display structure (already correct)
  - Do NOT change Effective Setpoints layout
  - Do NOT change light name display format
  - Do NOT change WebSocket code
  - Do NOT use `replaceAll` on gap/grid classes

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: All changes are targeted edits with exact line numbers
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/services/api.ts:39-53` — automationClient and weatherClient both have timeout configured. Follow this pattern.
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/Dashboard.tsx:147-155` — All other Promise.all members have `.catch()` handlers.

  **Files to modify**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/services/api.ts` — Add timeout to backendClient (lines 32-37)
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/Dashboard.tsx` — Multiple edits as described above

  **Acceptance Criteria**:
  - [x] `backendClient` has `timeout: 10000` in api.ts
  - [x] `getAllDevices()` has `.catch(() => [])` in Promise.all
  - [x] Main content container uses flex layout with 37%/37%/26% widths
  - [x] No Device Config button in Dashboard footer
  - [x] System section has `overflow-y-auto` class
  - [x] clusterA/B calls and processing code removed from Promise.all
  - [x] `npm run build` succeeds with 0 errors (run from `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`)

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Frontend builds successfully
    Tool: Bash (npm)
    Preconditions: Node.js installed, dependencies installed
    Steps:
      1. Run: npm run build (in /home/antoine/ProjectCEA-ui/Infrastructure/frontend)
      2. Assert: exit code 0
    Expected Result: Build completes with 0 errors
    Evidence: Build output captured

  Scenario: Verify timeout added
    Tool: Bash (grep)
    Steps:
      1. grep -n "timeout" /home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/services/api.ts
      2. Assert: 3 timeout configurations found (backend, automation, weather)
    Expected Result: All 3 clients have timeout
    Evidence: grep output captured

  Scenario: Verify Device Config button removed
    Tool: Bash (grep)
    Steps:
      1. grep -n "Device Config" /home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/Dashboard.tsx
      2. Assert: NO matches found
    Expected Result: Zero occurrences of "Device Config"
    Evidence: grep output captured
  ```

  **Commit**: YES
  - Message: `fix(frontend): add timeout, fix layout widths, remove device config button, cleanup broken calls`
  - Files: `Infrastructure/frontend/src/services/api.ts`, `Infrastructure/frontend/src/pages/Dashboard.tsx`

---

- [x] 3. Re-enable clusterA/B live sensor API calls

  **What to do**:
  - After Task 1 fixes the backend, re-add the clusterA/B API calls to Dashboard.tsx Promise.all
  - Add back to the destructured array: `clusterAData, clusterBData,`
  - Add back to Promise.all:
    ```typescript
    apiClient.getLiveSensorData('Flower Room', 'clusterA').catch(() => ({})),
    apiClient.getLiveSensorData('Flower Room', 'clusterB').catch(() => ({})),
    ```
  - Add back the processing code for clusterA/B data:
    ```typescript
    const clusterAFlat: Record<string, number> = {}
    const clusterBFlat: Record<string, number> = {}
    for (const [sensorType, resp] of Object.entries(clusterAData || {})) {
      const dp = Array.isArray((resp as any)?.data) && (resp as any).data.length > 0 ? (resp as any).data[0] : null
      if (dp?.value != null) clusterAFlat[`Flower Room_clusterA_${sensorType}`] = dp.value
    }
    for (const [sensorType, resp] of Object.entries(clusterBData || {})) {
      const dp = Array.isArray((resp as any)?.data) && (resp as any).data.length > 0 ? (resp as any).data[0] : null
      if (dp?.value != null) clusterBFlat[`Flower Room_clusterB_${sensorType}`] = dp.value
    }
    if (Object.keys(clusterAFlat).length > 0) setSensorData(prev => ({ ...prev, ...clusterAFlat }))
    if (Object.keys(clusterBFlat).length > 0) setSensorData(prev => ({ ...prev, ...clusterBFlat }))
    ```

  **Must NOT do**:
  - Do NOT re-add without `.catch()` handlers
  - Do NOT change the display JSX — only data fetching and processing

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Re-adding previously-removed code with known pattern
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential after Wave 1)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/src/pages/Dashboard.tsx:136-185` — Original Promise.all block and processing code to restore

  **Acceptance Criteria**:
  - [ ] `getLiveSensorData` calls for clusterA and clusterB present in Promise.all with `.catch()`
  - [ ] clusterAFlat/clusterBFlat processing code re-added
  - [ ] `npm run build` succeeds with 0 errors

  **Commit**: YES
  - Message: `feat(frontend): re-enable Flower Room clusterA/B live sensor fetching`
  - Files: `Infrastructure/frontend/src/pages/Dashboard.tsx`

---

- [x] 4. Diagnose Lab temp/water temp data issue

  **What to do**:
  - This is an operational diagnostic task — the frontend code is confirmed correct
  - Check if the `onewire-worker` service is running: `systemctl status onewire-worker`
  - Check if Redis keys exist: `redis-cli GET sensor:lab_temp` and `redis-cli GET sensor:water_temperature`
  - Check key TTL: `redis-cli TTL sensor:lab_temp` (should be ≤10s if recently written)
  - If service is not running, start it: `sudo systemctl start onewire-worker`
  - If service is running but keys are empty, check logs: `journalctl -u onewire-worker -n 50`
  - Test the backend API endpoint: `curl -s http://localhost:8000/api/sensors/Lab/main/live`
  - Report findings

  **Must NOT do**:
  - Do NOT modify frontend code
  - Do NOT modify backend code for Lab sensors
  - Do NOT change Redis TTL configuration

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Operational diagnosis with shell commands
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 3)
  - **Blocks**: Task 5 (information needed)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/README.md` — Lists `onewire-worker-service` as "1-Wire DS18B20 (lab/water temp) on GPIO 24"
  - `Infrastructure/backend/app/routes/sensors.py:209-210` — Lab sensor types: `["lab_temp", "water_temperature"]`
  - Redis key pattern: `sensor:lab_temp`, `sensor:water_temperature` (10s TTL)

  **Acceptance Criteria**:
  - [ ] `systemctl status onewire-worker` checked and state reported
  - [ ] `redis-cli GET sensor:lab_temp` result reported
  - [ ] `redis-cli GET sensor:water_temperature` result reported
  - [ ] `curl -s http://localhost:8000/api/sensors/Lab/main/live` response captured
  - [ ] If service was stopped: started and enabled
  - [ ] Diagnostic summary provided

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Check onewire-worker and Lab data pipeline
    Tool: Bash
    Steps:
      1. systemctl status onewire-worker → report active/inactive/failed
      2. If inactive: sudo systemctl start onewire-worker && sudo systemctl enable onewire-worker
      3. Wait 15 seconds
      4. redis-cli GET sensor:lab_temp → report value or nil
      5. redis-cli GET sensor:water_temperature → report value or nil
      6. curl -s http://localhost:8000/api/sensors/Lab/main/live → report response
    Expected Result: Service running and data flowing
    Evidence: All outputs captured
  ```

  **Commit**: NO (operational task)

---

- [x] 5. Build, verify, and deploy

  **What to do**:
  - Build the frontend: `npm run build` in `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/`
  - Copy built dist to production location (follow existing deploy pattern)
  - Restart automation-service: `sudo systemctl restart automation-service`
  - Verify backend was already restarted in Task 1
  - Run end-to-end verification

  **Must NOT do**:
  - Do NOT run deploy.sh unless asked
  - Do NOT modify any code

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Build + deploy + verification
  - **Skills**: [`playwright`]
    - `playwright`: Visual verification of layout changes

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final)
  - **Blocks**: None
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/README.md` — Build: `npm run build`, then restart automation-service
  - `/home/antoine/ProjectCEA-ui/Infrastructure/frontend/AGENTS.md` — "After build, restart automation-service to serve new dist/"

  **Acceptance Criteria**:
  - [ ] `npm run build` exits 0
  - [ ] `sudo systemctl restart automation-service` succeeds
  - [ ] Dashboard accessible at configured URL
  - [ ] Flower Room clusterA and clusterB return different sensor values via API

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Dashboard loads successfully
    Tool: Playwright (playwright skill)
    Steps:
      1. Navigate to: http://localhost:8001
      2. Wait for: page content visible (timeout: 15s)
      3. Assert: 3 columns visible (Veg, Flower, System)
      4. Screenshot: .sisyphus/evidence/task-5-dashboard-loaded.png
    Expected Result: Dashboard renders with all 3 columns
    Evidence: .sisyphus/evidence/task-5-dashboard-loaded.png

  Scenario: Device Config button is gone
    Tool: Playwright (playwright skill)
    Steps:
      1. Navigate to: http://localhost:8001
      2. Wait for page load (timeout: 15s)
      3. Assert: text "Device Config" is NOT present on page
      4. Screenshot: .sisyphus/evidence/task-5-no-device-config.png
    Expected Result: No Device Config button on dashboard
    Evidence: .sisyphus/evidence/task-5-no-device-config.png

  Scenario: Backend returns correct cluster data
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterA/live | python3 -m json.tool
      2. curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterB/live | python3 -m json.tool
      3. Assert: clusterA has "_f" suffix sensors, clusterB has "_b" suffix sensors
      4. Assert: responses are DIFFERENT
    Expected Result: Front and Back return distinct data
    Evidence: Response bodies captured
  ```

  **Commit**: NO (deployment only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `fix(backend): correct get_sensor_suffix to handle clusterA/clusterB naming` | sensors.py, database.py | curl API test |
| 2 | `fix(frontend): add timeout, fix layout widths, remove device config button, cleanup broken calls` | api.ts, Dashboard.tsx | npm run build |
| 3 | `feat(frontend): re-enable Flower Room clusterA/B live sensor fetching` | Dashboard.tsx | npm run build |

---

## Success Criteria

### Verification Commands
```bash
# Backend fix verified
curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterA/live  # Should have _f sensors
curl -s http://localhost:8000/api/sensors/Flower%20Room/clusterB/live  # Should have _b sensors

# Frontend build
cd /home/antoine/ProjectCEA-ui/Infrastructure/frontend && npm run build  # exit 0

# Services running
systemctl status cea-backend automation-service  # active (running)

# Lab diagnosis
redis-cli GET sensor:lab_temp  # numeric value or nil
```

### Final Checklist
- [x] Flower Room clusterA returns "_f" sensor data
- [x] Flower Room clusterB returns "_b" sensor data
- [x] Dashboard loads within 10 seconds even if backend is slow
- [x] Column widths are 37%/37%/26%
- [x] No Device Config button in Dashboard footer
- [x] System section has scroll overflow
- [x] `npm run build` passes with 0 errors
- [x] Both services running: cea-backend, automation-service
- [x] Lab temp diagnosis complete
