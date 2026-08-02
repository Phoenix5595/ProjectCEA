/**
 * Maps aligned series to uPlot series options.
 *
 * Each series gets a family scale, a family-derived stroke color, and a line
 * style: sensor means are solid, control targets (point/step/linear) are
 * dotted, and projected segments use the projected color at lower opacity with
 * the target dash. Min/max envelope edges are hidden (they only feed bands).
 */
import uPlot from 'uplot'
import type { AlignedData, AlignedSeries } from '../../data'
import { resolveFamily, type ChartFamily } from './family'
import { readToken } from './tokens'

/** True for the hidden min/max envelope edges that only feed bands. */
export function isEnvelopeSeries(series: AlignedSeries): boolean {
  return series.key.endsWith(':min') || series.key.endsWith(':max')
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
  if (series.origin === 'projected') return readToken('targetProjected')
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
      const projected = s.origin === 'projected'
      const color = projected ? readToken('targetProjected') : familyColor(family)
      const series: uPlot.Series = {
        label: s.label,
        scale: family,
        spanGaps: false,
        points: { show: false },
        stroke: color,
        width: 1.5,
      }
      if (isEnvelopeSeries(s)) {
        series.show = false
      }
      if (s.kind === 'point' || s.kind === 'step' || s.kind === 'linear') {
        series.dash = parseDash(readToken('targetDash'))
      }
      if (projected) {
        const opacity = parseFloat(readToken('targetProjectedOpacity'))
        series.stroke = withAlpha(color, Number.isFinite(opacity) ? opacity : 0.5)
      }
      return series
    }),
  ]
}
