/**
 * Shared x-axis construction for the alignment layer.
 *
 * Builds the sorted, unique timestamp grid, derives the visible window from
 * fixed or live ranges, and locates the current moment on the grid.
 */
import type { AlignInput } from './alignSeries.types'

export const DEFAULT_MAX_POINTS = 5000

/** Resolve the visible window from a fixed or live range relative to `now`. */
export function windowBounds(range: AlignInput['range'], now: Date): { start: number; end: number } {
  if (range.kind === 'fixed') return { start: range.start.getTime(), end: range.end.getTime() }
  return { start: now.getTime() - range.duration, end: now.getTime() }
}

/** Collect every candidate timestamp in the window, sorted and unique. */
export function collectTimestamps(
  input: AlignInput,
  start: number,
  end: number,
  now: number,
): number[] {
  const set = new Set<number>([start, end, now])
  for (const s of input.series) {
    for (const p of s.points) set.add(p.timestamp.getTime())
  }
  for (const resp of [input.controlHistory, input.projectionHistory]) {
    if (!resp) continue
    for (const cs of [...resp.climate, ...resp.lights]) {
      for (const p of cs.points) set.add(p.timestamp.getTime())
      for (const st of cs.steps) set.add(st.timestamp.getTime())
      for (const ln of cs.linear) {
        set.add(ln.start.getTime())
        set.add(ln.end.getTime())
      }
    }
    for (const ds of resp.devices) {
      for (const p of ds.points) set.add(p.timestamp.getTime())
    }
    for (const ps of resp.pid) {
      for (const p of ps.points) set.add(p.timestamp.getTime())
    }
  }
  for (const p of input.photoperiod) set.add(p.timestamp.getTime())
  return [...set].filter((t) => t >= start && t <= end).sort((a, b) => a - b)
}

/** Deterministic uniform grid of exactly `maxPoints` slots from start to end. */
export function coarsenedGrid(start: number, end: number, maxPoints: number): number[] {
  const n = Math.max(2, maxPoints)
  const x: number[] = []
  for (let i = 0; i < n; i++) {
    x.push(start + Math.round(((end - start) * i) / (n - 1)))
  }
  const out: number[] = []
  for (const t of x) if (out[out.length - 1] !== t) out.push(t)
  return out
}

/** Index of the largest x slot at or before `now`. */
export function indexOfNow(x: number[], now: number): number {
  let lo = 0
  let hi = x.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (x[mid] <= now) lo = mid
    else hi = mid - 1
  }
  return lo
}
