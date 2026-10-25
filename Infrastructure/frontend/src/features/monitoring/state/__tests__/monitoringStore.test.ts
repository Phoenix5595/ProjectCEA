import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mocked } from 'vitest'

import type {
  ControlMonitoringResponse,
  MonitoringResponse,
  ProjectionPublicationResponse,
} from '../../api'
import { MonitoringApi, MonitoringHttpError } from '../../api'
import { MonitoringStore } from '../monitoringStore'
import type { MonitoringStoreOptions } from '../monitoringStore.types'

const T0 = '2026-08-02T12:00:00.000Z'

function sensorRangeResponse(
  average = 24.5,
  timestamp = new Date('2026-08-02T11:00:00.000Z')
): MonitoringResponse {
  return {
    metadata: {
      generated_at: new Date(T0),
      tier: 'raw',
      range: { start: new Date('2026-08-02T11:00:00.000Z'), end: new Date(T0) },
      room: { room: 'Flower Room', nodes: ['front', 'back'] },
      interval_seconds: 60,
    },
    series: [
      {
        sensor: 'dry_bulb',
        node: 'front',
        unit_family: 'celsius',
        unit: '°C',
        points: [
          {
            timestamp,
            average,
            minimum: 24.1,
            maximum: 24.9,
            sample_count: 60,
          },
        ],
      },
    ],
    statistics: [
      {
        sensor: 'dry_bulb',
        node: 'front',
        minimum: 24.1,
        maximum: 25.2,
        average: 24.65,
        stddev_samp: 0.21,
        sample_count: 3600,
      },
    ],
  }
}

function climateSeries(points: { time: Date; value: number }[]) {
  return {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map(p => ({
      time: p.time,
      value: p.value,
      nominal_value: p.value,
      metric: 'heating_setpoint',
      provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
    })),
    steps: [],
    linear: [],
  }
}

function controlResponse(
  overrides: Partial<ControlMonitoringResponse> = {}
): ControlMonitoringResponse {
  return {
    range: { start: new Date('2026-08-02T11:00:00.000Z'), end: new Date(T0) },
    runtime_snapshot_version: 7,
    cursors: [
      { source: 'effective_setpoints', cursor: '42', has_more: false },
      { source: 'automation_state', cursor: '10', has_more: false },
      { source: 'photoperiod_history', cursor: '3', has_more: false },
    ],
    flush_health: [
      {
        source: 'photoperiod_history',
        dropped_rows: 0,
        last_flushed_at: new Date(T0),
        healthy: true,
      },
    ],
    climate: [climateSeries([])],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
    ...overrides,
  }
}

function projectionResponse(
  overrides: {
    revision?: string
    validUntil?: Date
    quality?: ProjectionPublicationResponse['quality']
  } = {}
): ProjectionPublicationResponse {
  const validUntil = overrides.validUntil ?? new Date('2099-01-01T00:00:00.000Z')
  return {
    quality: overrides.quality ?? 'estimated',
    value:
      overrides.quality === 'unavailable'
        ? []
        : [
            {
              version: {
                contract_version: 1,
                config_version: 7,
                revision: overrides.revision ?? '8f8c3db',
              },
              generated_at: new Date(T0),
              valid_from: new Date(T0),
              valid_until: validUntil,
              series: [
                {
                  series_id: { value: 'climate.heating_setpoint_target' },
                  value: 22,
                  quality: 'estimated',
                  valid_from: new Date(T0),
                  valid_until: validUntil,
                },
              ],
            },
          ],
  }
}

interface Deferred<T> {
  readonly promise: Promise<T>
  resolve(value: T): void
  reject(reason: unknown): void
}

function deferred<T>(): Deferred<T> {
  let resolvePromise: ((value: T | PromiseLike<T>) => void) | null = null
  let rejectPromise: ((reason?: unknown) => void) | null = null
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve
    rejectPromise = reject
  })
  return {
    promise,
    resolve(value: T): void {
      if (resolvePromise === null) throw new Error('Deferred promise was not initialized')
      resolvePromise(value)
    },
    reject(reason: unknown): void {
      if (rejectPromise === null) throw new Error('Deferred promise was not initialized')
      rejectPromise(reason)
    },
  }
}

function makeApi(): Mocked<MonitoringApi> {
  return {
    sensorRange: vi.fn(),
    sensorLive: vi.fn(),
    controlRange: vi.fn(),
    controlTail: vi.fn(),
    controlProjection: vi.fn(),
  } as unknown as Mocked<MonitoringApi>
}

