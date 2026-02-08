# ProjectCEA Remaining Tasks Plan

## Status Summary
- **Phases 1-5**: ✅ Complete
- **Frontend Fixes**: ✅ Complete (light polling, max=180 validation)
- **Grafana Fix**: ✅ Complete (removed duplicates, enabled UI updates, disabled sync timer)
- **Light Intensity Fix**: ✅ Complete (int→round rounding fix)

---

## Investigation Findings

### Light Intensity Mismatch Issue
**Root Cause**: Rounding truncation in device_controller.py line 304
- Was: `intensity_percent = int(intensity * 100)` - truncates 0.999 → 99%
- Fixed: `intensity_percent = round(intensity * 100)` - rounds 0.999 → 100%

**Additional Issues Found**:
1. `log_control_action()` signature mismatch - `device_type` kwarg not expected
2. Scheduler refresh now happens every 1s with light status polling

### Grafana Dashboard Reverting Issue
**Root Causes Found**:
1. `grafana-sync.timer` was restoring old versions every 5 minutes
2. Duplicate `*_restored.json` files conflicting with originals
3. `allowUiUpdates: false` prevented saving UI changes

**Fixes Applied**:
1. Disabled grafana-sync.timer
2. Removed duplicate restored files
3. Set `allowUiUpdates: true` in provisioning config

---

## Immediate Tasks

### 1. PID Reset Button
**Priority**: Medium | **Effort**: 30 min

**Backend**: ✅ Done - `/api/pid/parameters/{device_type}/reset` endpoint in routes/pid.py

**Frontend** (pending):
- Add resetPIDParameters(deviceType) to api.ts
- Add Reset button to PIDEditor.tsx after Save button

### 2. Leaf Delta Inputs
**Priority**: Medium | **Effort**: 45 min

**Backend**: ✅ Done - leaf_delta_day/night columns and interpolation

**Frontend** (pending):
- Add two number inputs (-5 to +5, step 0.1) to ClimateScheduleEditor.tsx
- Label: "Leaf Delta Day/Night (°C)"

---

## Phase 6: Home Server AI

### 6.1 Data Sync (2-4 hours)
- rsync or API sync from mothernode to home server
- Export measurement_hourly and measurement_daily tables
- Schedule hourly sync with checksums

### 6.2 XGBoost Spike Prediction (8-16 hours)
- Export training data via ai_export.py
- Feature engineering (lag, rate of change, time encoding)
- Train models for temp/humidity spike prediction
- Export to ONNX/pickle

### 6.3 Prediction API (4-8 hours)
- FastAPI service for predictions
- POST /predict/spike endpoint
- Grafana visualization panel

---

## Phase 7-8: Code Quality

### 7.1 Type Hints (4-8 hours)
- Add type hints to all Python files
- Configure mypy, add to CI/CD

### 7.2 Testing (8-16 hours)
- Fix existing pytest tests
- Add unit tests for controllers
- Add integration tests for APIs
- Target 70% coverage

---

## Known Issues
1. log_control_action signature mismatch warnings
2. automation-service >30s startup causing watchdog timeout
3. Grafana sync timer disabled - manual dashboard export needed
