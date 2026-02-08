# CEA Dashboard UX/Optimization Plan

**Version:** 1.0  
**Date:** 2026-01-11  
**Status:** Ready for Implementation

---

## Executive Summary

Complete redesign of the ProjectCEA dashboard focusing on:
- High-density, no-scroll dark mode interface
- Room modes system (Veg/Flower/Drying/Sleep)
- Real-time multi-instance sync
- 24h stacked-lane setpoint curve editor

---

# PART 1: CORE DASHBOARD

## 1.1 Locked Constraints

| Constraint | Value |
|------------|-------|
| Scrolling | None (anywhere) |
| Theme | Dark mode, high density |
| Live charts | None (Grafana handles telemetry) |
| Only graph | Setpoint curve editor (24h stacked lanes) |
| Refresh rate | 1 second target |
| Display | Desktop monitors only |
| Users | Single user, 3 machines (mothernode, motherbrain, pravda) |
| Navigation | Page-based (not overlay) |

## 1.2 Information Architecture

Main Dashboard (Overview):
- Flower Room Card (45%) - Click to open Room Detail
- Veg Room Card (45%) - Click to open Room Detail
- Lab Panel (10%, collapsible)

Room Detail (per room):
- Header: Room, Mode, Period, Next Transition
- Left: Live Values + PID + Overrides
- Right: 24h Setpoint Curve Editor (stacked lanes)
- Active Alarms Strip

Alarm Programming Page (separate):
- Trigger configuration only (rarely used)

## 1.3 Main Dashboard Layout

Grid: 45% Flower | 45% Veg | 10% Lab (collapsible)

### Room Card Contents

| Section | Contents |
|---------|----------|
| Status Strip | Current mode, period, next transition, automation state |
| Mode Selector | (Flower only) Dropdown + submode buttons |
| Metrics | Temp, RH, VPD, CO2, Light - Actual / Setpoint / Delta |
| Automation State | Lights intensity, relay grid, HVAC/Dehum/CO2 |
| Active Alarms | Device faults, critical alerts |

### Lab Panel
- Collapsed: status dot + alarm count
- Expanded: compact numeric list only

## 1.4 Room Detail Layout

Left Column - Live Control:
- Live values + setpoints + deltas (all 5 metrics)
- PID controls (expandable)
- Manual overrides (dead-man toggles)

Right Column - Setpoints:
- 24h stacked-lane curve editor
- Lanes: Temp / RH / VPD / CO2 / Light
- Period boundaries visible
- Edit within period boundaries only
- Save / Revert / Apply

## 1.5 Setpoint Curve Editor

- Full 24h view, stacked lanes
- Draggable points, snap-to grid
- No live telemetry overlay
- Period bands as visual markers
- Draft -> Review -> Apply flow

---

# PART 2: ROOM MODES SYSTEM

## 2.1 Mode Structure

| Room | Available Modes | Notes |
|------|-----------------|-------|
| Veg Room | Veg only | Fixed, cannot change |
| Flower Room | Flower, Drying, Sleep | Can switch |
| Lab | TBD | Not defined yet |

### Flower Mode Submodes

| Submode | Photoperiod | Notes |
|---------|-------------|-------|
| Stretch | 12/12 | Early flowering |
| Bulk | 12/12 | Mid flowering |
| Ripen | 12/12 | Late flowering |

Each submode has different setpoints, ramp durations, pre-day/pre-night durations.

### Constant Modes (No Periods)

| Mode | Photoperiod | Notes |
|------|-------------|-------|
| Drying | 24h lights off | Constant setpoints |
| Sleep | 24h lights off | Different constant setpoints |

## 2.2 Light Presets

| Preset | Hours On | Hours Off |
|--------|----------|-----------|
| Veg | 18 | 6 |
| Flower | 12 | 12 |
| Drying | 0 | 24 |
| Sleep | 0 | 24 |

## 2.3 Climate Modes

New modes to create:
- DRYING - 24h constant setpoints
- SLEEP - 24h constant setpoints

## 2.4 Mode Selector UI (Flower Room)

Layout:
- Status strip showing LIVE mode (Amber color)
- Dropdown for main modes (Flower/Drying/Sleep)
- 3 submode buttons (STR/BLK/RPN) - only visible when Flower selected
- Commit button - active when editing differs from live

Visual States:
- Synced: Neutral colors, button shows SYNCED
- Editing: Blue tint, button shows ACTIVATE [MODE]
- Committing: Animation, then reverts

Keyboard Shortcuts:
- Ctrl+1/2/3: Select main mode
- Shift+1/2/3: Select submode
- Esc: Discard changes
- Ctrl+Enter: Commit changes

## 2.5 Mode Switching Behavior

- Can edit any mode without changing active mode
- Mode change affects light + climate atomically
- Submode change keeps 12/12, changes climate only

