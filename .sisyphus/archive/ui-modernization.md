# UI Modernization Plan - Zone Configuration Dashboard

**Created**: 2026-01-16
**Status**: PLANNED
**Priority**: HIGH

---

## Research Insights

### High Performance HMI Principles (ISA-101)
- **Grayscale base** with color reserved for alerts/exceptions
- **Reduce cognitive load** - users shouldn't interpret raw data
- **Visual hierarchy** - most important info immediately visible
- **Consistent patterns** - same action, same location

### Modern Dashboard Trends 2025-2026
- **Dark mode first** - 67% of users enable it
- **Information density** - compact layouts, minimal chrome
- **Progressive disclosure** - show essentials, expand for details
- **Functional minimalism** - every pixel earns its place

### Home Assistant / Control Panel Patterns
- **Card-based layouts** with clear boundaries
- **Inline editing** - no modals for simple changes
- **Status at a glance** - current values prominent
- **Subtle animations** for state changes

---

## Design Specification

### Layout: Single-Screen Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🌱 Flower Room                                          [Manual] [← Rooms] │
├─────────────────────────────────────────────────────────────────────────────┤
│ TIMELINE (full width)                                                       │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ 00  02  04  06  08  10  12  14  16  18  20  22  24                     │ │
│ │ ░░░░░░░░░░░░████████████████████████████████░░░░░░░░░░░░░░             │ │
│ │         ▲PRE              DAY              ▲PRE    NIGHT              │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│ SCHEDULE          │ DAY SETPOINTS           │ NIGHT SETPOINTS              │
│ ┌───────────────┐ │ ┌─────────────────────┐ │ ┌─────────────────────────┐  │
│ │ ☀️ 06:00      │ │ │ 🌡️ Heat  24°C      │ │ │ 🌡️ Heat  20°C          │  │
│ │ 🌙 18:00      │ │ │ ❄️ Cool  28°C      │ │ │ ❄️ Cool  24°C          │  │
│ │ ⏱️ Pre: 30m   │ │ │ 💧 VPD   1.0 kPa   │ │ │ 💧 VPD   0.8 kPa       │  │
│ │ 🍃 Δ: -2°C    │ │ │ 🌬️ CO2   800 ppm   │ │ │ 🌬️ CO2   600 ppm       │  │
│ └───────────────┘ │ └─────────────────────┘ │ └─────────────────────────┘  │
│                   │        [Save Day]       │       [Save Night]           │
├───────────────────┴─────────────────────────┴──────────────────────────────┤
│ LIGHTS                              │ PID CONTROL                          │
│ ┌─────────────────────────────────┐ │ ┌──────────────────────────────────┐ │
│ │ Main    ████████████░░ 85%     │ │ │ Device: [Heater ▼]               │ │
│ │ Suppl   ████████░░░░░░ 60%     │ │ │ Mode: (●Auto) (○PID) (○ON/OFF)   │ │
│ │                                 │ │ │ Kp: 22.3  Ki: 0.018  Kd: 0.5    │ │
│ │ [Schedule ▼]                    │ │ │ [Reset] [Save]  [History ▼]     │ │
│ └─────────────────────────────────┘ │ └──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Color Palette (Dark Mode First)

| Element | Color | Tailwind Class |
|---------|-------|----------------|
| Background | Near black | `bg-gray-950` |
| Cards | Dark gray | `bg-gray-900` |
| Borders | Subtle | `border-gray-800` |
| Text primary | White | `text-gray-100` |
| Text secondary | Muted | `text-gray-400` |
| Accent (active) | Cyan | `text-cyan-400` |
| Warning | Amber | `text-amber-400` |
| Success | Green | `text-green-400` |
| Heat icon | Red-orange | `text-orange-400` |
| Cool icon | Blue | `text-blue-400` |

### Spacing System (Compact)

| Element | Current | New |
|---------|---------|-----|
| Page padding | `p-6` | `p-3` |
| Card padding | `p-4` | `p-2` |
| Section gaps | `gap-6` | `gap-2` |
| Input height | `h-10` | `h-7` |
| Font size | `text-base` | `text-sm` |
| Label size | `text-sm` | `text-xs` |

---

## Component Changes

### 1. ZoneConfig.tsx - Layout Restructure

**Current**: Tabs (Climate | Lights | PID)
**New**: Single-page grid layout

