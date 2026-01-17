# Zone Configuration - Complete Redesign v3

**Created**: 2026-01-16  
**Updated**: 2026-01-17  
**Status**: PLANNED  
**Priority**: HIGH  

---

## Overview

Complete redesign of the Zone Configuration page with:
1. **Corrected UI Layout** - CircularTimePicker left, Timeline+Setpoints right
2. **Room Modes System** - Veg, Flower (with submodes), Drying, Sleep

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

## Component Structure

### ZoneConfig.tsx - New Layout

```tsx
<div className="flex flex-col h-full gap-2 p-3 bg-gray-950 min-h-screen">
  {/* Header - Mode selector + Save */}
  <div className="flex items-center justify-between">
    <div className="flex items-center gap-3">
      <h1 className="text-xl font-bold">{roomName}</h1>
      <RoomModeSelector location={location} cluster={cluster} />
    </div>
    <div className="flex items-center gap-2">
      <button className="px-4 py-2 bg-cyan-600 rounded">SAVE</button>
      <button onClick={() => navigate(-1)}>←</button>
    </div>
  </div>

  {/* Main content: LEFT (1/3) + RIGHT (2/3) */}
  <div className="flex gap-3 flex-1">
    
    {/* LEFT 1/3: Circular Photoperiod + Lights */}
    <div className="w-1/3 flex flex-col gap-3">
      <CircularTimePicker 
        showPresetButtons={false}  /* Remove Veg/Flower buttons */
        dayStartTime={dayStart}
        dayEndTime={dayEnd}
        rampUpDuration={rampUp}
        rampDownDuration={rampDown}
        lockedPhotoperiodHours={isFlowerMode ? 12 : isVegMode ? 18 : null}
      />
      <LightSlidersPanel location={location} cluster={cluster} />
    </div>
    
    {/* RIGHT 2/3: Timeline (top) + Setpoints (bottom) */}
    <div className="w-2/3 flex flex-col gap-3">
      <SetpointTimeline 
        className="h-[140px]" 
        location={location} 
        cluster={cluster} 
      />
      <SetpointsTable 
        location={location} 
        cluster={cluster}
        className="flex-1"
      />
    </div>
    
  </div>

  {/* Full width: PID compact row */}
  <PIDCompactRow location={location} cluster={cluster} />
</div>
```

---

## Component Details

### 1. Header with RoomModeSelector

Mode dropdown + separate submode toggle buttons:

```tsx
<div className="flex items-center gap-2">
  {/* Mode Dropdown */}
  <select className="bg-gray-800 px-3 py-1 rounded">
    <option>Veg</option>
    <option>Flower</option>
    <option>Drying</option>
    <option>Sleep</option>
  </select>
  
  {/* Submode Buttons - only visible when Flower mode */}
  {mode === 'FLOWER' && (
    <div className="flex gap-1">
      <button className={submode === 'STR' ? 'bg-amber-600' : 'bg-gray-700'}>STR</button>
      <button className={submode === 'BLK' ? 'bg-amber-600' : 'bg-gray-700'}>BLK</button>
      <button className={submode === 'RPN' ? 'bg-amber-600' : 'bg-gray-700'}>RPN</button>
    </div>
  )}
</div>
```

### 2. CircularTimePicker Modifications

- **Remove**: Veg/Flower preset buttons (`showPresetButtons={false}`)
- **Keep**: Start/End time inputs, Ramp Up/Down inputs, Lock toggle
- **Size**: Consider reducing from 300px to 250px for better fit in 1/3 width

The existing `CircularTimePicker.tsx` already has a `showPresetButtons` prop - just pass `false`.

### 3. LightSlidersPanel (NEW)

Fetches actual light device names from API:

```tsx
interface Light {
  device_id: string
  device_name: string      // "HLG_600_Main"
  display_name: string     // "HLG 600"
  current_intensity: number
  target_intensity: number
}

function LightSlidersPanel({ location, cluster }: Props) {
  const [lights, setLights] = useState<Light[]>([])
  
  useEffect(() => {
    // Fetch actual light devices for this zone
    apiClient.getLightsForZone(location, cluster).then(setLights)
  }, [location, cluster])
  
  return (
    <div className="bg-gray-900 rounded p-2 space-y-2">
      <h3 className="text-xs text-gray-400 uppercase">Lights</h3>
      {lights.map(light => (
        <LightSlider
          key={light.device_id}
          name={light.display_name}  /* "HLG 600", "Far Red", etc. */
          currentValue={light.current_intensity}
          targetValue={light.target_intensity}
          onChange={(val) => updateLightIntensity(light.device_id, val)}
        />
      ))}
    </div>
  )
}
```

