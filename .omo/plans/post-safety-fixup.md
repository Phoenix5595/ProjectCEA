# post-safety-fixup - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** The frontend control pages will load again. Right now they show "Unknown location/cluster" because the production database is missing 3 migrations from the recent production-safety work — the code expects tables and data that were never created. This plan runs those migrations, cleans up stale cache keys, and restarts the service so everything loads correctly.

**Why this approach:** The production-safety plan shipped all its code changes (scheduler rewrite, new light intensity tables, DELETE guard, documentation) across 7 commits and 2 deploys — but `deploy.sh` has no migration step, so the database schema was never updated. The device_registry is empty (0 rows), the light_target_intensity table doesn't exist, and the scheduler is stuck in failsafe mode logging warnings every tick. The fix is purely operational: backup, migrate, purge stale Redis keys, restart. No code changes needed.

**What it will NOT do:**
- Will NOT modify `deploy.sh` to auto-run migrations — you chose manual migration control.
- Will NOT add frontend ErrorBoundaries or error states — you scoped that out.
- Will NOT modify any code files — this is a database + Redis + restart operation only.

**Effort:** Quick
**Risk:** Medium — touching production database; mitigated by pg_dump backup before any migration.
**Decisions to sanity-check:** None — root cause is confirmed, fix is straightforward.

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Quick, Medium risk — run 3 pending alembic migrations against production cea_sensors (009 seed device_registry, 03fbbb9b5ba3 light tables, 04fbbb9b5ba4 cleanup), purge stale Redis schedule-state keys, restart automation-service, verify control pages load

## Scope
### Must have
- Apply 3 pending alembic migrations to production database `cea_sensors`:
  - `009_seed_device_registry_from_yaml` — seeds device_registry from automation_config.yaml (currently 0 rows)
  - `03fbbb9b5ba3_add_light_targets_and_programs` — creates light_target_intensity + light_programs tables
  - `04fbbb9b5ba4_remove_obsolete_light_schedule_rows` — deletes SUN/MOON rows (already 0, idempotent)
- Verify the migration fixes the root cause: `/api/devices/Flower Room/main` returns 200 (was 404 "Unknown location/cluster")
- Verify `light_target_intensity` table is populated with migrated intensity rows
- Purge stale Redis keys from dead SchedulesMixin (T10 acceptance criteria, never executed)
- Restart automation-service so it loads the new schema + data into Scheduler caches at startup
- Verify the control-loop warnings ("no mode_params for X/Y - returning True failsafe") stop after restart

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT modify deploy.sh — user explicitly chose manual migration control
- Do NOT add ErrorBoundaries or frontend hardening — user explicitly scoped this out
- Do NOT auto-run migrations in deploy.sh — user explicitly chose manual only
- Do NOT modify any code files — this is a database + Redis + restart operation only
- Do NOT run migrations against `cea_sensors_test` by mistake — this plan targets PRODUCTION `cea_sensors`
- Do NOT skip the pre-migration backup (pg_dump)
- Do NOT touch the frontend code — the control pages will work once the backend serves real data

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: none (no code changes, only DB migrations + Redis cleanup + service restart)
- Evidence: .omo/evidence/task-<N>-post-safety-fixup.<ext>

