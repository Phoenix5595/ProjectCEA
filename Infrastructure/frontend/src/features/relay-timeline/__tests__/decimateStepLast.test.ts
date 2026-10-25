import { describe, expect, it } from 'vitest'

import { decimateStepLast } from '../decimateStepLast'

describe('decimateStepLast', () => {
  it('keeps every sample when the line already fits the plot columns', () => {
    const result = decimateStepLast([0, 10, 20], [1, 2, 3], 5)
    expect(result).toEqual({ xs: [0, 10, 20], ys: [1, 2, 3] })
  })

  it('keeps the last sample of each pixel column', () => {
    // Given: eight samples over four columns (two samples per column)
    const result = decimateStepLast(
      [0, 10, 20, 30, 40, 50, 60, 70],
      [10, 20, 30, 40, 50, 60, 70, 80],
      4
    )
    // Then: the second (latest) sample of each column survives
    expect(result).toEqual({
      xs: [10, 30, 50, 70],
      ys: [20, 40, 60, 80],
    })
  })

  it('never returns more points than columns', () => {
    const xs = Array.from({ length: 500 }, (_, index) => index)
    const ys = xs.map(index => index % 100)
    const result = decimateStepLast(xs, ys, 120)
    expect(result.xs.length).toBeLessThanOrEqual(120)
  })

  it('handles a flat x axis without dividing by zero', () => {
    const result = decimateStepLast([5, 5, 5], [1, 2, 3], 1)
    expect(result).toEqual({ xs: [5], ys: [3] })
  })
})
