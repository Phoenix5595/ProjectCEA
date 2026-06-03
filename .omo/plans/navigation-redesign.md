# Navigation Redesign - Sidebar with Sector Tabs

## TL;DR

Create a new navigation architecture with a collapsible sidebar containing 5 main tabs (Overview, Laboratory, Vegetation, Flower, Devices). Each sector tab has sub-pages accessible via a top ribbon via manual toggle,. The sidebar collapses showing only icons in compact mode.

## User Clarifications (from Q&A)

- **State Persistence**: Sidebar collapsed state saves to localStorage (`cea-sidebar-collapsed`), persists across sessions
- **Legacy Routes**: Old `/zone/:location/:cluster` and `/device-config` redirect to new URLs
- **Grafana Auth**: Same auth as main app (iframe embedding)
- **Mobile Breakpoint**: 768px (hamburger menu below this width)
- **Scope Priority**: Navigation structure first, Grafana pages can be added later

## Current State

**Existing Routes:**
- `/` - Dashboard (main - shows Veg Room, Flower Room, Lab)
- `/zone/:location/:cluster` - ZoneConfig (Veg/Flower room config)
- `/device-config` - Device configuration

**Files:**
- `App.tsx` - Simple route definitions
- `Dashboard.tsx` - Main dashboard with 3 room cards
- `ZoneConfig.tsx` - Room configuration (setpoints, lights, PID)
- `DeviceConfig.tsx` - Device management

## Target Navigation Structure

```
┌─────────────────────────────────────────────────────────────┐
│ [≡]  Siberian Jungle                              [Theme]  │
├─────┬─────────────────────────────────────────────────────┤
│     │ ┌─────────┬─────────┬────────────┐                   │
│ O   │ │Overview │Monitoring│Control    │                   │
│     │ └─────────┴─────────┴────────────┘                   │
│ L   │                                                     │
│     │              [Page Content]                         │
│ V   │                                                     │
│     │                                                     │
│ F   │                                                     │
│     │                                                     │
│ D   │                                                     │
└─────┴─────────────────────────────────────────────────────┘

Sidebar (collapsible):
- Overview (always visible, non-collapsible) → /
- Laboratory → /laboratory/*
- Vegetation → /vegetation/*
- Flower → /flower/*
- Devices → /devices/*
```

## Page Mapping

| New URL | Content | Source |
|---------|---------|--------|
| `/` | Main Dashboard | Existing Dashboard |
| `/laboratory` | Laboratory Overview | New (redirects to /laboratory/climate) |
| `/laboratory/climate` | Lab + Outdoor climate + Grafana | New + Grafana |
| `/laboratory/water` | Water/irrigation Grafana | New + Grafana |
| `/laboratory/infrastructure` | System infrastructure Grafana | New + Grafana |
| `/vegetation` | Veg Room Overview | New (redirects to /vegetation/monitoring) |
| `/vegetation/monitoring` | Veg Room Grafana dashboards | New + Grafana |
| `/vegetation/control` | Veg ZoneConfig | Existing ZoneConfig |
| `/flower` | Flower Room Overview | New (redirects to /flower/monitoring) |
| `/flower/monitoring` | Flower Room Grafana dashboards | New + Grafana |
| `/flower/control` | Flower ZoneConfig | Existing ZoneConfig |
| `/flower/soil` | Flower Soil Grafana (future) | New + Grafana |
| `/devices` | Device Overview | Existing DeviceConfig |
| `/devices/*` | Device sub-pages | TBD |

## Tasks

### Phase 1: Sidebar Infrastructure

#### Task 1: Create Sidebar Component
**File**: `src/components/Sidebar.tsx` (new)

Create a collapsible sidebar component:
- 5 main tabs with icons + labels
- Manual toggle button for collapse/expand
- Icons only in collapsed mode
- Overview tab is always full width (non-collapsible)
- Smooth transition animation
- **State Persistence**: localStorage key `cea-sidebar-collapsed`

**Props:**
```typescript
interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}
```

**Structure:**
```tsx
<div className={`${collapsed ? 'w-16' : 'w-56'} transition-all ...`}>
  {/* Toggle button */}
  {/* Overview tab (always visible) */}
  {/* Laboratory tab (expandable) */}
  {/* Vegetation tab (expandable) */}
  {/* Flower tab (expandable) */}
  {/* Devices tab (expandable) */}
</div>
```

---

#### Task 2: Create Top Ribbon Component
**File**: `src/components/TopRibbon.tsx` (new)

