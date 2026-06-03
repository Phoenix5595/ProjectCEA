# Deprecated Mode-Based Setpoint System — Reference Manifest

**Task:** 4 of `climate-period-ramp-migration.md`  
**Generated:** 2026-03-18  
**Scope:** All references to the deprecated mode-based setpoint system across `Infrastructure/automation-service/app/` (Python backend) and `Infrastructure/frontend/src/` (TypeScript frontend).

---

## Summary

| Category | Count | Description |
|----------|-------|-------------|
| **REMOVE** | ~11 files | Entire files that implement the deprecated system |
| **MODIFY** | ~20 files | Files that reference the deprecated system and need updating |
| **KEEP** | ~15 entries | Comments, docs, historical data, unrelated code, test fixtures |

**Total unique files affected:** ~31  
**Total references found:** ~250+

---

## WHAT IS BEING DEPRECATED

The mode-based setpoint system consists of:
- `setpoints` database table (DAY/NIGHT/PRE_DAY/PRE_NIGHT rows with `ramp_in_duration`)
- `SetpointRepository.get_setpoint()` / `set_setpoint()` / `get_all_setpoints_for_location_cluster()` methods
- `Scheduler.get_climate_mode()` method (lines 672–769)
- `ramp_in_duration` field throughout the stack
- `/api/setpoints/*` REST routes
- `SetpointsTable` and `SetpointEditor` React components
- StateManager setpoint cache (mode-unaware)

**What replaces it:**
- `climate_periods` table (already exists)
- `ClimatePeriodRepository.get_active_period()` (already exists)
- `ramp_minutes` field in `climate_periods` table
- `/api/climate-periods` REST routes (already exists)
- `ClimatePeriodsTable` React component (already exists)

---

## Category Definitions

- **REMOVE:** Entire files (or large contiguous blocks) that implement the deprecated system and will be deleted wholesale.
- **MODIFY:** Files that reference the deprecated system but need targeted updates (not full removal). Often involves replacing one method/data source with another.
- **KEEP:** References in comments, docstrings, historical data, test fixtures, unrelated code, or configuration strings that describe behavior without implementing it.

---

## REMOVE — Entire Files (Dead Code)

### 1. `Infrastructure/automation-service/app/routes/setpoints.py`
**Lines:** 1–376 (entire file)  
**Reason:** Implements the deprecated `/api/setpoints/*` REST routes. Contains `get_setpoints`, `update_setpoints`, `get_all_setpoints_for_location_cluster`, and `get_effective_setpoints` (effective endpoint is kept, but this entire file goes and the effective endpoint moves to `climate_periods`).

```
@router.get("/api/setpoints")                    # L57
@router.get("/api/setpoints/{location}/{cluster}")  # L65
@router.get("/api/setpoints/{location}/{cluster}/all-modes")  # L102
@router.post("/api/setpoints/{location}/{cluster}")  # L121
@router.get("/api/setpoints/{location}/{cluster}/effective")  # L358
```

---

### 2. `Infrastructure/automation-service/app/repositories/setpoints.py` — Mode-based methods only
**Lines:** 104–282 (methods), 104–505 (entire file)  
**Reason:** The `get_setpoint()`, `set_setpoint()`, and `get_all_setpoints_for_location_cluster()` methods directly query the `setpoints` table. The `effective_setpoints`-related methods (`log_effective_setpoint`, `log_effective_setpoints`, `get_latest_effective_setpoints`, `flush_batch_buffer`) operate on `effective_setpoints` table and should be KEPT.

```
async def get_setpoint(...)  # L104-166 — REMOVE (queries setpoints table)
async def set_setpoint(...)  # L168-264 — REMOVE (writes to setpoints table)
async def get_all_setpoints_for_location_cluster(...)  # L266-282 — REMOVE (queries setpoints table)
async def get_latest_effective_setpoints(...)  # L284-301 — KEEP (effective_setpoints table)
async def log_effective_setpoint(...)  # L303-359 — KEEP (effective_setpoints table)
async def log_effective_setpoints(...)  # L361-475 — KEEP (effective_setpoints table)
async def flush_batch_buffer(...)  # L29-102 — KEEP (effective_setpoints batch writes)
```

---

### 3. `Infrastructure/automation-service/app/repositories/mode_sync.py`
**Lines:** 1–150 (entire file)  
**Reason:** `sync_climate_setpoints_from_mode_parameters()` reads from `mode_parameters` table and writes to `setpoints` table. Once `setpoints` table is deprecated, this sync function is no longer needed.

```
async def sync_climate_setpoints_from_mode_parameters(...)  # L11-150
```

---

### 4. `Infrastructure/automation-service/app/control/scheduler.py` — `get_climate_mode()` method
**Lines:** 672–769 (method body), plus method signature at 672  
**Reason:** This entire method computes discrete climate modes (PRE_DAY/DAY/PRE_NIGHT/NIGHT) from schedule bounds. It will be replaced by `ClimatePeriodRepository.get_active_period()`.

