import { describe, expect, it, vi } from 'vitest'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { createMonitoringChartFeed } from '../MonitoringChartFeed'

function makeData(value: number): AlignedData {
  return {
    x: [1000, 2000, 3000],
    series: [
      {
        key: seriesKey('sensor', 'temperature', 'mean'),
        label: 'Temperature',
        kind: 'sensor',
        source: 'sensor',
        metric: 'temperature',
        family: 'temperature',
        role: 'mean',
        y: [20, 21, value],
        origin: 'recorded',
        quality: 'exact',
        isAggregated: false,
      },
    ],
    bands: [],
    photoperiod: [],
    nowIndex: 2,
    aggregated: false,
  }
}

describe('MonitoringChartFeed', () => {
  it('keeps the structural snapshot stable while publishing bounded live buffers', () => {
    // Given: one selected range and a bounded aligned chart buffer.
    const range = { kind: 'live', duration: 3_600_000 } as const
    const feed = createMonitoringChartFeed(makeData(22), range)
    const structural = feed.getStructuralSnapshot()
    const listener = vi.fn()
    const unsubscribe = feed.subscribe(listener)

    // When: a live-tail revision replaces only the chart buffer.
    feed.publish(makeData(23), range)

    // Then: imperative subscribers receive the buffer without a structural revision.
    expect(listener).toHaveBeenCalledTimes(1)
    expect(feed.getStructuralSnapshot()).toBe(structural)
    expect(feed.getData().series[0]?.y).toEqual([20, 21, 23])

    // When: the source unsubscribes before another tick.
    unsubscribe()
    feed.publish(makeData(24), range)

    // Then: no further chart draw can be scheduled through this listener.
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
