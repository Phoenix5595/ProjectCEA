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

### Climate timeline compact and expanded states

- The compact Control timeline is read-only. It shows saved scheduled and runtime-effective trajectories, a UTC-window label with the selected display timezone, and explicit source configuration revision/freshness metadata. It has one Expand control; pointer drag and keyboard adjustment do nothing in this state.
- Expanded Control keeps the compact plot, adds scheduled-only editing handles, Review, Apply, and Discard controls, and leaves the primary climate-period table immediately below as the complete keyboard and fine-tuning fallback. Effective trajectories are never draggable or editable.
- Scheduled and effective trajectories use both line style and a text label. Saved and draft values use both line style and a visible Saved/Draft state label; color alone never communicates either distinction.
- Climate panels are unit-aligned: temperature (`°C`), VPD (`kPa`), and CO2 (`ppm`) never share an unlabeled scale. Metric/unit labels remain visible in compact and expanded states.
- The period band sits below trajectory curves. It renders faint period labels centered in their exact half-open period extents, truncates without overlap at narrow widths, exposes the full label through accessible text, and never intercepts an editing pointer event.
- Scheduled transition labels distinguish calendar-scheduled values from runtime-effective values. An effective trajectory starts at its runtime observation time; stale or unavailable runtime state is shown as an explicit gap with its warning or assumption, never as a continued line.

### Editing, conflicts, and stale state

- Dragging or keyboard editing changes the shared draft only. It never persists, actuates, or changes monitoring. The primary table and expanded graph update the same draft immediately; collapse preserves that draft.
- Every graphical adjustment has an equivalent keyboard path: focus a scheduled segment/handle, use arrow keys for the documented increment, and use the table for direct numeric/time entry. Focus returns to the affected handle or table field after review, validation, conflict, or discard.
- Review identifies the base configuration revision and draft revision. Apply is enabled only from the reviewed, valid draft state. Discard restores the saved revision and removes the draft marker.
- A stale configuration or revision conflict preserves the draft, marks it Conflict/Stale in text and with `--mon-stale`, and offers Reload saved values plus Review draft. It never silently overwrites, merges, clamps, or discards edits.
- Unavailable segments visibly break the line. Assumptions and warnings appear in the timeline metadata and accessible table alternative, including an explicit "assumes override remains active" statement when applicable.

### Operator handoff boundaries

- The climate periods table is permanent and remains the primary fine-tuning interface. Operators can edit period names, times, targets, and ramps there without opening the graph. The expanded graph is supplementary.
- Compact Control is a read-only view of saved scheduled and runtime-effective trajectories. Expand creates an unsaved draft overlay. Review validates that draft, and Apply is the only path that persists it. Discard removes the draft without changing saved authority.
- Monitoring consumes the saved rich trajectory only. It never reads Control draft state, and it does not actuate devices. Historical monitoring remains authoritative where recorded data overlaps the saved projection.
- The rich trajectory envelope is additive to legacy monitoring contracts. Consumers that need only scalar `value` and `quality` fields can continue using the legacy series; rich consumers must preserve `source`, `trajectory_kind`, revision scope, and explicit UTC/timezone window metadata. Draft envelopes must not be published as saved monitoring data.
- The projection assumes an indefinite runtime override remains active until the finite requested window ends when no expiry is available. The assumption is surfaced as a warning and an unavailable runtime state creates a visible gap rather than an invented continuation.

### Rollout order

1. Deploy backend and automation read-only projection and preview support, then verify saved revision and unavailable-segment behavior.
2. Deploy monitoring-service rich read compatibility, retaining legacy series as the fallback contract.
3. Deploy the frontend Control and monitoring consumers, then verify the table, Review/Apply boundary, saved-only monitoring, and both daily and rolling windows.
4. Enable operator use of Apply only after the preceding read paths are healthy. No rollout step changes hardware cadence or requires a production data migration.

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