```
def get_climate_mode(...)  # L672-769 — REMOVE (entire method)
  """Get current climate mode (PRE_DAY, DAY, PRE_NIGHT, NIGHT)..."""  # L682
  return ("PRE_DAY", ...)  # L746
  return ("DAY", ...)  # L752/L756
  return ("PRE_NIGHT", ...)  # L762
  return ("NIGHT", ...)  # L766/L769
```

**KEEP:** All other content in `scheduler.py` (light scheduling, `_time_to_minutes`, other methods).

---

### 5. `Infrastructure/automation-service/app/state/__init__.py` — Setpoint convenience API
**Lines:** 537–686 (method bodies)  
**Reason:** `get_setpoint()` and `set_setpoint()` on StateManager are convenience wrappers around the mode-based setpoint cache in Redis. They will be replaced by direct reads from `climate_periods`-derived effective setpoints.

```
async def get_setpoint(...)  # L537-629 — REMOVE
  ramp_key = f"setpoint:{location}:{cluster}:ramp_in_duration"  # L558
  ramp_in_duration = await self.get(ramp_key)  # L565
  result["ramp_in_duration"] = int(ramp_in_duration)  # L620
async def set_setpoint(...)  # L631-686 — REMOVE
  ramp_in_duration: int | None = None  # L640
  await self.set(f"setpoint:{location}:{cluster}:ramp_in_duration", ...)  # L681-685
```

---

### 6. `Infrastructure/automation-service/config_cli.py` — setpoint commands
**Lines:** 185–379 (functions `cmd_setpoint_get`, `cmd_setpoint_set`)  
**Reason:** CLI commands for getting/setting mode-based setpoints using `setpoint_repo.get_setpoint` and `setpoint_repo.set_setpoint`.

```
async def cmd_setpoint_get(...)  # L185-238
  setpoint = await db.setpoint_repo.get_setpoint(location, cluster, mode)  # L192
  setpoint = await db.setpoint_repo.get_setpoint(location, cluster, None)  # L200
  await db.setpoint_repo.get_all_setpoints_for_location_cluster(...)  # L203
async def cmd_setpoint_set(...)  # L240-379
  existing = await db.setpoint_repo.get_setpoint(location, cluster, mode)  # L277
  await db.setpoint_repo.set_setpoint(...)  # L353
```

---

### 7. `Infrastructure/automation-service/scripts/validate_loop_performance.py`
**Lines:** 40–48 (mock for `setpoint_repo.get_setpoint`)  
**Reason:** Test/validation script that mocks the deprecated `setpoint_repo.get_setpoint` method. Will need updating to mock `climate_period_repo.get_active_period()`.

```
db._setpoint_repo.get_setpoint = AsyncMock(...)  # L40
```

---

### 8. `Infrastructure/frontend/src/components/SetpointEditor.tsx`
**Lines:** 1–338 (entire file)  
**Reason:** Standalone setpoint editor that calls `/api/setpoints/*`, uses `ramp_in_duration`, and works with mode-based setpoints. This UI is specific to the deprecated system.

```
interface SetpointEditorProps { mode?: 'DAY' | 'NIGHT' | null }  # L11
export default function SetpointEditor(...)  # L14
  ramp_in_duration: setpoint.ramp_in_duration ?? 0  # L40
  setRampInDuration(setpoint.ramp_in_duration ?? 0)  # L58
  ramp_in_duration: rampInDuration  # L166
  className=...{errors.ramp_in_duration ? ...}  # L301
  Current: {savedValues.ramp_in_duration ?? 0} minutes  # L304
```

---

### 9. `Infrastructure/frontend/src/components/SetpointsTable.tsx`
**Lines:** 1–149 (entire file)  
**Reason:** Legacy component (149 lines) that appears to render setpoints per mode. This is separate from the `ClimateScheduleEditor`/`SetpointTimeline` combo.

```
interface SetpointsTableProps { ... }  # L3
export default function SetpointsTable(...)  # L10
```

---

### 10. `Infrastructure/frontend/src/types/setpoint.ts`
**Lines:** 1–26 (entire file — consider merging into climate period types)  
**Reason:** TypeScript types for the mode-based setpoint system. The `ramp_in_duration` field and `mode` field will be replaced by `ramp_minutes` from climate periods. `TRANSITION` mode value is deprecated.

```
export interface Setpoint {  # L5
  ramp_in_duration?: number;  # L11 — REMOVE (replaced by ramp_minutes)
export interface SetpointUpdate {  # L16
  ramp_in_duration?: number;  # L22 — REMOVE
```

---

### 11. `Infrastructure/automation-service/AGENTS.md`
**Lines:** 38, 44, 45, 48, 49, 50, 82  
**Reason:** Documentation describing the deprecated mode-based setpoint system (PRE_DAY/DAY/PRE_NIGHT/NIGHT, `ramp_in_duration`). Needs updating to reflect climate periods architecture.

