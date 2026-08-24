/**
 * Builds the shared x time scale and per-family y scales/axes.
 *
 * Temperature is the only left-axis family; every other family renders on the
 * right. Scales are derived from the full series list (not visibility), so
 * hiding one series never removes its sibling family scale. Explicit canonical
 * bounds are preserved; families without one auto-range.
 */
import uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { resolveFamily, type ChartFamily } from './family'
import { familyColor } from './seriesOptions'

/** Canonical visible bounds per family (see DESIGN.md section 2). */
const RANGE_BOUNDS: Partial<
  Record<ChartFamily, { min?: number; max?: number; padEachSide?: number }>
> = {
  temperature: { padEachSide: 10 },
  rh: { max: 100 },
  device: { min: 0, max: 100 },
  light: { min: 0, max: 100 },
  pressure: { min: 1012, max: 1014 },
}

function boundedRange(
  forcedMin?: number,
  forcedMax?: number,
  padEachSide?: number,
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    let lo =
      typeof initMin === 'number' && Number.isFinite(initMin) ? initMin : (forcedMin ?? 0)
    let hi =
      typeof initMax === 'number' && Number.isFinite(initMax) ? initMax : (forcedMax ?? lo + 1)
    if (padEachSide !== undefined && Number.isFinite(lo) && Number.isFinite(hi)) {
      lo -= padEachSide
      hi += padEachSide
    }
    if (forcedMin !== undefined && lo > forcedMin) lo = forcedMin
    if (forcedMax !== undefined && hi < forcedMax) hi = forcedMax
    if (lo >= hi) hi = lo + 1
    return [lo, hi]
  }
}

export interface ChartScales {
  scales: uPlot.Scales
  axes: uPlot.Axis[]
}

function readTokenXStroke(): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue('--text-muted')
    .trim()
  return value === '' ? 'rgba(200, 214, 194, 0.9)' : value
}

/** Build scales and axes for every family present in the data. */
export function buildScales(data: AlignedData): ChartScales {
  const families = new Set<ChartFamily>()
  for (const s of data.series) families.add(resolveFamily(s))

  const scales: uPlot.Scales = { x: { time: true } }
  const axes: uPlot.Axis[] = [
    {
      scale: 'x',
      stroke: readTokenXStroke(),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: 'rgba(128, 128, 128, 0.25)' },
    },
  ]

  for (const family of families) {
    // auto:true is required for uPlot to rescale user-defined value scales;
    // the bounded range function then clamps the proposed extent.
    const scale: uPlot.Scale = { auto: true }
    const bounds = RANGE_BOUNDS[family]
    if (bounds !== undefined) scale.range = boundedRange(bounds.min, bounds.max, bounds.padEachSide)
    scales[family] = scale

    const isTemperature = family === 'temperature'
    axes.push({
      scale: family,
      side: isTemperature ? 3 : 1,
      stroke: familyColor(family),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: familyColor(family) },
      size: 48,
    })
  }

  return { scales, axes }
}
