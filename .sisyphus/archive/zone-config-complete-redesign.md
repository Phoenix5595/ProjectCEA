# Zone Configuration - Complete Redesign v4

**Created**: 2026-01-16  
**Updated**: 2026-01-17 (Post-Exploration)  
**Status**: READY FOR IMPLEMENTATION  
**Priority**: HIGH  

---

## Overview

Complete redesign of the Zone Configuration page with:
1. **Corrected UI Layout** - CircularTimePicker left, Timeline+Setpoints right
2. **Room Modes System** - Already implemented in backend, frontend integration only

---

## Exploration Findings (2026-01-17)

### What Already Exists ✅

| Component/Feature | Status | Notes |
|-------------------|--------|-------|
| **Backend Mode Infrastructure** | ✅ Complete | `room_modes`, `flower_submodes`, `active_room_modes`, `mode_parameters` tables |
| **Backend Mode Endpoints** | ✅ Complete | `/api/room-modes/`, `/api/lights/`, `/api/setpoints/` |
| **API Client Methods** | ✅ Complete | `getLightsForZone()`, `getRoomModeWithParams()`, `setRoomMode()` |
| **CircularTimePicker.tsx** | ✅ Exists | 676 lines, has `showPresetButtons` prop |
| **SetpointTimeline.tsx** | ✅ Exists | 826 lines, fully functional |
| **SetpointsTable.tsx** | ✅ Exists | 74 lines, compact table |
| **LightSlider.tsx** | ✅ Exists | 88 lines, has current value marker |
| **RoomModeSelector.tsx** | ✅ Exists | 114 lines, dropdown with submodes |
| **PIDEditor.tsx** | ✅ Exists | 323 lines, multi-row (needs compact version) |
| **Types** | ✅ Complete | `ModeParameters`, `RoomModeWithParams`, `LightDevice` |

### What Needs Work ⚠️

| Task | Action | Notes |
|------|--------|-------|
| **ZoneConfig.tsx** | MODIFY | Major layout restructure |
| **LightSlidersPanel.tsx** | CREATE | Wrapper that fetches real device names |
| **PIDCompactRow.tsx** | CREATE | Single-line compact PID |
| **RoomModeSelector.tsx** | MODIFY | Add separate submode buttons |

---

## Final Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🌱 Flower Room          [Flower ▼] [STR][BLK][RPN]        [SAVE] [←]        │
├─────────────────────────────┬───────────────────────────────────────────────┤
│   CIRCULAR PHOTOPERIOD      │           TIMELINE (2/3)                      │
│   (1/3)                     │                                               │
│                             │   00  03  06  09  12  15  18  21  24          │
│      ┌───────────┐          │   ░░░░░░░░████████████████████░░░░░░░░░        │
│     /   12 noon   \         │   PRE    ▲ DAY ▲    PRE    NIGHT              │
│    │      ●        │        │                                               │
│   9│    /   \      │3       ├───────────────────────────────────────────────┤
│    │   ●─────●     │        │   SETPOINTS (below timeline)                  │
│     \  6 midnight /         │   ┌───────┬───────┬───────┬───────┬───────┐   │
│      └───────────┘          │   │       │🌡Heat │❄Cool │💧VPD │🌬CO2  │   │
│                             │   │ ☀️DAY │ 24°  │ 28°  │ 1.0  │ 800   │   │
│ Start:[17:00] End:[05:00]   │   │ 🌙NIGHT│ 20° │ 24°  │ 0.8  │ 600   │   │
│ Ramp ⏫[30] ⏬[30] 🔒[12h]   │   └───────┴───────┴───────┴───────┴───────┘   │
│ ─────────────────────────── │                                               │
│ LIGHTS                      │                                               │
│ HLG 600      [████░░] 85%   │                                               │
│ Far Red      [██░░░░] 40%   │                                               │
├─────────────────────────────┴───────────────────────────────────────────────┤
│ PID [Heater▼] (●Auto ○PID ○ON/OFF)  Kp[22.3] Ki[0.02] Kd[0.5] [Reset][Hist]│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Layout Specifications

