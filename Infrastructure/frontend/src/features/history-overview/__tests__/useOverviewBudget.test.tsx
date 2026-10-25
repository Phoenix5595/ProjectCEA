import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useOverviewBudget } from '../useOverviewBudget'

describe('useOverviewBudget', () => {
  it('keeps the published budget while chart re-reports stay within 10 %', () => {
    const { result } = renderHook(() => useOverviewBudget(2))

    act(() => result.current.reportBudget('climate', 1718))
    expect(result.current.budget).toBe(1718)

    // Remounted charts re-report slightly different widths after each load;
    // none of them may trigger another request.
    for (const width of [1693, 1718, 1693, 1718]) {
      act(() => result.current.reportBudget('device', width))
      expect(result.current.budget).toBe(1718)
    }
  })

  it('publishes the smallest chart width once it moves by more than 10 %', () => {
    const { result } = renderHook(() => useOverviewBudget(2))

    act(() => result.current.reportBudget('climate', 1700))
    act(() => result.current.reportBudget('device', 1200))
    expect(result.current.budget).toBe(1200)
    act(() => result.current.reportBudget('device', 5000))
    expect(result.current.budget).toBe(1700)
  })
})