## Execution strategy
### Parallel execution waves
- **Wave 1 (sequential):** T1 (backup → migrate → restart → verify). Single linear flow — migrations must run in order, restart must happen after migrations, verification must happen after restart.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 2 | — |
| 2 | 1 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Backup production DB, run 3 pending alembic migrations, purge stale Redis keys, restart automation-service
  What to do / Must NOT do:
  - **Step 1 — Backup:** Create a pg_dump of `cea_sensors` before any migration:
    ```bash
    sudo -u postgres pg_dump cea_sensors > /tmp/cea_sensors_backup_$(date +%Y%m%d_%H%M%S).sql
    ```
    Verify the backup file is non-empty (should be multiple MB).
  - **Step 2 — Pre-migration state check:** Record current state for evidence:
    ```bash
    sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"
    # Expected: 008_device_registry
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM device_registry;"
    # Expected: 0
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM information_schema.tables WHERE table_name = 'light_target_intensity';"
    # Expected: 0
    ```
  - **Step 3 — Run migrations:** From the deployed automation-service directory, activate the venv and run alembic:
    ```bash
    cd /opt/projectcea/current/Infrastructure/automation-service
    source .venv/bin/activate
    alembic upgrade head
    ```
    This applies: 009 (seed device_registry) → 03fbbb9b5ba3 (create light tables + migrate intensities) → 04fbbb9b5ba4 (delete SUN/MOON rows).
    - **Must NOT** run `alembic downgrade` under any circumstances.
    - **Must NOT** run migrations against `cea_sensors_test` — this is the production database `cea_sensors`.
    - **Must NOT** interrupt the migration mid-run — if it fails, check the backup and report immediately.
  - **Step 4 — Post-migration DB verification:** Verify each migration applied correctly:
    ```bash
    sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"
    # Expected: 04fbbb9b5ba4
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM device_registry;"
    # Expected: 50+ rows (seeded from YAML)
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM light_target_intensity;"
    # Expected: 20+ rows (migrated from mode_parameters.main_light_intensity per mode)
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM light_programs;"
    # Expected: 0 (empty, ready for future use)
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM schedules s JOIN device_registry d ON s.device_name = d.device_name WHERE d.device_type = 'light' AND s.mode IN ('SUN','MOON');"
    # Expected: 0 (deleted by migration 04fbbb9b5ba4)
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM schedules WHERE device_name = 'room_schedule';"
    # Expected: 0 (deleted by migration 04fbbb9b5ba4)
    ```
  - **Step 5 — Purge stale Redis keys:** Remove the 3 dead schedule-state key forms written by the deleted SchedulesMixin:
    ```bash
    redis-cli --scan --pattern "schedule:state:*" | xargs -r redis-cli DEL
    redis-cli --scan --pattern "cea:schedule:*:state" | xargs -r redis-cli DEL
    redis-cli --scan --pattern "cea:schedule:state:*" | xargs -r redis-cli DEL
    # Verify clean:
    redis-cli --scan --pattern "schedule:state:*" | wc -l  # Expected: 0
    redis-cli --scan --pattern "cea:schedule:*:state" | wc -l  # Expected: 0
    redis-cli --scan --pattern "cea:schedule:state:*" | wc -l  # Expected: 0
    ```
  - **Step 6 — Restart automation-service:** Restart so the startup data-loading code (T6) loads mode_parameters + light_target_intensity + light_programs + device_lookup into Scheduler caches:
    ```bash
    sudo systemctl restart automation-service
    # Wait for service to be active:
    sudo systemctl is-active automation-service  # Expected: active
    ```
  - **Step 7 — Post-restart API verification:** Verify the root-cause endpoint now returns 200:
    ```bash
    # Need API key (from /opt/projectcea/current/Infrastructure/frontend/.env.production)
    API_KEY="0e1e28754260f4e810ff8a9503336ad6008e8d30d837b7e2e9819e54fa3479e9"
    
    # The endpoint that was returning "Unknown location/cluster":
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Flower%20Room/main
    # Expected: 200 (was 404)
    
    # Zone status (was returning empty):
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Flower%20Room/main/zone-status | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'lights count: {len(d.get(\"lights\",[]))}')"
    # Expected: lights count > 0
    
    # Device registry (was returning empty):
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'devices count: {len(d) if isinstance(d,list) else len(d.get(\"devices\",[]))}')"
    # Expected: devices count > 0
    ```
  - **Step 8 — Post-restart log verification:** Verify the failsafe warnings stopped:
    ```bash
    # Wait 30 seconds after restart for control loop to tick:
    sleep 30
    # Check for failsafe warnings (should be GONE or greatly reduced):
    journalctl -u automation-service --since "30 seconds ago" --no-pager 2>&1 | grep -c "no mode_params"
    # Expected: 0 (was every tick)
    ```
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/alembic/versions/009_seed_device_registry_from_yaml.py` (seeds device_registry from YAML)
  - `Infrastructure/automation-service/alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py` (creates light tables + migrates intensities)
  - `Infrastructure/automation-service/alembic/versions/04fbbb9b5ba4_remove_obsolete_light_schedule_rows.py` (deletes SUN/MOON + room_schedule rows)
  - `Infrastructure/automation-service/app/cluster_config.py:28-48` (ensure_configured_cluster — raises 404 "Unknown location/cluster" when device_configs is empty)
  - `Infrastructure/automation-service/app/config.py` (get_devices() — reads from device_registry table, was flipped in commit 8884bae)
  - `Infrastructure/automation-service/.env.production` (ALEMBIC_DATABASE_URL or DB connection config)
  - `Infrastructure/frontend/.env.production` (VITE_CEA_API_KEY for API auth)
  Acceptance criteria (agent-executable):
  - `sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"` returns `04fbbb9b5ba4`
  - `sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM device_registry;"` returns > 0
  - `sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM light_target_intensity;"` returns > 0
  - `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Flower%20Room/main` returns `200`
  - `journalctl -u automation-service --since "30 seconds ago" --no-pager 2>&1 | grep -c "no mode_params"` returns `0`
  - `redis-cli --scan --pattern "schedule:state:*" | wc -l` returns `0`
  QA scenarios:
  - happy: all 3 migrations apply cleanly, device_registry seeded, light_target_intensity populated, API returns 200, control loop stops failsafe warnings. Evidence `.omo/evidence/task-1-post-safety-fixup.txt`
  - failure: migration fails (e.g., YAML is malformed, FK constraint violation) → alembic rolls back, report the error, restore from backup. Evidence `.omo/evidence/task-1-post-safety-fixup-failure.txt`
  Commit: N | (database operations + restart, no code commit)