```
# Lines to update in this documentation file:
- L38: Climate (slave): PRE_DAY, DAY, PRE_NIGHT, NIGHT...
- L44: PRE_DAY: Ramp from NIGHT → PRE_DAY setpoints...
- L45: PRE_NIGHT: Ramp from DAY → PRE_NIGHT setpoints...
- L48: ramp_in_duration: 0-240 minutes
- L49: PRE_NIGHT: Fetches DAY setpoints, ramps to PRE_NIGHT
- L50: PRE_DAY: Fetches NIGHT setpoints, ramps to PRE_DAY
- L82: | `/api/setpoints` | Target values per mode |
```

---

## MODIFY — Files Needing Targeted Updates

### 12. `Infrastructure/automation-service/app/control/control_engine.py`
**Lines:** 318, 357–365, 389–404, 426, 428–434  
**Reason:** Uses `get_climate_mode` to determine climate mode, reads setpoints via `setpoint_repo.get_setpoint`, and handles `ramp_in_duration`.

```
# L318: Comment — MODIFY (update comment to reference climate periods)
# Together they feed get_climate_mode for climate mode (setpoints only).

# L357: Call to deprecated method — MODIFY (replace with get_active_period)
mode_result = self.scheduler.get_climate_mode(...)

# L389-391: Direct setpoints table read — MODIFY (use climate_periods)
setpoint_data = await self.database.setpoint_repo.get_setpoint(location, cluster, current_mode)

# L394, 403: ramp_in_duration — MODIFY (use ramp_minutes from climate_periods)
ramp_in_duration=setpoint_data.get("ramp_in_duration"),

# L426, 428-434: Ramp logging with ramp_in_duration — MODIFY
ramp_in_duration={setpoint_data.get('ramp_in_duration', 0)}
if current_mode in ["PRE_DAY", "PRE_NIGHT"]:
  ramp_in = setpoint_data.get("ramp_in_duration", 0) or 0
```

---

### 13. `Infrastructure/automation-service/app/routes/debug.py`
**Lines:** 66–76, 91–94, 105  
**Reason:** Debug endpoint that calls `get_climate_mode` and reads from `setpoints` table.

```
# L66: Call to deprecated method — MODIFY (replace with get_active_period)
mode_result = scheduler.get_climate_mode(...)

# L91, 94: Direct setpoints table read — MODIFY
setpoints = await db.setpoint_repo.get_setpoint(location, cluster, derived_mode)
setpoints = await db.setpoint_repo.get_setpoint(location, cluster, None)

# L105: Response field — MODIFY (update to reference climate period data)
"setpoints": setpoints,
```

---

### 14. `Infrastructure/automation-service/app/routes/routes.py`
**Lines:** 22, 40, 80–82  
**Reason:** Registers the deprecated `setpoints` router.

```
from app.routes import setpoints  # L22 — MODIFY (remove import)
app.include_router(setpoints.router, tags=["setpoints"])  # L40 — MODIFY (remove)
app.dependency_overrides[setpoints.get_database] = ...  # L80 — MODIFY (remove)
app.dependency_overrides[setpoints.get_config] = ...  # L81 — MODIFY (remove)
```

---

### 15. `Infrastructure/automation-service/app/main.py`
**Lines:** 45–47  
**Reason:** OpenAPI tags include "setpoints" as a named endpoint group.

```
{
    "name": "setpoints",  # L45 — MODIFY (remove or rename to climate-periods)
    "description": "Climate setpoint management..."  # L46 — MODIFY
},
```

---

### 16. `Infrastructure/automation-service/app/services/schedule_state.py`
**Lines:** 18, 46–58, 92, 107, 118–124  
**Reason:** Builds schedule state that includes mode-based setpoints read from the `setpoints` table. Will need to read from `climate_periods` instead.

```
SETPOINT_MODES = ("DAY", "NIGHT", "PRE_DAY", "PRE_NIGHT")  # L18 — MODIFY (remove constant)
for mode in SETPOINT_MODES:  # L46 — MODIFY (iterate climate periods instead)
  setpoint_data = await setpoint_repo.get_setpoint(location, cluster, mode)  # L47
  "ramp_in_duration": setpoint_data.get("ramp_in_duration", 0) or 0,  # L55 — MODIFY
"setpoints": setpoints,  # L92 — MODIFY (key name may change)
"Queries all room schedules, climate schedules, setpoints (including PRE_DAY..."  # L107 — MODIFY (docstring)
FROM setpoints  # L123 — MODIFY (remove from UNION query)
```

---

### 17. `Infrastructure/automation-service/app/routes/schedules/climate.py`
**Lines:** 42, 52, 67, 78, 87–96, 107, 123, 165, 168–179, 294–295, 341  
**Reason:** Climate schedule endpoints that read/write mode-based setpoints via `setpoint_repo.get_setpoint` and the `setpoints` table. Uses `SETPOINT_MODES` constant and validates `ramp_in_duration`.

