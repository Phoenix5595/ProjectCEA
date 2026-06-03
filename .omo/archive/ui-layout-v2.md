# UI Layout V2 - Zone Configuration Dashboard

**Created**: 2026-01-16
**Status**: PLANNED
**Priority**: HIGH

---

## Research Insights

### Slider UI Best Practices (eleken.co)
- **Real-time value bubbles** above sliders for instant feedback
- **Quick visual adjustments** + precise input fields for both casual and power users
- **Current value markers** as clear visual indicators

### Home Assistant Patterns
- **Circular sliders** for lights with real-time feedback
- **Integrated control centers** with compact layouts
- **Gradient sliders** showing intensity visually

---

## Layout Specification

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🌱 Flower Room                                          [Manual] [← Back]  │
├──────────────────────────┬──────────────────────────────────────────────────┤
│ SCHEDULE & LIGHTS        │ TIMELINE (2/3 width, 2.5x height ~200px)        │
│ (1/3 width)              │                                                  │
│                          │  00  03  06  09  12  15  18  21  24              │
│ ☀️ Day Start   [06:00]   │  ░░░░░░░░████████████████████░░░░░░░░░           │
│ 🌙 Night Start [18:00]   │  ░░░░░░░░████████████████████░░░░░░░░░           │
│                          │       ▲PRE        DAY        ▲PRE   NIGHT       │
│ ⏫ Ramp Up     [30] min  │  ─────────────────────────────────────────       │
│ ⏬ Ramp Down   [30] min  │  Temp ──── VPD ──── CO2 ────                     │
│                          │                                                  │
│ 🌅 Pre-Day     [30] min  │                                                  │
│ 🌆 Pre-Night   [30] min  │                                                  │
│ ──────────────────────── │                                                  │
│ LIGHT INTENSITY          │                                                  │
│                          │                                                  │
│ Main Light               │                                                  │
│ [████████████░░░░|░85%]  │                                                  │
│ Current: 82%  Target: 85%│                                                  │
│                          │                                                  │
│ Supplemental             │                                                  │
│ [██████░░░░░░░░░|░60%]   │                                                  │
│ Current: 58%  Target: 60%│                                                  │
├──────────────────────────┴──────────────────────────────────────────────────┤
│ SETPOINTS & DELTAS (full width, 2 rows)                                     │
│ ┌────────────────────────────────────────────────────────────────────────┐  │
│ │     │ 🌡️ Heat  │ ❄️ Cool  │ 💧 VPD    │ 🌬️ CO2    │ 🍃 Leaf Δ │        │  │
│ │ DAY │ [24]°C   │ [28]°C   │ [1.0]kPa  │ [800]ppm  │ [-2]°C    │ [Save] │  │
│ │NIGHT│ [20]°C   │ [24]°C   │ [0.8]kPa  │ [600]ppm  │ [-1]°C    │ [Save] │  │
│ └────────────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│ PID CONTROL (full width, compact)                                           │
│ ┌────────────────────────────────────────────────────────────────────────┐  │
│ │ Device: [Heater ▼]  Mode: (●Auto)(○PID)(○ON/OFF)                       │  │
│ │ Kp: [22.3]  Ki: [0.018]  Kd: [0.5]  [Reset] [Save]  [History ▼]       │  │
│ └────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Structure

### ZoneConfig.tsx - New Layout

```tsx
<div className="p-3 bg-gray-950 min-h-screen flex flex-col gap-2">
  {/* Header */}
  <Header roomName={roomName} />
  
  {/* Top Section: Schedule+Lights (1/3) | Timeline (2/3) */}
  <div className="flex gap-2" style={{ height: '200px' }}>
    <ScheduleLightsPanel className="w-1/3" />
    <SetpointTimeline className="w-2/3 h-full" />
  </div>
  
  {/* Setpoints Table (full width) */}
  <SetpointsTable />
  
  {/* PID Control (full width) */}
  <PIDCard />
</div>
```

### ScheduleLightsPanel.tsx (NEW)

Combines schedule times + light intensity in left panel:

```tsx
<div className="bg-gray-900 rounded p-2 flex flex-col gap-2 h-full">
  {/* Schedule Section */}
  <div className="space-y-1">
    <h3 className="text-xs text-gray-400 uppercase">Schedule</h3>
    <div className="grid grid-cols-2 gap-1 text-sm">
      <TimeInput icon="☀️" label="Day" value={dayStart} />
      <TimeInput icon="🌙" label="Night" value={nightStart} />
      <NumberInput icon="⏫" label="Ramp Up" value={rampUp} unit="min" />
      <NumberInput icon="⏬" label="Ramp Down" value={rampDown} unit="min" />
      <NumberInput icon="🌅" label="Pre-Day" value={preDay} unit="min" />
      <NumberInput icon="🌆" label="Pre-Night" value={preNight} unit="min" />
    </div>
  </div>
  
  <div className="border-t border-gray-800 my-1" />
  
  {/* Light Intensity Section */}
  <div className="space-y-2 flex-1">
    <h3 className="text-xs text-gray-400 uppercase">Light Intensity</h3>
    <LightSlider 
      label="Main"
      current={82}
      target={85}
      onChange={setMainTarget}
    />
    <LightSlider 
      label="Supplemental"
      current={58}
      target={60}
      onChange={setSuppTarget}
    />
  </div>
</div>
```