### 4. LightSlider Component

Shows device name, slider with current value marker, and editable target:

```tsx
interface LightSliderProps {
  name: string           // Display name like "HLG 600"
  currentValue: number   // Actual current intensity (0-100)
  targetValue: number    // Target intensity (0-100)
  onChange: (value: number) => void
}

function LightSlider({ name, currentValue, targetValue, onChange }: LightSliderProps) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-sm text-gray-300">{name}</span>
        <div className="flex items-center gap-1">
          <input 
            type="number" 
            min={0}
            max={100}
            value={targetValue}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-12 bg-gray-800 text-right text-sm rounded px-1"
          />
          <span className="text-xs text-gray-500">%</span>
        </div>
      </div>
      <div className="relative h-4 bg-gray-800 rounded overflow-hidden">
        {/* Filled portion (target) */}
        <div 
          className="absolute left-0 top-0 h-full bg-amber-500/60"
          style={{ width: `${targetValue}%` }}
        />
        {/* Current value marker (white line) */}
        <div 
          className="absolute top-0 h-full w-0.5 bg-white"
          style={{ left: `${currentValue}%` }}
        />
      </div>
    </div>
  )
}
```

### 5. SetpointsTable (Compact)

Inline editable grid with Day/Night rows:

```tsx
interface SetpointsTableProps {
  location: string
  cluster: string
  className?: string
}

function SetpointsTable({ location, cluster, className }: SetpointsTableProps) {
  const [daySetpoints, setDaySetpoints] = useState({ heat: 24, cool: 28, vpd: 1.0, co2: 800, leaf: -2 })
  const [nightSetpoints, setNightSetpoints] = useState({ heat: 20, cool: 24, vpd: 0.8, co2: 600, leaf: -1 })
  
  return (
    <div className={`bg-gray-900 rounded p-2 ${className}`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-gray-400">
            <th className="w-20 text-left"></th>
            <th className="text-center">🌡️ Heat</th>
            <th className="text-center">❄️ Cool</th>
            <th className="text-center">💧 VPD</th>
            <th className="text-center">🌬️ CO2</th>
            <th className="text-center">🍃 Leaf Δ</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="text-amber-400 font-medium">☀️ DAY</td>
            <td className="text-center"><input value={daySetpoints.heat} className="w-12 bg-gray-800 text-center rounded" />°</td>
            <td className="text-center"><input value={daySetpoints.cool} className="w-12 bg-gray-800 text-center rounded" />°</td>
            <td className="text-center"><input value={daySetpoints.vpd} className="w-12 bg-gray-800 text-center rounded" /></td>
            <td className="text-center"><input value={daySetpoints.co2} className="w-14 bg-gray-800 text-center rounded" /></td>
            <td className="text-center"><input value={daySetpoints.leaf} className="w-12 bg-gray-800 text-center rounded" />°</td>
          </tr>
          <tr>
            <td className="text-blue-400 font-medium">🌙 NIGHT</td>
            <td className="text-center"><input value={nightSetpoints.heat} className="w-12 bg-gray-800 text-center rounded" />°</td>
            <td className="text-center"><input value={nightSetpoints.cool} className="w-12 bg-gray-800 text-center rounded" />°</td>
            <td className="text-center"><input value={nightSetpoints.vpd} className="w-12 bg-gray-800 text-center rounded" /></td>
            <td className="text-center"><input value={nightSetpoints.co2} className="w-14 bg-gray-800 text-center rounded" /></td>
            <td className="text-center"><input value={nightSetpoints.leaf} className="w-12 bg-gray-800 text-center rounded" />°</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}
```

### 6. PIDCompactRow (NEW)

Single-line compact PID controls:

```tsx
function PIDCompactRow({ location, cluster }: Props) {
  const [device, setDevice] = useState('heater')
  const [mode, setMode] = useState<'auto' | 'pid' | 'onoff'>('auto')
  const [kp, setKp] = useState(22.3)
  const [ki, setKi] = useState(0.02)
  const [kd, setKd] = useState(0.5)
  
  return (
    <div className="bg-gray-900 rounded p-2 flex items-center gap-4 text-sm">
      {/* Device selector */}
      <select 
        value={device}
        onChange={(e) => setDevice(e.target.value)}
        className="bg-gray-800 px-2 py-1 rounded"
      >
        <option value="heater">Heater</option>
        <option value="ac">AC</option>
        <option value="dehumidifier">Dehumidifier</option>
      </select>
      
      {/* Mode radio buttons */}
      <div className="flex gap-3">
        <label className="flex items-center gap-1 cursor-pointer">
          <input 
            type="radio" 
            name="pidMode" 
            checked={mode === 'auto'}
            onChange={() => setMode('auto')}
          /> Auto
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input 
            type="radio" 
            name="pidMode"
            checked={mode === 'pid'}
            onChange={() => setMode('pid')}
          /> PID
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input 
            type="radio" 
            name="pidMode"
            checked={mode === 'onoff'}
            onChange={() => setMode('onoff')}
          /> ON/OFF
        </label>
      </div>
      
      {/* K-values */}
      <div className="flex gap-3">
        <span className="flex items-center">
          Kp:<input value={kp} onChange={(e) => setKp(Number(e.target.value))} className="w-14 bg-gray-800 ml-1 px-1 rounded text-center" />
        </span>
        <span className="flex items-center">
          Ki:<input value={ki} onChange={(e) => setKi(Number(e.target.value))} className="w-14 bg-gray-800 ml-1 px-1 rounded text-center" />
        </span>
        <span className="flex items-center">
          Kd:<input value={kd} onChange={(e) => setKd(Number(e.target.value))} className="w-14 bg-gray-800 ml-1 px-1 rounded text-center" />
        </span>
      </div>
      
      {/* Actions */}
      <div className="flex gap-2 ml-auto">
        <button className="px-2 py-1 bg-gray-700 rounded hover:bg-gray-600">Reset</button>
        <button className="px-2 py-1 bg-gray-700 rounded hover:bg-gray-600">History ▼</button>
      </div>
    </div>
  )
}
```

---

## Files to Modify/Create

### Frontend

| File | Action | Description |
|------|--------|-------------|
| `pages/ZoneConfig.tsx` | MODIFY | Major restructure - new layout |
| `components/RoomModeSelector.tsx` | MODIFY | Add separate submode buttons |
| `components/CircularTimePicker.tsx` | NONE | Already has `showPresetButtons` prop |
| `components/LightSlidersPanel.tsx` | **CREATE** | Fetches real device names |
| `components/LightSlider.tsx` | **CREATE** | Slider with current marker |
| `components/SetpointsTable.tsx` | **CREATE** | Compact Day/Night table |
| `components/SetpointTimeline.tsx` | MODIFY | Adjust height to ~140px |
| `components/PIDCompactRow.tsx` | **CREATE** | Single-line PID |
| `services/api.ts` | MODIFY | Add `getLightsForZone()` method |

### Backend

| File | Action | Description |
|------|--------|-------------|
| `database.py` | MODIFY | Add mode tables + methods |
| `routes/modes.py` | **CREATE** | Mode endpoints |
| `routes/lights.py` | MODIFY | Add endpoint for lights by zone |
| `main.py` | MODIFY | Register modes router |

---

## Room Modes System

### Mode Definitions

| Mode | Submodes | Photoperiod | Description |
|------|----------|-------------|-------------|
| **Veg** | None | 18h on / 6h off | Vegetative growth (Veg Room only) |
| **Flower** | STR, BLK, RPN | 12h on / 12h off | Flowering phase |
| **Drying** | None | 0h on / 24h off | Post-harvest drying |
| **Sleep** | None | 0h on / 24h off | Room inactive |

### Flower Submodes

| Submode | Name | Description |
|---------|------|-------------|
| **STR** | Stretch | First 2-3 weeks of flower, plants stretch |
| **BLK** | Bulk | Main flowering, bud development |
| **RPN** | Ripen | Final 1-2 weeks, finishing |

### Mode Persistence

Each mode/submode combination saves its own parameters:
- Setpoints (Heat, Cool, VPD, CO2, Leaf Δ)
- Light schedule (start, end, ramps)
- Light intensities

When switching modes:
1. Auto-save current mode's parameters
2. Load saved parameters for new mode
3. If no saved params, use mode defaults

---

## Database Schema

