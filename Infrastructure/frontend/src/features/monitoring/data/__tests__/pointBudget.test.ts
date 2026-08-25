import { describe, expect, it } from 'vitest'

import { decimateSeries, panelBudget, requestBudget, shouldReportBudget } from '../pointBudget'

describe('panelBudget', () => {
  it.each([
    [0, 500],
    [250, 500],
    [1_000, 2_000],
    [3_000, 6_000],
    [20_000, 20_000],
  ])('maps width %d to budget %d', (width, expected) => {
    expect(panelBudget(width)).toBe(expected)
  })

  it('never lowers a budget as rendered width grows', () => {
    // Given: an ordered range of possible panel widths
    const widths: number[] = []
    for (let index = 0; index <= 80; index++) widths.push(index * 125)

    // When: deriving their budgets
    const budgets = widths.map(panelBudget)

    // Then: every wider panel has an equal or larger budget
    for (let index = 1; index < budgets.length; index++) {
      expect(budgets[index]).toBeGreaterThanOrEqual(budgets[index - 1] ?? 0)
    }
  })
})

describe('shouldReportBudget', () => {
  it('suppresses changes below ten percent', () => {
    expect(shouldReportBudget(2_000, 2_100)).toBe(false)
  })

  it('reports changes at or above ten percent', () => {
    expect(shouldReportBudget(2_000, 2_220)).toBe(true)
  })

  it('reports transitions across either clamp boundary', () => {
    expect(shouldReportBudget(500, 502)).toBe(true)
    expect(shouldReportBudget(19_998, 20_000)).toBe(true)
  })
})

describe('requestBudget', () => {
  it('uses the widest rendered panel while retaining a bounded empty fallback', () => {
    // Given: no panels, then two independently rendered panels
    const widths = [300, 1_200]

    // When: deriving their aggregate range-request budgets
    const emptyBudget = requestBudget([])
    const aggregateBudget = requestBudget(widths)

    // Then: one shared range request serves the widest panel's needs
    expect(emptyBudget).toBe(500)
    expect(aggregateBudget).toBe(panelBudget(1_200))
  })
})

describe('decimateSeries', () => {
  it('preserves the first and last non-gap points while striding intermediate values', () => {
    // Given: a dense continuous series
    const points = [0, 1, 2, 3, 4, 5]

    // When: reducing it to three display points
    const result = decimateSeries(points, 3)

    // Then: endpoints remain and interior values are evenly spaced
    expect(result).toEqual([0, 2, 5])
  })

  it('retains every null separator and never bridges independent value runs', () => {
    // Given: three real-data runs separated by explicit gaps
    const points = [0, 1, 2, null, 3, 4, 5, 6, null, 7, 8]

    // When: decimating with only enough capacity for run endpoints
    const result = decimateSeries(points, 6)

    // Then: each null boundary and every run endpoint survives
    expect(result).toEqual([0, 2, null, 3, 6, null, 7, 8])
  })
})
