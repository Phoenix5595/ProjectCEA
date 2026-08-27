import { describe, expect, it, vi } from 'vitest'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { createPanelAlignment } from '../../data/panelAlignment'
import type { MonitoringRange, StoreState } from '../../state'
import {
  createMonitoringChartFeed,
  createMonitoringPanelChartFeed,
} from '../MonitoringChartFeed'

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

function stateFor(range: MonitoringRange, fulfilledRange: StoreState['fulfilledRange'], value: number): StoreState {
  return {
    range,
    fulfilledRange,
    isLive: range.kind === 'live',
    data: {
      series: [{
        sensor: 'temperature',
        node: 'main',
        unit_family: 'celsius',
        unit: '°C',
        points: [{
          timestamp: fulfilledRange?.end ?? new Date(0),
          average: value,
          minimum: value,
          maximum: value,
          sample_count: 1,
        }],
      }],
      statistics: [],
      live: [],
      controlHistory: null,
      projectionHistory: null,
      photoperiod: [],
      cursors: [],
      projectionRevision: null,
      projectionVersion: null,
      anchorFingerprint: null,
      anchorQuality: null,
      anchorValidUntil: null,
      runtimeSnapshotVersion: null,
      flushHealth: [],
    },
    loading: false,
    tailLoading: false,
    reconciling: false,
    errors: [],
    lastGoodRangeAt: null,
    rangeErrorAt: null,
  }
}

function panelFeed() {
  return createMonitoringPanelChartFeed({ alignment: createPanelAlignment(), seriesSpecs: [] })
}

