# Draft: PID Mode Select Fix + Auto PID Verification

## Requirements (confirmed)
- **Issue**: PID mode selector buttons don't respond to clicks
- **Scope**: Full end-to-end verification of auto_pid implementation

## ROOT CAUSE IDENTIFIED ✓

### Backend Bug (PRIMARY CAUSE)
**File**: `Infrastructure/automation-service/app/routes/pid.py:373-374`
**Problem**: `update_autotune_state()` called with wrong parameters:
```python
# BROKEN (current code):
await database.update_autotune_state(device_type, is_active=True, status="running", cycles_completed=0)

# CORRECT (expected signature):
await database.update_autotune_state(device_type, state="running")  # or similar
```
**Impact**: API returns 500 error when switching to `auto_pid` mode

### Frontend Issue (SECONDARY)
**File**: `Infrastructure/frontend/src/components/VerticalPIDBlock.tsx:99-101`
**Problem**: Error caught silently with `logger.error()` - no toast/feedback to user
**Impact**: User clicks button, API fails, no indication of failure

### Dead Code (CLEANUP)
- `PIDEditor.tsx` - never imported/used anywhere
- `PIDModeSelector.tsx` - only used by PIDEditor (also dead)
- Actual component: `VerticalPIDBlock.tsx` (inline mode buttons)

## Auto PID Implementation Status
- **RelayAutoTuner**: ✓ Complete - Åström-Hägglund method, oscillation tracking, Ziegler-Nichols
- **PIDControllerManager**: ✓ Routes to `_process_autotune_control()` for auto_pid mode
- **API Endpoints**: ✓ GET/POST /api/pid/mode/{device_type} exist
- **Frontend Status Display**: ⚠️ Needs verification after backend fix

## Scope Boundaries
- INCLUDE: Fix backend bug, add error toast, verify auto_pid end-to-end
- EXCLUDE: PID algorithm changes, new features
- OPTIONAL: Remove dead code (PIDEditor, PIDModeSelector)
