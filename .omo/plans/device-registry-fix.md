# device-registry-fix - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** The device list in the frontend will populate with all 13 devices. Right now it shows nothing because a Pydantic validation regex rejects the human-readable device names that were accidentally seeded into the `device_name` column instead of `display_name`. This plan fixes the data (canonical machine names in `device_name`, human-readable names in `display_name`), makes the list resilient to bad rows (skip instead of wipe), and then purges all the dead YAML-writing code that the previous two plans were supposed to remove.

**Why this approach:** Two previous plans (centralized-device-table and production-safety) partially deprecated YAML as the device data source but left dead code: two endpoints that write to YAML (`POST/DELETE /api/devices/channels/{channel}`), a YAML fallback in `config.get_devices()`, and the `devices:` block in `automation_config.yaml`. The migration that seeded the registry from YAML also put the YAML key (human-readable name) into `device_name` instead of `display_name` for non-light devices. The fix is in three waves: fix the data + model (deploy 1), then remove the dead YAML code (deploy 2), then verify.

**What it will NOT do:**
- Will NOT add frontend ErrorBoundaries or error states — user scoped this out.
- Will NOT auto-run migrations in `deploy.sh` — user chose manual migration control.
- Will NOT remove the YAML config file itself — it still holds hardware, control, and sensors config.
- Will NOT touch the `LightDevice` model — it's correct and all lights already match the pattern.

**Effort:** Medium
**Risk:** Medium — touches production database data (migration 010) and removes endpoints.
**Decisions to sanity-check:** Canonical naming scheme `<type>_<room>_<index>` for non-lights (e.g., `heater_f_1`, `dehumidifier_f_1`, `fan_f_1`). Full YAML purge of the `devices:` block. Three-wave structure with two deploys.

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Medium, Medium risk — fix device_registry seed data (migration 010: canonicalize device_name, move human-readable to display_name), fix get_all_devices_flat per-row error handling, remove dead POST/DELETE channels endpoints + ChannelDeviceUpdate schema + YAML fallback in get_devices + devices block from YAML; two deploys

## Scope
### Must have
- Fix `Device.device_name` regex pattern to match the canonical `<type>_<room>_<index>` scheme for ALL device types (not just lights). Lights already use `light_f_1`, `light_v_1`. Non-lights must follow: `heater_f_1`, `dehumidifier_f_1`, `fan_f_1`, `exhaust_fan` stays as-is only if it matches the pattern.
- Write alembic migration `010_canonicalize_device_names` that:
  - Canonicalizes all non-light `device_name` values to `<type>_<room>_<index>` format
  - Moves the current human-readable `device_name` values (`Heater Flower`, `Midea Cube 50 pints`, `Exhaust 4 inches`, etc.) into `display_name` (currently NULL)
  - Is idempotent (running twice is a no-op)
  - Is reversible in the sense that `display_name` is preserved (downgrade copies `display_name` back to `device_name` for non-lights)
