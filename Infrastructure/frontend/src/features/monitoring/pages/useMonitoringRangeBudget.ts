import { useCallback, useRef } from 'react'

import type { MonitoringStore } from '../state'

export type ChartBudgetKey = 'climate' | 'device'

export function useMonitoringRangeBudget(store: MonitoringStore): {
  readonly reportBudget: (key: ChartBudgetKey, budget: number) => void
} {
  const budgetsRef = useRef<Record<ChartBudgetKey, number | null>>({
    climate: null,
    device: null,
  })

  const reportBudget = useCallback((key: ChartBudgetKey, budget: number): void => {
    const previous = budgetsRef.current
    if (previous[key] === budget) return
    const previousMaximum = Math.max(previous.climate ?? 0, previous.device ?? 0)
    const next = { ...previous, [key]: budget }
    budgetsRef.current = next
    const nextMaximum = Math.max(next.climate ?? 0, next.device ?? 0)
    if (nextMaximum === previousMaximum || nextMaximum <= 0) return
    store.setRangeBudget(nextMaximum)
  }, [store])

  return { reportBudget }
}