```
class ClimateScheduleSetpoint(BaseModel):  # L34
  ramp_in_duration: int | None = None  # L42 — MODIFY (remove field)
setpoints: dict[str, ClimateScheduleSetpoint]  # L52 — MODIFY (dict keys change)
and setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)  # L67 — MODIFY (docstring)
"setpoints": {"DAY": {}, "NIGHT": {}, "PRE_DAY": {}, "PRE_NIGHT": {}}  # L78 — MODIFY
setpoint_data = await database.setpoint_repo.get_setpoint(location, cluster, mode)  # L87 — MODIFY
"ramp_in_duration": setpoint_data.get("ramp_in_duration", 0) or 0,  # L95 — MODIFY
"setpoints": setpoints,  # L107 — MODIFY
Setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)  # L123 — MODIFY (docstring)
detail=f"Invalid mode in setpoints: {mode}. Valid modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT"  # L165 — MODIFY
ramp_in = setpoint_data.ramp_in_duration or 0  # L169 — MODIFY
detail=f"ramp_in_duration for {mode} must be between 0 and 240 minutes"  # L173 — MODIFY
f"VPD ramp_in_duration for {mode} is {ramp_in} minutes..."  # L179 — MODIFY
updates["ramp_in_duration"] = setpoint_data.ramp_in_duration  # L295 — MODIFY
"setpoints": { mode: setpoint.model_dump() ... }  # L341 — MODIFY
```

---

### 18. `Infrastructure/automation-service/app/routes/schedules/utils.py`
**Lines:** 12, 54–67, 110  
**Reason:** Shared utility `_build_schedule_state()` that reads mode-based setpoints. Used by both the deprecated setpoints routes and the climate schedule routes.

```
SETPOINT_MODES = ("DAY", "NIGHT", "PRE_DAY", "PRE_NIGHT")  # L12 — MODIFY (remove constant)
for mode in SETPOINT_MODES:  # L55 — MODIFY
  setpoint_data = await database.setpoint_repo.get_setpoint(location, cluster, mode)  # L56 — MODIFY
  "ramp_in_duration": setpoint_data.get("ramp_in_duration", 0) or 0,  # L64 — MODIFY
"setpoints": setpoints,  # L110 — MODIFY
```

---

### 19. `Infrastructure/automation-service/app/control/setpoint_manager.py`
**Lines:** 379, 392, 404, 411, 480, 512, 527, 566–576, 581–584, 596, 605  
**Reason:** Setpoint manager uses `ramp_in_duration` and mode-based setpoint lookups via `setpoint_repo.get_setpoint`. The `mode_fallbacks` dict at lines 580–584 defines PRE_DAY/DAY/PRE_NIGHT/NIGHT transitions.

```
current_mode: Current climate mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT)  # L379 — MODIFY (comment)
ramp_in_duration = setpoint_data.get("ramp_in_duration", 0) or 0  # L392 — MODIFY
if ramp_in_duration > 0:  # L404 — MODIFY
  ramp_in_duration,  # L411 — MODIFY
ramp_in_duration: float,  # L480 — MODIFY (parameter)
ramp_in_duration,  # L512 — MODIFY (usage)
f"RAMPS INITIATED: {location}/{cluster} {ramps_started} over {ramp_in_duration}min"  # L527 — MODIFY
prev_setpoint_data = await self.database.setpoint_repo.get_setpoint(...)  # L566 — MODIFY
primary_data = await self.database.setpoint_repo.get_setpoint(...)  # L596 — MODIFY
secondary_data = await self.database.setpoint_repo.get_setpoint(...)  # L605 — MODIFY
"PRE_DAY": ("NIGHT", None),  # L581 — MODIFY (fallback dict)
"DAY": ("PRE_DAY", "NIGHT"),  # L582 — MODIFY
"PRE_NIGHT": ("DAY", None),  # L583 — MODIFY
"NIGHT": ("PRE_NIGHT", "DAY"),  # L584 — MODIFY
```

---

### 20. `Infrastructure/automation-service/app/control/leaf_delta.py`
**Lines:** 16–17, 41–48, 116  
**Reason:** `ClimateMode` enum defines PRE_DAY and PRE_NIGHT as values. The `get_leaf_delta()` function handles transitions between these modes. These will be updated to reference climate period names instead.

```
PRE_DAY = "pre_day"  # L17 — MODIFY (enum value)
PRE_NIGHT = "pre_night"  # L17 — MODIFY (enum value)
elif current_mode == ClimateMode.PRE_DAY:  # L41 — MODIFY
elif current_mode == ClimateMode.PRE_NIGHT:  # L45 — MODIFY
if self._current_mode in (ClimateMode.PRE_DAY, ClimateMode.PRE_NIGHT):  # L116 — MODIFY
```

---

### 21. `Infrastructure/automation-service/app/services/mode_transition_service.py`
**Lines:** 8, 128–129  
**Reason:** Imports and calls `sync_climate_setpoints_from_mode_parameters()`, which writes to the deprecated `setpoints` table.