| Section | Position | Width | Content |
|---------|----------|-------|---------|
| **Header** | Top | 100% | Room name, Mode dropdown, Submode buttons, Save, Back |
| **Left Column** | Left | 1/3 | CircularTimePicker (no presets) + Light sliders |
| **Right Column Top** | Right top | 2/3 | SetpointTimeline (~140px height) |
| **Right Column Bottom** | Right bottom | 2/3 | SetpointsTable (Day/Night rows) |
| **Footer** | Bottom | 100% | PID compact single row |

---

## Current vs Target Layout

### Current ZoneConfig.tsx Layout
```
┌─────────────────────────────────────────────────────────────────┐
│ Header: Title + Mode Selector + Save + Back                     │
├───────────────────────────────────┬─────────────────────────────┤
│ SetpointTimeline (flex-[2])       │ ScheduleLightsPanel (flex-1)│
│ LEFT - 2/3 width                  │ RIGHT - 1/3 width           │
├───────────────────────────────────┼─────────────────────────────┤
│ SetpointsTable (flex-[2])         │ PIDEditor (flex-1)          │
│ LEFT - 2/3 width                  │ RIGHT - 1/3 width           │
└───────────────────────────────────┴─────────────────────────────┘
```

### Target ZoneConfig.tsx Layout
```
┌─────────────────────────────────────────────────────────────────┐
│ Header: Title + [Mode ▼] [STR][BLK][RPN] + Save + Back          │
├─────────────────────────┬───────────────────────────────────────┤
│ CircularTimePicker      │ SetpointTimeline                      │
│ (1/3)                   │ (2/3 - top)                           │
│                         ├───────────────────────────────────────┤
│ LightSlidersPanel       │ SetpointsTable                        │
│ (1/3)                   │ (2/3 - bottom)                        │
├─────────────────────────┴───────────────────────────────────────┤
│ PIDCompactRow (full width)                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Create LightSlidersPanel.tsx (20 min)

New component that fetches real device names and uses existing LightSlider:

```tsx
// components/LightSlidersPanel.tsx
import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import LightSlider from './LightSlider'

interface LightSlidersPanelProps {
  location: string
  cluster: string
  mainIntensity: number
  supplementalIntensity: number
  currentMainIntensity?: number
  currentSupplementalIntensity?: number
  onChange: (updates: { main_light_intensity?: number; supplemental_light_intensity?: number }) => void
}

export default function LightSlidersPanel({
  location,
  cluster,
  mainIntensity,
  supplementalIntensity,
  currentMainIntensity,
  currentSupplementalIntensity,
  onChange
}: LightSlidersPanelProps) {
  const [lights, setLights] = useState<Array<{ device_name: string; display_name: string }>>([])

  useEffect(() => {
    apiClient.getLightsForZone(location, cluster).then(setLights).catch(console.error)
  }, [location, cluster])

  // Map device names to display names, fallback to generic labels
  const mainLabel = lights.find(l => l.device_name.toLowerCase().includes('main'))?.display_name || 'Main'
  const suppLabel = lights.find(l => l.device_name.toLowerCase().includes('supp'))?.display_name || 'Supplemental'

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
      <div className="text-xs text-gray-400 uppercase font-bold tracking-wider mb-2">Lights</div>
      <div className="space-y-2">
        <LightSlider
          label={mainLabel}
          value={mainIntensity}
          currentValue={currentMainIntensity}
          onChange={(v) => onChange({ main_light_intensity: v })}
        />
        <LightSlider
          label={suppLabel}
          value={supplementalIntensity}
          currentValue={currentSupplementalIntensity}
          onChange={(v) => onChange({ supplemental_light_intensity: v })}
        />
      </div>
    </div>
  )
}
```

### Phase 2: Create PIDCompactRow.tsx (25 min)

Compact single-line PID controls, reusing logic from PIDEditor:

```tsx
// components/PIDCompactRow.tsx
import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import type { PIDControlMode } from '../types/pid'

