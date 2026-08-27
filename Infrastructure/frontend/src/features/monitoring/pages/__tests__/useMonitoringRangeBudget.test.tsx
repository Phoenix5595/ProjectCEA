import { act, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MonitoringApi } from '../../api'
import { MonitoringStore } from '../../state'
import { useMonitoringRangeBudget } from '../useMonitoringRangeBudget'

class RecordingMonitoringStore extends MonitoringStore {
  readonly reportedBudgets: number[] = []

  override setRangeBudget(maxPoints: number): void {
    this.reportedBudgets.push(maxPoints)
  }
}

describe('useMonitoringRangeBudget', () => {
  it('reports only changes to the widest chart budget without rerendering', () => {
    // Given: a mounted coordinator with an in-memory recording store
    const store = new RecordingMonitoringStore('flower', new MonitoringApi())
    let reportBudget: ((key: 'climate' | 'device', budget: number) => void) | null = null
    let renders = 0
    function Coordinator(): null {
      renders += 1
      reportBudget = useMonitoringRangeBudget(store).reportBudget
      return null
    }
    render(<Coordinator />)

    // When: the two charts report widths, then update their individual values
    act(() => {
      reportBudget?.('climate', 800)
      reportBudget?.('device', 1_200)
      reportBudget?.('climate', 800)
      reportBudget?.('climate', 600)
      reportBudget?.('device', 500)
    })

    // Then: only aggregate maxima reach the store and reports do not rerender React
    expect(store.reportedBudgets).toEqual([800, 1_200, 600])
    expect(renders).toBe(1)
  })
})