### LightSlider Component (NEW or update LightManager)

Slider with:
- Current value marker (vertical line)
- Target value (draggable handle)
- Input field for precise value
- Current/Target display below

```tsx
<div className="space-y-1">
  <div className="flex justify-between text-xs text-gray-400">
    <span>{label}</span>
    <input 
      type="number" 
      value={target} 
      className="w-12 h-5 bg-gray-800 text-right text-sm"
    />
  </div>
  <div className="relative h-6 bg-gray-800 rounded overflow-hidden">
    {/* Filled portion */}
    <div 
      className="absolute left-0 top-0 h-full bg-amber-500/60"
      style={{ width: `${target}%` }}
    />
    {/* Current value marker line */}
    <div 
      className="absolute top-0 h-full w-0.5 bg-white"
      style={{ left: `${current}%` }}
    />
    {/* Slider handle */}
    <input 
      type="range" 
      min={0} max={100} 
      value={target}
      className="absolute inset-0 w-full opacity-0 cursor-pointer"
    />
  </div>
  <div className="flex justify-between text-xs text-gray-500">
    <span>Current: {current}%</span>
    <span>Target: {target}%</span>
  </div>
</div>
```

### SetpointsTable.tsx (NEW)

Compact table with Day/Night rows:

```tsx
<div className="bg-gray-900 rounded p-2">
  <table className="w-full text-sm">
    <thead>
      <tr className="text-xs text-gray-400">
        <th className="w-16"></th>
        <th>🌡️ Heat</th>
        <th>❄️ Cool</th>
        <th>💧 VPD</th>
        <th>🌬️ CO2</th>
        <th>🍃 Leaf Δ</th>
        <th className="w-16"></th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td className="text-amber-400">DAY</td>
        <td><input className="w-14 h-6 bg-gray-800" value={24} />°C</td>
        <td><input className="w-14 h-6 bg-gray-800" value={28} />°C</td>
        <td><input className="w-14 h-6 bg-gray-800" value={1.0} />kPa</td>
        <td><input className="w-14 h-6 bg-gray-800" value={800} />ppm</td>
        <td><input className="w-14 h-6 bg-gray-800" value={-2} />°C</td>
        <td><button className="h-6 px-2 bg-cyan-600 rounded text-xs">Save</button></td>
      </tr>
      <tr>
        <td className="text-blue-400">NIGHT</td>
        <td><input className="w-14 h-6 bg-gray-800" value={20} />°C</td>
        <td><input className="w-14 h-6 bg-gray-800" value={24} />°C</td>
        <td><input className="w-14 h-6 bg-gray-800" value={0.8} />kPa</td>
        <td><input className="w-14 h-6 bg-gray-800" value={600} />ppm</td>
        <td><input className="w-14 h-6 bg-gray-800" value={-1} />°C</td>
        <td><button className="h-6 px-2 bg-cyan-600 rounded text-xs">Save</button></td>
      </tr>
    </tbody>
  </table>
</div>
```

### SetpointTimeline.tsx Updates

- Height: `h-[200px]` (2.5x the current h-20)
- Full width within its container (2/3 of screen)
- Keep overlaid Y-axis labels
- Ensure all setpoint lines visible

### PIDCard.tsx (Compact PIDEditor)

Single-row compact layout:
- Device dropdown + Mode pills + K-values inline
- History collapsed by default

---

## Files to Modify/Create

| File | Action |
|------|--------|
| `ZoneConfig.tsx` | Major restructure |
| `ScheduleLightsPanel.tsx` | NEW - combines schedule + lights |
| `LightSlider.tsx` | NEW - slider with current marker |
| `SetpointsTable.tsx` | NEW - compact table |
| `SetpointTimeline.tsx` | Update height, keep full width |
| `PIDEditor.tsx` | Compact single-row |
| `ClimateScheduleEditor.tsx` | May be deprecated or split |
| `LightManager.tsx` | Functionality moves to ScheduleLightsPanel |

---

## Implementation Order

| Phase | Task | Time |
|-------|------|------|
| 1 | Create ScheduleLightsPanel with schedule inputs | 30m |
| 2 | Create LightSlider with current value marker | 20m |
| 3 | Integrate lights into ScheduleLightsPanel | 15m |
| 4 | Update SetpointTimeline to h-[200px] | 10m |
| 5 | Create SetpointsTable (Day/Night rows) | 25m |
| 6 | Compact PIDEditor to single row | 20m |
| 7 | Restructure ZoneConfig.tsx layout | 20m |
| 8 | Test & verify all functionality works | 15m |

**Total**: ~2.5 hours

---

## Success Criteria

- [ ] Timeline is 2/3 width, 2.5x taller (~200px)
- [ ] Left panel (1/3) has schedule times + light sliders
- [ ] Light sliders have current value marker line
- [ ] Light sliders have input field for precise value
- [ ] Light sliders show current and target values
- [ ] Setpoints in compact table (Day/Night rows)
- [ ] PID in single compact row
- [ ] All existing functionality preserved
- [ ] Build passes with no errors
