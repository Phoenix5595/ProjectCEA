import { describe, expect, it } from 'vitest'

import { decimateSeries, MAX_POINTS_PER_LINE, requestBudget } from '../pointBudget'

describe('requestBudget', () => {
  it.each([
    [375, 375],
    [800, 800],
    [1_280, 1_280],
    [4_000, 4_000],
    [5, 10],
    [100_000, 4_000],
    [0, 10],
    [-100, 10],
    [Number.NaN, 10],
    [Number.POSITIVE_INFINITY, 4_000],
    [1_800.9, 1_800],
  ])('maps plot width %d to budget %d', (width, expected) => {
    expect(requestBudget(width)).toBe(expected)
  })

  it('never exceeds the 4000-point per-line maximum', () => {
    // Given: any plot wider than the point ceiling
    const widths = [4_001, 8_000, 20_000, MAX_POINTS_PER_LINE * 10]

    // When/Then: R1 caps every displayed line at the constant
    for (const width of widths) expect(requestBudget(width)).toBe(MAX_POINTS_PER_LINE)
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