```
from ..repositories.mode_sync import sync_climate_setpoints_from_mode_parameters  # L8 — MODIFY (remove import)
climate_sync_result = await sync_climate_setpoints_from_mode_parameters(...)  # L128 — MODIFY (remove or replace with climate_periods sync)
```

---

### 22. `Infrastructure/frontend/src/services/api.ts`
**Lines:** 3, 72–86  
**Reason:** API client calls `/api/setpoints/*` endpoints. These need to be replaced with `/api/climate-periods/*` calls.

```
import type { Setpoint, SetpointUpdate } from '../types/setpoint'  # L3 — MODIFY (update/remove import)
async getSetpoints(location, cluster, mode)  # L72 — MODIFY (replace with climate period API)
  GET /api/setpoints/...  # L74 — MODIFY
async getAllSetpointsForLocationCluster(location, cluster)  # L78 — MODIFY (replace)
  GET /api/setpoints/.../all-modes  # L79 — MODIFY
async updateSetpoints(location, cluster, setpoints)  # L83 — MODIFY (replace)
  POST /api/setpoints/...  # L84 — MODIFY
```

---

### 23. `Infrastructure/frontend/src/components/ClimateScheduleEditor.tsx`
**Lines:** 18–24, 21–22, 133–134, 242–243, 250–251  
**Reason:** Uses `setpoints` dict keyed by PRE_DAY/PRE_NIGHT/DAY/NIGHT with `ramp_in_duration`. Will need to update to use `ramp_minutes` from climate periods.

```
setpoints: {  # L18
  PRE_DAY?: any  # L21 — MODIFY (key name may stay, but field names change)
  PRE_NIGHT?: any  # L22 — MODIFY
{setpoint.ramp_in_duration > 0 && (  # L133 — MODIFY (ramp_in_duration → ramp_minutes)
<span>Ramp: {setpoint.ramp_in_duration}m</span>  # L134 — MODIFY
PRE_DAY: { ...(climateData.setpoints?.PRE_DAY || {}) },  # L242 — MODIFY
PRE_NIGHT: { ...(climateData.setpoints?.PRE_NIGHT || {}) }  # L243 — MODIFY
PRE_DAY: { ...setpointsMap.PRE_DAY, ...(climateData.setpoints?.PRE_DAY || {}) },  # L250 — MODIFY
PRE_NIGHT: { ...setpointsMap.PRE_NIGHT, ...(climateData.setpoints?.PRE_NIGHT || {}) }  # L251 — MODIFY
```

---

### 24. `Infrastructure/frontend/src/components/SetpointTimeline.tsx`
**Lines:** 18–19, 65, 454–455, 462–514, 522, 565–566, 578, 620, 726, 728, 735, 742, 749  
**Reason:** Timeline renders PRE_DAY/DAY/PRE_NIGHT/NIGHT periods with `ramp_in_duration` from the setpoints data structure. Will need to read `ramp_minutes` from climate periods instead.

```
PRE_DAY?: any  # L18 — MODIFY (interface prop)
PRE_NIGHT?: any  # L19 — MODIFY
// PRE_NIGHT happens during day, ending at day end  # L65 — MODIFY (comment)
PRE_DAY takes precedence over DAY during PRE_DAY period  # L454-455 — MODIFY (comments)
if (_setpoints.PRE_DAY && preDayDuration > 0) {  # L463 — MODIFY
const rampIn = _setpoints.PRE_DAY.ramp_in_duration || 0  # L466 — MODIFY
const rampIn = _setpoints.DAY.ramp_in_duration || 0  # L475 — MODIFY
if (_setpoints.PRE_NIGHT && preNightDuration > 0) {  # L502 — MODIFY
const rampIn = _setpoints.PRE_NIGHT.ramp_in_duration || 0  # L505 — MODIFY
const rampIn = _setpoints.NIGHT.ramp_in_duration || 0  # L514 — MODIFY
PRE_DAY comes before DAY, so it should be rendered first  # L522 — MODIFY (comment)
NIGHT -> PRE_DAY  # L565-566 — MODIFY (comment)
PRE_DAY (first in array), previous should be NIGHT  # L578 — MODIFY (comment)
// Draw ramp if ramp_in_duration > 0  # L620 — MODIFY (comment)
if (_setpoints.DAY || _setpoints.NIGHT || _setpoints.PRE_DAY || _setpoints.PRE_NIGHT) {  # L726 — MODIFY
hasHeating = [_setpoints.PRE_DAY, _setpoints.DAY, ...]  # L728 — MODIFY
hasCooling = [_setpoints.PRE_DAY, _setpoints.DAY, ...]  # L735 — MODIFY
hasVPD = [_setpoints.PRE_DAY, _setpoints.DAY, ...]  # L742 — MODIFY
hasCO2 = [_setpoints.PRE_DAY, _setpoints.DAY, ...]  # L749 — MODIFY
```

---

### 25. `Infrastructure/frontend/src/pages/ZoneConfig.tsx`
**Lines:** 192–195  
**Reason:** Passes mode-based setpoints object (with `ramp_in_duration`) to `SetpointTimeline`. Needs to use `ramp_minutes` from climate periods.