```sql
-- Room modes table
CREATE TABLE room_mode (
    id SERIAL PRIMARY KEY,
    location VARCHAR(50) NOT NULL,
    cluster VARCHAR(50) NOT NULL,
    mode VARCHAR(20) NOT NULL,  -- VEG, FLOWER, DRYING, SLEEP
    submode VARCHAR(20),         -- STR, BLK, RPN (nullable)
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(location, cluster)
);

-- Mode-specific setpoints
CREATE TABLE mode_setpoints (
    id SERIAL PRIMARY KEY,
    location VARCHAR(50) NOT NULL,
    cluster VARCHAR(50) NOT NULL,
    mode VARCHAR(20) NOT NULL,
    submode VARCHAR(20),
    period VARCHAR(10) NOT NULL,  -- DAY, NIGHT
    heat_setpoint FLOAT,
    cool_setpoint FLOAT,
    vpd_setpoint FLOAT,
    co2_setpoint FLOAT,
    leaf_delta FLOAT,
    UNIQUE(location, cluster, mode, submode, period)
);

-- Mode-specific light settings
CREATE TABLE mode_lights (
    id SERIAL PRIMARY KEY,
    location VARCHAR(50) NOT NULL,
    cluster VARCHAR(50) NOT NULL,
    mode VARCHAR(20) NOT NULL,
    submode VARCHAR(20),
    day_start_time TIME,
    day_end_time TIME,
    ramp_up_duration INTEGER,
    ramp_down_duration INTEGER,
    UNIQUE(location, cluster, mode, submode)
);
```

---

## API Endpoints

### Mode Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/modes/{location}/{cluster}` | Get current mode |
| PUT | `/api/modes/{location}/{cluster}` | Set mode (auto-saves old, loads new) |
| GET | `/api/modes/{location}/{cluster}/params` | Get mode-specific params |
| PUT | `/api/modes/{location}/{cluster}/params` | Save mode-specific params |

### Light Device Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/lights/{location}/{cluster}` | Get lights for zone with real names |

---

## Implementation Phases

| Phase | Description | Time |
|-------|-------------|------|
| **1** | Backend: Mode tables + API | 1h 15m |
| **2** | Frontend: ZoneConfig layout restructure | 45m |
| **3** | Frontend: LightSlidersPanel with real device names | 30m |
| **4** | Frontend: SetpointsTable compact | 25m |
| **5** | Frontend: PIDCompactRow | 25m |
| **6** | Frontend: RoomModeSelector with submode buttons | 20m |
| **7** | Testing + Build verification | 30m |
| **Total** | | ~4h 10m |

---

## Success Criteria

- [ ] CircularTimePicker on LEFT (1/3), no preset buttons
- [ ] Timeline on RIGHT TOP (2/3), ~140px height
- [ ] Setpoints table on RIGHT BOTTOM (below timeline)
- [ ] Light sliders show real device names (e.g., "HLG 600")
- [ ] Light sliders have white current value marker
- [ ] Mode dropdown + separate submode buttons [STR][BLK][RPN]
- [ ] PID in single compact row (full width)
- [ ] No scrolling on 1080p viewport
- [ ] Build passes with no errors
- [ ] Mode switching saves/loads parameters correctly

---

## Key Decisions (Confirmed by User)

| Decision | Choice |
|----------|--------|
| Layout order | CircularTimePicker LEFT, Timeline RIGHT |
| Setpoints position | Below timeline in right column |
| Preset buttons | REMOVE from CircularTimePicker (modes handle this) |
| Light labels | Use real device names from API |
| Header banner | Keep mode selector + save + back |
| Submode buttons | Visible only when Flower mode selected |

---

## Design System

### Colors (Dark Mode)

| Element | Tailwind | Usage |
|---------|----------|-------|
| Background | `bg-gray-950` | Page |
| Cards | `bg-gray-900` | Panels |
| Borders | `border-gray-800` | Dividers |
| Text | `text-gray-100` | Primary |
| Muted | `text-gray-400` | Labels |
| Active | `text-amber-400` | Live values |
| Edit | `text-cyan-400` | Interactive |
| Heat | `text-orange-400` | 🌡️ |
| Cool | `text-blue-400` | ❄️ |
| VPD | `text-emerald-400` | 💧 |
| CO2 | `text-purple-400` | 🌬️ |

### Spacing (Compact)

| Element | Value |
|---------|-------|
| Page padding | `p-3` |
| Card padding | `p-2` |
| Gaps | `gap-2` to `gap-3` |
| Input height | `h-7` |
| Font body | `text-sm` |
| Font labels | `text-xs` |