- Fix `get_all_devices_flat()` error handling: one bad row should NOT wipe the entire list — skip the bad row, log a warning, continue.
- Remove `POST /api/devices/channels/{channel}` endpoint — writes to YAML, marked deprecated in centralized-device-table plan, has ZERO frontend callers (`updateChannelDevice` in api.ts is never called by any component).
- Remove `DELETE /api/devices/channels/{channel}` endpoint — writes to YAML, has ZERO frontend callers (`clearChannelDevice` in api.ts is never called by any component).
- Remove the corresponding `updateChannelDevice()` and `clearChannelDevice()` methods from frontend `api.ts` — dead code, zero callers.
- Remove the `ChannelDeviceUpdate` schema from `app/schemas/device.py` — only used by the removed `POST /api/devices/channels/{channel}` endpoint.
- Remove the YAML fallback in `config.py:get_devices()` (lines 296-297: `if self._device_repo is None: return self._config.get("devices", {})`). Replace with: log ERROR and return `{}` when `_device_repo is None` (the DB is the only data source now).
- Remove the bootstrap warning code in `config.py:get_devices()` (lines 299-306) — no longer needed since the YAML fallback is gone.
- Remove the `devices:` block from `automation_config.yaml` — devices are DB-only. Keep `hardware:`, `control:`, `sensors:` blocks (those are still YAML-sourced).
- Remove `write_full_config()` calls from `devices.py` — the two dead endpoints that called it are removed. If `write_full_config` is no longer called by any route, keep the method on `config.py` itself (it's used by `system_config.py:201` which writes non-device config) but remove the device-specific usages.
- Deploy Wave 1 (model fix + migration + error handling).
- Deploy Wave 3 (dead code removal).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT modify the `LightDevice` model — its pattern `^light_[fvlo]_\d+$` is correct and all lights already match.
- Do NOT remove `GET /api/devices/channels` — the relay matrix (`RelayChannelMatrix.tsx`) and `DeviceManager.tsx` depend on it. It reads from DB via `get_all_as_hierarchy()` and does NOT write to YAML.
- Do NOT remove `GET /api/devices` (devices.py:87-110) — it returns relay states, consumed by `useSensorPolling.ts`.
- Do NOT remove `config.write_full_config()` method itself — it's still used by `system_config.py:201` for non-device config writing (hardware, control params, etc.).
- Do NOT remove `automation_config.yaml` — it still holds `hardware:`, `control:`, `sensors:` blocks. Only remove the `devices:` block.
- Do NOT change `config.get_devices()` return type or contract — it still returns `dict[str, Any]` hierarchy. Just remove the YAML fallback branch.
- Do NOT add ErrorBoundaries or frontend hardening — user explicitly scoped this out in the previous plan.
- Do NOT auto-run migrations in `deploy.sh` — user explicitly chose manual migration control.
- Do NOT touch `schedules.yaml` or `rules.yaml` — those are separate config files, not the `devices:` block.
- Do NOT remove the `RelayChannelMatrix` component — it reads from `GET /api/devices/channels` which stays.
- Do NOT modify `DfrBoardsPanel` — it reads from `GET /api/lights/dfr/assignments` which reads from `config.get_devices()` (now DB-only after Wave 3). It will work correctly once the registry has real data.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + framework: pytest (backend), vitest (frontend)
- Evidence: .omo/evidence/task-<N>-device-registry-fix.<ext>

## Execution strategy
### Parallel execution waves
- **Wave 1 (code + migration):** T1 (alembic migration 010), T2 (fix Device model pattern + get_all_devices_flat error handling). T1 and T2 are independent code changes but T1's migration depends on T2's new pattern being in the deployed code. Deploy after T1+T2.
- **Wave 2 (deploy 1 + verify):** T3 (deploy + run migration + verify DeviceTable populates + `GET /api/devices/registry` returns 13 devices).
- **Wave 3 (dead code removal):** T4 (remove POST/DELETE channels endpoints), T5 (remove ChannelDeviceUpdate schema + api.ts dead methods), T6 (remove YAML fallback in get_devices + remove devices: block from YAML). T4, T5, T6 are independent files. Deploy after all three.
- **Wave 4 (deploy 2 + verify):** T7 (deploy + verify all endpoints still work + no YAML writing anywhere).
- **CRITICAL GUARDRAIL:** Do NOT deploy Wave 1 without the migration. The new Device model pattern must match the migration output. Deploy Wave 1 code + run migration 010 in the same deploy window.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 3 | 2 |
| 2 | — | 3 | 1 |
| 3 | 1, 2 | 4, 5, 6 | — |
| 4 | 3 | 7 | 5, 6 |
| 5 | 3 | 7 | 4, 6 |
| 6 | 3 | 7 | 4, 5 |
| 7 | 4, 5, 6 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Alembic migration 010: canonicalize non-light device_name + move human-readable to display_name
  What to do / Must NOT do:
  - **Create `Infrastructure/automation-service/alembic/versions/010_canonicalize_device_names.py`:**
  - **For each non-light device in device_registry:**
    - If `device_name` does NOT match `^[a-z][a-z0-9]*_[fvlo]_\d+$`:
      - Copy current `device_name` to `display_name` (only if `display_name` is NULL or empty)
      - Generate canonical `device_name` as `<type>_<room_prefix>_<index>` where:
        - `<type>` = `device_type` (e.g., `heating` → `heater`, `dehumidifier` → `dehumidifier`, `fan` → `fan`)
        - `<room_prefix>` = `f` for Flower Room, `v` for Veg Room, `l` for Lab, `o` for Outside
        - `<index>` = 1-based index within the same `(device_type, room)` group
      - UPDATE the row with the new `device_name` and `display_name`
    - If `device_name` already matches the pattern: skip (idempotent)
  - **Type alias map** (canonicalize `device_type` to the `<type>` part of the name):
    - `heating` → `heater`
    - `dehumidifier` → `dehumidifier`
    - `fan` → `fan`
    - `exhaust` → `exhaust` (already matches, but unlikely to exist)
    - `humidifier` → `humidifier`
    - `co2` → `co2`
    - `light` → skip (lights already handled by LightDevice pattern)
  - **Room prefix map:**
    ```python
    _ROOM_PREFIXES = {"Flower Room": "f", "Veg Room": "v", "Lab": "l", "Outside": "o"}
    ```
  - **Expected result after migration:**
    | Current device_name      | New device_name        | display_name          | device_type  | location     |
    | ------------------------ | ---------------------- | --------------------- | ------------ | ------------ |
    | `Heater Flower`            | `heater_f_1`             | `Heater Flower`         | heating      | Flower Room  |
    | `Midea Cube 50 pints`      | `dehumidifier_f_1`       | `Midea Cube 50 pints`   | dehumidifier | Flower Room  |
    | `exhaust_fan` (Flower)     | `fan_f_1`                | `exhaust_fan`           | fan          | Flower Room  |
    | `light_f_1`                | `light_f_1` (no change)  | `Chilled Front`         | light        | Flower Room  |
    | `Heater Veg`               | `heater_v_1`             | `Heater Veg`            | heating      | Veg Room     |
    | `Ivation 35 pints`         | `dehumidifier_v_1`       | `Ivation 35 pints`      | dehumidifier | Veg Room     |
    | `Exhaust 4 inches`         | `fan_v_1`                | `Exhaust 4 inches`      | fan          | Veg Room     |
    | `exhaust_fan` (Veg)        | `fan_v_2`                | `exhaust_fan`           | fan          | Veg Room     |
  - **downgrade():** Copy `display_name` back to `device_name` for non-light devices where `display_name` is not NULL and `device_name` matches the canonical pattern. This is best-effort reversibility.
  - **Must NOT** touch light devices — they already match the `LightDevice` pattern `^light_[fvlo]_\d+$`.
  - **Must NOT** change `device_id` — only `device_name` and `display_name`.
  - **Must NOT** run against `cea_sensors_test` — this migration targets PRODUCTION `cea_sensors`.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3 | Can parallelize with: 2
  References:
  - `Infrastructure/automation-service/alembic/versions/009_seed_device_registry_from_yaml.py:99` (line that put YAML key into device_name — the bug source)
  - `Infrastructure/automation-service/app/models/device_registry.py:37-40` (Device.device_name regex pattern)
  - `Infrastructure/automation-service/app/models/device_registry.py:67-69` (LightDevice.device_name regex pattern — DO NOT CHANGE)
  - `Infrastructure/automation-service/alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py` (migration revision chain: 03fbbb9b5ba3 → 04fbbb9b5ba4 → 010)
  Acceptance criteria:
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT device_name, display_name, device_type, location FROM device_registry WHERE device_type != 'light' ORDER BY location, device_type;"` — all non-light `device_name` values match `^[a-z][a-z0-9]*_[fvlo]_\d+$`
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT count(*) FROM device_registry WHERE display_name IS NULL AND device_type != 'light';"` — returns 0 (all non-lights have display_name)
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT device_name FROM device_registry WHERE device_type='light';"` — all lights unchanged
  QA scenarios: happy — migration canonicalizes 7 non-light devices, display_name populated. failure — migration is idempotent (running twice doesn't change anything). Evidence `.omo/evidence/task-1-device-registry-fix.txt`
  Commit: Y | feat(db): migration 010 canonicalize non-light device_name, move human-readable to display_name

- [x] 2. Fix Device model pattern + get_all_devices_flat error handling
  What to do / Must NOT do:
  - **In `Infrastructure/automation-service/app/models/device_registry.py:37-40`:**
    - Change the `Device.device_name` pattern from `^[a-z][a-z0-9]*_[fvlo]_\d+$` to `^[a-z][a-z0-9]*_[fvlo]_\d+$` (KEEP the same pattern — the migration 010 makes the data match it).
    - Actually, the pattern is correct — the problem is the DATA doesn't match it. After migration 010, the data will match. So no change to the pattern is needed.
    - BUT: add a `min_length=1` constraint as a backstop in case data gets into the DB without matching the pattern (belt-and-suspenders).
  - **In `Infrastructure/automation-service/app/repositories/devices.py:287-302` (`get_all_devices_flat`):**
    - Change the list comprehension from:
      ```python
      return [_row_to_typed_device(dict(row)) for row in rows]
      ```
    - To a per-row try/except that skips bad rows instead of failing the entire list:
      ```python
      result: list[Device | LightDevice] = []
      for row in rows:
          try:
              result.append(_row_to_typed_device(dict(row)))
          except Exception as row_err:
              logger.warning(f"Skipping malformed device row (device_id={row.get('device_id')}): {row_err}")
      return result
      ```
    - Remove the outer `try/except` that returns `[]` on any failure — it's now per-row.
    - But keep a top-level `try/except` for DB connection errors (pool acquire failed) that logs an error and returns `[]` — DB connectivity is a real failure that should fail-closed.
  - **Must NOT** change the `LightDevice` model or its pattern.
  - **Must NOT** remove the pattern from `Device.device_name` — the pattern enforces canonical naming for new device creation. The migration makes existing data comply.
  - **Must NOT** change `_row_to_typed_device`, `_row_to_device`, or `_row_to_light_device` — they work correctly when the data matches the patterns.
  - **Test:** Add `tests/test_device_registry_fix.py`:
    1. `test_get_all_devices_flat_skips_bad_row` — insert a row with a non-matching device_name, verify `get_all_devices_flat()` returns the other devices (not `[]`).
    2. `test_device_model_rejects_non_canonical_name` — `Device(device_name="Heater Flower")` raises `ValidationError`.
    3. `test_device_model_accepts_canonical_name` — `Device(device_name="heater_f_1")` succeeds.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3 | Can parallelize with: 1
  References:
  - `Infrastructure/automation-service/app/models/device_registry.py:24-42` (Device model — pattern stays, add min_length)
  - `Infrastructure/automation-service/app/repositories/devices.py:287-302` (get_all_devices_flat — per-row error handling)
  - `Infrastructure/automation-service/app/repositories/devices.py:44-48` (_row_to_typed_device — DO NOT CHANGE)
  - `Infrastructure/automation-service/app/repositories/devices.py:51-76` (_row_to_device — DO NOT CHANGE)
  - `Infrastructure/automation-service/app/repositories/devices.py:79-100` (_row_to_light_device — DO NOT CHANGE)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/models/device_registry.py app/repositories/devices.py` passes.
  - `cd Infrastructure/automation-service && pytest tests/test_device_registry_fix.py -q` passes (3 new tests).
  - `cd Infrastructure/automation-service && pytest tests/test_device_crud_endpoint.py -q` passes (existing tests still pass).
  QA scenarios: happy — `get_all_devices_flat()` with one bad row returns N-1 devices, logs a warning. failure — all rows bad → returns `[]` + logs warnings (not a crash). Evidence `.omo/evidence/task-2-device-registry-fix.txt`
  Commit: Y | fix(repos): per-row error handling in get_all_devices_flat, skip bad rows instead of failing entire list

- [x] 3. Deploy Wave 1 + run migration 010 + verify DeviceTable populates
  What to do / Must NOT do:
  - **Step 1 — Pre-deploy verification:**
    ```bash
    cd Infrastructure/automation-service && ruff check . && pytest tests/ -q
    cd Infrastructure/frontend && npx tsc --noEmit
    ```
  - **Step 2 — Deploy:**
    ```bash
    ./deploy.sh
    ```
  - **Step 3 — Run migration 010 against production:**
    ```bash
    sudo -u postgres pg_dump cea_sensors > /tmp/cea_sensors_backup_$(date +%Y%m%d_%H%M%S).sql
    # Copy migration file to deployed directory:
    sudo cp Infrastructure/automation-service/alembic/versions/010_canonicalize_device_names.py /opt/projectcea/current/Infrastructure/automation-service/alembic/versions/
    sudo chown cea:cea /opt/projectcea/current/Infrastructure/automation-service/alembic/versions/010_canonicalize_device_names.py
    cd /opt/projectcea/current/Infrastructure/automation-service
    sudo -u cea bash -c 'export PYTHONPATH=/opt/projectcea/current/Infrastructure:$PYTHONPATH && source .venv/bin/activate && alembic upgrade head'
    ```
    If alembic fails (like last time with the password issue), run the migration SQL directly:
    - Read the generated SQL from the migration file
    - Run each `op.execute(...)` via `sudo -u postgres psql -d cea_sensors -c "..."`
    - Update `alembic_version` to `010_canonicalize_device_names`
  - **Step 4 — Post-migration DB verification:**
    ```bash
    sudo -u postgres psql -d cea_sensors -c "SELECT device_name, display_name, device_type, location FROM device_registry ORDER BY location, device_type, device_name;"
    # Expected: all device_name values match canonical pattern, all non-lights have display_name
    sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM device_registry WHERE display_name IS NULL AND device_type != 'light';"
    # Expected: 0
    ```
  - **Step 5 — Restart automation-service** (so it reloads the canonical names):
    ```bash
    sudo systemctl restart automation-service
    sleep 5
    sudo systemctl is-active automation-service  # Expected: active
    ```
  - **Step 6 — API verification:**
    ```bash
    API_KEY="0e1e28754260f4e810ff8a9503336ad6008e8d30d837b7e2e9819e54fa3479e9"
    # The endpoint that was returning []:
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'devices: {len(d)}'); [print(f'  {x.get(\"device_name\")}: display={x.get(\"display_name\")} type={x.get(\"device_type\")}') for x in d[:5]]"
    # Expected: devices: 13, all with canonical device_name and display_name populated
    ```
  - **Step 7 — Log verification:**
    ```bash
    journalctl -u automation-service --since "2 minutes ago" --no-pager 2>&1 | grep "Failed to get flat device list\|ValidationError\|device_name" | head -5
    # Expected: empty (no validation errors)
    ```
  - Must NOT run `alembic downgrade`.
  - Must NOT skip the pg_dump backup.
  - Must NOT run migration against `cea_sensors_test`.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 4, 5, 6 | Can parallelize with: —
  References:
  - `deploy.sh` (project root)
  - `Infrastructure/automation-service/alembic/versions/010_canonicalize_device_names.py` (created in T1)
  - `Infrastructure/automation-service/app/models/device_registry.py` (fixed in T2)
  Acceptance criteria:
  - `sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"` returns `010_canonicalize_device_names`
  - `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"` returns `13`
  - `journalctl -u automation-service --since "2 minutes ago" --no-pager 2>&1 | grep -c "Failed to get flat device list"` returns `0`
  - `journalctl -u automation-service --since "2 minutes ago" --no-pager 2>&1 | grep -c "Hardware batch.*ok.*0 failed"` > 0
  QA scenarios: happy — DeviceTable shows all 13 devices with human-readable display names. failure — migration fails → restore from backup. Evidence `.omo/evidence/task-3-device-registry-fix.txt`
  Commit: N | (deploy + migration + verification, no code commit)

- [x] 4. Remove dead POST /api/devices/channels/{channel} + DELETE /api/devices/channels/{channel} endpoints
  What to do / Must NOT do:
  - **In `Infrastructure/automation-service/app/routes/devices.py`:**
    - DELETE the `update_channel_device` function (lines 559-658) — `POST /api/devices/channels/{channel}` endpoint. Writes to YAML via `config.write_full_config()`. Has ZERO frontend callers (`updateChannelDevice` in api.ts is never called by any component).
    - DELETE the `clear_channel_device` function (lines 662-720) — `DELETE /api/devices/channels/{channel}` endpoint. Also writes to YAML. Has ZERO frontend callers (`clearChannelDevice` in api.ts is never called by any component).
    - Keep `GET /api/devices/channels` (line 502) — the relay matrix depends on it. It reads from DB via `get_all_as_hierarchy()` and does NOT write to YAML.
  - **Run `ruff check` and `pytest`** to verify no import errors or broken references.
  - **Must NOT** remove `GET /api/devices/channels` — the relay matrix and DeviceManager depend on it.
  - **Must NOT** remove `GET /api/devices` (line 87) — returns relay states, consumed by `useSensorPolling.ts`.
  - **Must NOT** remove `write_full_config` from `config.py` — it's still used by `system_config.py:201`.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 7 | Can parallelize with: 5, 6
  References:
  - `Infrastructure/automation-service/app/routes/devices.py:559-658` (update_channel_device — DELETE)
  - `Infrastructure/automation-service/app/routes/devices.py:662-720` (clear_channel_device — DELETE)
  - `Infrastructure/automation-service/app/routes/devices.py:502-557` (get_all_channels — KEEP)
  - `Infrastructure/automation-service/app/routes/devices.py:87-110` (get_all_devices — KEEP)
  - `Infrastructure/frontend/src/services/api.ts:303` (updateChannelDevice — dead, no callers)
  - `Infrastructure/frontend/src/services/api.ts:329` (clearChannelDevice — dead, no callers)
  Acceptance criteria:
  - `grep -n "update_channel_device\|clear_channel_device" Infrastructure/automation-service/app/routes/devices.py` returns nothing.
  - `cd Infrastructure/automation-service && ruff check app/routes/devices.py` passes.
  - `cd Infrastructure/automation-service && pytest tests/ -q` passes.
  QA scenarios: happy — endpoints removed, no broken imports, tests pass. failure — an import that depended on the removed functions breaks → fix the import. Evidence `.omo/evidence/task-4-device-registry-fix.txt`
  Commit: Y | refactor(routes): remove dead POST/DELETE /api/devices/channels/{channel} endpoints that wrote to YAML

- [x] 5. Remove dead ChannelDeviceUpdate schema + dead frontend api.ts methods
  What to do / Must NOT do:
  - **In `Infrastructure/automation-service/app/schemas/device.py`:**
    - DELETE the `ChannelDeviceUpdate` class (line 38) — only used by the removed `POST /api/devices/channels/{channel}` endpoint.
  - **In `Infrastructure/automation-service/app/schemas/__init__.py`:**
    - Remove `ChannelDeviceUpdate` from imports (line 12) and `__all__` (line 55).
  - **In `Infrastructure/frontend/src/services/api.ts`:**
    - DELETE `updateChannelDevice()` method (line 303) — zero callers.
    - DELETE `clearChannelDevice()` method (line 329) — zero callers.
    - Delete the `ChannelInfo` import if it's no longer used after the removals (check with grep).
  - **Must NOT** remove `ChannelInfo` type or `getChannels()` method — they're used by `DeviceManager.tsx`.
  - **Must NOT** remove the `ChannelDeviceUpdate` import from `devices.py` — T4 already deleted the functions that imported it. But if `devices.py` still imports it at the top (unused import), remove that import too.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 7 | Can parallelize with: 4, 6
  References:
  - `Infrastructure/automation-service/app/schemas/device.py:38` (ChannelDeviceUpdate class — DELETE)
  - `Infrastructure/automation-service/app/schemas/__init__.py:12,55` (import + __all__ — remove ChannelDeviceUpdate)
  - `Infrastructure/frontend/src/services/api.ts:303` (updateChannelDevice — DELETE)
  - `Infrastructure/frontend/src/services/api.ts:329` (clearChannelDevice — DELETE)
  Acceptance criteria:
  - `grep -rn "ChannelDeviceUpdate" Infrastructure/automation-service/app/` returns nothing.
  - `grep -rn "updateChannelDevice\|clearChannelDevice" Infrastructure/frontend/src/` returns nothing.
  - `cd Infrastructure/automation-service && ruff check .` passes.
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes.
  QA scenarios: happy — dead code removed, no broken imports. failure — an existing test references ChannelDeviceUpdate → update the test. Evidence `.omo/evidence/task-5-device-registry-fix.txt`
  Commit: Y | refactor(schemas): remove dead ChannelDeviceUpdate schema + dead frontend api.ts methods

- [x] 6. Remove YAML fallback in config.get_devices() + remove devices: block from automation_config.yaml
  What to do / Must NOT do:
  - **In `Infrastructure/automation-service/app/config.py:get_devices()` (lines 290-308):**
    - Replace the YAML fallback (`if self._device_repo is None: return self._config.get("devices", {})`) with:
      ```python
      if self._device_repo is None:
          logger.error("DeviceRepository not set — cannot read devices. DB is the only data source.")
          return {}
      ```
    - Remove the bootstrap check block (lines 299-306: `if not self._bootstrap_checked: ...`) — no longer needed since there's no YAML fallback to warn about.
    - Remove `self._yaml_has_devices` field (line 211) and its initialization (line 244) — no longer needed.
    - Remove `self._bootstrap_checked` field and its initialization — no longer needed.
  - **In `Infrastructure/automation-service/automation_config.yaml`:**
    - DELETE the entire `devices:` block (all rooms, clusters, device definitions). Keep `hardware:`, `control:`, `sensors:` blocks.
    - YAML `devices:` block is now DB-only. The file stays for non-device config.
  - **Must NOT** remove `write_full_config()` method from `config.py` — still used by `system_config.py:201`.
  - **Must NOT** remove `automation_config.yaml` itself — it still has `hardware:`, `control:`, `sensors:`.
  - **Must NOT** remove `schedules_path` or `rules_path` from `config.py` — those are separate config files.
  - **Test:** Add `tests/test_config_no_yaml_fallback.py`:
    1. `test_get_devices_returns_empty_when_no_repo` — when `_device_repo is None`, `get_devices()` returns `{}` (not YAML).
    2. `test_get_devices_no_bootstrap_check` — the `_bootstrap_checked` field is gone.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 7 | Can parallelize with: 4, 5
  References:
  - `Infrastructure/automation-service/app/config.py:290-308` (get_devices — remove YAML fallback)
  - `Infrastructure/automation-service/app/config.py:211,244` (_yaml_has_devices — remove)
  - `Infrastructure/automation-service/app/config.py:299-306` (bootstrap check — remove)
  - `Infrastructure/automation-service/app/config.py:201-202` (schedules_path, rules_path — KEEP)
  - `Infrastructure/automation-service/app/config.py:503` (write_full_config — KEEP, used by system_config.py)
  - `Infrastructure/automation-service/automation_config.yaml` (devices: block — DELETE)
  Acceptance criteria:
  - `grep -n "_yaml_has_devices\|_bootstrap_checked" Infrastructure/automation-service/app/config.py` returns nothing.
  - `grep -n "devices:" Infrastructure/automation-service/automation_config.yaml` returns nothing (devices block removed).
  - `grep -n "hardware:\|control:\|sensors:" Infrastructure/automation-service/automation_config.yaml` returns matches (non-device config preserved).
  - `cd Infrastructure/automation-service && ruff check app/config.py` passes.
  - `cd Infrastructure/automation-service && pytest tests/test_config_no_yaml_fallback.py -q` passes.
  QA scenarios: happy — config.get_devices() returns {} when no repo, automation_config.yaml has no devices block. failure — some code depends on YAML devices → it should use config.get_devices() which reads from DB. Evidence `.omo/evidence/task-6-device-registry-fix.txt`
  Commit: Y | refactor(config): remove YAML device fallback, devices are DB-only; remove devices block from YAML

- [x] 7. Deploy Wave 2 + verify all endpoints still work + no YAML writing
  What to do / Must NOT do:
  - **Step 1 — Pre-deploy verification:**
    ```bash
    cd Infrastructure/automation-service && ruff check . && pytest tests/ -q
    cd Infrastructure/frontend && npx tsc --noEmit && npm run build
    ```
  - **Step 2 — Deploy:**
    ```bash
    ./deploy.sh
    ```
  - **Step 3 — Restart automation-service:**
    ```bash
    sudo systemctl restart automation-service
    sleep 5
    sudo systemctl is-active automation-service  # Expected: active
    ```
  - **Step 4 — Verify all endpoints:**
    ```bash
    API_KEY="0e1e28754260f4e810ff8a9503336ad6008e8d30d837b7e2e9819e54fa3479e9"
    
    # Device registry (was returning [], should return 13):
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry
    # Expected: 200
    
    # Device list (was returning 404, should return 200):
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/Flower%20Room/main
    # Expected: 200
    
    # DFR assignments (was working, should still work):
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/dfr/assignments
    # Expected: 200
    
    # Channels (was working, should still work):
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/channels
    # Expected: 200
    
    # Removed endpoints (should return 404/405):
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" -X POST http://127.0.0.1:8001/api/devices/channels/0 -H "Content-Type: application/json" -d '{}'
    # Expected: 404 or 405 (endpoint removed)
    
    curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" -X DELETE http://127.0.0.1:8001/api/devices/channels/0
    # Expected: 404 or 405 (endpoint removed)
    ```
  - **Step 5 — Verify no YAML writing:**
    ```bash
    # Check that automation_config.yaml has no devices block:
    grep -c "devices:" /opt/projectcea/current/Infrastructure/automation-service/automation_config.yaml
    # Expected: 0
    
    # Check that config.get_devices() doesn't reference YAML:
    grep -c "_yaml_has_devices\|_config.get.*devices" /opt/projectcea/current/Infrastructure/automation-service/app/config.py
    # Expected: 0
    ```
  - **Step 6 — Log verification:**
    ```bash
    journalctl -u automation-service --since "2 minutes ago" --no-pager 2>&1 | grep -i "error\|exception\|traceback" | grep -v "WARNING" | head -10
    # Expected: empty (no errors)
    
    journalctl -u automation-service --since "2 minutes ago" --no-pager 2>&1 | grep "Hardware batch" | tail -3
    # Expected: "N ok, 0 failed"
    ```
  - Must NOT run any DELETE/POST/PUT against production (except the guard-verification probe).
  - Must NOT skip the health checks in deploy.sh.
  Parallelization: Wave 4 | Blocked by: 4, 5, 6 | Blocks: — | Can parallelize with: —
  References:
  - `deploy.sh` (project root)
  - All changed files from T4, T5, T6
  Acceptance criteria:
  - `deploy.sh` exits 0, all health checks pass.
  - `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry` returns `200`
  - `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/registry | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"` returns `13`
  - `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" -X POST http://127.0.0.1:8001/api/devices/channels/0 ...` returns `404` or `405`
  - `grep -c "devices:" /opt/projectcea/current/Infrastructure/automation-service/automation_config.yaml` returns `0`
  QA scenarios: happy — all endpoints work, dead endpoints 404, no YAML devices block, DeviceTable populates. failure — an endpoint breaks → rollback. Evidence `.omo/evidence/task-7-device-registry-fix.txt`
  Commit: N | (deploy + verification, no code commit)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA — user verifies DeviceTable populates in the frontend
- [x] F4. Scope fidelity

## Commit strategy
- Wave 1: T1 `feat(db): migration 010 canonicalize non-light device_name, move human-readable to display_name`; T2 `fix(repos): per-row error handling in get_all_devices_flat, skip bad rows instead of failing entire list`
- Wave 2: T3 (no commit — deploy + migration + verification)
- Wave 3: T4 `refactor(routes): remove dead POST/DELETE /api/devices/channels/{channel} endpoints that wrote to YAML`; T5 `refactor(schemas): remove dead ChannelDeviceUpdate schema + dead frontend api.ts methods`; T6 `refactor(config): remove YAML device fallback, devices are DB-only; remove devices block from YAML`
- Wave 4: T7 (no commit — deploy + verification)

## Success criteria
- All 13 devices in `device_registry` have canonical `device_name` values matching `^[a-z][a-z0-9]*_[fvlo]_\d+$` (lights) or `^[a-z][a-z0-9]*_[fvlo]_\d+$` (non-lights).
- All non-light devices have `display_name` populated with their human-readable name.
- `GET /api/devices/registry` returns 13 devices (was returning `[]`).
- `get_all_devices_flat()` skips bad rows instead of returning `[]` for the entire list.
- `POST /api/devices/channels/{channel}` and `DELETE /api/devices/channels/{channel}` endpoints are removed (return 404/405).
- `ChannelDeviceUpdate` schema removed from backend.
- `updateChannelDevice()` and `clearChannelDevice()` methods removed from frontend `api.ts`.
- `config.get_devices()` has no YAML fallback — returns `{}` when `_device_repo is None`.
- `automation_config.yaml` has no `devices:` block (hardware, control, sensors preserved).
- `ruff check .`, `tsc --noEmit`, `pytest`, `npm run build` all pass.
- Two successful deploys (Wave 1 + Wave 2).