const DEVICE_TYPES = ['heater', 'fan', 'co2']

export default function PIDCompactRow() {
  const [device, setDevice] = useState('heater')
  const [mode, setMode] = useState<PIDControlMode>('pid')
  const [kp, setKp] = useState(0)
  const [ki, setKi] = useState(0)
  const [kd, setKd] = useState(0)
  const [loading, setLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  useEffect(() => {
    loadData()
  }, [device])

  async function loadData() {
    try {
      const [params, modeInfo] = await Promise.all([
        apiClient.getPIDParameters(device),
        apiClient.getPIDMode(device)
      ])
      setKp(params.kp)
      setKi(params.ki)
      setKd(params.kd)
      setMode(modeInfo.mode)
    } catch (err) {
      console.error('Failed to load PID data:', err)
    }
  }

  async function handleModeChange(newMode: PIDControlMode) {
    try {
      await apiClient.setPIDMode(device, { mode: newMode })
      setMode(newMode)
    } catch (err) {
      console.error('Failed to change mode:', err)
    }
  }

  async function handleSave() {
    setLoading(true)
    try {
      await apiClient.updatePIDParameters(device, { kp, ki, kd })
    } catch (err) {
      console.error('Failed to save:', err)
    }
    setLoading(false)
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
      <div className="flex items-center gap-3 text-xs">
        {/* Device selector */}
        <select
          value={device}
          onChange={(e) => setDevice(e.target.value)}
          className="bg-gray-800 border border-gray-700 px-2 py-1 rounded text-gray-200"
        >
          {DEVICE_TYPES.map(t => (
            <option key={t} value={t}>{t.toUpperCase()}</option>
          ))}
        </select>

        {/* Mode buttons */}
        <div className="flex gap-1">
          {(['auto_pid', 'pid', 'on_off'] as PIDControlMode[]).map(m => (
            <button
              key={m}
              onClick={() => handleModeChange(m)}
              className={`px-2 py-1 rounded ${mode === m ? 'bg-cyan-700 text-white' : 'bg-gray-800 text-gray-400'}`}
            >
              {m === 'auto_pid' ? 'Auto' : m === 'pid' ? 'PID' : 'ON/OFF'}
            </button>
          ))}
        </div>

        {/* K-values (only show for PID modes) */}
        {mode !== 'on_off' && (
          <div className="flex gap-2">
            <label className="flex items-center gap-1">
              Kp:<input
                type="number"
                step="0.1"
                value={kp}
                onChange={(e) => setKp(parseFloat(e.target.value))}
                disabled={mode === 'auto_pid'}
                className="w-14 bg-gray-800 border border-gray-700 px-1 rounded text-center text-gray-200 disabled:opacity-50"
              />
            </label>
            <label className="flex items-center gap-1">
              Ki:<input
                type="number"
                step="0.001"
                value={ki}
                onChange={(e) => setKi(parseFloat(e.target.value))}
                disabled={mode === 'auto_pid'}
                className="w-14 bg-gray-800 border border-gray-700 px-1 rounded text-center text-gray-200 disabled:opacity-50"
              />
            </label>
            <label className="flex items-center gap-1">
              Kd:<input
                type="number"
                step="0.01"
                value={kd}
                onChange={(e) => setKd(parseFloat(e.target.value))}
                disabled={mode === 'auto_pid'}
                className="w-14 bg-gray-800 border border-gray-700 px-1 rounded text-center text-gray-200 disabled:opacity-50"
              />
            </label>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2 ml-auto">
          {mode === 'pid' && (
            <button
              onClick={handleSave}
              disabled={loading}
              className="px-2 py-1 bg-cyan-700 hover:bg-cyan-600 rounded text-white disabled:opacity-50"
            >
              {loading ? '...' : 'Save'}
            </button>
          )}
          <button
            onClick={() => setHistoryOpen(!historyOpen)}
            className="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-gray-400"
          >
            History {historyOpen ? '▲' : '▼'}
          </button>
        </div>
      </div>
    </div>
  )
}
```

### Phase 3: Modify RoomModeSelector.tsx (15 min)

Add separate submode buttons that appear when Flower mode is selected:

**Changes needed:**
- Extract submode selection from dropdown
- Add toggle button group [STR][BLK][RPN] visible when mode === 'flower'
- Keep existing mode dropdown functionality

### Phase 4: Restructure ZoneConfig.tsx (40 min)

Major layout changes:
1. Import CircularTimePicker (currently not imported)
2. Replace ScheduleLightsPanel position with CircularTimePicker
3. Add LightSlidersPanel below CircularTimePicker
4. Move SetpointsTable to right column below timeline
5. Replace PIDEditor with PIDCompactRow (full width)

**New structure:**
```tsx
<div className="min-h-screen bg-gray-950 p-2">
  <div className="max-w-[1920px] mx-auto h-[calc(100vh-1rem)] flex flex-col">
    
    {/* Header */}
    <div className="flex items-center justify-between mb-2 px-1">
      <h1>...</h1>
      <div className="flex items-center gap-3">
        <RoomModeSelector ... />  {/* Now with submode buttons */}
        <button>SAVE</button>
        <Link>← Back</Link>
      </div>
    </div>

    {/* Main content */}
    <div className="flex-1 flex gap-2 min-h-0">
      
      {/* LEFT 1/3: CircularTimePicker + Lights */}
      <div className="w-1/3 flex flex-col gap-2">
        <CircularTimePicker
          showPresetButtons={false}
          dayStartTime={params.day_start_time}
          dayEndTime={params.night_start_time}
          onDayStartChange={(t) => handleParamChange({ day_start_time: t })}
          onDayEndChange={(t) => handleParamChange({ night_start_time: t })}
          rampUpDuration={params.ramp_up_minutes}
          rampDownDuration={params.ramp_down_minutes}
          onRampUpDurationChange={(d) => handleParamChange({ ramp_up_minutes: d })}
          onRampDownDurationChange={(d) => handleParamChange({ ramp_down_minutes: d })}
          lockedPhotoperiodHours={roomMode?.mode_name === 'flower' ? 12 : roomMode?.mode_name === 'veg' ? 18 : undefined}
        />
        <LightSlidersPanel
          location={location}
          cluster={cluster}
          mainIntensity={params.main_light_intensity}
          supplementalIntensity={params.supplemental_light_intensity}
          currentMainIntensity={savedParams?.main_light_intensity}
          currentSupplementalIntensity={savedParams?.supplemental_light_intensity}
          onChange={handleParamChange}
        />
      </div>

      {/* RIGHT 2/3: Timeline + Setpoints */}
      <div className="w-2/3 flex flex-col gap-2">
        <SetpointTimeline ... className="h-[200px]" />
        <SetpointsTable ... className="flex-1" />
      </div>

    </div>

    {/* PID Compact Row (full width) */}
    <PIDCompactRow />

  </div>
</div>
```

### Phase 5: Testing + Verification (20 min)

- [ ] Run `npm run build` - no errors
- [ ] Check layout on 1080p viewport - no scrolling
- [ ] Verify CircularTimePicker shows on left
- [ ] Verify Timeline shows on right top
- [ ] Verify Setpoints table shows below timeline
- [ ] Verify light sliders fetch real device names
- [ ] Verify PID row is compact and full width
- [ ] Verify mode switching works
- [ ] Verify submode buttons appear for Flower mode
- [ ] Run `lsp_diagnostics` on modified files

---

## Files to Modify/Create

### Frontend Only (No Backend Changes Needed!)

| File | Action | Lines | Description |
|------|--------|-------|-------------|
| `components/LightSlidersPanel.tsx` | **CREATE** | ~50 | Fetches device names, wraps LightSlider |
| `components/PIDCompactRow.tsx` | **CREATE** | ~100 | Single-line compact PID |
| `components/RoomModeSelector.tsx` | MODIFY | +30 | Add submode toggle buttons |
| `pages/ZoneConfig.tsx` | MODIFY | ~50 | Restructure layout |

**Total new code: ~230 lines**

---

## Implementation Timeline

| Phase | Task | Est. Time |
|-------|------|-----------|
| 1 | Create `LightSlidersPanel.tsx` | 20 min |
| 2 | Create `PIDCompactRow.tsx` | 25 min |
| 3 | Modify `RoomModeSelector.tsx` | 15 min |
| 4 | Restructure `ZoneConfig.tsx` | 40 min |
| 5 | Testing + Verification | 20 min |
| **Total** | | **~2 hours** |

---

## Success Criteria

- [ ] CircularTimePicker on LEFT (1/3), no preset buttons
- [ ] Timeline on RIGHT TOP (2/3)
- [ ] Setpoints table on RIGHT BOTTOM (below timeline)
- [ ] Light sliders show real device names (e.g., "HLG 600")
- [ ] Light sliders have white current value marker
- [ ] Mode dropdown + separate submode buttons [STR][BLK][RPN]
- [ ] PID in single compact row (full width)
- [ ] No scrolling on 1080p viewport
- [ ] Build passes with no errors (`npm run build`)
- [ ] No TypeScript errors (`lsp_diagnostics`)

---

## Key Decisions (Confirmed by User)

| Decision | Choice |
|----------|--------|
| Layout order | CircularTimePicker LEFT (1/3), Timeline+Setpoints RIGHT (2/3) |
| Setpoints position | Below timeline in right column |
| Preset buttons | REMOVE from CircularTimePicker (modes handle this) |
| Light labels | Use real device names from API |
| Header banner | Keep mode selector + save + back |
| Submode buttons | Visible only when Flower mode selected |
| Backend work | NOT NEEDED - all infrastructure exists |

---

## Existing Components Reference

### CircularTimePicker.tsx (676 lines)
- Location: `components/CircularTimePicker.tsx`
- Key props: `showPresetButtons`, `dayStartTime`, `dayEndTime`, `rampUpDuration`, `rampDownDuration`, `lockedPhotoperiodHours`
- Already supports hiding preset buttons

### LightSlider.tsx (88 lines)
- Location: `components/LightSlider.tsx`
- Props: `label`, `value`, `currentValue`, `onChange`, `min`, `max`, `disabled`
- Already has white current value marker

### SetpointsTable.tsx (74 lines)
- Location: `components/SetpointsTable.tsx`
- Props: `params`, `currentParams`, `isConstant`, `onChange`
- Compact Day/Night table already implemented

### SetpointTimeline.tsx (826 lines)
- Location: `components/SetpointTimeline.tsx`
- Full 24-hour visualization with setpoint lines
- Already fully functional

### PIDEditor.tsx (323 lines)
- Location: `components/PIDEditor.tsx`
- Full multi-row PID editor
- Will create compact version, not modify this

### RoomModeSelector.tsx (114 lines)
- Location: `components/RoomModeSelector.tsx`
- Dropdown with submodes inside
- Will add separate submode buttons

---

## API Methods Available (from api.ts)

```typescript
// Lights
apiClient.getLightsForZone(location, cluster)  // Returns light devices with names
apiClient.setLightIntensity(location, cluster, deviceName, intensity)

// Room Modes
apiClient.getRoomModes()                        // List all modes
apiClient.getFlowerSubmodes()                   // List flower submodes
apiClient.getRoomModeWithParams(location, cluster)  // Get current mode + params
apiClient.setRoomMode(location, cluster, request)   // Change mode
apiClient.updateRoomParameters(location, cluster, params)  // Save params

// PID
apiClient.getPIDParameters(deviceType)
apiClient.getPIDMode(deviceType)
apiClient.setPIDMode(deviceType, update)
apiClient.updatePIDParameters(deviceType, params)
```

All methods already exist - no API changes needed!
