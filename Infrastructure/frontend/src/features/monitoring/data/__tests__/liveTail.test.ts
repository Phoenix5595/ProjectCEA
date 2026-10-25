import { describe, expect, it } from 'vitest'

import { LiveTailBuffer } from '../liveTail'

describe('LiveTailBuffer', () => {
  it('replaces the retained point inside one bucket instead of appending', () => {
    // Given: one-second buckets and two samples of the same bucket
    const buffer = new LiveTailBuffer(1_000)

    // When: the second sample of bucket 1 arrives
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_000, value: 24.1 })
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_600, value: 24.4 })

    // Then: only the newest value of that bucket survives
    expect(buffer.pointsFor('dry_bulb')).toEqual([{ t: 1_600, v: 24.4 }])
  })

  it('ignores a sample whose Redis observation timestamp did not advance', () => {
    // Given: a retained sample
    const buffer = new LiveTailBuffer(1_000)
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_000, value: 24.1 })

    // When: the poll repeats the same instant with a new value
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_000, value: 99 })

    // Then: the tail keeps the first observation
    expect(buffer.pointsFor('dry_bulb')).toEqual([{ t: 1_000, v: 24.1 }])
  })

  it('drops raw points covered by a completed recorded bucket', () => {
    // Given: raw points from two buckets
    const buffer = new LiveTailBuffer(1_000)
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_000, value: 24.1 })
    buffer.push({ sensor: 'dry_bulb', timestamp: 2_000, value: 24.2 })

    // When: the first bucket completes
    buffer.reset(2_000)

    // Then: the covered point is gone and an older sample is rejected
    expect(buffer.pointsFor('dry_bulb')).toEqual([{ t: 2_000, v: 24.2 }])
    buffer.push({ sensor: 'dry_bulb', timestamp: 1_500, value: 24.3 })
    expect(buffer.pointsFor('dry_bulb')).toEqual([{ t: 2_000, v: 24.2 }])
    expect(buffer.baseCompleteUntil).toBe(2_000)
  })

  it('keeps at most one raw point per bucket across 3,600 one-second pushes', () => {
    // Given: one-second pushes over one hour and ten-minute buckets
    const buffer = new LiveTailBuffer(600_000)
    const start = Date.parse('2026-10-24T00:00:00.000Z')

    // When: 3,600 one-second samples are pushed
    for (let tick = 0; tick < 3_600; tick++) {
      buffer.push({ sensor: 'dry_bulb', timestamp: start + tick * 1_000, value: 20 + tick / 1_000 })
    }

    // Then: the tail is bounded by the number of buckets it spans (≤ 7)
    expect(buffer.size).toBeLessThanOrEqual(7)
    expect(buffer.pointsFor('dry_bulb').length).toBeLessThanOrEqual(7)
    const last = buffer.pointsFor('dry_bulb').at(-1)
    expect(last?.v).toBe(23.599)
  })
})