function sourceFor(initial: StoreState): {
  readonly source: { readonly getSnapshot: () => StoreState; readonly subscribe: (listener: () => void) => () => void }
  emit(next: StoreState): void
} {
  let snapshot = initial
  const listeners = new Set<() => void>()
  return {
    source: {
      getSnapshot: () => snapshot,
      subscribe: (listener) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
    },
    emit(next) {
      snapshot = next
      for (const listener of listeners) listener()
    },
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

  it('does not bump structural revision when only the range changes', () => {
    // Given: a feed with one series and a live range.
    const liveRange: MonitoringRange = { kind: 'live', duration: 3_600_000 }
    const feed = createMonitoringChartFeed(makeData(22), liveRange)
    const structural = feed.getStructuralSnapshot()
    const listener = vi.fn()
    feed.subscribe(listener)

    // When: the user switches to a fixed range with the same series shape.
    const fixedRange: MonitoringRange = { kind: 'fixed', start: new Date(1000), end: new Date(2000) }
    feed.publish(makeData(22), fixedRange)

    // Then: the structural snapshot identity is stable (no chart recreation),
    // but subscribers still observe the new data.
    expect(feed.getStructuralSnapshot()).toBe(structural)
    expect(listener).toHaveBeenCalledTimes(1)
    expect(feed.getStructuralSnapshot().range).toBe(fixedRange)
    expect(feed.getStructuralSnapshot().revision).toBe(structural.revision)
  })

  it('bumps structural revision when series shape changes', () => {
    // Given: a feed with one series and a live range.
    const liveRange: MonitoringRange = { kind: 'live', duration: 3_600_000 }
    const feed = createMonitoringChartFeed(makeData(22), liveRange)
    const structural = feed.getStructuralSnapshot()
    const listener = vi.fn()
    feed.subscribe(listener)

    // When: a new series key appears (same range).
    const next: AlignedData = {
      ...makeData(22),
      series: [
        makeData(22).series[0]!,
        {
          key: seriesKey('sensor', 'humidity', 'mean'),
          label: 'Humidity',
          kind: 'sensor',
          source: 'sensor',
          metric: 'humidity',
          family: 'rh',
          role: 'mean',
          y: [40, 41, 42],
          origin: 'recorded',
          quality: 'exact',
          isAggregated: false,
        },
      ],
    }
    feed.publish(next, liveRange)

    // Then: a new structural snapshot is emitted.
    const nextStructural = feed.getStructuralSnapshot()
    expect(nextStructural).not.toBe(structural)
    expect(nextStructural.revision).toBe(structural.revision + 1)
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('publishes a requested range only after its history fulfillment', () => {
    // Given: a chart showing a previously fulfilled live viewport.
    const liveRange = { kind: 'live', duration: 3_600_000 } as const
    const liveFulfillment = { range: liveRange, start: new Date(0), end: new Date(3_600_000), revision: 0 }
    const source = sourceFor(stateFor(liveRange, liveFulfillment, 20))
    const feed = panelFeed()
    feed.connect(source.source, source.source.getSnapshot())
    const previousData = feed.getData()
    const previousStructural = feed.getStructuralSnapshot()
    const listener = vi.fn()
    feed.subscribe(listener)

    // When: a fixed request is emitted before history fulfills.
    const fixedRange = { kind: 'fixed', start: new Date(4_000_000), end: new Date(7_600_000) } as const
    source.emit(stateFor(fixedRange, null, 21))

    // Then: the requested viewport cannot replace the last fulfilled one.
    expect(feed.getData()).toBe(previousData)
    expect(feed.getStructuralSnapshot()).toBe(previousStructural)
    expect(listener).not.toHaveBeenCalled()

    // When: the same request enters loading without fulfillment.
    source.emit({ ...stateFor(fixedRange, null, 21), loading: true })

    // Then: the requested viewport cannot replace the last fulfilled one.
    expect(feed.getData()).toBe(previousData)
    expect(feed.getStructuralSnapshot()).toBe(previousStructural)
    expect(feed.getStructuralSnapshot().range).toBe(liveRange)
    expect(feed.getStructuralSnapshot().viewportRevision).toBe(0)
    expect(listener).not.toHaveBeenCalled()

    // When: the transaction fulfills with fresh history.
    const fixedFulfillment = { range: fixedRange, start: fixedRange.start, end: fixedRange.end, revision: 1 }
    source.emit(stateFor(fixedRange, fixedFulfillment, 22))

    // Then: exactly the fulfilled viewport and data become observable.
    expect(feed.getData()).not.toBe(previousData)
    expect(feed.getStructuralSnapshot()).toBe(previousStructural)
    expect(feed.getStructuralSnapshot().range).toBe(fixedRange)
    expect(feed.getStructuralSnapshot().viewportRevision).toBe(1)
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('keeps the viewport revision stable for same-range live ticks', () => {
    // Given: a fulfilled live range and its first aligned payload.
    const range = { kind: 'live', duration: 3_600_000 } as const
    const fulfilled = { range, start: new Date(0), end: new Date(3_600_000), revision: 1 }
    const source = sourceFor(stateFor(range, fulfilled, 20))
    const feed = panelFeed()
    feed.connect(source.source, source.source.getSnapshot())
    const previousData = feed.getData()
    const structural = feed.getStructuralSnapshot()

    // When: a live tick supplies new data under the same fulfilled viewport.
    source.emit(stateFor(range, fulfilled, 21))

    // Then: data updates without a viewport or structural transition.
    expect(feed.getData()).not.toBe(previousData)
    expect(feed.getStructuralSnapshot()).toBe(structural)
    expect(feed.getStructuralSnapshot().viewportRevision).toBe(1)
  })

  it('keeps the fulfilled viewport when an error arrives without new fulfillment', () => {
    // Given: a chart with an established fulfilled range.
    const range = { kind: 'live', duration: 3_600_000 } as const
    const fulfilled = { range, start: new Date(0), end: new Date(3_600_000), revision: 1 }
    const source = sourceFor(stateFor(range, fulfilled, 20))
    const feed = panelFeed()
    feed.connect(source.source, source.source.getSnapshot())
    const structural = feed.getStructuralSnapshot()

    // When: an error changes without a replacement fulfillment.
    source.emit({ ...stateFor(range, fulfilled, 20), errors: ['range history failed'] })

    // Then: the viewport remains unchanged.
    expect(feed.getStructuralSnapshot()).toBe(structural)
    expect(feed.getStructuralSnapshot().viewportRevision).toBe(1)
  })

  it('rejects stale fulfillments after a newer viewport is published', () => {
    // Given: the feed has accepted revision two.
    const newestRange = { kind: 'fixed', start: new Date(3_600_000), end: new Date(7_200_000) } as const
    const newest = { range: newestRange, start: newestRange.start, end: newestRange.end, revision: 2 }
    const source = sourceFor(stateFor(newestRange, newest, 22))
    const feed = panelFeed()
    feed.connect(source.source, source.source.getSnapshot())
    const data = feed.getData()

    // When: an older fulfillment arrives out of order.
    const staleRange = { kind: 'fixed', start: new Date(0), end: new Date(3_600_000) } as const
    const stale = { range: staleRange, start: staleRange.start, end: staleRange.end, revision: 1 }
    source.emit(stateFor(staleRange, stale, 19))

    // Then: neither data nor viewport regresses.
    expect(feed.getData()).toBe(data)
    expect(feed.getStructuralSnapshot().range).toBe(newestRange)
    expect(feed.getStructuralSnapshot().viewportRevision).toBe(2)
  })
})
