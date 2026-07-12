# dfr-panel-cleanup - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** The DFR boards panel becomes a clean display of which light is connected to each dimming channel — no more redundant dropdown for assignment (that's done in the device table now). Each channel slot shows the room prominently. Board names appear once as headers, not repeated on every channel. Renaming a light from either the device table or the DFR panel instantly updates the other.

**Why this approach:** The device registry table is the single source of truth for device configuration since the `centralized-device-table` refactor. DFR assignment belongs there too — the existing DFR panel dropdown was duplicated functionality using a separate API path. The fix also closes a silent data-loss bug: the device table's edit form was sending DFR board/channel changes that the backend silently discarded.

**What it will NOT do:**
- Will not remove the DFR assignment API endpoint (kept for scripting/future use).
- Will not remove the Test Light or Rename buttons from the DFR panel.
- Will not add React Query or new frontend dependencies.

**Effort:** Short
**Risk:** Low — backend fix adds fields to an existing model + one conflict check; frontend removes a dropdown and restructures labels; no schema migration.
**Decisions to sanity-check:** Room badge uses the same pill style as the device table's type badge; shared refresh uses a simple counter-key pattern (not React Query).

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Short, Low risk — fix backend DFR update persistence + conflict check, refactor DFR panel to read-only assignment display with room badges, wire shared refresh between DeviceTable and DfrBoardsPanel

