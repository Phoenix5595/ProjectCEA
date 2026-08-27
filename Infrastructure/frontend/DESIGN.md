# CEA Monitoring Design System

Visual and accessibility contract for the native Flower and Veg monitoring dashboards at `/flower/monitoring` and `/vegetation/monitoring`. Every monitoring color, size, and state traces back to a named token defined here and in `src/styles/themes.css`.

The monitoring token set is additive-only: it never removes or renames an existing theme variable, and it never touches product pages or Grafana code. `src/features/monitoring/designTokens.ts` and `__tests__/designTokens.test.ts` enforce that every monitoring token exists in all six themes.

## Design Tokens

Required tokens are listed in `src/features/monitoring/designTokens.ts` (`REQUIRED_MONITORING_TOKENS`).

### Metric families

| Token | Meaning |
|---|---|
| `--mon-family-temperature` | Temperature series color (left axis) |
| `--mon-family-rh` | Relative-humidity series color (right axis) |
| `--mon-family-vpd` | VPD series color (right axis) |
| `--mon-family-co2` | CO₂ series color (right axis) |
| `--mon-family-pressure` | Pressure series color (right axis) |
| `--mon-family-device` | Device output/state series color (right axis) |
| `--mon-family-light` | Light intensity/duty-cycle series color (right axis) |

### Node variants

| Token | Meaning |
|---|---|
| `--mon-node-front` | Flower Front node variant color |
| `--mon-node-back` | Flower Back node variant color |

### Envelope and targets

| Token | Meaning |
|---|---|
| `--mon-envelope-fill` | Translucent min/max band fill |
| `--mon-envelope-stroke` | Min/max band edge stroke |
| `--mon-target-recorded` | Historical effective-target color |
| `--mon-target-projected` | Projected target color |
| `--mon-target-projected-opacity` | Opacity for projected segments |
| `--mon-target-dash` | Dash pattern for recorded/projected targets |

### Sun/moon and interaction

| Token | Meaning |
|---|---|
| `--mon-sun-bg` | DAY background interval fill |
| `--mon-moon-bg` | NIGHT background interval fill |
| `--mon-focus-ring` | Keyboard focus ring color |
| `--mon-tooltip-bg` / `--mon-tooltip-border` / `--mon-tooltip-text` | Tooltip colors |
| `--mon-stale` / `--mon-error` | Provenance and error colors |
| `--mon-axis-left` / `--mon-axis-right` | Axis label/tick colors |

## Axis Contract

- Temperature uses one left family axis.
- RH, VPD, CO₂, pressure, percent/device output, and light intensity use right-side family scales.
- Axis labels and ticks use the family color.
- Canonical soft bounds are preserved:
  - Temperature soft minimum `15°C`.
  - RH soft maximum `100%`.
  - Percent/light/device scales `0–100%`.
  - Flower pressure soft range `1012–1014 hPa`.
  - Families without an explicit bound auto-range.

## Overlays

- Sun/moon are plot-wide background intervals, not y-series or legend items.
- Recorded and projected effective targets are dotted lines. Projected segments use lower opacity and a "Projected" legend suffix.
- A visible "now" divider separates recorded history from projection.

## Primitives

### Chart region

- Two chart regions per room: climate, and CO₂/pressure/device/PID/light.
- Sensor mean is a solid line; bucket min/max is a translucent envelope.
- Legend swatches are keyboard-focusable buttons with `aria-pressed`; click or Enter/Space toggles a series. A reset action restores all series.
- Time controls offer presets plus absolute start/end entry, enforce 5m–7d, expose Reset Zoom, and support drag-to-zoom.

### Table and card

- Semantic HTML tables retain Grafana ordering, units, and Last Update rows. Tables double as the chart's accessible data alternative.
- Cards wrap each chart region and table with existing surface/border tokens.

## Spacing, Type, and Radius

- Spacing uses the existing 4px-based scale from `src/styles/index.css`.
- Type uses `JetBrains Mono` (self-hosted, `--font-sans`/`--font-mono`). Axis labels and table cells use the mono stack.
- Corners stay sharp app-wide (`--radius-sm: 2px`, `--radius-md: 2px`, `--radius-lg: 3px`).

## Responsive Layout

Chart regions and tables stack to a single column below `768px`. At 375px, 768px, and 1280px the layout must remain usable: axes legible, tables horizontally scrollable, controls reachable.

## Motion Constraints

- GPU-composited animation only (`transform`, `opacity`, `filter`); never animate layout properties.
- Motion serves meaning: live append, pause/resume, and series-toggle state changes are the only animated moments.
- The live "now" divider advances with the data; no continuous idle animation.

## Accessibility Targets

- WCAG 2.2 AA-oriented contrast for all text and interactive elements.
- Canvas/plot has an accessible name; a semantic table alternative provides data for screen readers.
- Keyboard controls: legend toggles, time-range entry, Reset Zoom, and pause-live action are all keyboard-reachable.
- Focus is visible via `--mon-focus-ring`.
- Missing Flower Front data is a valid empty state, not an error.

## Primitive Showcase

A harness must exercise every primitive in isolation before product-page work. It renders a chart with left and right family axes, a min/max envelope, recorded vs projected targets with a "now" divider, a sun/moon overlay, a semantic table, and keyboard-focusable legend toggles. The harness asserts fixture origin and route guard so no request leaves `127.0.0.1:4173`.