Horizontal sub-tab navigation that appears below header when viewing a sector page:
- Shows sub-tabs for current sector
- Active tab highlighted
- Smooth horizontal scroll on mobile

**Props:**
```typescript
interface TopRibbonProps {
  sector: 'laboratory' | 'vegetation' | 'flower' | 'devices'
  activeTab: string
  onTabChange: (tab: string) => void
}
```

---

#### Task 3: Update App Layout
**File**: `src/App.tsx`

Wrap all routes in new layout structure:
```tsx
<Layout>
  <Routes>
    {/* All routes */}
  </Routes>
</Layout>
```

**New Layout component** (`src/components/Layout.tsx`):
- Manages sidebar collapsed state
- Renders Sidebar + TopRibbon + Outlet
- Handles mobile hamburger menu

---

### Phase 2: Route Restructuring

#### Task 4: Update React Router Configuration
**File**: `src/App.tsx`

New route hierarchy with redirects for legacy routes:
```tsx
<Routes>
  {/* Legacy route redirects */}
  <Route path="/zone/Veg Room/main" element={<Navigate to="/vegetation/control" replace />} />
  <Route path="/zone/Flower Room/main" element={<Navigate to="/flower/control" replace />} />
  <Route path="/device-config" element={<Navigate to="/devices" replace />} />
  
  {/* Overview - no sub-tabs */}
  <Route path="/" element={<Layout><Dashboard /></Layout>} />
  
  {/* Laboratory sector */}
  <Route path="/laboratory" element={<Layout sector="laboratory"><LaboratoryOverview /></Layout>} />
  <Route path="/laboratory/climate" element={<Layout sector="laboratory"><LaboratoryClimate /></Layout>} />
  <Route path="/laboratory/water" element={<Layout sector="laboratory"><LaboratoryWater /></Layout>} />
  <Route path="/laboratory/infrastructure" element={<Layout sector="laboratory"><LaboratoryInfrastructure /></Layout>} />
  
  {/* Vegetation sector */}
  <Route path="/vegetation" element={<Layout sector="vegetation"><VegetationOverview /></Layout>} />
  <Route path="/vegetation/monitoring" element={<Layout sector="vegetation"><VegetationMonitoring /></Layout>} />
  <Route path="/vegetation/control" element={<Layout sector="vegetation"><ZoneConfig location="Veg Room" /></Layout>} />
  
  {/* Flower sector */}
  <Route path="/flower" element={<Layout sector="flower"><FlowerOverview /></Layout>} />
  <Route path="/flower/monitoring" element={<Layout sector="flower"><FlowerMonitoring /></Layout>} />
  <Route path="/flower/control" element={<Layout sector="flower"><ZoneConfig location="Flower Room" /></Layout>} />
  <Route path="/flower/soil" element={<Layout sector="flower"><FlowerSoil /></Layout>} />
  
  {/* Devices sector */}
  <Route path="/devices" element={<Layout sector="devices"><DevicesOverview /></Layout>} />
  <Route path="/devices/:page" element={<Layout sector="devices"><DevicePage /></Layout>} />
</Routes>
```

---

### Phase 3: Page Components

#### Task 5: Create Sector Overview Pages
**Files**: 
- `src/pages/LaboratoryOverview.tsx` (new)
- `src/pages/VegetationOverview.tsx` (new)
- `src/pages/FlowerOverview.tsx` (new)

Simple redirect pages that go to default sub-tab:
```tsx
// VegetationOverview.tsx example
import { Navigate } from 'react-router-dom'
export default function VegetationOverview() {
  return <Navigate to="/vegetation/monitoring" replace />
}
```

---

#### Task 6: Create Grafana Embed Components
**File**: `src/components/GrafanaPanel.tsx` (new)

Reusable component to embed Grafana dashboards:
```typescript
interface GrafanaPanelProps {
  dashboardUid: string
  panelId?: number
  title?: string
}
```

**Implementation:**
- Iframe embedding from Grafana URL
- Handle authentication (Grafana API key or anonymous)
- Responsive sizing

---

#### Task 7: Create Laboratory Pages
**Files**:
- `src/pages/LaboratoryClimate.tsx` (new)
- `src/pages/LaboratoryWater.tsx` (new)
- `src/pages/LaboratoryInfrastructure.tsx` (new)

Each page embeds relevant Grafana dashboard:
- LaboratoryClimate: Lab room sensors + outdoor weather
- LaboratoryWater: Water/irrigation metrics
- LaboratoryInfrastructure: System services, database, Redis