- [x] 2. End-to-end verification: control pages load, devices visible, light targets correct
  What to do / Must NOT do:
  - After T1 completes, verify the full control page data flow works:
  - **Step 1 — Verify all control-page endpoints return valid data:**
    ```bash
    API_KEY="0e1e28754260f4e810ff8a9503336ad6008e8d30d837b7e2e9819e54fa3479e9"
    
    # Devices endpoint (root cause of "Unknown location/cluster"):
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Flower%20Room/main | python3 -c "import sys,json; d=json.load(sys.stdin); devs=d.get('devices',{}); print(f'devices: {len(devs)}'); [print(f'  {k}: type={v.get(\"device_type\")}') for k,v in list(devs.items())[:5]]"
    # Expected: devices > 0, lights listed with device_type='light'
    
    # Zone lights status:
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Flower%20Room/main/zone-status | python3 -c "import sys,json; d=json.load(sys.stdin); lights=d.get('lights',[]); print(f'lights: {len(lights)}'); [print(f'  {l.get(\"device\")}: intensity={l.get(\"intensity\")}, target={l.get(\"day_target_intensity\")}') for l in lights[:5]]"
    # Expected: lights > 0, each with target intensity from light_target_intensity
    
    # Room schedule:
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/room-schedule/Flower%20Room/main | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d, indent=2))"
    # Expected: day_start_time, night_start_time, ramp_up_duration, ramp_down_duration
    
    # Veg Room devices too:
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Veg%20Room/main
    # Expected: 200
    ```
  - **Step 2 — Verify Scheduler startup data loading:** Check the service logs for the startup data loading messages:
    ```bash
    journalctl -u automation-service --since "5 minutes ago" --no-pager | grep -i "mode_param\|intensit\|program\|scheduler_ready\|loaded"
    # Expected: data loading log entries (from T6 background_tasks startup)
    ```
  - **Step 3 — Verify no crashes or errors in logs:**
    ```bash
    journalctl -u automation-service --since "5 minutes ago" --no-pager 2>&1 | grep -i "error\|exception\|traceback\|does not exist\|relation" | grep -v "WARNING" | tail -20
    # Expected: empty (no errors)
    ```
  - **Step 4 — Verify hardware batch execution succeeds:**
    ```bash
    journalctl -u automation-service --since "1 minute ago" --no-pager 2>&1 | grep "Hardware batch" | tail -5
    # Expected: "Hardware batch: N ok, 0 failed" (was "0 ok, 3 failed")
    ```
  - Must NOT make HTTP requests with DELETE/POST/PUT — GET only for verification.
  - Must NOT modify any code or config.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: — | Can parallelize with: —
  References:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` (the control page that was showing the error — calls /api/devices/{location}/{cluster} on mount)
  - `Infrastructure/automation-service/app/routes/devices.py:113-162` (get_devices_for_location_cluster — was returning 404)
  - `Infrastructure/automation-service/app/cluster_config.py:28-48` (ensure_configured_cluster — raises 404 when device_configs is empty)
  Acceptance criteria (agent-executable):
  - `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Flower%20Room/main` returns `200`
  - `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Veg%20Room/main` returns `200`
  - Zone-status response has `lights` array with length > 0
  - `journalctl -u automation-service --since "1 minute ago" --no-pager 2>&1 | grep "Hardware batch" | tail -1` shows "0 failed"
  QA scenarios:
  - happy: all endpoints return 200 with real data, hardware batch succeeds, no errors in logs. Evidence `.omo/evidence/task-2-post-safety-fixup.txt`
  - failure: endpoint still returns 404 → migration 009 didn't seed devices, or config.get_devices() has a different code path. Evidence `.omo/evidence/task-2-post-safety-fixup-failure.txt`
  Commit: N | (verification only, no code changes)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [~] F1. Plan compliance audit — scope is DB migration + Redis cleanup only, no code changes to audit
- [~] F2. Code quality review — no code changes, N/A
- [~] F3. Real manual QA — user will verify frontend control pages load (user confirmed the symptom is "error message shown")
- [~] F4. Scope fidelity — no code scope to verify

## Commit strategy
- No commits — this plan is database operations + Redis cleanup + service restart only. No code files are modified.

## Success criteria
- `alembic_version` is `04fbbb9b5ba4` (was `008_device_registry`).
- `device_registry` has 50+ rows seeded from YAML (was 0).
- `light_target_intensity` table exists and has 20+ rows migrated from `mode_parameters.main_light_intensity`.
- `light_programs` table exists (0 rows, ready for future use).
- Stale Redis schedule-state keys purged (`schedule:state:*`, `cea:schedule:*:state`, `cea:schedule:state:*` all return 0).
- `/api/devices/Flower Room/main` returns HTTP 200 (was 404 "Unknown location/cluster").
- `/api/lights/Flower Room/main/zone-status` returns lights array with > 0 entries.
- `journalctl` shows no "no mode_params" failsafe warnings after restart (was every tick).
- `journalctl` shows "Hardware batch: N ok, 0 failed" (was "0 ok, 3 failed").
- Automation service startup loads mode_parameters + intensities + programs into Scheduler caches (T6 code, was waiting for the tables to exist).
