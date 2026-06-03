# ZoneConfig Layout Refactor

## TL;DR

Refactor the ZoneConfig page layout to create a clean rectangular composition with the circular time picker, setpoints, and lights, while moving notes and PID to appropriate positions below.

## Current Layout Problems

1. **Light names overlap** in `LightSlidersPanel.tsx` when viewport is narrow
2. **Labels above inputs** in CircularTimePicker waste vertical space
3. **Setpoints section is too tall** - should be half the height of the time picker
4. **Lights, Setpoints, and Time Picker don't form a rectangle** - they're side-by-side with mismatched heights
5. **Notes and PID positioning** - need to be reorganized

## Target Layout

```
┌─────────────────────────────────────────────────────────────────┐
│                     Climate Timeline (270px)                     │
└─────────────────────────────────────────────────────────────────┘

┌────────────────────┬────────────────────────────────────────────┐
│                    │  Setpoints (half height of time picker)    │
│  Circular Time     ├────────────────────────────────────────────┤
│  Picker            │  Lights (half height of time picker)       │
│  (with horizontal  │                                            │
│   labels)          │  [vertical sliders, no overflow]            │
│                    │                                            │
└────────────────────┴────────────────────────────────────────────┘

┌────────────────────┬────────────────────────────────────────────┐
│  Notes             │  PID Control                               │
│  (same width as    │  (same width as setpoints/lights)          │
│   time picker)     │                                            │
└────────────────────┴────────────────────────────────────────────┘
```

## Tasks

### Task 1: CircularTimePicker - Horizontal Labels

**Goal**: Move labels (Start, End, ↑ min, ↓ min) to the LEFT of the input fields instead of above them.

**File**: `Infrastructure/frontend/src/components/CircularTimePicker.tsx`

**Current structure** (lines 528-594):
```tsx
<div className="grid grid-cols-2 gap-1">
  <div className="flex flex-col items-center">
    <label className="block text-[12px] text-text-muted mb-0.5">Start</label>
    <input ... />
  </div>
  ...
</div>
```

**New structure**:
```tsx
<div className="grid grid-cols-2 gap-1">
  <div className="flex items-center gap-1">
    <label className="text-[12px] text-text-muted w-10 shrink-0">Start</label>
    <input ... className="flex-1 ..." />
  </div>
  ...
</div>
```

**Changes**:
- Remove `flex-col items-center` from parent divs
- Add `flex items-center gap-1` for horizontal layout
- Add fixed width to labels (`w-10 shrink-0`)
- Remove `w-1/2` from inputs, use `flex-1`
- Add `justify-center` to the outer grid container

**Expected height reduction**: ~10px (removes label height + margin)

---

### Task 2: ZoneConfig - Create Two-Column Right Side Layout

**Goal**: Split the right side (65% width) into two stacked sections: Setpoints on top, Lights below.

**File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`

**Current structure** (lines 219-230):
```tsx
<div className="w-[65%] flex flex-col gap-2">
  <div className="bg-surface-primary rounded-lg border border-border-subtle p-3">
    <SetpointsTable ... />
  </div>
</div>
```

**New structure**:
```tsx
<div className="w-[65%] flex flex-col gap-2">
  {/* Top half: Setpoints */}
  <div className="flex-1 bg-surface-primary rounded-lg border border-border-subtle p-2 overflow-hidden">
    <SetpointsTable ... />
  </div>
  {/* Bottom half: Lights */}
  <div className="flex-1 bg-surface-primary rounded-lg border border-border-subtle p-2 overflow-hidden">
    <VerticalLightsBlock location={location} cluster={cluster} compact={true} />
  </div>
</div>
```

**Changes**:
- Split into two `flex-1` containers (equal height)
- Add `overflow-hidden` to prevent scrollbars
- Pass `compact` prop to `VerticalLightsBlock`

---

### Task 3: VerticalLightsBlock - Compact Mode with Horizontal Sliders

**Goal**: Create a compact mode where lights are displayed horizontally with smaller sliders, no overflow issues.

**File**: `Infrastructure/frontend/src/components/VerticalLightsBlock.tsx`

**Add prop**:
```tsx
interface VerticalLightsBlockProps {
  location: string | null
  cluster: string | null
  compact?: boolean  // NEW
}
```

**Compact mode changes**:
- Horizontal layout: lights displayed in a row
- Smaller slider height: `min-h-[80px]` instead of `min-h-[120px]`
- Truncated names: `text-[12px] truncate max-w-[80px]`
- CUR/TGT displayed inline below slider, not above
- No "Save Pending Changes" button at bottom (use a small icon instead)

---

### Task 4: SetpointsTable - Reduce Padding and Font Size

**Goal**: Make the setpoints table more compact to fit in half the height.

**File**: `Infrastructure/frontend/src/components/SetpointsTable.tsx`

**Changes**:
- Reduce padding from `p-3` to `p-2`
- Reduce title font size from `text-[14px]` to `text-[12px]`
- Reduce gap from `gap-2` to `gap-1`
- Reduce PeriodCard padding from `p-2` to `p-1.5`
- Reduce input field height if possible

---

### Task 5: ZoneConfig - Move Notes Below Time Picker

**Goal**: Create a new row with Notes below the time picker, same width.

**File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`

**Current position**: Notes is in the third row with Lights and PID (line 241)

**New position**: Below the time picker, in the left column (35% width)

---

### Task 6: ZoneConfig - Move PID Below Setpoints/Lights

**Goal**: Move PID control to below the setpoints and lights section, same width (65%).

**File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`

**Current position**: PID is in the third row with Lights and Notes (line 240)

**New position**: Below the setpoints and lights, in the right column (65% width)

---

### Task 7: Remove Third Row Grid

**Goal**: Eliminate the `grid-cols-3` row and integrate components into the new layout.

**File**: `Infrastructure/frontend/src/pages/ZoneConfig.tsx`

**Current** (lines 232-242):
```tsx
<div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
  <VerticalLightsBlock ... />
  <VerticalPIDBlock />
  <VerticalNotesBlock ... />
</div>
```

**This entire row will be removed** - Lights moved to right side below setpoints, Notes moved below time picker, PID moved below right side.

---

## Verification

After all changes:
- Time picker, setpoints, and lights form a rectangle
- No scrolling inside any component
- Light names don't overlap
- Labels are horizontal in CircularTimePicker
- Notes is below time picker (same width)
- PID is below setpoints/lights (same width)

## Files to Modify

1. `Infrastructure/frontend/src/components/CircularTimePicker.tsx` - horizontal labels
2. `Infrastructure/frontend/src/pages/ZoneConfig.tsx` - layout restructure
3. `Infrastructure/frontend/src/components/VerticalLightsBlock.tsx` - compact mode
4. `Infrastructure/frontend/src/components/SetpointsTable.tsx` - reduce padding/sizes

## Constraints

- **NO scrolling inside components** - non-negotiable
- **NO horizontal flip for setpoints** - keep Day/Night/Pre-Day/Pre-Night as-is
- **Preserve picker size** - only reduce surrounding box by ~10px
- **Maintain all functionality** - just reorganize layout
