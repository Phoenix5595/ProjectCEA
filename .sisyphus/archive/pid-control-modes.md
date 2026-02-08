# PID Control Modes - Implementation Plan

**Created**: 2026-01-16
**Status**: PLANNED
**Priority**: HIGH

---

## Summary

Implement three PID control modes (Auto PID, Manual PID, ON/OFF) with full backend auto-tuning integration and frontend UI for mode selection and K-value management.

---

## Requirements

1. **Three Control Modes per Device Type**:
   - **Auto PID**: Auto-tuner runs continuously, K values update automatically with explanations
   - **PID (Manual)**: Current K values used, editable and savable by user
   - **ON/OFF**: Simple binary control, no PID

2. **Auto-tuning**: When Auto PID mode is activated, the Relay Feedback auto-tuner starts and keeps running

3. **K-Value Change Explanations**: When auto-PID changes K values, show a dialog explaining why (for future AI integration)

4. **Multi-device Protection**: Frontend may be open on 3+ devices; show stale data warnings

5. **Validation Fix**: Add 'fan' device type to frontend validation

---

## Phase 1: Backend - Database & API (Priority: HIGH)

### Task 1.1: Database Schema Updates

**File**: `Infrastructure/automation-service/app/database.py`

```sql
-- Add control_mode to pid_parameters
ALTER TABLE pid_parameters 
ADD COLUMN IF NOT EXISTS control_mode TEXT DEFAULT 'pid' 
CHECK (control_mode IN ('auto_pid', 'pid', 'on_off'));

-- Add change_reason to pid_parameter_history
ALTER TABLE pid_parameter_history 
ADD COLUMN IF NOT EXISTS change_reason TEXT;

-- Add autotune state table
CREATE TABLE IF NOT EXISTS pid_autotune_state (
    device_type TEXT PRIMARY KEY,
    is_active BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMP WITH TIME ZONE,
    cycles_completed INTEGER DEFAULT 0,
    current_amplitude REAL,
    current_period REAL,
    last_update TIMESTAMP WITH TIME ZONE,
    status TEXT DEFAULT 'idle'  -- 'idle', 'running', 'calculating', 'complete', 'error'
);
```

### Task 1.2: New API Endpoints

**File**: `Infrastructure/automation-service/app/routes/pid.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/pid/mode/{device_type}` | GET | Get current control mode |
| `/api/pid/mode/{device_type}` | POST | Set control mode (starts/stops auto-tune) |
| `/api/pid/autotune/{device_type}/status` | GET | Get auto-tune progress/status |
| `/api/pid/autotune/{device_type}/stop` | POST | Force stop auto-tuning |

**Request/Response Models**:

```python
class PIDModeUpdate(BaseModel):
    mode: Literal['auto_pid', 'pid', 'on_off']

class PIDModeResponse(BaseModel):
    device_type: str
    mode: str
    autotune_active: bool
    updated_at: Optional[str]

class AutotuneStatus(BaseModel):
    device_type: str
    is_active: bool
    status: str  # 'idle', 'running', 'calculating', 'complete', 'error'
    cycles_completed: int
    estimated_remaining_cycles: int
    current_ku: Optional[float]  # Ultimate gain (if calculated)
    current_tu: Optional[float]  # Ultimate period (if calculated)
    suggested_kp: Optional[float]
    suggested_ki: Optional[float]
    suggested_kd: Optional[float]
    last_change_reason: Optional[str]
```

### Task 1.3: Database Manager Methods

**File**: `Infrastructure/automation-service/app/database.py`

Add methods:
- `get_pid_control_mode(device_type) -> str`
- `set_pid_control_mode(device_type, mode) -> bool`
- `get_autotune_state(device_type) -> dict`
- `update_autotune_state(device_type, state) -> bool`
- `set_pid_parameters_with_reason(device_type, kp, ki, kd, reason, source) -> bool`

### Task 1.4: Control Loop Integration

**File**: `Infrastructure/automation-service/app/control/pid_controller_manager.py`

Modify `process_pid_control()` to:
1. Check control mode before running PID
2. If `auto_pid`: Run `RelayAutoTuner.update()` instead of normal PID
3. If `pid`: Run normal PID with current K values
4. If `on_off`: Return binary 0 or 100 based on error sign

**File**: `Infrastructure/automation-service/app/control/control_engine.py`

Add:
- Auto-tuner instance management per device
- Apply tuning results when auto-tune completes
- Log change reasons to `pid_parameter_history`

### Task 1.5: WebSocket Notifications

**File**: `Infrastructure/automation-service/app/routes/websocket.py`

Add message types:
- `pid_mode_changed`: When mode changes
- `pid_params_changed`: When K values change (includes `change_reason`)
- `autotune_progress`: Periodic updates during auto-tuning

---

## Phase 2: Frontend - Validation Fix (Priority: HIGH)

### Task 2.1: Add Fan Validation Ranges

**File**: `Infrastructure/frontend/src/utils/validation.ts`

