import { useCallback, useRef } from 'react'

import { requestBudget } from '../data'

export function useRequestBudgetReporter(
  onRequestBudgetChange?: (maxPoints: number) => void,
): (panelWidth: number) => void {
  const onRequestBudgetChangeRef = useRef(onRequestBudgetChange)
  onRequestBudgetChangeRef.current = onRequestBudgetChange
  const reportedRequestBudgetRef = useRef<number | null>(null)

  return useCallback((panelWidth: number): void => {
    const maxPoints = requestBudget([panelWidth])
    if (reportedRequestBudgetRef.current === maxPoints) return
    reportedRequestBudgetRef.current = maxPoints
    onRequestBudgetChangeRef.current?.(maxPoints)
  }, [])
}
