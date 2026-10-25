import { useCallback, useRef } from 'react'

import type { MonitoringStore } from '../state'

export type ChartBudgetKey = 'climate' | 'device'

const Y_AXIS_WIDTH_PX = 48
const FRAME_PADDING_PX = 16

export interface RangeBudgetOptions {
  /** y-axis count of the placeholder panel; each axis is at most 48 px wide. */
  readonly yAxisCount?: number
}

export interface MonitoringRangeBudget {
  /**
   * Report one chart's exact plot-area budget (points = CSS pixels). The page
   * budget is the minimum over every mounted chart, so no line exceeds one
   * point per pixel.
   */
  readonly reportBudget: (key: ChartBudgetKey, budget: number) => void
  /** Attach to the first panel's `.mon-chart__frame` placeholder. */
  readonly placeholderRef: (node: HTMLElement | null) => void
}

/**
 * Page-level R1 budget coordinator.
 *
 * Before any chart has a plot area (and therefore before the first range load
 * can start), the first panel's frame placeholder is measured: frame width
 * minus the at-most-48 px y axes and the frame padding. That underestimates
 * the plot width, so the first request can only be too small, never too
 * large. Exact chart reports then take over.
 */
export function useMonitoringRangeBudget(
  store: MonitoringStore,
  options: RangeBudgetOptions = {}
): MonitoringRangeBudget {
  const budgetsRef = useRef<Record<ChartBudgetKey, number | null>>({
    climate: null,
    device: null,
  })
  const provisionalRef = useRef<number | null>(null)
  const reportedRef = useRef<number | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const yAxisCount = options.yAxisCount ?? 1

  const flush = useCallback((): void => {
    const measured = [budgetsRef.current.climate, budgetsRef.current.device].filter(
      (value): value is number => value !== null
    )
    const budget = measured.length > 0 ? Math.min(...measured) : provisionalRef.current
    if (budget === null || budget === reportedRef.current) return
    reportedRef.current = budget
    store.setRangeBudget(budget)
  }, [store])

  const placeholderRef = useCallback(
    (node: HTMLElement | null): void => {
      observerRef.current?.disconnect()
      observerRef.current = null
      if (node === null) return
      const measure = (): void => {
        if (node.clientWidth <= 0) return
        provisionalRef.current = Math.max(
          10,
          Math.floor(node.clientWidth - Y_AXIS_WIDTH_PX * yAxisCount - FRAME_PADDING_PX)
        )
        if (budgetsRef.current.climate === null && budgetsRef.current.device === null) flush()
      }
      const observer = new ResizeObserver(measure)
      observer.observe(node)
      observerRef.current = observer
      measure()
    },
    [flush, yAxisCount]
  )

  const reportBudget = useCallback(
    (key: ChartBudgetKey, budget: number): void => {
      if (!Number.isFinite(budget)) return
      const normalized = Math.max(1, Math.floor(budget))
      if (budgetsRef.current[key] === normalized) return
      budgetsRef.current = { ...budgetsRef.current, [key]: normalized }
      flush()
    },
    [flush]
  )

  return { reportBudget, placeholderRef }
}