```typescript
const PID_RANGES: Record<string, ...> = {
  heater: { kp: { min: 0.0, max: 100.0 }, ki: { min: 0.0, max: 1.0 }, kd: { min: 0.0, max: 10.0 } },
  co2: { kp: { min: 0.0, max: 50.0 }, ki: { min: 0.0, max: 0.5 }, kd: { min: 0.0, max: 5.0 } },
  // ADD THIS:
  fan: { kp: { min: 0.0, max: 100.0 }, ki: { min: 0.0, max: 1.0 }, kd: { min: 0.0, max: 10.0 } },
};
```

---

## Phase 3: Frontend - Types & API (Priority: HIGH)

### Task 3.1: Update PID Types

**File**: `Infrastructure/frontend/src/types/pid.ts`

```typescript
export type PIDControlMode = 'auto_pid' | 'pid' | 'on_off';

export interface PIDParameters {
  kp: number;
  ki: number;
  kd: number;
  control_mode: PIDControlMode;
  updated_at?: string;
  updated_by?: string;
  source?: string;
}

export interface AutotuneStatus {
  device_type: string;
  is_active: boolean;
  status: 'idle' | 'running' | 'calculating' | 'complete' | 'error';
  cycles_completed: number;
  estimated_remaining_cycles: number;
  current_ku?: number;
  current_tu?: number;
  suggested_kp?: number;
  suggested_ki?: number;
  suggested_kd?: number;
  last_change_reason?: string;
}

export interface PIDChangeNotification {
  device_type: string;
  old_values: { kp: number; ki: number; kd: number };
  new_values: { kp: number; ki: number; kd: number };
  reason: string;
  timestamp: string;
}
```

### Task 3.2: Update API Client

**File**: `Infrastructure/frontend/src/services/api.ts`

Add methods:
```typescript
async getPIDMode(deviceType: string): Promise<PIDModeResponse>
async setPIDMode(deviceType: string, mode: PIDControlMode): Promise<PIDModeResponse>
async getAutotuneStatus(deviceType: string): Promise<AutotuneStatus>
async stopAutotune(deviceType: string): Promise<void>
```

---

## Phase 4: Frontend - PIDEditor Rewrite (Priority: HIGH)

### Task 4.1: Mode Selector Component

**File**: `Infrastructure/frontend/src/components/PIDModeSelector.tsx` (NEW)

```
┌─────────────────────────────────────────────┐
│  Control Mode                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Auto PID │ │   PID    │ │  ON/OFF  │    │
│  │    ○     │ │    ●     │ │    ○     │    │
│  └──────────┘ └──────────┘ └──────────┘    │
│                                             │
│  ⓘ Auto PID: System continuously tunes     │
│     K values using relay feedback method    │
└─────────────────────────────────────────────┘
```

### Task 4.2: Enhanced PIDEditor

**File**: `Infrastructure/frontend/src/components/PIDEditor.tsx`

Features:
1. **Mode selector** at top (Auto PID / PID / ON/OFF)
2. **K-value inputs**:
   - Read-only with "Auto-tuning..." badge when `auto_pid`
   - Editable when `pid`
   - Hidden when `on_off`
3. **Autotune progress** (when auto_pid active):
   - Cycles completed: 3/5
   - Status: "Measuring oscillations..."
   - Current estimates: Ku=XX, Tu=XX
4. **Audit info footer**: "Last updated: 2026-01-16 10:30 by system (auto_pid)"
5. **Save/Activate button**: Saves mode + K values (if manual)

### Task 4.3: K-Value Change Dialog

**File**: `Infrastructure/frontend/src/components/PIDChangeDialog.tsx` (NEW)

When auto-PID changes K values, show modal:

```
┌─────────────────────────────────────────────┐
│  ⚡ PID Parameters Updated                  │
├─────────────────────────────────────────────┤
│                                             │
│  Device: heater                             │
│                                             │
│  Changes:                                   │
│    Kp: 25.0 → 22.3                         │
│    Ki: 0.02 → 0.018                        │
│    Kd: 0.0 → 0.5                           │
│                                             │
│  Reason:                                    │
│  "Auto-tune detected 0.8°C overshoot.      │
│   Reduced Kp by 11% and added Kd to        │
│   improve settling time."                   │
│                                             │
│  Tuning metrics:                            │
│    Ultimate gain (Ku): 45.2                │
│    Ultimate period (Tu): 180s              │
│    Method: Ziegler-Nichols (some overshoot)│
│                                             │
│              [ Dismiss ]  [ View History ]  │
└─────────────────────────────────────────────┘
```

### Task 4.4: Multi-Device Protection

**File**: `Infrastructure/frontend/src/components/PIDEditor.tsx`

1. Store `last_known_updated_at` when loading
2. Before saving, fetch current `updated_at`
3. If different, show warning:

```
┌─────────────────────────────────────────────┐
│  ⚠️ Parameters Changed Externally          │
├─────────────────────────────────────────────┤
│  Another device or auto-tuner updated      │
│  the PID parameters while you were editing.│
│                                             │
│  Your values: Kp=25.0, Ki=0.02             │
│  Current values: Kp=22.3, Ki=0.018         │
│                                             │
│  [ Reload Current ]  [ Overwrite Anyway ]  │
└─────────────────────────────────────────────┘
```

