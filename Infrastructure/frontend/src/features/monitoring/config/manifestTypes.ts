/**
 * Typed monitoring manifest contracts.
 *
 * A manifest is the frontend's read-only contract for a room's monitoring
 * dashboard, extracted from the canonical Grafana dashboard JSON
 * (`Infrastructure/iskra_stack/dashboards/{flower,veg}_sector/*.json`). It
 * captures the panel order, series names, display names, units, explicit soft
 * bounds, colors, line styles, and table semantics that the native uPlot
 * dashboards must reproduce.
 *
 * The parity test (`__tests__/canonicalManifestParity.test.ts`) maps every
 * canonical panel/series/row field onto these manifests and fails loudly on
 * unknown additions, so the manifest can never silently drift from the
 * deployed Grafana contract.
 */

import type { UnitFamily } from '../api'

export type PanelKind = 'table' | 'timeseries'

/** A single series on a timeseries panel. */
export interface SeriesSpec {
  /** Canonical metric/series name as it appears in the dashboard JSON. */
  name: string
  /** Human display name (from the canonical `displayName` override). */
  displayName: string
  /** Unit family: celsius, percent, kpa, ppm, hpa, mm. */
  unit: string
  /** Axis placement. Temperature is left; other families are right/hidden. */
  axisPlacement?: 'auto' | 'right' | 'hidden'
  /** Explicit soft bounds (from `custom.axisSoftMin` / `axisSoftMax`). */
  softMin?: number
  softMax?: number
  /** Fixed color (hex or Grafana named color). */
  color?: string
  /** Line style fill (solid / dot / dash). */
  lineStyle?: 'solid' | 'dot' | 'dash'
  lineWidth?: number
  drawStyle?: 'line' | 'bars'
  lineInterpolation?: 'linear' | 'stepBefore' | 'stepAfter'
  decimals?: number
}

/** A timeseries panel (uPlot chart region). */
export interface TimeseriesPanelSpec {
  kind: 'timeseries'
  id: string
  title: string
  sources: Array<'sensor' | 'climate' | 'light' | 'device' | 'pid'>
  families: Array<'temperature' | 'rh' | 'vpd' | 'co2' | 'pressure' | 'device' | 'light'>
  /** Panel-level defaults (unit, soft bounds) applied to the whole panel. */
  defaults?: {
    unit?: UnitFamily
    softMin?: number
    softMax?: number
  }
  series: SeriesSpec[]
}

/** A table panel (semantic HTML table / current-value table). */
export interface TablePanelSpec {
  kind: 'table'
  id: string
  title: string
  /** Column headers in display order. */
  columns: string[]
  /** Ordered row labels (sensor names). */
  rows: string[]
  /**
   * Unit rows the canonical table renders beneath each sensor row (e.g. `°C`,
   * `%`, ` kPa`). The parity test treats these as mapped rather than requiring
   * them in `rows`.
   */
  units?: string[]
  /** Whether the table appends a "Last Update" row. */
  hasLastUpdate: boolean
}

export type MonitoringPanel = TimeseriesPanelSpec | TablePanelSpec

/** A room's full monitoring manifest. */
export interface MonitoringManifest {
  room: string
  /** Sensor URL slug(s) per the cluster topology contract. */
  sensorClusters: string[]
  panels: MonitoringPanel[]
}
