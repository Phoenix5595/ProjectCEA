# CEA Monitoring Design System

Visual and accessibility contract for the native Flower and Veg monitoring dashboards at `/flower/monitoring` and `/vegetation/monitoring`. Every monitoring color, size, and state traces back to a named token defined here and in `src/styles/themes.css`.

The monitoring token set is additive-only: it never removes or renames an existing theme variable, and it never touches product pages or code outside the monitoring feature. `src/features/monitoring/designTokens.ts` and `__tests__/designTokens.test.ts` enforce that every monitoring token exists in all six themes.

## Design Tokens

Required tokens are listed in `src/features/monitoring/designTokens.ts` (`REQUIRED_MONITORING_TOKENS`).

### Metric families

| Token                      | Meaning                                              |
| -------------------------- | ---------------------------------------------------- |
| `--mon-family-temperature` | Temperature series color (left axis)                 |
| `--mon-family-rh`          | Relative-humidity series color (right axis)          |
| `--mon-family-vpd`         | VPD series color (right axis)                        |
| `--mon-family-co2`         | CO₂ series color (right axis)                        |
| `--mon-family-pressure`    | Pressure series color (right axis)                   |
| `--mon-family-device`      | Device output/state series color (right axis)        |
| `--mon-family-light`       | Light intensity/duty-cycle series color (right axis) |

### Node variants

| Token              | Meaning                         |
| ------------------ | ------------------------------- |
| `--mon-node-front` | Flower Front node variant color |
| `--mon-node-back`  | Flower Back node variant color  |

### Envelope and targets

| Token                            | Meaning                                     |
| -------------------------------- | ------------------------------------------- |
| `--mon-envelope-fill`            | Translucent min/max band fill               |
| `--mon-envelope-stroke`          | Min/max band edge stroke                    |
| `--mon-target-recorded`          | Historical effective-target color           |
| `--mon-target-projected`         | Projected target color                      |
| `--mon-target-projected-opacity` | Opacity for projected segments              |
| `--mon-target-dash`              | Dash pattern for recorded/projected targets |

### Interaction

| Token                                                              | Meaning                        |
| ------------------------------------------------------------------ | ------------------------------ |
| `--mon-focus-ring`                                                 | Keyboard focus ring color      |
| `--mon-tooltip-bg` / `--mon-tooltip-border` / `--mon-tooltip-text` | Tooltip colors                 |
| `--mon-stale` / `--mon-error`                                      | Provenance and error colors    |
| `--mon-axis-left` / `--mon-axis-right`                             | Axis label/tick colors         |

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

- Sun/moon are plot-wide background intervals with fixed semantic fills that never follow the active theme; they are not y-series or legend items.
- Recorded and projected effective targets are dotted lines. Projected segments use lower opacity and a "Projected" legend suffix.
- Each effective climate target keeps its own colour (owner rule; the manifest series specs are the source, `config/flowerManifest.ts` and `config/vegManifest.ts`). The three targets must never share a colour, and the `--mon-target-*` tokens are only the fallback for an unknown target:

  | Target | Series name (recorded) / projected label | Colour | Style |
  | --- | --- | --- | --- |
  | Heating | `heating_setpoint` / `Heating Setpoint` | `#ff6fb0` pink | dotted, 2 px, step |
  | Cooling | `cooling_setpoint` / `Cooling Setpoint` | `#4fd1c5` teal | dotted, 2 px, step |
  | VPD | `vpd_setpoint` / `VPD Setpoint` | `#c0dd55` lime | dotted, 2 px, step |

  Commit `dcf21246` removed these specs together with the climate source, which left all three targets on the single fallback token; they were restored on 2026-10-25. Removing or merging a target spec is a design regression.
