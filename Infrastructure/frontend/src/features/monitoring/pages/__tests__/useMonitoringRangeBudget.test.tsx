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
  it('reports the minimum chart budget without rerendering', () => {
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

    // Then: the page budget is the minimum over mounted charts and reports do
    // not rerender React
    expect(store.reportedBudgets).toEqual([800, 600, 500])
    expect(renders).toBe(1)
  })

  it('seeds the first budget from the placeholder frame until a chart reports', () => {
    // Given: the first panel's frame placeholder, two 48 px y axes and padding
    const store = new RecordingMonitoringStore('flower', new MonitoringApi())
    let placeholderRef: ((node: HTMLElement | null) => void) | null = null
    function Coordinator(): null {
      placeholderRef = useMonitoringRangeBudget(store, { yAxisCount: 2 }).placeholderRef
      return null
    }
    render(<Coordinator />)
    const frame = document.createElement('div')
    Object.defineProperty(frame, 'clientWidth', { value: 1_200 })

    // When: the placeholder is attached before any chart exists
    act(() => {
      placeholderRef?.(frame)
    })

    // Then: the underestimated plot width releases the first range load
    expect(store.reportedBudgets).toEqual([1_088])

    // And an exact chart report takes over; the placeholder no longer flushes.
    act(() => {
      placeholderRef?.(null)
    })
    expect(store.reportedBudgets).toEqual([1_088])
  })
})