```
DAY: { ..., ramp_in_duration: params.ramp_up_minutes },  # L192 — MODIFY
NIGHT: { ..., ramp_in_duration: params.ramp_down_minutes },  # L193 — MODIFY
PRE_DAY: { ..., ramp_in_duration: params.pre_day_ramp_minutes },  # L194 — MODIFY
PRE_NIGHT: { ..., ramp_in_duration: params.pre_night_ramp_minutes },  # L195 — MODIFY
```

---

### 26. `Infrastructure/frontend/src/components/ZoneCard.tsx`
**Lines:** 3  
**Reason:** Imports `Setpoint` type which includes deprecated fields. Type reference may need updating.

```
import type { Setpoint } from '../types/setpoint'  # L3 — MODIFY (may need to reference climate period types instead)
```

---

### 27. `Infrastructure/frontend/src/components/LightSlider.tsx`
**Lines:** 61  
**Reason:** CSS class name references "setpoint" (`accent-setpoint`). This is purely a styling name (not related to the setpoints table), so it's KEEP. Listed here for completeness.

```
className="...from-accent-setpoint-dim to-accent-setpoint..."  # L61 — KEEP (CSS class name only)
```

---

### 28. `Infrastructure/automation-service/app/routes/schedules/models.py`
**Lines:** 52  
**Reason:** `ClimateScheduleCreate` model has a `mode: str` field with comment mentioning PRE_DAY/PRE_NIGHT.

```
mode: str  # DAY, NIGHT, PRE_DAY, PRE_NIGHT  # L52 — MODIFY (update comment, field may be removed/repurposed)
```

---

## KEEP — Historical / Unrelated / Test Fixtures

### 29. `Infrastructure/automation-service/app/control/AGENTS.md`
**Lines:** 51  
**Reason:** Documentation comment in AGENTS.md file (not Python code). Describes architecture; will be updated as part of broader documentation refresh.

```
Light intensity comes from light (sun/moon) via scheduler; setpoints come from climate (get_climate_mode)...  # L51 — KEEP (doc, will be updated)
```

---

### 30. `Infrastructure/automation-service/app/control/device_processor.py`
**Lines:** 65, 78, 99–100, 113  
**Reason:** `climate_mode_key` and `previous_climate_mode` are internal state tracking keys used by the control loop. These are about the *current mode state*, not about reading from the `setpoints` table. The mode values (PRE_DAY, etc.) come from scheduler output, but the state tracking mechanism itself is KEEP.

```
previous_climate_mode: str | None = None  # L65 — KEEP
previous_climate_mode: Previous climate mode for this location/cluster...  # L78 — KEEP (comment)
"previous_climate_mode": {(location, cluster): previous_climate_mode}  # L99-100 — KEEP
"previous_climate_mode": {},  # L113 — KEEP
```

---

### 31. `Infrastructure/automation-service/app/control/pid_controller_manager.py`
**Lines:** 334–335  
**Reason:** Reads `previous_climate_mode` from context for PID reset logic. State tracking (KEEP), not setpoints table access.

```
climate_mode_key = (location, cluster)
previous_mode = context.get("previous_climate_mode", {}).get(climate_mode_key)  # L335 — KEEP
```

---

### 32. `Infrastructure/automation-service/app/events/__init__.py`
**Lines:** 60  
**Reason:** `config_type` is a generic string field used in event logging. The value `"setpoints"` appears as a configuration category name. This is a string constant used for event classification, not a table reference. KEEP.

```
config_type: Configuration category (e.g., "ramp_times", "setpoints")  # L60 — KEEP (string constant)
```

---

### 33. `Infrastructure/automation-service/alembic/versions/001_baseline.py`
**Lines:** 75–76  
**Reason:** Database migration defines the `setpoints` table schema. This migration is historical (already applied). The table definition itself is part of the schema, but the migration file should not be modified. KEEP as historical record.

```
mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION', 'PRE_DAY', 'PRE_NIGHT')),  # L75 — KEEP (applied migration)
ramp_in_duration INTEGER CHECK (ramp_in_duration IS NULL OR (ramp_in_duration >= 0 AND ramp_in_duration <= 240)),  # L76 — KEEP (applied migration)
```

---

### 34. `Infrastructure/automation-service/REQUIREMENTS.md`
**Lines:** 4, 9, 11, 12, 13, 14, 17  
**Reason:** Requirements document describing the current (deprecated) system behavior. This is documentation that describes what IS, not what SHOULD BE. Will be updated as part of the migration. KEEP as reference.

