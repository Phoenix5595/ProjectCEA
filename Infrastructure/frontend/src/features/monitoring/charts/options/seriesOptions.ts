import uPlot from 'uplot'
import type { AlignedData, AlignedSeries } from '../../data'
import { resolveFamily, type ChartFamily } from './family'
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
  }
}

/** The drawn color for a series (projected uses the projected token). */
export function seriesColor(series: AlignedSeries): string {
  // Manifest-declared color wins (per-series Grafana parity), then family token.
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
    .filter((part) => part.length > 0)
    .map(Number)
}

/** Apply an alpha channel to a hex color, leaving other formats untouched. */
function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith('#')) return color
  const hex = color.slice(1)
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** Build the full uPlot series array (index 0 is the time axis). */
export function buildSeries(data: AlignedData): uPlot.Series[] {
  return [
    { label: 'Time' },
    ...data.series.map((s) => {
      const family = resolveFamily(s)
      const target = s.source === 'climate' || s.source === 'light'
      const projected = target && s.origin === 'projected'
      const color = seriesColor(s)
      const series: uPlot.Series = {
        label: s.label,
        scale: family,
        // Sensor samples land on a coarse shared grid far apart; without gap
        // bridging each sample is an isolated single-point path (invisible).
        spanGaps: s.source === 'sensor',
        points: { show: false },
        stroke: color,
        width: s.presentation?.lineWidth ?? (target ? 2 : 1.5),
      }
      if (isEnvelopeSeries(s)) {
        series.show = false
      }
      if (target) {
        series.dash = s.presentation?.dash ? [...s.presentation.dash] : parseDash(readToken('targetDash'))
      }
      if (projected) {
        const opacity = parseFloat(readToken('targetProjectedOpacity'))
        series.stroke = withAlpha(color, Number.isFinite(opacity) ? opacity : 0.5)
      }
      return series
    }),
  ]
}
