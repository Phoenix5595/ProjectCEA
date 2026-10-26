/**
 * Soil bed heatmap (R9): inverse-distance interpolation of current and past
 * probe placements on a 32×32 grid over one bed.
 *
 * Current placements weigh 1; closed placements fade linearly to zero over
 * 72 h. Cell value = Σ wᵢvᵢ / Σ wᵢ with wᵢ = fᵢ / max(dᵢ, 0.1 ft)²; cell
 * confidence = maxᵢ fᵢ·exp(−dᵢ / 1 ft) drives the alpha, so colour fades
 * away from where probes were. Colour stops are fixed data colours
 * (theme-independent, like the SUN/MOON fills).
 */
import type { SoilMetricName } from './api/contracts'

export const HEATMAP_GRID = 32
export const HEATMAP_STALE_HOURS = 72
export const MIN_DISTANCE_FT = 0.1
export const CONFIDENCE_LENGTH_FT = 1
export const MAX_OPACITY = 0.85

const STALE_MS = HEATMAP_STALE_HOURS * 3_600_000

export const HEATMAP_STOPS: Record<SoilMetricName, readonly string[]> = {
  water_content: ['#8c510a', '#d8b365', '#f6e8c3', '#5ab4ac', '#01665e'],
  temperature: ['#2c7bb6', '#abd9e9', '#ffffbf', '#fdae61', '#d7191c'],
  ec: ['#ffffcc', '#a1dab4', '#41b6c4', '#2c7fb8', '#253494'],
  ph: ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641'],
}

export interface HeatPoint {
  readonly x_frac: number
  readonly y_frac: number
  readonly value: number
  readonly active: boolean
  readonly removed_at: Date | null
}

export interface BedGeometry {
  readonly width_ft: number
  readonly length_ft: number
}

/** 1 for an active placement, else a linear fade to 0 over 72 h since removal. */
export function freshness(point: HeatPoint, nowMs: number): number {
  if (point.active || point.removed_at === null) return 1
  return Math.max(0, 1 - (nowMs - point.removed_at.getTime()) / STALE_MS)
}

/** Interpolated value and confidence at a position in feet; `null` without points. */
export function interpolateAt(
  bed: BedGeometry,
  points: readonly HeatPoint[],
  nowMs: number,
  xFt: number,
  yFt: number
): { value: number; confidence: number } | null {
  let weighted = 0
  let weights = 0
  let confidence = 0
  for (const point of points) {
    const fresh = freshness(point, nowMs)
    if (fresh <= 0) continue
    const distance = Math.hypot(
      point.x_frac * bed.width_ft - xFt,
      point.y_frac * bed.length_ft - yFt
    )
    const weight = fresh / Math.max(distance, MIN_DISTANCE_FT) ** 2
    weighted += weight * point.value
    weights += weight
    confidence = Math.max(confidence, fresh * Math.exp(-distance / CONFIDENCE_LENGTH_FT))
  }
  return weights === 0 ? null : { value: weighted / weights, confidence }
}

function percentile(sorted: readonly number[], p: number): number {
  const index = p * (sorted.length - 1)
  const lo = Math.floor(index)
  const hi = Math.ceil(index)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (index - lo)
}

/** Colour domain: 5th–95th percentile; a single or flat value gets ±max(10 %, 0.1). */
export function colorDomain(values: readonly number[]): [number, number] | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const lo = percentile(sorted, 0.05)
  const hi = percentile(sorted, 0.95)
  if (sorted.length === 1 || lo === hi) {
    const pad = Math.max(0.1 * Math.abs(lo), 0.1)
    return [lo - pad, lo + pad]
  }
  return [lo, hi]
}

function hexToRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16)
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff]
}

/** Colour at `t` ∈ [0, 1] along the metric's stops (linear in RGB). */
export function colorAt(metric: SoilMetricName, t: number): [number, number, number] {
  const stops = HEATMAP_STOPS[metric]
  const scaled = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const index = Math.min(stops.length - 2, Math.floor(scaled))
  const local = scaled - index
  const from = hexToRgb(stops[index])
  const to = hexToRgb(stops[index + 1])
  return [0, 1, 2].map(channel =>
    Math.round(from[channel] + (to[channel] - from[channel]) * local)
  ) as [number, number, number]
}

/** CSS gradient for the legend. */
export function legendGradient(metric: SoilMetricName): string {
  return `linear-gradient(to right, ${HEATMAP_STOPS[metric].join(', ')})`
}

/** Paint one bed onto a 32×32 canvas context (one pixel per cell). */
export function paintHeatmap(
  ctx: CanvasRenderingContext2D,
  bed: BedGeometry,
  points: readonly HeatPoint[],
  metric: SoilMetricName,
  domain: [number, number] | null,
  nowMs: number
): void {
  ctx.clearRect(0, 0, HEATMAP_GRID, HEATMAP_GRID)
  if (domain === null || !points.some(point => freshness(point, nowMs) > 0)) return
  const [lo, hi] = domain
  const image = ctx.createImageData(HEATMAP_GRID, HEATMAP_GRID)
  for (let j = 0; j < HEATMAP_GRID; j += 1) {
    for (let i = 0; i < HEATMAP_GRID; i += 1) {
      const cell = interpolateAt(
        bed,
        points,
        nowMs,
        ((i + 0.5) / HEATMAP_GRID) * bed.width_ft,
        ((j + 0.5) / HEATMAP_GRID) * bed.length_ft
      )
      if (cell === null) continue
      const [r, g, b] = colorAt(metric, (cell.value - lo) / (hi - lo))
      const offset = (j * HEATMAP_GRID + i) * 4
      image.data[offset] = r
      image.data[offset + 1] = g
      image.data[offset + 2] = b
      image.data[offset + 3] = Math.round(255 * MAX_OPACITY * cell.confidence)
    }
  }
  ctx.putImageData(image, 0, 0)
}
