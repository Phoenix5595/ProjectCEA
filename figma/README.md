# Figma — “Base UI” export pack

This folder is a **drop-in pack** to bring the current ProjectCEA UI into Figma in a way that stays **dynamic/responsive** across the main operator viewports.

## Folder layout (what belongs where)

- `figma/references/<viewport>/`: screen captures (PNG/PDF) used as ground-truth reference
- `figma/exports/`: assets exported *from* Figma for the app (icons/images/etc.)
- `figma/tokens/`: design tokens (generated from the frontend CSS variables; import into Figma)
- `figma/components/`: UI kit notes + mapping from Figma components to React components

## The 3 reference viewports to capture

- **Primary (fullscreen 1080p)**: `1920×1080`
- **Primary (half-width 1080p)**: `960×1080`
- **Secondary (“mobile big screen”)**: `768×1024` (matches the codebase mobile breakpoint)

> Note: some components (notably `CircularTimePicker`) switch layout based on **container width**, not window width. The code breakpoint is `STACK_LAYOUT_MAX_WIDTH_PX = 480` in `Infrastructure/frontend/src/components/CircularTimePicker.tsx`.

## What files you should place here

Put **one PNG (or PDF) per page** per viewport in the corresponding folder:

- `figma/references/1920x1080/`
- `figma/references/960x1080/`
- `figma/references/768x1024/`

Use these filenames (recommended):

- `dashboard.png`
- `flower_control.png`
- `veg_control.png`
- `lab_climate.png`
- `devices.png`
- `zoneconfig.png`
- `flower_monitoring.png`
- `veg_monitoring.png`

Also capture at least one “missing data” state where relevant:

- `dashboard_missing_data.png`
- `zoneconfig_narrow_picker.png` (for the stacked `CircularTimePicker` layout)

## How to import into Figma

1. Create a new Figma file.
2. Create 3 top-level frames named:
   - `Primary — 1920×1080`
   - `Primary — 960×1080`
   - `Secondary — 768×1024`
3. Drag the images into the file and place each into the matching frame.
4. Import tokens from `figma/tokens/` into your Figma “Variables” (colors + semantic tokens).
5. Build your **UI Kit** (components) using these reference frames as ground truth.

## Source-of-truth UI requirements (docs)

UI behavior and layout rules live in:

- `Infrastructure/frontend/REQUIREMENTS.md`