## Scope
### Must have
- `LightDeviceUpdate` model gains `board_id` and `dimming_channel` fields (optional, nullable) matching `LightDeviceCreate`'s field names. The model field is `board_id` (NOT `dimming_board_id`) to match what the frontend already sends and the create model's naming.
- `update_registry_device()` route handler reads the new fields and **maps** `board_id` → `dimming_board_id` when passing to `device_repo.update_light()` (repo's `allowed` set uses `dimming_board_id`). Unassigning (sending `null`) must work — use presence-in-body checking (`if "board_id" in light_fields:`) not `is not None` to distinguish "sent as null" from "not sent."
- `update_registry_device()` gains a DFR channel conflict check mirroring the existing relay channel check (lines 211-227) — if `board_id`/`dimming_channel` are being updated, iterate the hierarchy and raise 409 if another light already occupies that board+channel (excluding the current device_id).
- `lights.py` `update_light()` route handler (lines 872-882) gains the same field-passing lines for `board_id` and `dimming_channel` — even though no current caller sends them through that route, the model will accept them and the handler must pass them through to avoid a latent silent-data-loss path.
- `DfrBoardsPanel` assignment `<select>` dropdown removed — assignment display becomes read-only (shows the assigned light name + room, no dropdown).
- `DfrBoardsPanel` per-slot label changes from `DFR0 · CH0` to just `CH0` (board identity shown once as a board-level header, not repeated per slot).
- `DfrBoardsPanel` room display changes from subtle corner text to a prominent room badge on each assigned channel slot.
- `DeviceManager` passes a shared `refresh()` callback to both `DeviceTable` and `DfrBoardsPanel`; when either panel saves a rename, both re-fetch.
- `DfrBoardsPanel` Test and Rename actions preserved (they use the working `testLight()` and `updateDeviceConfig()`/`updateLight()` APIs).
- Backend tests: new pytest for the update path confirming `board_id`/`dimming_channel` persist via `PUT /api/devices/registry/{id}`, including unassign (`null`) and DFR conflict (409) tests.
- Frontend tests: `DfrBoardsPanel.test.tsx` label test (line 119-133) updated for new `CH0` label + `DFR0` board header. `tsc --noEmit` passes. `npm run build` passes.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT remove `PUT /api/lights/dfr/assign` endpoint or `assignDfrChannel()` from apiClient — DfrBoardsPanel's `applyAssignment` is removed from the UI but the API stays for potential future use or scripting.
- Do NOT remove Test Light or Rename actions from DfrBoardsPanel — these are explicitly kept per user decision.
- Do NOT migrate DfrBoardsPanel to React Query or add new dependencies — the shared parent refresh callback is the agreed approach.
- Do NOT change the `update_light()` repository method — it already accepts `dimming_board_id`/`dimming_channel` in its `allowed` set; the bug is only in the model + route handler filtering.
- Do NOT change `LightDeviceCreate` — it already has `board_id`/`dimming_channel`; only `LightDeviceUpdate` is missing them.
- Do NOT rename `board_id` to `dimming_board_id` in the frontend or the model — the model field MUST be `board_id` (matching `LightDeviceCreate`); the handler maps it to `dimming_board_id` when calling the repo.
- Do NOT touch the `assignDfrChannel` endpoint or `update_device_config` rename path — they work correctly and stay as-is.
- Do NOT add per-room grouping (organizing DFR channels by room sections) — user chose per-slot room badge, not board-level grouping.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after. Backend: pytest. Frontend: `tsc --noEmit` + `npm run build` + vitest.
- Evidence: `.omo/evidence/task-N-dfr-panel-cleanup.<ext>`

## Execution strategy
### Parallel execution waves
- **Wave 1 (backend, parallel):** Todo 1 (fix `LightDeviceUpdate` model + `update_registry_device()` handler) runs alone — it's a single-file backend change.
- **Wave 2 (frontend, sequential):** Todo 2 (DfrBoardsPanel refactor: remove dropdown, fix labels, add room badge) → Todo 3 (shared refresh wiring from DeviceManager). Sequential because Todo 3 touches the parent that renders Todo 2's output.
- **Wave 3 (final):** Todo 4 (build + tests + verify). Depends on all.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 4 | — |
| 2 | — | 3, 4 | 1 |
| 3 | 2 | 4 | — |
| 4 | 1, 2, 3 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Fix `LightDeviceUpdate` model + `update_registry_device()` + `lights.py` update_light to persist DFR board/channel on update
  What to do / Must NOT do:
  - **Model:** In `LightDeviceUpdate` class (`Infrastructure/automation-service/app/models/device_registry.py:85-99`), add two optional nullable fields. **Field name MUST be `board_id`** (NOT `dimming_board_id`) to match `LightDeviceCreate` (line 74) and what the frontend already sends (`DeviceTable.tsx:169`):
    ```python
    board_id: int | None = Field(default=None, description="DFR0971 board ID (0, 1, 2)")
    dimming_channel: int | None = Field(default=None, ge=0, le=1, description="DFR0971 channel (0 or 1)")
    ```
  - **`devices_crud.py` handler:** In `update_registry_device()` (`Infrastructure/automation-service/app/routes/devices_crud.py:170-277`), after the existing `if light_update.safety_level is not None:` block (around line 209), add field-passing using **presence-in-body** checking (NOT `is not None` — that would prevent unbinding since `None` is the Pydantic default). The `light_fields` dict at line 191 contains only keys that were explicitly sent in the request body, so checking `if "board_id" in light_fields:` detects both "sent a value" and "sent as null":
    ```python
    if "board_id" in light_fields:
        update_fields["dimming_board_id"] = light_update.board_id
    if "dimming_channel" in light_fields:
        update_fields["dimming_channel"] = light_update.dimming_channel
    ```
    Note: the key in `update_fields` MUST be `dimming_board_id` (matching the repository's `allowed` set at `devices.py:437`), but the model field name is `board_id` — this maps between the two naming conventions.
  - **DFR conflict check:** Add a DFR channel conflict check in `update_registry_device()` mirroring the relay channel conflict check at `devices_crud.py:211-227`. When `dimming_board_id` and `dimming_channel` are in `update_fields`, iterate the hierarchy via `device_repo.get_all_as_hierarchy()` and raise HTTP 409 if another light with a different `device_id` already has the same `dimming_board_id` + `dimming_channel` combination. Exclude the current device being updated. Pattern:
    ```python
    if "dimming_board_id" in update_fields and "dimming_channel" in update_fields:
        hierarchy = await device_repo.get_all_as_hierarchy()
        for loc, clusters in hierarchy.items():
            for clu, devices in clusters.items():
                for dev_name, dev_info in devices.items():
                    if (dev_info.get("device_type") == "light"
                        and dev_info.get("device_id") != device_id
                        and dev_info.get("board_id") == update_fields["dimming_board_id"]
                        and dev_info.get("dimming_channel") == update_fields["dimming_channel"]):
                        raise HTTPException(
                            status_code=409,
                            detail=f"DFR board {update_fields['dimming_board_id']} channel {update_fields['dimming_channel']} is already occupied by {dev_info.get('device_name')}"
                        )
    ```
    Place this check BEFORE the `device_repo.update_light()` call, mirroring the relay channel check position at lines 211-227.
  - **`lights.py` handler:** In `update_light()` route (`Infrastructure/automation-service/app/routes/lights.py:857-897`), the handler manually constructs `update_fields` field-by-field (lines 872-882). Add the same field-passing lines after the `safety_level` check:
    ```python
    if "board_id" in body.model_fields_set:
        update_fields["dimming_board_id"] = body.board_id
    if "dimming_channel" in body.model_fields_set:
        update_fields["dimming_channel"] = body.dimming_channel
    ```
    Note: `lights.py` receives a typed `LightDeviceUpdate` (not a raw dict), so use `body.model_fields_set` (Pydantic v2) to check which fields were explicitly set. This prevents the latent silent-data-loss path if anyone sends these fields through `PUT /api/lights/{device_id}`.
  - The repository's `update_light()` method at `Infrastructure/automation-service/app/repositories/devices.py:428-467` already accepts `dimming_board_id` and `dimming_channel` in its `allowed` set (lines 437-438). No changes needed there.
  - Do NOT change `LightDeviceCreate` — it already has `board_id` and `dimming_channel`.
  - Do NOT change the repository method.
  - Do NOT rename `board_id` to `dimming_board_id` in the model — the handler maps between the two names.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 4 | Can parallelize with: 2
  References:
  - `Infrastructure/automation-service/app/models/device_registry.py:85-99` (`LightDeviceUpdate` class — add `board_id` + `dimming_channel` fields here)
  - `Infrastructure/automation-service/app/models/device_registry.py:71-84` (`LightDeviceCreate` — reference for field naming; already has `board_id` at line 74 and `dimming_channel` at line 75)
  - `Infrastructure/automation-service/app/routes/devices_crud.py:190-191` (the `light_fields` filter dict — use `"board_id" in light_fields` for presence checking)
  - `Infrastructure/automation-service/app/routes/devices_crud.py:199-209` (existing `update_fields` construction — add new fields after `safety_level`)
  - `Infrastructure/automation-service/app/routes/devices_crud.py:211-227` (relay channel conflict check — mirror this pattern for DFR conflict check)
  - `Infrastructure/automation-service/app/routes/devices_crud.py:83-105` (create path's DFR conflict check — reference for what fields to check)
  - `Infrastructure/automation-service/app/routes/lights.py:857-897` (`update_light` route handler — add field-passing lines after line 882)
  - `Infrastructure/automation-service/app/repositories/devices.py:428-440` (`update_light()` `allowed` set — already includes `dimming_board_id`/`dimming_channel`, no change needed)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/models/device_registry.py app/routes/devices_crud.py app/routes/lights.py` passes.
  - New pytest `tests/test_device_registry_dfr_update.py`:
    1. **Assign test:** Mock `device_repo`, call `update_registry_device` with `{"device_type": "light", "display_name": "Test Light", "board_id": 1, "dimming_channel": 0}` for an existing light; assert `device_repo.update_light` was called with `dimming_board_id=1, dimming_channel=0` in the kwargs.
    2. **Unassign test:** Call `update_registry_device` with `{"board_id": null, "dimming_channel": null}`; assert `update_light` was called with `dimming_board_id=None, dimming_channel=None` (unbinding works).
    3. **DFR conflict test:** Mock `device_repo.get_all_as_hierarchy` to return a light already occupying `board_id=0, dimming_channel=1`; call `update_registry_device` with `{"board_id": 0, "dimming_channel": 1}` for a different device_id; assert HTTP 409 is raised.
    4. **Invalid value test:** Call `update_registry_device` with `{"dimming_channel": 5}`; assert Pydantic validation error (ge=0, le=1 constraint).
  - `cd Infrastructure/automation-service && pytest tests/test_device_registry_dfr_update.py -v` passes.
  QA scenarios: happy — DeviceTable edit sends board_id=2, dimming_channel=1; backend persists to device_registry. failure — DFR conflict raises 409; invalid dimming_channel=5 rejected by Pydantic; unassign sends null and actually clears the column. Evidence `.omo/evidence/task-1-dfr-panel-cleanup.txt`
  Commit: Y | fix(backend): allow DFR board/channel update via device registry PUT + conflict check

- [x] 2. Refactor DfrBoardsPanel: remove assignment dropdown, fix DFR label, add room badge, keep Test + Rename
  What to do / Must NOT do:
  - **Remove assignment dropdown:** In `DfrBoardsPanel.tsx` (around lines 480-492), remove the `<select>` element that calls `applyAssignment`. Replace with read-only text showing the assigned light's display name or "Unassigned".
  - **Remove `applyAssignment` function** (lines 178-220) — no longer called from the UI. The API method `assignDfrChannel` stays in `apiClient` (guardrail: do NOT remove the API endpoint).
  - **Fix DFR label:** Change the per-slot label from `DFR{board.board_id} · CH{ch}` (line 474) to just `CH{ch}`. Add a board-level header showing `DFR{board.board_id}` once per board card (above the channel slots). The board header should be prominent — use the same `uppercase font-bold tracking-wider` styling as the panel title.
  - **Add room badge:** Replace the subtle corner text `{assignment ? assignment.location : 'Unassigned'}` (line 476) with a visible room badge on assigned slots. The badge should be a colored pill/tag showing the room name (e.g. `<span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2 py-0.5 text-xs font-medium text-btn-primary-text">{assignment.location}</span>` — match the DeviceTable's device-type badge styling at line 370-372). Unassigned slots show nothing (or a muted "—" placeholder).
  - **Keep Test action:** The `testLight()` function (lines 340-379) and its button stay unchanged.
  - **Keep Rename action:** The `saveRename()` function (lines 222-246), its input + Save button, and the `updateDeviceConfig()` call stay unchanged.
  - **Keep Edit form:** The `saveEdit()` function (lines 293-338) and its form (display name, room, per-room index) stay unchanged — this is the rename + room-change path through `updateLight()`.
  - **Keep Remove action:** The `removeLight()` function (lines 381-420) and its confirm button stay unchanged.
  - Remove `lightOptions` useMemo (lines 124-135) — no longer needed since the dropdown is gone.
  - Remove `workingKey` guard in `applyAssignment` — the function is removed; other actions (test, rename, edit, remove) keep their own `workingKey` guards.
  - Must NOT remove the `data` state or `refresh` callback — they're needed for read-only display + test/rename/remove.
  - Must NOT change the `getDfrAssignments` API call or response shape — the panel still reads assignments, just doesn't write them.
  - Update `DfrBoardsPanel.test.tsx` — the test at line 119-133 ("shows DFR board_id and channel label instead of relay identity") asserts `labelText === 'DFR0 · CH0'`. Change this to assert `'CH0'` (channel-only label) and add a separate assertion for the board header element asserting `'DFR0'` is present. No dropdown test exists — do not add one.
  Parallelization: Wave 2 | Blocked by: — | Blocks: 3 | Can parallelize with: 1
  References:
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:178-220` (`applyAssignment` — remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:124-135` (`lightOptions` — remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:454-492` (`renderChannel` — the slot with select dropdown, label, and room text)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:474` (label `DFR{board.board_id} · CH{ch}` — change to `CH{ch}`, add board header above)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:476` (room text — replace with badge)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:222-246` (`saveRename` — keep)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:293-338` (`saveEdit` — keep)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:340-379` (`testLight` — keep)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:381-420` (`removeLight` — keep)
  - `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx` (existing test — update for new layout)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:226,247` (where DfrBoardsPanel and DeviceTable are rendered as siblings)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes with 0 errors.
  - `cd Infrastructure/frontend && npm run build` passes.
  - `grep -n "applyAssignment" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing (function removed).
  - `grep -n "lightOptions" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing (unused memo removed).
  - `grep -n "<select" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns only the edit form's room `<select>` (for the rename/edit form), NOT the assignment dropdown.
  - `grep -n "DFR.*CH" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing (the combined label is split into separate board header + channel label).
  - `cd Infrastructure/frontend && npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` passes.
  QA scenarios: happy — DfrBoardsPanel shows board header "DFR0", each slot shows "CH0"/"CH1", assigned slots show room badge, Test and Rename buttons work. failure — tsc errors on removed `applyAssignment` references; vitest fails on old dropdown assertions. Evidence `.omo/evidence/task-2-dfr-panel-cleanup.txt`
  Commit: Y | feat(frontend): DFR panel read-only assignment, room badge, board header label

- [x] 3. Wire shared refresh callback from DeviceManager to DeviceTable + DfrBoardsPanel
  What to do / Must NOT do:
  - In `DeviceManager.tsx`, extract a shared refresh mechanism. Currently `<DfrBoardsPanel />` (line 226) and `<DeviceTable />` (line 247) are rendered with no props — each manages its own internal `refresh()` state.
  - Approach: Add a `refreshKey` state (a number counter) to `DeviceManager`. Pass `refreshKey` as a prop to both `DfrBoardsPanel` and `DeviceTable`. Each child uses `refreshKey` in a `useEffect` dependency to trigger its own `refresh()` when the key changes. When either child finishes a save operation, it calls an `onRefresh` callback prop (passed from DeviceManager) that increments `refreshKey`.
  - Add `onRefresh?: () => void` prop to `DfrBoardsPanel`. After `saveRename()`, `saveEdit()`, `removeLight()` complete their own `refresh()`, call `onRefresh?.()`.
  - Add `onRefresh?: () => void` prop to `DeviceTable`. After `submitEdit()`, `submitAdd()`, `confirmDelete()` complete their own `refresh()`, call `onRefresh?.()`.
  - In `DeviceManager`, define:
    ```tsx
    const [refreshKey, setRefreshKey] = useState(0)
    const handleSharedRefresh = useCallback(() => setRefreshKey(k => k + 1), [])
    ```
    And render:
    ```tsx
    <DfrBoardsPanel refreshKey={refreshKey} onRefresh={handleSharedRefresh} />
    <DeviceTable refreshKey={refreshKey} onRefresh={handleSharedRefresh} />
    ```
  - Each child component: add `refreshKey: number` and `onRefresh: () => void` to props. In the existing `useEffect(() => { void refresh() }, [refresh])`, add `refreshKey` to the dependency array: `useEffect(() => { void refresh() }, [refresh, refreshKey])`.
  - Must NOT remove the child's own `refresh()` function — each panel still manages its own data fetching; the shared key just triggers re-fetch.
  - Must NOT use React Query or a global state manager — explicit shared-key callback is the agreed approach.
  - Must NOT block the child's own refresh on the parent's — the child does its own `refresh()` first, then calls `onRefresh?.()` to trigger the sibling.
  - Must NOT pass `refreshKey` to `RelayChannelMatrix` — the relay matrix doesn't depend on device rename.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 4 | Can parallelize with: —
  References:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:224-262` (the "devices" tab render section — where DfrBoardsPanel and DeviceTable are rendered)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:27-30` (DeviceManager function signature — add state here)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:57` (`export default function DfrBoardsPanel()` — add props)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:79-110` (`refresh` callback)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:112-114` (`useEffect` that calls `refresh()` — add `refreshKey` to dependency array)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:238` (after `saveRename` calls `await refresh()` — add `onRefresh?.()`)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:329` (after `saveEdit` calls `await refresh()` — add `onRefresh?.()`)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:407` (after `removeLight` calls `await refresh()` — add `onRefresh?.()`)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:43` (`export default function DeviceTable()` — add props)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:53-64` (`refresh` callback + useEffect — add refreshKey dep)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:142` (after `submitAdd` calls `await refresh()` — add `onRefresh?.()`)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:183` (after `submitEdit` calls `await refresh()` — add `onRefresh?.()`)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:198` (after `confirmDelete` calls `await refresh()` — add `onRefresh?.()`)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes with 0 errors.
  - `cd Infrastructure/frontend && npm run build` passes.
  - `grep -n "refreshKey" Infrastructure/frontend/src/components/DeviceManager.tsx` returns the state declaration + the two prop passes.
  - `grep -n "onRefresh" Infrastructure/frontend/src/components/DeviceManager.tsx` returns the callback + two prop passes.
  - `grep -n "onRefresh" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns the prop in the function signature + the calls after save/rename/remove.
  - `grep -n "onRefresh" Infrastructure/frontend/src/components/devices/DeviceTable.tsx` returns the prop in the function signature + the calls after edit/add/delete.
  QA scenarios: happy — rename a light in DeviceTable; DfrBoardsPanel re-fetches and shows the new name. Rename in DfrBoardsPanel; DeviceTable re-fetches and shows the new name. failure — tsc errors on missing props; build fails. Evidence `.omo/evidence/task-3-dfr-panel-cleanup.txt`
  Commit: Y | feat(frontend): shared refresh callback between DeviceTable and DfrBoardsPanel

- [x] 4. Final: build + type-check + backend tests + frontend tests + verify no broken references
  What to do / Must NOT do:
  - `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q` (all tests including the new one from Todo 1).
  - `cd Infrastructure/frontend && npx tsc --noEmit && npm run build && npx vitest run` (all frontend tests).
  - `grep -rn "applyAssignment" Infrastructure/frontend/src/components/devices/` returns nothing (removed in Todo 2).
  - `grep -rn "DFR.*CH" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing (label split in Todo 2).
  - `grep -rn "lightOptions" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing (removed in Todo 2).
  - `grep -n "board_id" Infrastructure/automation-service/app/models/device_registry.py` returns at least 2 matches (LightDeviceCreate + LightDeviceUpdate).
  - `grep -n "dimming_board_id" Infrastructure/automation-service/app/routes/devices_crud.py` returns at least 1 match (the update_fields mapping added in Todo 1).
  - Must NOT deploy or run curl verification — this is a frontend + backend fix plan. The existing deploy.sh handles deployment when the user is ready.
  Parallelization: Wave 3 | Blocked by: 1, 2, 3 | Blocks: —
  References:
  - `Infrastructure/automation-service/tests/test_device_registry_dfr_update.py` (new test file from Todo 1)
  - `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx` (updated test from Todo 2)
  Acceptance criteria: ruff passes, all pytest pass, tsc 0 errors, npm build succeeds, vitest passes, no broken references to removed code.
  QA scenarios: happy — all checks green. failure — any test or build fails; grep finds stale references. Evidence `.omo/evidence/task-4-dfr-panel-cleanup.txt`
  Commit: N | (verification only; code commits in Todos 1-3)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [~] F1. Plan compliance audit — blocked: not deployed, pending new deploy after production-safety plan
- [~] F2. Code quality review — blocked: not deployed, pending new deploy after production-safety plan
- [~] F3. Real manual QA — blocked: F3 destroyed production data on 2026-07-08. User permanently banned F3. Will never run again.
- [~] F4. Scope fidelity — blocked: not deployed, pending new deploy after production-safety plan

## Commit strategy
- Wave 1 (backend): Todo 1 gets its own commit: `fix(backend): allow DFR board/channel update via device registry PUT + conflict check`
- Wave 2 (frontend): Todo 2 commit: `feat(frontend): DFR panel read-only assignment, room badge, board header label`; Todo 3 commit: `feat(frontend): shared refresh callback between DeviceTable and DfrBoardsPanel`
- Wave 3 (verification): Todo 4 produces no separate code commit.

## Success criteria
- DeviceTable can edit a light's DFR board and channel assignment, and the change persists to the database (verified by re-reading the device registry after edit).
- DFR channel conflict on update raises HTTP 409 (verified by pytest).
- DFR panel shows read-only assignment display — no dropdown, just the assigned light name and room badge.
- DFR panel shows `DFR0` as a board header once, with `CH0`/`CH1` per channel slot (no repeated board label).
- DFR panel room badge is prominently visible on each assigned channel slot.
- Renaming a light in either DeviceTable or DfrBoardsPanel causes the other panel to re-fetch and display the new name.
- DFR panel Test and Rename buttons still work.
- `ruff check .` (backend) and `tsc --noEmit` + `npm run build` + vitest (frontend) pass; new pytest passes.
