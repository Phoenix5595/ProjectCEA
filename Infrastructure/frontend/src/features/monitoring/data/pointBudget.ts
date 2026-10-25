/**
 * Stable monitoring point budgets derived only from rendered width.
 *
 * R1: one point per CSS pixel of the plot area, never more than
 * `MAX_POINTS_PER_LINE`. The page budget is the minimum over every mounted
 * chart (see `pages/useMonitoringRangeBudget.ts`), so no displayed line can
 * exceed one point per pixel.
 */
export const MAX_POINTS_PER_LINE = 4_000
export const MIN_BUDGET = 10

interface PointRun {
  readonly start: number
  readonly length: number
}

/** Budget for one plot area: one point per CSS pixel, bounded to 10–4000. */
export function requestBudget(plotWidthCssPx: number): number {
  if (Number.isNaN(plotWidthCssPx)) return MIN_BUDGET
  return Math.min(MAX_POINTS_PER_LINE, Math.max(MIN_BUDGET, Math.floor(plotWidthCssPx)))
}

/**
 * Evenly decimate each contiguous value run without removing explicit null
 * separators. A budget too small for all run endpoints grows only as needed to
 * preserve the topology that prevents uPlot from drawing across real gaps.
 */
export function decimateSeries<T>(points: readonly T[], budget: number): T[] {
  const runs: PointRun[] = []
  let runStart = -1

  for (let index = 0; index < points.length; index++) {
    if (points[index] === null) {
      if (runStart >= 0) {
        runs.push({ start: runStart, length: index - runStart })
        runStart = -1
      }
    } else if (runStart < 0) {
      runStart = index
    }
  }
  if (runStart >= 0) runs.push({ start: runStart, length: points.length - runStart })

  const valueCount = runs.reduce((count, run) => count + run.length, 0)
  const normalizedBudget = Number.isFinite(budget)
    ? Math.max(0, Math.floor(budget))
    : Number.isNaN(budget)
      ? 0
      : valueCount
  if (valueCount <= normalizedBudget) return [...points]

  const endpointCount = runs.reduce((count, run) => count + Math.min(run.length, 2), 0)
  const targetCount = Math.max(endpointCount, normalizedBudget)
  const interiorCount = valueCount - endpointCount
  const extraPoints = targetCount - endpointCount
  const runBudgets = runs.map(run => Math.min(run.length, 2))
  let distributed = 0

  if (interiorCount > 0) {
    for (let index = 0; index < runs.length; index++) {
      const run = runs[index]
      const runBudget = runBudgets[index]
      if (run === undefined || runBudget === undefined) continue
      const extra = Math.floor((extraPoints * Math.max(run.length - 2, 0)) / interiorCount)
      runBudgets[index] = runBudget + extra
      distributed += extra
    }
  }

  for (let index = 0; distributed < extraPoints; index = (index + 1) % runs.length) {
    const run = runs[index]
    const runBudget = runBudgets[index]
    if (run !== undefined && runBudget !== undefined && runBudget < run.length) {
      runBudgets[index] = runBudget + 1
      distributed += 1
    }
  }

  const selected = new Set<number>()
  for (let index = 0; index < runs.length; index++) {
    const run = runs[index]
    const runBudget = runBudgets[index]
    if (run === undefined || runBudget === undefined) continue
    if (runBudget === 1) {
      selected.add(run.start)
      continue
    }
    for (let pointIndex = 0; pointIndex < runBudget; pointIndex++) {
      const offset = Math.floor((pointIndex * (run.length - 1)) / (runBudget - 1))
      selected.add(run.start + offset)
    }
  }

  return points.filter((point, index) => point === null || selected.has(index))
}