---

#### Task 8: Create Vegetation/Flower Monitoring Pages
**Files**:
- `src/pages/VegetationMonitoring.tsx` (new)
- `src/pages/FlowerMonitoring.tsx` (new)
- `src/pages/FlowerSoil.tsx` (new - placeholder for future)

Each embeds Grafana dashboards for the room

---

#### Task 9: Update ZoneConfig Integration
**File**: `src/pages/ZoneConfig.tsx`

Modify to accept location prop directly (for route params):
```typescript
// Currently reads from URL params
// Change to also accept props
interface ZoneConfigProps {
  location?: string
  cluster?: string
}
```

Create wrapper components:
- `src/pages/VegetationControl.tsx`
- `src/pages/FlowerControl.tsx`

---

### Phase 4: Styling & Polish

#### Task 10: Sidebar Icons
**File**: `src/components/Sidebar.tsx`

Add icons for each tab (using existing icons or emoji):
- Overview: 🏠 or grid icon
- Laboratory: 🔬 or flask icon
- Vegetation: 🌱 or leaf icon
- Flower: 🌸 or flower icon
- Devices: ⚙️ or cog icon

---

#### Task 11: Mobile Responsiveness
**File**: `src/components/Layout.tsx`

- Add hamburger menu for mobile (below 768px)
- Hide sidebar on mobile, show as drawer
- Top ribbon scrolls horizontally on narrow screens

---

## Implementation Order

1. **Task 1**: Create Sidebar component (foundation)
2. **Task 2**: Create TopRibbon component
3. **Task 3**: Create Layout wrapper + update App.tsx
4. **Task 4**: Define all routes
5. **Task 5**: Create overview redirect pages
6. **Task 6**: Create Grafana embed component
7. **Task 7-9**: Create sector pages with Grafana
8. **Task 10**: Add icons
9. **Task 11**: Mobile responsiveness

## Constraints

- **No breaking changes**: Keep existing routes working during transition
- **Grafana integration**: Uses existing Grafana instance at `/grafana`
- **Manual collapse**: Toggle button, no auto-collapse
- **Overview is special**: Cannot be collapsed, always shows full

## Files to Modify/Create

| File | Action |
|------|--------|
| `src/components/Sidebar.tsx` | Create |
| `src/components/TopRibbon.tsx` | Create |
| `src/components/Layout.tsx` | Create |
| `src/components/GrafanaPanel.tsx` | Create |
| `src/pages/LaboratoryOverview.tsx` | Create |
| `src/pages/LaboratoryClimate.tsx` | Create |
| `src/pages/LaboratoryWater.tsx` | Create |
| `src/pages/LaboratoryInfrastructure.tsx` | Create |
| `src/pages/VegetationOverview.tsx` | Create |
| `src/pages/VegetationMonitoring.tsx` | Create |
| `src/pages/VegetationControl.tsx` | Create |
| `src/pages/FlowerOverview.tsx` | Create |
| `src/pages/FlowerMonitoring.tsx` | Create |
| `src/pages/FlowerControl.tsx` | Create |
| `src/pages/FlowerSoil.tsx` | Create |
| `src/App.tsx` | Modify - add routes |
| `src/pages/ZoneConfig.tsx` | Modify - add props |

## Verification

After implementation:
- [x] Sidebar shows 5 tabs with icons
- [x] Sidebar collapses via manual toggle
- [x] Overview tab stays full even when collapsed
- [x] Top ribbon shows sub-tabs when in a sector
- [x] All routes work correctly
- [x] Legacy routes redirect to new URLs
- [x] Sidebar collapsed state persists in localStorage
- [x] Mobile hamburger menu works below 768px
- [x] No console errors

## Notes

- Grafana dashboards need to be created separately (user will handle)
- Existing pages (Dashboard, ZoneConfig, DeviceConfig) remain functional
- Transition period: old routes redirect to new structure
- **State Persistence**: localStorage key `cea-sidebar-collapsed`
- **Mobile Breakpoint**: 768px (below = hamburger menu)
- **Grafana**: Same auth as main app

## Legacy Route Migration

| Old Route                | New Route              | Behavior   |
| ------------------------ | ---------------------- | ---------- |
| `/`                        | `/`                      | Same       |
| `/zone/Veg Room/main`      | `/vegetation/control`    | Redirect   |
| `/zone/Flower Room/main`   | `/flower/control`        | Redirect   |
| `/device-config`           | `/devices`               | Redirect   |
