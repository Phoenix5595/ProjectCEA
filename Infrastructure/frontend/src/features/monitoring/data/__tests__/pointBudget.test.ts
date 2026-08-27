import { describe, expect, it } from 'vitest'

import { decimateSeries, panelBudget, requestBudget, shouldReportBudget } from '../pointBudget'

describe('panelBudget', () => {
  it.each([
    [375, 375],
    [800, 800],
    [1_280, 1_280],
    [5, 10],
    [100_000, 50_000],
    [0, 10],
    [-100, 10],
    [Number.NaN, 10],
    [Number.POSITIVE_INFINITY, 50_000],
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
  it('reports the first and every distinct integer budget', () => {
    // Given: an initial report and adjacent integer budgets
    const first = shouldReportBudget(null, 800)
    const duplicate = shouldReportBudget(800, 800)

    // When: the rendered width advances one CSS pixel
    const next = shouldReportBudget(800, 801)

    // Then: only duplicate budgets are suppressed
    expect(first).toBe(true)
    expect(duplicate).toBe(false)
    expect(next).toBe(true)
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
    expect(emptyBudget).toBe(10)
    expect(aggregateBudget).toBe(1_200)
  })

  it('ignores non-positive and non-finite panel widths', () => {
    // Given: no valid rendered panel widths
    const widths = [Number.NaN, 0, -1]

    // When: deriving a shared range-request budget
    const budget = requestBudget(widths)

    // Then: the API-compatible floor is retained
    expect(budget).toBe(10)
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
