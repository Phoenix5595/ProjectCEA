import { describe, expect, it } from 'vitest'

import { colorDomain, freshness, interpolateAt, type HeatPoint } from '../heatmap'

const BED = { width_ft: 4, length_ft: 4 }
const NOW = Date.parse('2026-10-26T12:00:00Z')

function point(overrides: Partial<HeatPoint> = {}): HeatPoint {
  return { x_frac: 0.5, y_frac: 0.5, value: 30, active: true, removed_at: null, ...overrides }
}

describe('soil heatmap interpolation', () => {
  it('spreads a single point as a uniform value', () => {
    const points = [point({ value: 41 })]
    for (const [x, y] of [
      [0.1, 0.1],
      [2, 2],
      [3.9, 0.4],
    ] as const) {
      expect(interpolateAt(BED, points, NOW, x, y)?.value).toBeCloseTo(41)
    }
  })

  it('halves the confidence ln 2 ft away from a current point', () => {
    const cell = interpolateAt(BED, [point()], NOW, 2 + Math.LN2, 2)
    expect(cell?.confidence).toBeCloseTo(0.5)
  })

  it('weights the nearer point more', () => {
    const points = [point({ x_frac: 0.25, value: 20 }), point({ x_frac: 0.75, value: 40 })]
    const cell = interpolateAt(BED, points, NOW, 1.5, 2)
    expect(cell?.value).toBeGreaterThan(20)
    expect(cell?.value).toBeLessThan(30)
  })

  it('fades a closed point linearly over 72 h', () => {
    const removed = (hours: number) =>
      point({ active: false, removed_at: new Date(NOW - hours * 3_600_000) })
    expect(freshness(removed(36), NOW)).toBeCloseTo(0.5)
    expect(freshness(removed(80), NOW)).toBe(0)
    expect(freshness(point(), NOW)).toBe(1)
    expect(interpolateAt(BED, [removed(80)], NOW, 2, 2)).toBeNull()
  })
})

describe('soil heatmap colour domain', () => {
  it('uses the 5th–95th percentile with linear interpolation', () => {
    const values = Array.from({ length: 21 }, (_, index) => index * 10)
    expect(colorDomain(values)).toEqual([10, 190])
    expect(colorDomain([0, 100])).toEqual([5, 95])
  })

  it('pads a single or flat value', () => {
    expect(colorDomain([40])).toEqual([36, 44])
    expect(colorDomain([0.5, 0.5])).toEqual([0.4, 0.6])
    expect(colorDomain([])).toBeNull()
  })
})
