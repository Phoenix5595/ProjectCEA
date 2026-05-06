# Capture checklist (per docs)

These are the minimum reference captures to recreate the UI in Figma as a **responsive** design system.

## Viewports

- [ ] `1920×1080` (fullscreen)
- [ ] `960×1080` (half-width)
- [ ] `768×1024` (mobile big screen / tablet breakpoint)

## Pages / states

### Dashboard
- [ ] Normal
- [ ] Missing-data placeholders visible (Flower front/back cards should keep the 2×2 grid + unplug marker)

### ZoneConfig
- [ ] Wide layout (picker container ≥ 480px → `CircularTimePicker` wide)
- [ ] Narrow layout (picker container < 480px → stacked fields under dial)
- [ ] ClimatePeriodsTable visible (background-only tints)

### Devices
- [ ] DFR0971 boards panel visible (3 square cards, 2 channels each)

### Monitoring
- [ ] Flower monitoring (Grafana embed visible)
- [ ] Veg monitoring (Grafana embed visible)

## Tokens (sanity checks)

- [ ] Confirm default theme is `botanical` (see `Infrastructure/frontend/src/contexts/ThemeContext.tsx`)
- [ ] Confirm semantic tokens exist in `figma/tokens/semantic.css-vars.json`
- [ ] Confirm default theme values exist in `figma/tokens/themes/botanical.css-vars.json`

## Naming

Use consistent filenames across viewports (e.g. `dashboard.png`, `zoneconfig.png`).