- Monitoring graphs only the **effective** setpoint trajectory (recorded `effective_setpoints` history plus the projection's `trajectory_kind: 'effective'` segments), because it is the target the automation follows with its ramps computed by the system. The projection's scheduled climate trajectory is never drawn on Monitoring; it belongs to the Control timeline. Recorded and projected values of a target merge into **one line** that continues across now: held steps, replaced by the exact ramp wherever a ramp segment covers the slot (never a held value running alongside a ramp). Each line is named `Heating Setpoint` / `Cooling Setpoint` / `VPD Setpoint` so it takes the colour above. Light intensity projection handling is unchanged.
- Sensor and light intensity lines are 1 px (owner decision 2026-10-25; VPD used to be 3 px). Setpoint targets stay 2 px dotted.
- Setpoint targets are **lines only**, on both Monitoring and Historical. Never fill a min–max band around them, even when source history is bucketed or rebucketed by the browser; recorded extrema must not add shading or extra target lines. Sensor min–max bands remain unchanged.
- Chart hover tooltips show each series' label and value (with unit) and, for bucketed sensor points, the bucket span, sample count and min–max. They never show origin/quality provenance text such as `recorded/exact`; that detail lives only in the dev-only data table.
- Dry/wet bulb lines on the climate graphs are only the declared node sensors (`dry_bulb_f`/`_b`, `wet_bulb_f`/`_b`, `_v` for Veg). Unsuffixed `dry_bulb`/`wet_bulb` readings (written by the CAN processor before its node assignments load at startup) are never drawn.
- Min–max band boundaries are drawn with a transparent stroke: a zero canvas line width is ignored by the browser, which otherwise draws the boundaries as stray lines.
- Sun/moon fills are `rgba(251, 191, 36, 0.09)` / `rgba(129, 140, 248, 0.09)` on monitoring graphs; bucketed sensor min–max bands are drawn over them at 15 % of the series colour.
- A visible "now" divider separates recorded history from projection.

## Primitives

### Chart region

- Two chart regions per room: environmental climate, and CO₂/pressure/light intensity. Relay/device state, requested PID and other non-light automation tracking render only on Automation.
- Sensor mean is a solid line; bucket min/max is a translucent envelope.
- Legend swatches are keyboard-focusable buttons with `aria-pressed`; click or Enter/Space toggles a series. A reset action restores all series.
- Detailed time controls retain their 5m–7d bounds and drag-to-zoom. Historical adds 5 days (Monitoring), 7 days, 2 weeks, one/three Toronto calendar months and the room's current calendar run. A sub-seven-day drag first snaps to seven days; a second short drag switches to Monitoring at the selected range.

### Table and card

- Semantic HTML tables retain the canonical dashboard ordering, units, and Last Update rows. Tables double as the chart's accessible data alternative.
- Cards wrap each chart region and table with existing surface/border tokens.
- Historical uses the finest fitting 1–12-hour bucket with an explicit 1000-point budget; partial edge buckets count. Keep min/max envelopes and sensor-only statistics below the two graphs, with the established 780px/320px chart heights and no internally scrolling wrappers.

### Climate timeline compact and expanded states

- The compact Control timeline is read-only. It shows saved scheduled and runtime-effective trajectories, an `America/Toronto` time label, and explicit source configuration revision/freshness metadata. It has one Expand control; pointer drag and keyboard adjustment do nothing in this state.
- Chart time is the Toronto wall clock, without a UTC suffix: axis ticks and tooltip timestamps use `America/Toronto`, and the daily window is the Toronto local calendar day (Toronto midnight to the next Toronto midnight, 1380/1440/1500 minutes across DST dates). Instants stay absolute; only their displayed wall clock changes.
- The Draft row and curve render only when the editor holds unsaved changes and at least one plotted draft value differs from Saved at the same x, compared at the tooltip's displayed precision (temperature 0.1 °C, VPD 0.01 kPa, CO₂ 1 ppm). Photoperiod-only or label-only edits therefore add no Draft line.
- The Running forecast renders only where it differs from Saved at the same displayed precision when the inspected profile is the active profile; with an inactive profile selected, Saved and the forecast both remain visible unchanged.
- Photoperiod and climate periods are independent (R5): sun/moon bands come only from recorded or projected photoperiod, never from climate period boundaries, and editing a climate period never moves a sun/moon boundary.
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

### Timeline uPlot renderer and interactive editing

- The Control timeline plot body renders on uPlot (1.6.32, separate light mount in `src/features/climate-timeline/charts/`; the monitoring `UPlotChart` wrapper is unchanged). Scheduled and effective setpoint trajectories for heating, cooling, VPD, and CO₂ are sampled from the same backend envelope monitoring consumes (projection parity test in the feature's `__tests__`), with full-height sun/moon shading behind the lines in the owner's original colors (`rgba(234,179,8,0.45)` sun, `rgba(168,85,247,0.35)` moon).
- Temperature (heating/cooling) uses the left `°C` family axis; VPD (`kPa`) and CO₂ (`ppm`) use stacked right-side family scales with the product soft bounds (10–35, 0–5, 400–2000). Scheduled lines are solid; effective lines are dashed and distinguished by text labels, never color alone. Unavailable segments break the line and are never bridged.
- Faint period names (alpha 0.25, mono font) are painted on the canvas below the curve zone over the photoperiod shading; they truncate without overlap, never intercept pointer events, and mirror the visible table which remains the accessible source.
- In the expanded editor with the daily window, mouse-drag handles edit scheduled setpoint values (snap 0.1 °C/kPa, 10 ppm; product-range validation rejects out-of-range drags visibly instead of clamping) and period boundaries (5-minute snap clamped between neighbours). Every frame of a drag coalesces into at most one draft commit; nothing persists during a drag; the effective series and the rolling/now-based window are never draggable; the table retains full keyboard parity.
- During a drag or the dirty-not-yet-previewed window, the chart paints the labelled local draft estimate ("Local draft estimate" tag) and marks the effective line stale ("Effective stale — preview pending"); a preview 250 ms after the last edit (single-flight, latest-result ownership) restores the backend envelope. Apply stays explicit; 409 conflicts keep the draft and mark it stale.

### Operator handoff boundaries

- The climate periods table is permanent and remains the primary fine-tuning interface. Operators can edit period names, times, targets, and ramps there without opening the graph. The expanded graph is supplementary.
- Compact Control is a read-only view of saved scheduled and runtime-effective trajectories. Expand creates an unsaved draft overlay. Review validates that draft, and Apply is the only path that persists it. Discard removes the draft without changing saved authority.
- Monitoring consumes saved light trajectories, recorded environmental sensors and recorded effective climate setpoints (plus projected setpoints on the Monitoring page only, never on Historical), never Control draft state, relay/PID automation tracking, and never actuates devices. Recorded phase transitions and their separate coverage remain historical authority; a current schedule or projection cannot recolor the past.
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

## Soil Page Layout Contract

The `/flower/soil` page reuses the monitoring layout system verbatim instead of inventing parallel structure:

- **Box system** (`monitoring.css`): `.mon-page` hosts a `.mon-layout` grid of two zones — a `.mon-side` column of compact `mon-card` boxes and a `.mon-main` column of chart/table cards.
- **Side boxes** (`.mon-side`): compact cards at `4px` padding with `2px` margins, zero table-cell padding, and `position: sticky` at ≥1100px. Each box carries a `mon-card__title` and a `MonitoringFreshness` chip.
- **Main cards** (`.mon-main`): `4px` padding, `1rem` internal gap, tight `mon-card__title` margins. Contrast comes from the shared surface/border tokens (`surface-secondary` on `surface-base`, `border-default`); never introduce ad-hoc wrappers with custom padding.
- **Soil extension — `.mon-layout--beds`**: the side column hosts the bed schematic cards, using the same compact card treatment as monitoring without a second large gap between cards.
- **Chart sizing**: `.mon-chart` is `height: 100%`, so its wrapper must carry a definite height. Monitoring pages use fixed pixel heights; the soil History tab is a `flex min-h-0 flex-1 flex-col` panel that receives the remaining viewport height from the page shell, and its chart frame is `min-h-0 flex-1`, so the graph fills the component. A wrapper sized only by `min-height` without a definite flex parent collapses the uPlot frame to zero height.
- **Data table**: the shared `ChartDataTable` primitive ("View data as table", consuming the exact `AlignedData` the plot renders) renders only in dev/fixture builds (R6); production keeps the legend's hidden semantic table. The page does not maintain its own table markup.
- Beds render Back Bed above Front Bed; probe identity is the registry hardware address (`Soil probe #N`), and per-metric fresh/stale text uses `--mon-stale`.

## Responsive Layout

Chart regions and tables retain the existing single-column rule below `768px`; small-screen viewport QA is outside this pass.

### Dashboard viewport contract

The monitoring layout guidance above remains unchanged. Dashboard `/` uses a separate viewport layout:

- Dashboard `/` desktop QA covers 1920×1080 and 1280×1440: calendar and inspector share the upper center row, Flower/Veg/Lab cards occupy ordered rows below, the Event Log sits above the square water-tank graphic in the right rail, and Mothernode remains beneath the workspace.
- The existing 768–1099px compact dashboard layout is retained but is outside this pass's focused QA; the 960px redesign is deferred. The web app has no mobile drawer or supported mobile-layout guarantee, and a separate mobile app does not exist yet.
- At both supported desktop viewports the dashboard does not scroll the document or dashboard panels or overlap. Dialogs/popovers are transient overlays and may scroll independently.
- The dashboard calendar renders six rows in every month, keeps 44px minimum date targets, and preserves task, note, and phase marker counts in each date's accessible name.
- Desktop navigation uses a permanent 30px icon rail app-wide; there is no mobile drawer.

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

- A harness must exercise every primitive in isolation before product-page work. It renders a chart with left and right family axes, a min/max envelope, recorded vs projected targets with a "now" divider, a sun/moon overlay, a semantic table, and keyboard-focusable legend toggles. The harness asserts the configured exact fixture origin (`http://127.0.0.1:${MONITORING_FIXTURE_PORT}`, default port 4187; allowed ports 4187–4199) and route guard so no request leaves the loopback preview.

## Event Log Alert Console

Contract for the event-log feature (`src/features/event-log/`), which presents the operational event stream as a colour-coded alert console. The monitoring `--mon-*` namespace is NOT used here; the log declares its own additive `--event-*` tokens in `src/styles/themes.css`.

### Category palette

One hue per backend `EventCategory`, owner-approved. Tokens come in triplets: base hue, `-dim` chip background, `-border` under-border. Coverage is enforced by `src/features/event-log/__tests__/categoryTheme.test.ts` for every category in all six themes.

| Category                                  | Label           | Hue family |
| ----------------------------------------- | --------------- | ---------- |
| `relay`                                   | Relay           | green      |
| `manual_override`                         | Manual override | amber      |
| `ramp`                                    | Ramp            | teal       |
| `control`                                 | Control         | ice blue   |
| `mutation`                                | Mutation        | violet     |
| `alarm`                                   | Alarm           | red        |
| `system`                                  | System          | slate      |
| `sensor` (display-only split of `system`) | Sensors         | orange     |

The `Sensors` bucket is a frontend presentation split: `system`-category events of type `sensor.*` / `device.*` display in the orange Sensors bucket; platform events stay slate. Grouping and expansion use the display bucket; the category filter chips keep operating on the raw backend category, so a `system` filter shows System and Sensors rows.

- **Colour never alone (WCAG 1.4.1):** every coloured chip is paired with its text label; unknown categories resolve the "Other" fallback instead of crashing.
- **Relay-active emphasis:** relay events with an engaged state (`state: true` / `observed_state: true`) render the state text in the relay green shade (`categoryTheme.relayActiveStateClass`).
- **Severity honesty:** `critical` uses a filled badge plus a heavier left border; `error` keeps an outline treatment — the two never render identically.

### Views

- **Dashboard compact console:** grouped by default, with eight category summaries per page. Each two-line summary shows category/count/time then latest label/severity; visible group paging keeps categories beyond the first eight reachable.
- **Dashboard flat "All events" and expanded-category list:** five newest-first rows per page with visible status and disabled Previous/Next bounds. Filter, view, and category changes reset paging; live result shrink clamps it. A compact row opens an overlay with full recorded source, reason, timestamps, and safe payload, restoring focus on close.
- **Full-width room log:** remains flat by default with its established filters, full newest-first list, max-height scroll, and inline row details. Dashboard paging and detail dialogs do not change this view.

### Recorded reasons

- A flat row’s reason hover is scoped to that event and displays only its recorded `reason_text`; it never infers a cause.
- A grouped category’s reason help identifies the latest event in that category. Historical reasons are not current control authority.
- PID binary relay explanations use the recorded controller inputs/output and the evaluated hysteresis boundary. Show them only on successful changed-state `relay.commanded` events; failed, interlocked, manual, unchanged, and unmatched `relay.observed` events remain unattributed.

### Dual timestamps and from-to values

- Every row shows BOTH timestamps visibly: the relative chip ("3m ago") and a localized absolute clock (`formatLocalTime`: HH:MM:SS, with the date when not today). Title-attribute-only timestamps are not acceptable.
- Self-contained setpoint changes render "from → to" values on the source line via `formatSetpointFromTo`: lights as percent ("44.2% → 43.8%"), heating/cooling in °C, VPD in kPa, CO2 in ppm. Legacy payloads without `previous_setpoint` render no change text and no crash.