```
Climate modes supported: DAY, NIGHT, PRE_DAY, PRE_NIGHT. `ramp_in_duration` validated 0–240 minutes...  # L4 — KEEP (doc)
PRE_NIGHT: Climate transition period...  # L9 — KEEP (doc)
PRE_DAY: Climate transition period...  # L11 — KEEP (doc)
Period Priority: PRE_DAY > DAY > PRE_NIGHT > NIGHT  # L12 — KEEP (doc)
Ramp Logic: PRE_NIGHT ramps from DAY setpoints...  # L13 — KEEP (doc)
Light (master): sun and moon. Climate (slave): PRE_DAY...  # L14 — KEEP (doc)
Keep UI/DB schema aligned for setpoints (modes + `ramp_in_duration`)...  # L17 — KEEP (doc)
```

---

### 35. `Infrastructure/frontend/REQUIREMENTS.md`
**Lines:** 7, 10  
**Reason:** Frontend requirements document describing current behavior. KEEP.

```
PRE_DAY and PRE_NIGHT setpoints/ramp-in override DAY during their periods...  # L7 — KEEP (doc)
Keep `setpoints` and schedules in sync with backend schema (modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT; includes `ramp_in_duration`).  # L10 — KEEP (doc)
```

---

### 36. `Infrastructure/frontend/README.md`
**Lines:** 105–107, 122  
**Reason:** README documenting API endpoints. These describe current behavior. KEEP.

```
GET /api/setpoints/{location}/{cluster}?mode={mode}  # L105 — KEEP (current behavior)
POST /api/setpoints/{location}/{cluster}  # L106 — KEEP (current behavior)
GET /api/setpoints/{location}/{cluster}/all-modes  # L107 — KEEP (current behavior)
PRE_DAY and PRE_NIGHT setpoints/ramp-in take precedence...  # L122 — KEEP (doc)
```

---

### 37. `Infrastructure/frontend/AGENTS.md`
**Lines:** 40  
**Reason:** Frontend architecture documentation. KEEP.

```
climate (PRE_DAY, DAY, PRE_NIGHT, NIGHT) drives setpoints...  # L40 — KEEP (doc)
```

---

### 38. `Infrastructure/database/cea_schema.sql`
**Lines:** 178  
**Reason:** SQL comment in schema file. KEEP.

```
-- Replaces fixed PRE_DAY/DAY/PRE_NIGHT/NIGHT with flexible periods  # L178 — KEEP (comment)
```

---

### 39. `Infrastructure/database/REQUIREMENTS.md`
**Lines:** 217  
**Reason:** Database requirements documentation. KEEP.

```
keep `ramp_in_duration` aligned with UI expectations  # L217 — KEEP (doc)
```

---

### 40. `Infrastructure/frontend/grafana/SETPOINTS_IN_GRAFANA.md`
**Lines:** 10, 39  
**Reason:** Grafana documentation describing query patterns. KEEP.

```
Modes supported: `DAY`, `NIGHT`, `PRE_DAY`, `PRE_NIGHT`...  # L10 — KEEP (doc)
`setpoints` (automation-service schema): `location`, `cluster`, `mode` (`DAY`/`NIGHT`/`PRE_DAY`/`PRE_NIGHT`)...  # L39 — KEEP (doc)
```

---

### 41. `Infrastructure/frontend/src/styles/index.css`
**Lines:** 140  
**Reason:** CSS comment naming a period/mode color variable. Pure styling, not related to the setpoints table. KEEP.

```
/* Period/Mode colors (SetpointsTable) */  # L140 — KEEP (CSS class comment)
```

---

### 42. `ARCHITECTURE.md`
**Lines:** 236, 392  
**Reason:** Top-level architecture documentation describing current system. KEEP.

```
Mode Transitions: Smooth transitions between DAY/NIGHT/PRE_DAY/PRE_NIGHT  # L236 — KEEP (doc)
Transition Periods: Optional PRE_DAY/PRE_NIGHT for gradual setpoint changes  # L392 — KEEP (doc)
```

---

### 43. `ARCHITECTURE_SCHEMATIC.md`
**Lines:** 125  
**Reason:** Architecture schematic. KEEP.

```
Scheduler: mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT), effective setpoints (ramp).  # L125 — KEEP (doc)
```

---

### 44. `Infrastructure/automation-service/app/repositories/schedules.py`
**Lines:** 118  
**Reason:** Comment in schedule repository. KEEP.

```
climate mode (PRE_DAY, DAY, PRE_NIGHT, NIGHT) for setpoints.  # L118 — KEEP (comment)
```

---

### 45. `Infrastructure/automation-service/app/repositories/setpoints.py`
**Lines:** 324, 398  
**Reason:** Docstrings in the `effective_setpoints`-related methods. These reference the `mode` parameter values (including PRE_DAY/PRE_NIGHT) which are still valid for the `effective_setpoints` table's `mode` column. KEEP.

```
mode: Mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT/TRANSITION) or None  # L324 — KEEP (docstring for effective_setpoints)
mode: Current mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT) or None  # L398 — KEEP (docstring for effective_setpoints)
```

---

### 46. `Infrastructure/pyright_errors.json` and `Infrastructure/pyright_errors_v2.json`
**Lines:** Multiple  
**Reason:** These are error log files generated by the pyright type checker. They contain file paths referencing `routes/setpoints.py`. These are historical artifacts. KEEP.