---

# PART 3: MULTI-USER AND SYNC

## 3.1 Real-Time Sync

- All edits propagate immediately to all instances
- Updated fields show brief highlight pulse
- Last updated by [machine] on hover

## 3.2 Conflict Handling

| Tier | Operations | Concurrency |
|------|------------|-------------|
| A (Critical) | Curve edits, mode changes, overrides | Pessimistic lock |
| B (Normal) | Setpoint changes, PID tuning | Optimistic (diff) |

Lock Behavior:
- Lock per room/curve (not global)
- Auto-expire on inactivity (30-60s)
- Other instances see read-only + lock owner
- Steal lock requires confirmation

---

# PART 4: ALARMS

## 4.1 Active Alarms
- Visible on main dashboard + room detail
- Device faults, critical alerts only
- Environmental alarms handled by Grafana

## 4.2 Alarm Programming Page
- Separate subpage for trigger configuration
- Rarely used, but accessible
- Publish changes with confirmation

---

# PART 5: VISUAL SYSTEM

## 5.1 Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| bg-depth-0 | #050505 | Global background |
| bg-depth-1 | #1A1A1A | Card surface |
| bg-depth-2 | #2A2A2A | Hover/active |
| color-live | #FFB800 | Active state (Amber) |
| color-edit | #00F0FF | Edit mode (Cyan) |
| color-ok | #00FF94 | OK/normal |
| color-warn | #FF9F1C | Warning |
| color-crit | #FF3B30 | Critical |

## 5.2 Typography

| Element | Font | Size |
|---------|------|------|
| Labels | JetBrains Mono | 11-12px |
| Values | JetBrains Mono | 16-20px |
| Primary Data | JetBrains Mono | 28-48px |
| Room Title | Unbounded | 24px |

---

# PART 6: DATABASE CHANGES

## 6.1 New Tables

light_presets:
- id, name, hours_on, hours_off, description

room_modes:
- id, name, light_preset_id, is_constant

flower_submodes:
- id, name, order_index, pre_day_duration, pre_night_duration, ramp_in_duration

room_active_mode:
- room (PK), mode_id, submode_id, activated_at

## 6.2 New Climate Modes

Add to setpoints table:
- mode = DRYING (24h constant)
- mode = SLEEP (24h constant)

## 6.3 Seed Data

Light presets: veg(18/6), flower(12/12), drying(0/24), sleep(0/24)
Room modes: VEG, FLOWER, DRYING, SLEEP
Flower submodes: STRETCH, BULK, RIPEN

---

# PART 7: API CHANGES

## 7.1 New Endpoints

GET  /rooms/{roomId}/mode
POST /rooms/{roomId}/mode/activate
GET  /rooms/{roomId}/mode/available
GET  /modes
GET  /modes/{modeId}
PUT  /modes/{modeId}
GET  /modes/{modeId}/submodes
GET  /light-presets

## 7.2 WebSocket Events

room.mode.changed
room.setpoint.updated
room.override.activated
room.lock.acquired
room.lock.released

---

# PART 8: IMPLEMENTATION PHASES

| Phase | Tasks | Effort |
|-------|-------|--------|
| 1 | Main dashboard layout redesign | 2-3 days |
| 2 | Room detail + curve editor | 3-4 days |
| 3 | Room modes system (DB + API + UI) | 3-4 days |
| 4 | Multi-user sync + conflict handling | 2-3 days |
| 5 | Alarm programming page | 1-2 days |
| 6 | Polish + keyboard shortcuts | 1 day |

Total estimate: 12-17 days

---

# PART 9: SUCCESS CRITERIA

| Criteria | Target |
|----------|--------|
| Status scan time | < 5 seconds |
| Clicks to room detail | 1 |
| Scrolling required | None |
| Mode switch time | < 2 seconds |
| Sync latency | < 1 second |
| Keyboard navigation | Full support |

---

# PART 10: RISKS AND MITIGATIONS

| Risk | Mitigation |
|------|------------|
| Over-densification | Strict content budget per card |
| Mode switch safety | Confirmation + atomic apply |
| Curve edit conflicts | Pessimistic locking |
| Stale locks | Auto-expire + heartbeat |
| Real-time sync latency | WebSocket + optimistic UI |

---

# PART 11: COMPONENT LIST

Main Dashboard:
- RoomCard, StatusStrip, ModeSelector, SubmodeButtons
- MetricTile, DeltaChip, RelayGrid, AlarmBadge, LabPanel

Room Detail:
- RoomDetailHeader, LiveValuePanel, PIDControls
- ManualOverridePanel, SetpointCurveEditor, CurveLane
- PeriodMarker, ActionBar

Shared:
- Toast, ConfirmDialog, LockIndica
