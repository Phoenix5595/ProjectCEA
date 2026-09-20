/**
 * Builds the shared x time scale and per-family y scales/axes.
 *
 * Temperature is the only left-axis family; every other family renders on the
 * right. Scales are derived from the full series list (not visibility), so
 * hiding one series never removes its sibling family scale. Explicit canonical
 * bounds are preserved; families without one auto-range.
 */
import type uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { type ChartFamily, chartFamilyForUnit, resolveFamily } from './family'
import { familyColor } from './seriesOptions'

/** Hard visible bounds for families that always render inside a fixed window. */
const RANGE_BOUNDS: Partial<
  Record<ChartFamily, { min?: number; max?: number }>
> = {
  temperature: {},
  rh: { max: 100 },
  device: { min: 0, max: 100 },
  light: { min: 0, max: 100 },
  pressure: { min: 1012, max: 1014 },
}


/** uPlot's default tick pass stops at the largest clean value within
 *  [scaleMin, scaleMax]; when the ±5% headroom is unlabeled the axis visually
 *  stops at the data extreme. Snap the padded bounds outward to the next
 *  1/2/10 step so the extreme always sits BETWEEN ticks. */
function snapPaddedBounds(lo: number, hi: number): [number, number] {
  const span = Math.max(hi - lo, 1e-9)
  const magnitude = 10 ** Math.floor(Math.log10(span / 4))
  const step = magnitude * (span / 4 / magnitude <= 1 ? 1 : span / 4 / magnitude <= 2 ? 2 : span / 4 / magnitude <= 5 ? 5 : 10)
  return [Math.floor(lo / step) * step, Math.ceil(hi / step) * step]
}

function boundedRange(
  forcedMin?: number,
  forcedMax?: number,
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    const observedMin = typeof initMin === 'number' && Number.isFinite(initMin) ? initMin : undefined
    const observedMax = typeof initMax === 'number' && Number.isFinite(initMax) ? initMax : undefined
    let lo = observedMin ?? (forcedMin ?? 0)
    let hi = observedMax ?? (forcedMax ?? lo + 1)
    if (observedMin !== undefined && observedMax !== undefined) {
      const padding = observedMin === observedMax
        ? Math.max(Math.abs(observedMin) * 0.05, 1)
        : (observedMax - observedMin) * 0.05
      lo -= padding
      hi += padding
    }
    if (forcedMin !== undefined && lo > forcedMin) lo = forcedMin
    if (forcedMax !== undefined && hi < forcedMax) hi = forcedMax
    if (lo >= hi) hi = lo + 1
    const [snappedLo, snappedHi] = snapPaddedBounds(lo, hi)
    if (!(forcedMin !== undefined && snappedLo < forcedMin)) lo = snappedLo
    if (!(forcedMax !== undefined && snappedHi > forcedMax)) hi = snappedHi
    return [lo, hi]
  }
}

function softBoundedRange(
  softMin?: number,
  softMax?: number,
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    const hasMin = typeof initMin === 'number' && Number.isFinite(initMin)
    const hasMax = typeof initMax === 'number' && Number.isFinite(initMax)
    const observedMin = hasMin ? initMin : undefined
    const observedMax = hasMax ? initMax : undefined
    let lo = observedMin ?? (softMin ?? (observedMax !== undefined ? observedMax - 1 : 0))
    let hi = observedMax ?? (softMax ?? lo + 1)
    if (softMin !== undefined && lo > softMin) lo = softMin
    if (softMax !== undefined && hi < softMax) hi = softMax
    if (observedMin !== undefined && observedMax !== undefined) {
      const padding =
        observedMin === observedMax
          ? Math.max(Math.abs(observedMin) * 0.05, 1)
          : (observedMax - observedMin) * 0.05
      lo = Math.min(lo, observedMin - padding)
      hi = Math.max(hi, observedMax + padding)
    }
    if (lo >= hi) hi = lo + 1
    return [lo, hi]
  }
}

interface SoftBounds {
  softMin?: number
  softMax?: number
}

function familySoftBounds(data: AlignedData): Map<ChartFamily, SoftBounds> {
  const bounds = new Map<ChartFamily, SoftBounds>()
  for (const s of data.series) {
    const p = s.presentation
    if (p === undefined || (p.softMin === undefined && p.softMax === undefined)) continue
    const family = resolveFamily(s)
    const existing = bounds.get(family)
    const next: SoftBounds = {
      softMin: p.softMin !== undefined ? Math.min(p.softMin, existing?.softMin ?? p.softMin) : existing?.softMin,
      softMax: p.softMax !== undefined ? Math.max(p.softMax, existing?.softMax ?? p.softMax) : existing?.softMax,
    }
    bounds.set(family, next)
  }
  return bounds
}

function defaultFamily(data: AlignedData): ChartFamily | undefined {
  const unit = data.scaleDefaults?.unit
  if (unit === undefined) return undefined
  return chartFamilyForUnit(unit)
}

export interface ChartScales {
  scales: uPlot.Scales
  axes: uPlot.Axis[]
}

function readTokenXStroke(): string {
  if (typeof document === 'undefined') return 'rgba(200, 214, 194, 0.9)'
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

  const seriesBounds = familySoftBounds(data)
  const defaultFamilyForUnit = defaultFamily(data)

  const AXIS_SIZES: Partial<Record<ChartFamily, number>> = { temperature: 40, vpd: 40 }

  for (const family of families) {
    // auto:true is required for uPlot to rescale user-defined value scales;
    // the bounded range function then clamps the proposed extent.
    const scale: uPlot.Scale = { auto: true }
    const soft = seriesBounds.get(family)
    if (soft?.softMin !== undefined || soft?.softMax !== undefined) {
      scale.range = softBoundedRange(soft.softMin, soft.softMax)
    } else if (defaultFamilyForUnit === family) {
      const d = data.scaleDefaults
      if (d?.softMin !== undefined || d?.softMax !== undefined) {
        scale.range = softBoundedRange(d.softMin, d.softMax)
      }
    }
    if (scale.range === undefined) {
      const bounds = RANGE_BOUNDS[family]
      scale.range = boundedRange(bounds?.min, bounds?.max)
    }
    if (family === 'vpd') {
      const inner = scale.range as NonNullable<uPlot.Scale['range']>
      const innerFn = typeof inner === 'function'
        ? inner
        : (_s: uPlot, a: number, b: number) => [a, b] as uPlot.Range.MinMax
      scale.range = (self, dataMin, dataMax, scaleKey) => {
        const [lo, hi] = innerFn(self, dataMin, dataMax, scaleKey)
        return [lo === null ? null : Math.max(0, lo), hi === null ? null : Math.max(0, hi)]
      }
    }
    scales[family] = scale

    const isTemperature = family === 'temperature'
    axes.push({
      scale: family,
      side: isTemperature ? 3 : 1,
      stroke: familyColor(family),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: familyColor(family) },
      size: AXIS_SIZES[family] ?? 48,
    })
  }

  return { scales, axes }
}