---

## Phase 5: WebSocket Integration (Priority: MEDIUM)

### Task 5.1: Listen for PID Updates

**File**: `Infrastructure/frontend/src/components/PIDEditor.tsx`

Subscribe to WebSocket messages:
- `pid_params_changed`: Trigger reload or show change dialog
- `autotune_progress`: Update progress display

---

## Implementation Order

| Phase | Task | Estimated Time | Dependencies |
|-------|------|----------------|--------------|
| 2.1 | Fan validation fix | 5 min | None |
| 1.1 | Database schema | 15 min | None |
| 1.2 | API endpoints | 45 min | 1.1 |
| 1.3 | Database methods | 30 min | 1.1 |
| 3.1 | Frontend types | 15 min | None |
| 3.2 | API client methods | 15 min | 3.1, 1.2 |
| 4.1 | Mode selector component | 30 min | 3.1 |
| 4.2 | PIDEditor rewrite | 60 min | 4.1, 3.2 |
| 1.4 | Control loop integration | 60 min | 1.2, 1.3 |
| 4.3 | Change dialog | 30 min | 4.2 |
| 4.4 | Multi-device protection | 20 min | 4.2 |
| 1.5 | WebSocket notifications | 30 min | 1.4 |
| 5.1 | WebSocket frontend | 20 min | 1.5, 4.2 |

**Total estimated time**: ~6 hours

---

## Testing Checklist

- [ ] Fan device type validates correctly in frontend
- [ ] Mode selector switches between auto_pid/pid/on_off
- [ ] K-value inputs are read-only in auto_pid mode
- [ ] K-value inputs are editable in pid mode
- [ ] K-value inputs are hidden in on_off mode
- [ ] Activating auto_pid starts the auto-tuner
- [ ] Auto-tune progress displays correctly
- [ ] K-value changes trigger dialog with reason
- [ ] Multi-device conflict detection works
- [ ] WebSocket updates reflect in UI
- [ ] Rate limiting still works (5s between updates)
- [ ] Reset to defaults works in all modes

---

## Files Modified

### Backend
- `Infrastructure/automation-service/app/database.py` - Schema + methods
- `Infrastructure/automation-service/app/routes/pid.py` - New endpoints
- `Infrastructure/automation-service/app/control/pid_controller_manager.py` - Mode handling
- `Infrastructure/automation-service/app/control/control_engine.py` - Auto-tuner integration
- `Infrastructure/automation-service/app/routes/websocket.py` - New message types

### Frontend
- `Infrastructure/frontend/src/utils/validation.ts` - Add fan ranges
- `Infrastructure/frontend/src/types/pid.ts` - New types
- `Infrastructure/frontend/src/services/api.ts` - New API methods
- `Infrastructure/frontend/src/components/PIDEditor.tsx` - Major rewrite
- `Infrastructure/frontend/src/components/PIDModeSelector.tsx` - NEW
- `Infrastructure/frontend/src/components/PIDChangeDialog.tsx` - NEW

---

## Notes

- **Locked Decision**: "PID | Self-tuning, UI shows K values + reset button" ✓
- **Rate Limiting**: Backend already has 5-second rate limit per device_type
- **Auto-tuner**: Uses Åström-Hägglund relay feedback method (already implemented in `pid_autotuner.py`)
- **Tuning Rules**: Ziegler-Nichols "some_overshoot" preset by default

---

## Clarifications (2026-01-16)

### Confirmed Decisions

1. **ON/OFF Mode**: Uses **hysteresis**, not simple binary
   - Example: Turn ON when error > +1.0°C, turn OFF when error < -0.5°C
   - Thresholds configurable per device type in database
   - Prevents rapid cycling near setpoint

2. **Auto-resume after reboot**: **YES**
   - `control_mode` persisted in database
   - On startup, check mode - if `auto_pid`, resume auto-tuning
   - Auto-tuner state (`pid_autotune_state` table) also persisted for continuity

3. **K-Value Change Dialog**: **Full modal dialog**
   - Appears every time auto-PID updates K values (~1-3 times/day during tuning)
   - Shows old vs new values, reason, tuning metrics
   - User must dismiss before continuing (important for awareness)
   - Prepares UX for future AI-driven explanations

### Additional Schema for Hysteresis

```sql
-- Add to pid_parameters table
ALTER TABLE pid_parameters 
ADD COLUMN IF NOT EXISTS hysteresis_high REAL DEFAULT 1.0,
ADD COLUMN IF NOT EXISTS hysteresis_low REAL DEFAULT 0.5;
```

### ON/OFF Logic (pseudo-code)

```python
def on_off_control(error: float, hysteresis_high: float, hysteresis_low: float, current_state: bool) -> bool:
    if error > hysteresis_high:
        return True  # Turn ON
    elif error < -hysteresis_low:
        return False  # Turn OFF
    else:
        return current_state  # Maintain current state (deadband)
```
