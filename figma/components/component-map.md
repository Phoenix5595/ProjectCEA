# Component map (Figma ↔ React)

Use this as the authoritative mapping when building the Figma UI kit from the reference captures.

## App shell

- **App layout / responsive sidebar**: `Infrastructure/frontend/src/components/Layout.tsx`
- **Sidebar (nav, collapsed state, mobile drawer)**: `Infrastructure/frontend/src/components/Sidebar.tsx`
- **Top ribbon (sector tabs + actions)**: `Infrastructure/frontend/src/components/TopRibbon.tsx`

## Theme / tokens

- **Theme variables (CSS vars per theme)**: `Infrastructure/frontend/src/styles/themes.css`
- **Semantic tokens + base styles**: `Infrastructure/frontend/src/styles/index.css`
- **Theme provider**: `Infrastructure/frontend/src/contexts/ThemeContext.tsx`

## Dashboard

- **Legacy room card (reference only)**: `Infrastructure/frontend/src/components/DashboardRoomCard.tsx`
- **System status panel**: `Infrastructure/frontend/src/components/SystemStatusPanel.tsx`

## Zone control (photoperiod + climate periods)

- **Circular time picker (wide vs stacked at 480px container width)**: `Infrastructure/frontend/src/components/CircularTimePicker.tsx`
- **Dial face rendering**: `Infrastructure/frontend/src/components/CircularClockFace.tsx`
- **Timeline**: `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx`
- **Climate periods table (tinted cell backgrounds)**: `Infrastructure/frontend/src/components/ClimatePeriodsTable.tsx`

## Devices

- **Device manager page content**: `Infrastructure/frontend/src/components/DeviceManager.tsx`
- **DFR boards panel**: `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx`
- **Relay matrix (SCADA 4-column panel, 2×8)**: `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx`
- **Relay channel box (horizontal SCADA cell, K1–K16 + LED)**: `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx`
- **Relay view models (labels, mapping helpers)**: `Infrastructure/frontend/src/components/devices/relayViewModel.ts`

## Monitoring (Grafana embeds)

- **Grafana iframe panel wrapper**: `Infrastructure/frontend/src/components/GrafanaPanel.tsx`

