---
slug: settings-relay-matrix-dfr-cleanup
status: delivered (18 todos, 2 deploys, Metis+Momus reviewed — all BLOCKERS+MAJORS+MINORS fixed; awaiting start-work)
intent: clear
pending-action: append todos into .omo/plans/settings-relay-matrix-dfr-cleanup.md
approach: control_history is the single source of truth for state changes. Wire hysteresis path into log_control_action. Read elapsed-since via existing get_last_changed_per_channel (0 callers today). Add ONE new column manual_expires_at on control_history for timed manual overrides. Frontend deletes all 3 ad-hoc timer implementations. Matrix chrome + RelayGlyph cut. DFR channels side-by-side. Deploy 4 in-flight untracked files. One commit, Ruff, ./deploy.sh.
---

# Draft: settings-relay-matrix-dfr-cleanup

## Components (topology ledger)

| ID | Outcome | Status | Evidence |
|----|---------|--------|----------|
| C1 | Matrix: cut panel chrome + RelayGlyph; fix elapsed-since via control_history | active | RelayChannelMatrix.tsx:126-219, RelayChannelBox.tsx:30-48, DeviceManager.tsx:134-138 |
| C2 | SystemSettingsPanel visible in Settings tab | active | Already wired — needs deploy only. Probe: GET /api/config → 404 |
| C3 | DFR channels side-by-side per board | active | DfrBoardsPanel.tsx:281 grid-cols-1 → grid-cols-2 |
| C4 | Backend: hysteresis path writes to control_history; manual_expires_at column; auto-expiry sweep | active | DeviceController._control_binary_device:660-727, control_actions.py:189 (unused), alembic 001/002 pattern |
| C5 | Frontend: delete 3 ad-hoc timer implementations; derive from polled device data | active | DeviceManager manualTimersByChannel, ZoneConfig relayTimestamps hack, ManualLightControl localStorage |

## Architecture decision (final, user-approved)

`control_history` IS the single source of truth. It already is for 2/5 state-change
paths (manual route + automation engine). Finish the job:

1. Hysteresis path (DeviceController._control_binary_device) calls log_control_action
2. Existing unused get_last_changed_per_channel() becomes the backend read for elapsed-since
3. ONE new nullable column manual_expires_at on control_history (only populated on timed manual)
4. Control loop sweep: SELECT WHERE manual_expires_at <= NOW() AND mode='manual' → revert
5. Frontend reads elapsed-since + countdown from polled device data, deletes all 3 ad-hoc timers

NO new Redis keys. NO new device_states columns. NO unified-method abstraction.

## Migration

New file: Infrastructure/automation-service/alembic/versions/006_add_control_history_manual_expires_at.py
- revision = "006_manual_expires_at"
- down_revision = "005_phase5a_reconcile" (current head)
- upgrade: ALTER TABLE control_history ADD COLUMN IF NOT EXISTS manual_expires_at TIMESTAMPTZ;
- downgrade: ALTER TABLE control_history DROP COLUMN IF EXISTS manual_expires_at;
- Pattern matches 002_add_control_history_load_percent.py exactly.

## Findings (cited)

### State-change path inventory (verified via codegraph_callers)
| Path | RM in-memory? | DB device_states? | control_history? | Has timestamp? |
|------|---------------|-------------------|-------------------|-----------------|
| Manual route (devices.py:188 control_device) | ✅ | ✅ | ✅ | ✅ implicit |
| Automation engine (control_engine.py:421 _set_device_state) | ✅ | ✅ | ✅ | ✅ |
| Binary hysteresis (device_controller.py:660 _control_binary_device) | ✅ | ❌ | ❌ | ❌ ← THE BUG |
| Mode change (devices.py:242 set_device_mode) | ❌ | ✅ | ❌ | ❌ |
| Restore on startup (relay_manager.py:207 restore_states) | ✅ | ❌ | ❌ | ❌ |

### Existing unused method (the read path)
control_actions.py:189 get_last_changed_per_channel() — 0 callers per codegraph.
Returns [{channel: 0-15, last_changed: ISO8601 | None}] bounded to last 30 days.

### Deploy-truth
Last ./deploy.sh: 20260629-182808-9535f69 at 2026-06-29 18:31:50 EDT.
4 in-flight untracked files (SystemSettingsPanel.tsx, system_config.py route + schema,
routes.py registration) + RelayChannelBox.tsx modified — never deployed.
Direct probe: GET /api/config → HTTP 404 (route not imported by running process).

### Symptom 3 root cause (CONFIRMED)
DeviceManager.tsx:134-138 passes {} as lastStateChangeByDevice map to
buildRelayChannelViewModels. ZoneConfig.tsx:142-152 builds map from relayState.timestamps.
But BOTH are ad-hoc fixes — the real fix is reading from control_history via
get_last_changed_per_channel(), which catches hysteresis cycles too.

### DFR layout
DfrBoardsPanel.tsx:281 — per-board channel grid is grid-cols-1, user wants grid-cols-2.

## Scope IN
- Alembic migration 006: add manual_expires_at column to control_history
- Wire hysteresis path (_control_binary_device) to call log_control_action
- Expose get_last_changed_per_channel via GET /api/devices/state-changes (or extend /api/devices)
- Extend DeviceControlRequest with optional duration_seconds; route computes manual_expires_at
- Control loop: each tick, scan for manual_expires_at <= NOW() AND mode='manual' → revert
- Frontend DeviceManager: replace {} map with fetched last_changed data; replace
  manualTimersByChannel with manual_expires_at from polled device data
- Frontend ZoneConfig: replace relayTimestamps hack with same fetched data
- Frontend ManualLightControl: delete localStorage timer; derive from polled device data
- Cut panel chrome (header, terminal strips, column labels, low-level input) from RelayChannelMatrix panel variant
- Cut RelayGlyph component from RelayChannelBox (both variants)
- DfrBoardsPanel.tsx:281 grid-cols-1 → grid-cols-2
- Commit 4 in-flight untracked files + all above changes
- Run Ruff (per Cursor rule)
- Run ./deploy.sh exactly once (per AGENTS.md + user death-penalty instruction)

## Scope OUT (Must NOT have)
- No manual systemctl restart automation-service (deploy script handles it)
- No new Redis keys for device state
- No new device_states columns
- No new unified set_device_state_with_authority abstraction
- No changes to DFR square aspect or rename field
- No changes to compact relay variant layout (only panel variant chrome is cut)
- No backend code changes to system_config.py route (already correct, just not deployed)

## Open questions
None remaining.

## Approval gate
status: approved (user said "yes, dig deeper" then approved lean control_history scope)
pending-action: append todos into .omo/plans/settings-relay-matrix-dfr-cleanup.md
