import { describe, expect, it } from 'vitest'

describe('uPlot honors enforced range functions', () => {
  it('temperature scale applies ten units of headroom', async () => {
    const { buildScales } = await import('../options/scales')

    const aligned = {
      x: [1e12, 1e12 + 60_000],
      series: [
        {
          key: 'sensor:dry_bulb_b:mean',
          label: 'dry_bulb_b',
          role: 'mean' as const,
          source: 'sensor' as const,
          family: 'temperature' as const,
          y: [22, null] as (number | null)[],
          quality: 'exact' as const,
          origin: 'recorded' as const,
        },
      ],
      bands: [],
      photoperiod: [],
      nowIndex: 0,
      aggregated: false,
    }
    const { scales, axes } = buildScales(aligned as never)
    expect(axes.find((a) => a.scale === 'temperature')?.side).toBe(3)

    expect(axes.find((axis) => axis.scale === 'temperature')?.side).toBe(3)
    const range = scales.temperature?.range
    if (typeof range !== 'function') throw new Error('Temperature range is required')
    expect(Reflect.apply(range, undefined, [undefined, 22, 25])).toEqual([12, 35])
  })
})