```
"file": "...routes/setpoints.py" (multiple entries)  # KEEP (error log files)
```

---

### 47. `Infrastructure/frontend/src/components/ClimateScheduleEditor.tsx` — `apiClient.getAllSetpointsForLocationCluster` call
**Lines:** 218–222  
**Reason:** Makes an API call to `/api/setpoints/.../all-modes` to get current setpoints for display. This call will be replaced by the `/api/climate-periods` equivalent. The code block itself is MODIFY (the API call changes), but the surrounding context is part of ClimateScheduleEditor which is already listed as MODIFY above.

---

## File Count by Category

| File | Category |
|------|----------|
| `app/routes/setpoints.py` | REMOVE |
| `app/repositories/setpoints.py` (methods) | REMOVE (partial) |
| `app/repositories/mode_sync.py` | REMOVE |
| `app/control/scheduler.py` (`get_climate_mode`) | REMOVE (partial) |
| `app/state/__init__.py` (setpoint API) | REMOVE (partial) |
| `config_cli.py` (setpoint commands) | REMOVE (partial) |
| `scripts/validate_loop_performance.py` | REMOVE (partial) |
| `src/components/SetpointEditor.tsx` | REMOVE |
| `src/components/SetpointsTable.tsx` | REMOVE |
| `src/types/setpoint.ts` | REMOVE (consider merge) |
| `AGENTS.md` (automation-service) | REMOVE (partial, doc update) |
| `app/control/control_engine.py` | MODIFY |
| `app/routes/debug.py` | MODIFY |
| `app/routes/routes.py` | MODIFY |
| `app/main.py` | MODIFY |
| `app/services/schedule_state.py` | MODIFY |
| `app/routes/schedules/climate.py` | MODIFY |
| `app/routes/schedules/utils.py` | MODIFY |
| `app/control/setpoint_manager.py` | MODIFY |
| `app/control/leaf_delta.py` | MODIFY |
| `app/services/mode_transition_service.py` | MODIFY |
| `src/services/api.ts` | MODIFY |
| `src/components/ClimateScheduleEditor.tsx` | MODIFY |
| `src/components/SetpointTimeline.tsx` | MODIFY |
| `src/pages/ZoneConfig.tsx` | MODIFY |
| `src/components/ZoneCard.tsx` | MODIFY |
| `app/routes/schedules/models.py` | MODIFY |
| `app/control/AGENTS.md` | MODIFY (doc) |
| `app/control/device_processor.py` | KEEP |
| `app/control/pid_controller_manager.py` | KEEP |
| `app/events/__init__.py` | KEEP |
| `alembic/versions/001_baseline.py` | KEEP |
| `REQUIREMENTS.md` (automation-service) | KEEP (doc) |
| `REQUIREMENTS.md` (frontend) | KEEP (doc) |
| `README.md` (frontend) | KEEP (doc) |
| `AGENTS.md` (frontend) | KEEP (doc) |
| `cea_schema.sql` | KEEP |
| `REQUIREMENTS.md` (database) | KEEP (doc) |
| `grafana/SETPOINTS_IN_GRAFANA.md` | KEEP (doc) |
| `styles/index.css` | KEEP |
| `ARCHITECTURE.md` | KEEP (doc) |
| `ARCHITECTURE_SCHEMATIC.md` | KEEP (doc) |
| `app/repositories/schedules.py` | KEEP (comment) |
| `app/repositories/setpoints.py` | KEEP (effective_setpoints methods + docstrings) |
| `pyright_errors.json` / `pyright_errors_v2.json` | KEEP |
| `src/components/LightSlider.tsx` | KEEP |

---

## Key Migration Mapping

| Deprecated | Replacement |
|-----------|-------------|
| `Scheduler.get_climate_mode()` | `ClimatePeriodRepository.get_active_period()` |
| `SetpointRepository.get_setpoint()` | `ClimatePeriodRepository.get_active_period()` |
| `setpoints` table | `climate_periods` table |
| `ramp_in_duration` field | `ramp_minutes` field in `climate_periods` |
| `/api/setpoints/*` | `/api/climate-periods/*` |
| `SetpointEditor` component | `ClimatePeriodsTable` component |
| `SetpointsTable` component | N/A (removed) |
| `StateManager.get_setpoint()` | Direct `climate_periods` reads |
| `SETPOINT_MODES = ("DAY", "NIGHT", "PRE_DAY", "PRE_NIGHT")` | `ClimatePeriod` enum |
| `ClimateMode.PRE_DAY/PRE_NIGHT` | Period names from `climate_periods` |
| `mode_fallbacks` dict in setpoint_manager | Managed by `ClimatePeriodRepository` |
| `sync_climate_setpoints_from_mode_parameters()` | N/A (removed) |
| `config_cli.py setpoint` commands | Replaced by `climate-period` CLI commands |

---

*End of manifest. Next step: Task 5 — implement the migration.*