function healthyDefaults(api: Mocked<MonitoringApi>): void {
  api.sensorRange.mockResolvedValue(sensorRangeResponse())
  api.sensorLive.mockResolvedValue([{ sensor: 'dry_bulb', value: 24.6, timestamp: new Date(T0) }])
  api.controlRange.mockResolvedValue(controlResponse())
  api.controlTail.mockResolvedValue(controlResponse())
  api.controlProjection.mockResolvedValue(projectionResponse())
}

/** Monitoring store whose first range load is released by a reported budget. */
function makeStore(
  api: Mocked<MonitoringApi>,
  options: MonitoringStoreOptions = {},
  budget = 2_000
): MonitoringStore {
  const store = new MonitoringStore('Flower Room', api, options)
  store.setRangeBudget(budget)
  return store
}

describe('monitoring store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(T0))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('streams sixty seconds of live ticks through bounded control windows', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.controlRange).toHaveBeenCalledTimes(1)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.sensorRange.mock.calls[0]?.slice(1)).toEqual([
      expect.any(String),
      expect.any(String),
      2000,
      { signal: expect.any(AbortSignal) },
    ])
    expect(api.controlRange.mock.calls[0]?.slice(1)).toEqual([
      expect.any(String),
      expect.any(String),
      2000,
      undefined,
      { signal: expect.any(AbortSignal) },
    ])
    expect(api.controlProjection).toHaveBeenCalledTimes(1)
    expect(
      api.sensorRange.mock.calls.length +
        api.controlRange.mock.calls.length +
        api.controlProjection.mock.calls.length
    ).toBe(3)

    await vi.advanceTimersByTimeAsync(60000)

    // Live sensor history is refreshed incrementally; statistics are not
    // refetched on every incremental response.
    expect(api.sensorRange.mock.calls.length).toBeGreaterThan(1)
    for (const call of api.sensorRange.mock.calls.slice(1)) {
      expect(call[3]).toBe(2000)
      expect(call[4]).toMatchObject({ intervalSeconds: expect.any(Number) })
    }
    expect(api.controlProjection).toHaveBeenCalledTimes(1)
    expect(api.controlTail.mock.calls.length).toBeGreaterThanOrEqual(60)

    const [loc, startArg, endArg] = api.controlTail.mock.calls.at(-1)!
    expect(loc).toBe('Flower Room')
    const span = Date.parse(endArg as string) - Date.parse(startArg as string)
    expect(span).toBeLessThanOrEqual(122_000)
    unsub()
  })

  it('reloads the three range sources when retrying an unchanged range', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.retry()
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.controlRange).toHaveBeenCalledTimes(2)
    expect(api.controlProjection).toHaveBeenCalledTimes(2)
    unsub()
  })

  it('drains paged backlog one page per tick', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlTail
      .mockReset()
      .mockResolvedValueOnce(
        controlResponse({
          cursors: [{ source: 'effective_setpoints', cursor: '42', has_more: true }],
        })
      )
      .mockResolvedValueOnce(
        controlResponse({
          cursors: [{ source: 'effective_setpoints', cursor: '43', has_more: false }],
        })
      )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)
    expect(api.controlTail).toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)
    expect(api.controlTail).toHaveBeenCalled()
    unsub()
  })

  it('uses projection-only reload', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlProjection
      .mockReset()
      .mockResolvedValueOnce(
        projectionResponse({ validUntil: new Date('2026-08-02T12:00:01.000Z') })
      )
      .mockResolvedValueOnce(projectionResponse({ revision: '8f8c3de' }))
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.controlProjection).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)

    expect(api.controlProjection).toHaveBeenCalledTimes(2)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot().data.projectionRevision).toBe('8f8c3de')
    expect(store.getSnapshot().data.projectionVersion).toBe(7)
    unsub()
  })

  it('does not apply a stale live projection completion after selecting a fixed range', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const staleProjection = deferred<ProjectionPublicationResponse>()
    api.controlProjection
      .mockReset()
      .mockResolvedValueOnce(projectionResponse({ validUntil: new Date(T0) }))
      .mockImplementationOnce(() => staleProjection.promise)
      .mockResolvedValue(projectionResponse({ revision: 'fixed-range' }))
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)
    staleProjection.resolve(projectionResponse({ revision: 'stale-live' }))
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.projectionRevision).toBe('fixed-range')
    unsub()
  })

  it('does not apply a stale live projection failure after selecting a fixed range', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const staleProjection = deferred<ProjectionPublicationResponse>()
    api.controlProjection
      .mockReset()
      .mockResolvedValueOnce(projectionResponse({ validUntil: new Date(T0) }))
      .mockImplementationOnce(() => staleProjection.promise)
      .mockResolvedValue(projectionResponse({ revision: 'fixed-range' }))
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)
    staleProjection.reject(new MonitoringHttpError('monitoring', 503, 'stale projection failure'))
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().errors).not.toContain('stale projection failure')
    unsub()
  })

  it('does not apply a stale live control tail after selecting a fixed range', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const staleTail = deferred<ControlMonitoringResponse>()
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    api.controlTail.mockImplementationOnce(() => staleTail.promise)

    await vi.advanceTimersByTimeAsync(1000)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)
    staleTail.resolve(
      controlResponse({
        climate: [climateSeries([{ time: new Date(T0), value: 99 }])],
      })
    )
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.controlHistory?.climate[0]?.points).toEqual([])
    unsub()
  })

  it('does not apply a stale live reconciliation after selecting a fixed range', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const staleReconciliation = deferred<ControlMonitoringResponse>()
    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'range unavailable')
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    api.controlRange
      .mockImplementationOnce(() => staleReconciliation.promise)
      .mockResolvedValue(controlResponse())

    await vi.advanceTimersByTimeAsync(61_000)
    expect(api.controlRange).toHaveBeenCalledTimes(2)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)
    staleReconciliation.resolve(
      controlResponse({
        climate: [climateSeries([{ time: new Date(T0), value: 99 }])],
      })
    )
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.controlHistory?.climate[0]?.points).toEqual([])
    unsub()
  })

  it('downgrades anchor at expiry', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlProjection
      .mockReset()
      .mockResolvedValue(projectionResponse({ validUntil: new Date('2026-08-02T11:59:00.000Z') }))
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.anchorQuality).toBe('estimated')

    await vi.advanceTimersByTimeAsync(1000)

    expect(store.getSnapshot().data.anchorQuality).toBe('estimated')
    unsub()
  })

  it('clears unavailable projections without touching recorded control facts', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange.mockResolvedValue(
      controlResponse({
        climate: [climateSeries([{ time: new Date(T0), value: 22 }])],
      })
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    api.controlProjection.mockResolvedValue(projectionResponse({ quality: 'unavailable' }))
    store.retry()
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.projectionHistory).toBeNull()
    expect(store.getSnapshot().data.controlHistory?.climate[0]?.points[0]?.value).toBe(22)
    unsub()
  })

  it('prevents overlap stale overwrite and resolution mixing', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange.mockReset().mockResolvedValue(
      controlResponse({
        climate: [
          climateSeries([
            { time: new Date('2026-08-02T11:00:00.000Z'), value: 22 },
            { time: new Date('2026-08-02T11:00:05.000Z'), value: 22.5 },
          ]),
        ],
      })
    )
    api.controlRange.mockReset().mockResolvedValue(
      controlResponse({
        climate: [
          climateSeries([
            { time: new Date('2026-08-02T11:00:00.000Z'), value: 22 },
            { time: new Date('2026-08-02T11:00:05.000Z'), value: 22.5 },
            { time: new Date('2026-08-02T11:00:10.000Z'), value: 23 },
          ]),
        ],
      })
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)

    const points = store.getSnapshot().data.controlHistory?.climate[0].points ?? []
    const timestamps = points.map(p => p.time.getTime())
    expect(new Set(timestamps).size).toBe(timestamps.length)
    expect(points).toHaveLength(3)
    expect(store.getSnapshot().data.series).toHaveLength(1)
    expect(store.getSnapshot().data.live).toHaveLength(1)
    unsub()
  })

  it('recovers when the initial control window fails', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange
      .mockReset()
      .mockRejectedValueOnce(new MonitoringHttpError('monitoring', 400, 'window failed'))
      .mockResolvedValue(controlResponse())
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)

    expect(store.getSnapshot().data.controlHistory).not.toBeNull()

    await vi.advanceTimersByTimeAsync(5000)

    const points = store.getSnapshot().data.controlHistory?.climate[0].points ?? []
    const timestamps = points.map(p => p.time.getTime())
    expect(new Set(timestamps).size).toBe(timestamps.length)
    unsub()
  })

  it('never removes sensor history or relabels recorded data on automation failure', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.series).toHaveLength(1)
    expect(store.getSnapshot().data.statistics).toHaveLength(1)
    expect(store.getSnapshot().data.controlHistory).not.toBeNull()
    expect(store.getSnapshot().errors).toEqual([])

    await vi.advanceTimersByTimeAsync(1000)
    expect(store.getSnapshot().data.live).toHaveLength(1)

    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'automation down')
    )
    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    const snap = store.getSnapshot()
    expect(snap.data.series).toHaveLength(1)
    expect(snap.data.statistics).toHaveLength(1)
    expect(snap.data.controlHistory).not.toBeNull()
    expect(snap.data.live).toHaveLength(1)
    expect(snap.errors.length).toBeGreaterThan(0)
    unsub()
  })

  it('keeps live-tick control reads bounded instead of full-range refetches', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    const rangeFixture = controlResponse().range
    const fullSpan = rangeFixture.end.getTime() - rangeFixture.start.getTime()

    await vi.advanceTimersByTimeAsync(60000)

    // Sensor history may refresh incrementally, but control reads stay tails.
    expect(api.sensorRange.mock.calls.length).toBeGreaterThan(1)
    expect(api.controlTail.mock.calls.length).toBeGreaterThan(0)
    for (const [, startArg, endArg] of api.controlTail.mock.calls) {
      const span = Date.parse(endArg as string) - Date.parse(startArg as string)
      expect(span).toBeLessThanOrEqual(fullSpan)
    }
    unsub()
  })

  it('refreshes sensor history incrementally at the live cadence', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    // No incremental before the 5 s minimum spacing.
    await vi.advanceTimersByTimeAsync(4_000)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.sensorRange.mock.calls[1]?.[4]).toMatchObject({
      intervalSeconds: expect.any(Number),
    })
    unsub()
  })

  it('clears the stale banner on the first tail after a failed full read once the full read recovers', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'control unavailable')
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().errors).toContain('control unavailable')
    expect(store.getSnapshot().rangeErrorAt).toEqual(new Date(T0))

    // The first successful 1 Hz tail reconciles the full window immediately,
    // so the banner clears without waiting for a retry cadence.
    await vi.advanceTimersByTimeAsync(1_000)

    expect(store.getSnapshot().errors).toEqual([])
    expect(store.getSnapshot().rangeErrorAt).toBeNull()
    expect(store.getSnapshot().lastGoodRangeAt).not.toBeNull()
    unsub()
  })

  it('keeps a persistent full-history failure visible while control tails succeed', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange.mockRejectedValue(
      new MonitoringHttpError('monitoring', 503, 'control unavailable')
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    for (let second = 0; second < 5; second++) {
      await vi.advanceTimersByTimeAsync(1_000)
      expect(store.getSnapshot().errors).toContain('control unavailable')
      expect(store.getSnapshot().rangeErrorAt).not.toBeNull()
    }
    expect(api.controlTail.mock.calls.length).toBeGreaterThan(0)
    unsub()
  })

  it('refetches range statistics when live duration changes', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.controlRange.mock.calls.length).toBeGreaterThanOrEqual(2)
    unsub()
  })

  it('does not refetch fixed range when set to identical bounds', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    const start = new Date('2026-08-02T10:00:00.000Z')
    const end = new Date('2026-08-02T11:00:00.000Z')
    store.setFixedRange(start, end)
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)

    store.setFixedRange(start, end)
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    unsub()
  })

  it('keeps stale statistics with range error metadata when sensor range fails', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    api.sensorRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'sensor range failed')
    )
    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    const snapshot = store.getSnapshot()
    expect(snapshot.data.statistics).toHaveLength(1)
    expect(snapshot.errors).toContain('sensor range failed')
    expect(snapshot.lastGoodRangeAt).toEqual(new Date(T0))
    expect(snapshot.rangeErrorAt).toEqual(new Date(T0))
    unsub()
  })

  it('preserves the previous last-good timestamp across a failed retry', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    const firstGoodAt = store.getSnapshot().lastGoodRangeAt
    expect(firstGoodAt).not.toBeNull()

    api.controlRange.mockRejectedValue(
      new MonitoringHttpError('monitoring', 503, 'range unavailable')
    )
    api.sensorRange.mockRejectedValue(
      new MonitoringHttpError('monitoring', 503, 'range unavailable')
    )
    store.retry()
    await vi.advanceTimersByTimeAsync(0)

    const snapshot = store.getSnapshot()
    expect(snapshot.errors.length).toBeGreaterThan(0)
    expect(snapshot.rangeErrorAt).not.toBeNull()
    expect(snapshot.lastGoodRangeAt).toEqual(firstGoodAt)
    unsub()
  })

  it('replaces overlapping range loads', async () => {
    const api = makeApi()
    healthyDefaults(api)
    let finishRange: (() => void) | undefined
    api.sensorRange.mockImplementation(
      () =>
        new Promise(resolve => {
          finishRange = () => resolve(sensorRangeResponse())
        })
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(2)

    finishRange?.()
    await vi.advanceTimersByTimeAsync(0)
    store.setLiveRange(900_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(3)
    unsub()
  })

  it('aborts superseded live and fixed range loads so only the latest response applies', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const signals: AbortSignal[] = []
    const resolveSensorRanges: Array<(value: MonitoringResponse) => void> = []
    api.sensorRange.mockImplementation(
      (_location, _start, _end, _maxPoints, options) =>
        new Promise<MonitoringResponse>(resolve => {
          if (options?.signal !== undefined) signals.push(options.signal)
          resolveSensorRanges.push(resolve)
        })
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    store.setFixedRange(new Date('2026-08-02T09:00:00.000Z'), new Date('2026-08-02T10:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(3)
    expect(signals).toHaveLength(3)
    expect(signals[0]?.aborted).toBe(true)
    expect(signals[1]?.aborted).toBe(true)
    expect(signals[2]?.aborted).toBe(false)
    expect(api.controlRange.mock.calls[2]?.[5]?.signal).toBe(signals[2])
    expect(api.controlProjection.mock.calls[2]?.[1]?.signal).toBe(signals[2])

    resolveSensorRanges[0]?.(sensorRangeResponse(10))
    resolveSensorRanges[2]?.(sensorRangeResponse(30))
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(30)
    expect(store.getSnapshot().errors).toEqual([])
    unsub()
  })

  it('aborts an outstanding range load when the last subscriber unsubscribes', async () => {
    const api = makeApi()
    healthyDefaults(api)
    let signal: AbortSignal | undefined
    api.sensorRange.mockImplementation(
      (_location, _start, _end, _maxPoints, options) =>
        new Promise<MonitoringResponse>(() => {
          signal = options?.signal
        })
    )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    unsub()

    expect(signal?.aborted).toBe(true)
  })

  it('waits for the first reported budget before the first range load', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).not.toHaveBeenCalled()
    expect(api.controlRange).not.toHaveBeenCalled()
    expect(api.controlProjection).not.toHaveBeenCalled()

    store.setRangeBudget(1_800)
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.sensorRange.mock.calls[0]?.[3]).toBe(1_800)
    expect(api.controlRange.mock.calls[0]?.[3]).toBe(1_800)
    expect(api.controlProjection).toHaveBeenCalledTimes(1)
    unsub()
  })

  it('reloads only when the reported budget moves by more than 10 %, debounced', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api, {}, 2_000)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    // +5 %: within tolerance, no reload.
    store.setRangeBudget(2_100)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    // -50 %: reloaded once after the 500 ms debounce.
    store.setRangeBudget(1_000)
    await vi.advanceTimersByTimeAsync(499)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.sensorRange.mock.calls[1]?.[3]).toBe(1_000)
    unsub()
  })

  it('keeps fixed range bounds unchanged when the chart resolution reloads', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    const start = new Date('2026-08-02T10:00:00.000Z')
    const end = new Date('2026-08-02T11:00:00.000Z')

    store.setFixedRange(start, end)
    await vi.advanceTimersByTimeAsync(0)
    store.setRangeBudget(3_000)
    await vi.advanceTimersByTimeAsync(500)

    expect(api.sensorRange.mock.calls.at(-1)?.slice(1, 3)).toEqual([
      start.toISOString(),
      end.toISOString(),
    ])
    expect(api.controlRange.mock.calls.at(-1)?.slice(1, 3)).toEqual([
      start.toISOString(),
      end.toISOString(),
    ])
    unsub()
  })

  it('reloads the live range with the new budget after the debounce', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.setRangeBudget(3_000)
    await vi.advanceTimersByTimeAsync(500)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.sensorRange.mock.calls.at(-1)?.[3]).toBe(3_000)
    unsub()
  })

  it('uses the chart point budget for recurring live history refreshes', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.setRangeBudget(3_000)
    await vi.advanceTimersByTimeAsync(61_000)

    expect(api.sensorRange.mock.calls.length).toBeGreaterThan(2)
    expect(api.sensorRange.mock.calls.at(-1)?.[3]).toBe(3_000)
    unsub()
  })

  it('keeps the fulfilled three-hour history while a one-hour request loads, then commits both atomically', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    store.setLiveRange(3 * 3600_000)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    const threeHourFulfilled = store.getSnapshot().fulfilledRange
    expect(threeHourFulfilled).toEqual({
      range: { kind: 'live', duration: 3 * 3600_000 },
      start: new Date('2026-08-02T09:00:00.000Z'),
      end: new Date(T0),
      revision: 1,
    })

    const sensor = deferred<MonitoringResponse>()
    const control = deferred<ControlMonitoringResponse>()
    const projection = deferred<ProjectionPublicationResponse>()
    api.sensorRange.mockImplementationOnce(() => sensor.promise)
    api.controlRange.mockImplementationOnce(() => control.promise)
    api.controlProjection.mockImplementationOnce(() => projection.promise)

    store.setLiveRange(3600_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().range).toEqual({ kind: 'live', duration: 3600_000 })
    expect(store.getSnapshot().fulfilledRange).toEqual(threeHourFulfilled)

    sensor.resolve(sensorRangeResponse(11))
    control.resolve(controlResponse())
    projection.resolve(projectionResponse())
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().fulfilledRange).toEqual({
      range: { kind: 'live', duration: 3600_000 },
      start: new Date('2026-08-02T11:00:00.000Z'),
      end: new Date(T0),
      revision: 2,
    })
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(11)
    unsub()
  })

  it('advances the fulfilled range when sensor history succeeds and control history fails', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    api.sensorRange.mockResolvedValueOnce(
      sensorRangeResponse(33, new Date('2026-08-02T11:45:00.000Z'))
    )
    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'control unavailable')
    )
    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().fulfilledRange?.revision).toBe(2)
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(33)
    expect(store.getSnapshot().data.controlHistory).not.toBeNull()
    expect(store.getSnapshot().errors).toContain('control unavailable')
    unsub()
  })

  it('applies a fulfilled projection while retaining history fulfillment when both histories fail', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    const fulfilledRange = store.getSnapshot().fulfilledRange
    expect(fulfilledRange).toBeDefined()

    api.sensorRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'sensor unavailable')
    )
    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'control unavailable')
    )
    api.controlProjection.mockResolvedValueOnce(
      projectionResponse({ revision: 'projection-recovered' })
    )
    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().fulfilledRange).toBe(fulfilledRange)
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(24.5)
    expect(store.getSnapshot().data.projectionRevision).toBe('projection-recovered')
    expect(store.getSnapshot().errors).toEqual(['sensor unavailable', 'control unavailable'])
    unsub()
  })

  it('prevents a superseded request from committing a fulfilled revision after the latest request settles', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    const oldSensor = deferred<MonitoringResponse>()
    const oldControl = deferred<ControlMonitoringResponse>()
    const oldProjection = deferred<ProjectionPublicationResponse>()
    api.sensorRange
      .mockImplementationOnce(() => oldSensor.promise)
      .mockResolvedValueOnce(sensorRangeResponse(30))
    api.controlRange
      .mockImplementationOnce(() => oldControl.promise)
      .mockResolvedValueOnce(controlResponse())
    api.controlProjection
      .mockImplementationOnce(() => oldProjection.promise)
      .mockResolvedValueOnce(projectionResponse())

    store.setLiveRange(1800_000)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().fulfilledRange?.revision).toBe(2)
    oldSensor.resolve(sensorRangeResponse(10))
    oldControl.resolve(controlResponse())
    oldProjection.resolve(projectionResponse())
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().fulfilledRange).toEqual({
      range: {
        kind: 'fixed',
        start: new Date('2026-08-02T10:00:00.000Z'),
        end: new Date('2026-08-02T11:00:00.000Z'),
      },
      start: new Date('2026-08-02T10:00:00.000Z'),
      end: new Date('2026-08-02T11:00:00.000Z'),
      revision: 2,
    })
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(30)
    unsub()
  })

  it('keeps last-good data and clears no errors during expected supersession', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const signals: AbortSignal[] = []
    api.sensorRange
      .mockReset()
      .mockResolvedValueOnce(sensorRangeResponse(20))
      .mockImplementation(
        (_location, _start, _end, _maxPoints, options) =>
          new Promise<MonitoringResponse>(() => {
            if (options?.signal !== undefined) signals.push(options.signal)
          })
      )
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.setLiveRange(1800_000)
    store.setFixedRange(new Date('2026-08-02T10:00:00.000Z'), new Date('2026-08-02T11:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)

    expect(signals[0]?.aborted).toBe(true)
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(20)
    expect(store.getSnapshot().errors).toEqual([])
    unsub()
  })

  it('restarts a changed range after a delayed initial load while control polling continues', async () => {
    const api = makeApi()
    healthyDefaults(api)
    let finishRange: (() => void) | undefined
    api.sensorRange
      .mockReset()
      .mockImplementationOnce(
        () =>
          new Promise(resolve => {
            finishRange = () => resolve(sensorRangeResponse())
          })
      )
      .mockResolvedValue(sensorRangeResponse())
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(1000)
    finishRange?.()
    await vi.advanceTimersByTimeAsync(0)

    expect(api.controlRange.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(store.getSnapshot().data.series).toHaveLength(1)
    unsub()
  })

  it('replaces raw tail points with the completed bucket from an incremental response', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.sensorLive.mockResolvedValue([])
    api.sensorRange.mockReset()
    const at = (minutes: number): Date =>
      new Date(new Date(T0).getTime() - (60 - minutes) * 60_000)
    const bucket = (time: Date, average: number): MonitoringResponse => ({
      metadata: {
        generated_at: new Date(T0),
        tier: '1min',
        range: { start: new Date('2026-08-02T11:00:00.000Z'), end: new Date(T0) },
        room: { room: 'Flower Room', nodes: ['front'] },
        interval_seconds: 60,
      },
      series: [
        {
          sensor: 'dry_bulb',
          node: 'front',
          unit_family: 'celsius',
          unit: '°C',
          points: [{ timestamp: time, average, minimum: average, maximum: average, sample_count: 60 }],
        },
      ],
      statistics: [],
    })
    api.sensorRange
      .mockResolvedValueOnce(bucket(at(0), 20))
      .mockResolvedValueOnce(bucket(at(2), 22))
      .mockResolvedValue(bucket(at(2), 22))
    const store = makeStore(api)
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    // A raw in-progress value inside the bucket that is about to complete.
    store.getSnapshot().data.liveTail.push({
      sensor: 'dry_bulb',
      timestamp: at(2).getTime() + 30_000,
      value: 99,
    })
    expect(store.getSnapshot().data.liveTail.size).toBe(1)

    // When: the incremental refresh returns the completed bucket.
    await vi.advanceTimersByTimeAsync(5_000)

    const snapshot = store.getSnapshot()
    expect(api.sensorRange.mock.calls[1]?.[4]).toMatchObject({ intervalSeconds: 60 })
    expect(snapshot.data.series[0]?.points.map(point => point.average)).toEqual([22])
    // Then: the completed bucket replaces the raw point it covers.
    expect(snapshot.data.liveTail.pointsFor('dry_bulb')).toHaveLength(0)
    unsub()
  })

  it('loads a historical store without projection, live polling or control tail', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = makeStore(api, { mode: 'historical' })
    store.setFixedRange(
      new Date('2026-08-02T10:00:00.000Z'),
      new Date('2026-08-02T11:00:00.000Z')
    )
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.controlRange).toHaveBeenCalledTimes(1)
    expect(api.controlProjection).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(30_000)

    expect(api.sensorLive).not.toHaveBeenCalled()
    expect(api.controlTail).not.toHaveBeenCalled()
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    const snapshot = store.getSnapshot()
    expect(snapshot.data.projectionHistory).toBeNull()
    expect(snapshot.data.controlHistory).not.toBeNull()
    unsub()
  })

  it('remembers the last live duration for the Now button', () => {
    const api = makeApi()
    const store = makeStore(api)
    expect(store.lastLiveDurationMs()).toBe(3600_000)
    store.setLiveRange(1800_000)
    expect(store.lastLiveDurationMs()).toBe(1800_000)
  })

  it('does not expose the removed seekTo method', () => {
    const api = makeApi()
    const store = makeStore(api)
    expect('seekTo' in store).toBe(false)
  })
})
