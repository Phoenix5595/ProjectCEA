/**
 * R1 request budget for the fixed-range history overview.
 *
 * One overview read serves both panels, so the page budget is the smallest
 * mounted chart plot width — never more than one requested point per CSS
 * pixel. Before the first chart exists, the first panel's frame placeholder is
 * measured: frame width minus its y-axis gutters (≤ 48 px each) and the 16 px
 * plot padding, which underestimates rather than overestimates the plot.
 *
 * Like the Monitoring store, a published budget changes only when the new one
 * differs by more than 10 %. Charts remount on every reload and their axis
 * gutters depend on the loaded data, so exact re-reports must not re-request
 * (otherwise two panels whose widths differ by a few pixels reload forever).
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { requestBudget } from '../monitoring/data'

/** Relative budget change that justifies a new overview request. */
const BUDGET_CHANGE_THRESHOLD = 0.1

export interface OverviewBudget {
  /** Null until the placeholder or a chart has been measured. */
  readonly budget: number | null
  readonly placeholderRef: (element: HTMLElement | null) => void
  readonly reportBudget: (key: string, plotWidthCssPx: number) => void
}

export function useOverviewBudget(yAxisCount: number): OverviewBudget {
  const [budget, setBudget] = useState<number | null>(null)
  const exactByChartRef = useRef(new Map<string, number>())
  const estimateRef = useRef<number | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)

  const publish = useCallback(() => {
    const exact = [...exactByChartRef.current.values()]
    const next = exact.length > 0 ? Math.min(...exact) : estimateRef.current
    setBudget(previous => {
      if (previous === null || next === null) return next ?? previous
      return Math.abs(next - previous) / previous > BUDGET_CHANGE_THRESHOLD ? next : previous
    })
  }, [])

  const placeholderRef = useCallback(
    (element: HTMLElement | null) => {
      observerRef.current?.disconnect()
      observerRef.current = null
      if (element === null) return
      const measure = (): void => {
        const width = element.clientWidth
        if (width <= 0) return
        estimateRef.current = requestBudget(width - 48 * yAxisCount - 16)
        publish()
      }
      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(measure)
        observer.observe(element)
        observerRef.current = observer
      }
      measure()
    },
    [publish, yAxisCount]
  )

  const reportBudget = useCallback(
    (key: string, plotWidthCssPx: number) => {
      exactByChartRef.current.set(key, requestBudget(plotWidthCssPx))
      publish()
    },
    [publish]
  )

  useEffect(() => () => observerRef.current?.disconnect(), [])

  return { budget, placeholderRef, reportBudget }
}