```tsx
<div className="p-3 space-y-2 bg-gray-950 min-h-screen">
  {/* Header */}
  <header className="flex justify-between items-center">
    <h1 className="text-lg font-semibold">{zoneName}</h1>
    <div className="flex gap-2">
      <ModeIndicator />
      <BackButton />
    </div>
  </header>
  
  {/* Timeline - FULL WIDTH */}
  <SetpointTimeline className="w-full h-20" />
  
  {/* 3-Column Grid: Schedule | Day | Night */}
  <div className="grid grid-cols-3 gap-2">
    <ScheduleCard />
    <SetpointCard period="day" />
    <SetpointCard period="night" />
  </div>
  
  {/* 2-Column Grid: Lights | PID */}
  <div className="grid grid-cols-2 gap-2">
    <LightsCard />
    <PIDCard />
  </div>
</div>
```

### 2. SetpointTimeline.tsx - Full Width, Compact

- Remove sidebar placement
- Full width with `h-20` (80px) height
- Inline time markers
- Visual day/night/pre-day/pre-night zones

### 3. SetpointCard.tsx - New Compact Component

Replace SetpointEditor with inline card:

```tsx
<div className="bg-gray-900 rounded p-2 space-y-1">
  <h3 className="text-xs text-gray-400 uppercase">Day Setpoints</h3>
  <div className="grid grid-cols-2 gap-1 text-sm">
    <SetpointRow icon="🌡️" label="Heat" value={24} unit="°C" />
    <SetpointRow icon="❄️" label="Cool" value={28} unit="°C" />
    <SetpointRow icon="💧" label="VPD" value={1.0} unit="kPa" />
    <SetpointRow icon="🌬️" label="CO2" value={800} unit="ppm" />
  </div>
  <button className="w-full h-6 text-xs bg-gray-800 rounded">Save</button>
</div>
```

### 4. ScheduleCard.tsx - New Compact Component

```tsx
<div className="bg-gray-900 rounded p-2 space-y-1">
  <h3 className="text-xs text-gray-400 uppercase">Schedule</h3>
  <div className="text-sm space-y-1">
    <div className="flex justify-between">
      <span>☀️ Day Start</span>
      <input type="time" value="06:00" className="bg-gray-800 w-20 h-6" />
    </div>
    <div className="flex justify-between">
      <span>🌙 Night Start</span>
      <input type="time" value="18:00" className="bg-gray-800 w-20 h-6" />
    </div>
    <div className="flex justify-between">
      <span>⏱️ Pre-period</span>
      <input type="number" value={30} className="bg-gray-800 w-16 h-6" />
    </div>
    <div className="flex justify-between">
      <span>🍃 Leaf Δ Day</span>
      <input type="number" value={-2} className="bg-gray-800 w-16 h-6" />
    </div>
  </div>
</div>
```

### 5. LightsCard.tsx - Compact Inline

- Slim slider rows with inline percentage
- Collapsible schedule section

### 6. PIDCard.tsx - Minimal Compact

- Device selector dropdown (not tabs)
- Mode as radio pills in a row
- K-values as inline inputs
- History collapsed by default

---

## Implementation Order

| Phase | Task | Est. Time |
|-------|------|-----------|
| 1 | Create compact card components (SetpointCard, ScheduleCard) | 30 min |
| 2 | Restructure ZoneConfig.tsx to grid layout | 20 min |
| 3 | Update SetpointTimeline for full-width compact | 20 min |
| 4 | Compact LightManager (LightsCard) | 20 min |
| 5 | Compact PIDEditor (PIDCard) | 20 min |
| 6 | Apply color palette + spacing system globally | 15 min |
| 7 | Test responsiveness + build | 15 min |

**Total**: ~2.5 hours

---

## Files to Modify

- `src/pages/ZoneConfig.tsx` - Layout restructure
- `src/components/SetpointTimeline.tsx` - Full width, compact
- `src/components/ClimateScheduleEditor.tsx` - May be split into cards
- `src/components/SetpointEditor.tsx` - Replace with SetpointCard
- `src/components/LightManager.tsx` - Compact LightsCard
- `src/components/PIDEditor.tsx` - Compact PIDCard
- `src/components/PIDModeSelector.tsx` - Inline pills
- `src/components/PIDHistoryTerminal.tsx` - Collapsed by default

---

## Success Criteria

- [ ] All room settings visible without scrolling (1080p viewport)
- [ ] Timeline takes full width
- [ ] Dark mode with grayscale base + color accents
- [ ] Consistent spacing (p-2, gap-2, h-7 inputs)
- [ ] Build passes with no errors
- [ ] All existing functionality preserved

---

## Visual Reference

**Inspiration**: High Performance HMI + Home Assistant Mushroom cards + AROYA dashboard

Key principles applied:
1. **Grayscale base** - bg-gray-950/900/800
2. **Color for meaning** - icons use semantic colors (heat=orange, cool=blue)
3. **Information hierarchy** - current values prominent, controls secondary
4. **Minimal chrome** - thin borders, subtle shadows, compact padding
