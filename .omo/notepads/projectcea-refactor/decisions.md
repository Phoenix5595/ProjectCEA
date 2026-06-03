# Decisions Log

## 2026-03-24 - Wave 1 Decisions (COMPLETE)

### Task 1: DFR0971 Simplification

### Task 1: DFR0971 Simplification
- Decision: Remove all board 0x59 special cases
- Reason: Boards should work generically, not with hacks
- Approach: Single retry (was 3), remove `force_reinitialize()` special case

### Task 2: Hardware Batch Sequential
- Decision: Remove PARALLEL_I2C flag entirely, keep only sequential path
- Reason: Parallel I2C on single bus provides no real benefit
- Implementation: Remove `asyncio.gather()` path, remove `get_flag("PARALLEL_I2C")` calls

### Task 3: Heating Safety Integration
- Decision: Integrate HeatingFailureSafety into device_processor
- Approach: One instance per room/zone, call update() in control loop
- Alert callback: Use existing alarm system

### Task 4: Redis Key Migration
- Decision: Add backward-compat shim, migrate to cea:* prefix
- Approach: Read from old keys if present, write to both
- Schema defined in app/redis/schema.py
- Updated mixins: modes, sensors, alarms, ramps, schedules, heartbeat, pid
- Helper functions added: get_with_backward_compat, set_with_backward_compat

## 2026-03-24 - Wave 2 Decisions (COMPLETE)

### Task 5: LightRampCalculator Extraction ✅
- Decision: Extract from scheduler.py into separate class
- Target: ~300 lines extracted, scheduler reduced by ~290 lines
- Implementation: LightRampCalculator created with get_schedule_intensity and get_light_intensity_details

### Task 6: VPD Cascade Integration ✅
- Decision: Wire VPDCascadeController into device_processor
- Already existed, just needed wiring
- Cascade priority: passive ventilation → dehumidification → thermal

### Task 7: Control Engine Split ✅
- Decision: Extract SensorReader, ClimatePeriodResolver, SetpointCalculator
- Implementation: 3 new classes created (~300 lines total)
- control_engine.py reduced from 828 to 811 lines (still references components)

## 2026-03-24 - Wave 3 Decisions

### Task 8: Light Component Consolidation
- Decision: Create useLightControl hook, rename VerticalLightsBlock to LightManager
- Dead code: LightManager.tsx (old, 478 lines), LightSlidersPanel.tsx (264 lines)
- Target: Single shared hook + renamed component

### Task 9: Dashboard Split
- Decision: Extract useWebSocket, useSensorPolling, useSystemStatus hooks
- DashboardRoomCard and SystemStatusPanel components
- Target: Dashboard.tsx from 825 to ~200 lines

### Task 10: CircularTimePicker Split
- Decision: Extract timeMath.ts, CircularClockFace.tsx, useClockInteraction.ts
- Consolidate duplicate timeToMinutes functions
- Target: CircularTimePicker from 632 to ~200 lines
- Decision: Extract from scheduler.py into separate class
- Target: ~300 lines extracted, scheduler reduced by ~200 lines

### Task 6: VPD Cascade Integration
- Decision: Wire VPDCascadeController into device_processor
- Already exists, just needs wiring

### Task 7: Control Engine Split
- Decision: Extract SensorReader, ClimatePeriodResolver, SetpointCalculator
- Target: control_engine.py 828 → 400 lines + 3 new classes
