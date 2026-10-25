import uPlot from 'uplot'

import type { AlignedData, AlignedSeries } from '../../data'
import { colorWithAlpha } from './colors'
import { lightTrajectoryPaths } from './lightTrajectoryPaths'

import { resolveFamily, type ChartFamily } from './family'
import { chartDataWithBands } from './sensorBands'
import { readToken } from './tokens'

export function isEnvelopeSeries(series: AlignedSeries): boolean {
  return series.role === 'min' || series.role === 'max'
}

/** The family token color for a chart family. */
export function familyColor(family: ChartFamily): string {
  switch (family) {
    case 'temperature':
      return readToken('familyTemperature')
    case 'rh':
      return readToken('familyRh')
    case 'vpd':
      return readToken('familyVpd')
    case 'co2':
      return readToken('familyCo2')
    case 'pressure':
      return readToken('familyPressure')
    case 'device':
      return readToken('familyDevice')
    case 'light':
      return readToken('familyLight')
    case 'water_content':
      return readToken('familyWaterContent')
    case 'conductivity':
      return readToken('familyConductivity')
    case 'ph':
      return readToken('familyPh')
  }
}

/** The drawn color for a series (projected uses the projected token). */
export function seriesColor(series: AlignedSeries): string {
  // Manifest-declared color wins (per-series parity), then family token.
  if (series.presentation?.color) return series.presentation.color
  if (series.source === 'climate' || series.source === 'light') {
    return series.origin === 'projected'
      ? readToken('targetProjected')
      : readToken('targetRecorded')
  }
  return familyColor(resolveFamily(series))
}

/** Parse a CSS dash string like "4 4" into a uPlot dash array. */
function parseDash(value: string): number[] {
  return value
    .split(/\s+/)
    .filter(part => part.length > 0)
    .map(Number)
}

/** Build the full uPlot series array (index 0 is the time axis). */
export function buildSeries(data: AlignedData, getData?: () => AlignedData): uPlot.Series[] {
  const expanded = chartDataWithBands(data).data
  const getCurrentData = getData ?? (() => data)
  return [
    { label: 'Time' },
    ...expanded.series.map(s => {
      const family = resolveFamily(s)
      const target = s.source === 'climate' || s.source === 'light'
      const projected = target && s.origin === 'projected'
      const color = seriesColor(s)
      const series: uPlot.Series = {
        label: s.label,
        scale: family,
        // Sensor samples land on a coarse shared grid far apart; without gap
        // bridging each sample is an isolated single-point path (invisible).
        spanGaps: s.source === 'sensor' && s.preserveGaps !== true,
        points: s.preserveGaps === true ? { show: true, size: 2 } : { show: false },
        stroke: color,
        // Sensor and light lines are 1 px (owner decision 2026-10-25);
        // climate targets keep 2 px dotted.
        width: s.presentation?.lineWidth ?? (s.source === 'climate' ? 2 : 1),
      }
      if (s.source === 'light' && s.lightTrajectory !== undefined) {
        series.paths = lightTrajectoryPaths(s.key, getCurrentData)
      } else if (s.kind === 'step') {
        const stepped = uPlot.paths.stepped
        if (stepped === undefined) throw new Error('uPlot stepped path renderer is unavailable')
        series.paths = stepped({ align: 1 })
      }
      if (isEnvelopeSeries(s)) {
        // Min/max boundaries only shape the band fill. Canvas ignores a zero
        // lineWidth (it keeps the previous width), so a transparent stroke is
        // what actually hides the boundary lines.
        series.width = 0
        series.stroke = 'rgba(0, 0, 0, 0)'
      }
      // Sensor-source series may declare a line pattern (soil: Water content
      // solid, EC dashed, pH dotted, temperature dash-dot) without touching
      // the target-series behavior above.
      if (!target && s.presentation?.dash) {
        const dash = [...s.presentation.dash]
        if (dash[0] === 0) {
          series.dash = [1, dash[1] ?? 5]
          series.cap = 'round'
        } else {
          series.dash = dash
        }
      }
      // Climate targets keep the token dash and projected alpha. The light
      // renderer owns per-segment styling (solid recorded, dotted projected)
      // so light traces never take a whole-series dash or projected alpha;
      // the dotted pattern is the only forecast marker on a mixed light trace.
      if (target && s.source !== 'light') {
        const dash = s.presentation?.dash
          ? [...s.presentation.dash]
          : parseDash(readToken('targetDash'))
        if (dash[0] === 0) {
          // uPlot draws a zero-length dash (dot style) as nothing; give dots a
          // 1px dash with round caps so dot-style setpoint lanes stay visible.
          series.dash = [1, dash[1] ?? 5]
          series.cap = 'round'
        } else {
          series.dash = dash
        }
        if (projected) {
          const opacity = parseFloat(readToken('targetProjectedOpacity'))
          series.stroke = colorWithAlpha(color, Number.isFinite(opacity) ? opacity : 0.5)
        }
      }
      return series
    }),
  ]
}
