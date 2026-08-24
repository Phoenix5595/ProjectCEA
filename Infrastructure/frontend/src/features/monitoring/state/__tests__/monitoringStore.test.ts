import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mocked } from 'vitest'

import type {
  ControlMonitoringResponse,
  MonitoringResponse,
} from '../../api'
import { MonitoringApi, MonitoringHttpError } from '../../api'
import { MonitoringStore } from '../monitoringStore'

const T0 = '2026-08-02T12:00:00.000Z'

function sensorRangeResponse(): MonitoringResponse {
  return {
    metadata: {
      generated_at: new Date(T0),
      tier: 'raw',
      range: { start: new Date('2026-08-02T11:00:00.000Z'), end: new Date(T0) },
      room: { room: 'Flower Room', nodes: ['front', 'back'] },
    },
    series: [
      {
        sensor: 'dry_bulb',
        node: 'front',
        unit_family: 'celsius',
        unit: '°C',
        points: [
          {
            timestamp: new Date('2026-08-02T11:00:00.000Z'),
            average: 24.5,
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

function sensorStatsResponse(): MonitoringResponse {
  return { ...sensorRangeResponse(), series: [] }
}

function climateSeries(points: { timestamp: Date; value: number }[]) {
  return {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map((p) => ({
      timestamp: p.timestamp,
      value: p.value,
      nominal_value: p.value,
      metric: 'heating_setpoint',
      provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
    })),
    steps: [],
    linear: [],
  }
}

function controlResponse(overrides: Partial<ControlMonitoringResponse> = {}): ControlMonitoringResponse {
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

const PROJ_META = {
  projection_revision: 'rev-1',
  anchor_fingerprint: 'fp-1',
  anchor_observed_at: new Date(T0),
  anchor_quality: 'exact' as const,
  anchor_valid_until: new Date('2099-01-01T00:00:00.000Z'),
}

function projectionResponse(meta: Partial<typeof PROJ_META> = {}): ControlMonitoringResponse {
  return controlResponse({
    climate: [
      {
        name: 'Heating Setpoint',
        provenance: { origin: 'projected' as const, quality: 'estimated' as const, is_aggregated: false },
        projection: { ...PROJ_META, ...meta },
        warnings: [],
        points: [],
        steps: [],
        linear: [],
      },
    ],
  })
}

function makeApi(): Mocked<MonitoringApi> {
  return {
    sensorRange: vi.fn(),
    sensorLive: vi.fn(),
    sensorStats: vi.fn(),
    controlRange: vi.fn(),
    controlTail: vi.fn(),
    controlProjection: vi.fn(),
  } as unknown as Mocked<MonitoringApi>
}

function healthyDefaults(api: Mocked<MonitoringApi>): void {
  api.sensorRange.mockResolvedValue(sensorRangeResponse())
  api.sensorStats.mockResolvedValue(sensorStatsResponse())
  api.sensorLive.mockResolvedValue([
    { sensor: 'dry_bulb', value: 24.6, timestamp: new Date(T0) },
  ])
  api.controlRange.mockResolvedValue(controlResponse())
  api.controlTail.mockResolvedValue(controlResponse())
  api.controlProjection.mockResolvedValue(projectionResponse())
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
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.controlRange).toHaveBeenCalledTimes(1)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.controlProjection).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(60000)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.sensorStats).toHaveBeenCalledTimes(1)
    expect(api.controlProjection).toHaveBeenCalledTimes(1)
    expect(api.controlTail.mock.calls.length).toBeGreaterThanOrEqual(60)

    const [loc, startArg, endArg] = api.controlTail.mock.calls.at(-1)!
    expect(loc).toBe('Flower Room')
    const span = Date.parse(endArg as string) - Date.parse(startArg as string)
    expect(span).toBeLessThanOrEqual(122_000)
    unsub()
  })

  it('drains paged backlog one page per tick', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlTail.mockReset()
      .mockResolvedValueOnce(
        controlResponse({ cursors: [{ source: 'effective_setpoints', cursor: '42', has_more: true }] }),
      )
      .mockResolvedValueOnce(
        controlResponse({ cursors: [{ source: 'effective_setpoints', cursor: '43', has_more: false }] }),
      )
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
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
        projectionResponse({ anchor_valid_until: new Date('2026-08-02T12:00:01.000Z') }),
      )
      .mockResolvedValueOnce(
        projectionResponse({ projection_revision: 'rev-2', anchor_fingerprint: 'fp-2' }),
      )
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.controlProjection).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)

    expect(api.controlProjection).toHaveBeenCalledTimes(2)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot().data.projectionRevision).toBe('rev-2')
    expect(store.getSnapshot().data.anchorFingerprint).toBe('fp-2')
    unsub()
  })

  it('downgrades anchor at expiry', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlProjection
      .mockReset()
      .mockResolvedValue(projectionResponse({ anchor_valid_until: new Date('2026-08-02T11:59:00.000Z') }))
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.anchorQuality).toBe('exact')

    await vi.advanceTimersByTimeAsync(1000)

    expect(store.getSnapshot().data.anchorQuality).toBe('estimated')
    unsub()
  })

  it('prevents overlap stale overwrite and resolution mixing', async () => {
    const api = makeApi()
    healthyDefaults(api)
    api.controlRange.mockReset().mockResolvedValue(
      controlResponse({
        climate: [
          climateSeries([
            { timestamp: new Date('2026-08-02T11:00:00.000Z'), value: 22 },
            { timestamp: new Date('2026-08-02T11:00:05.000Z'), value: 22.5 },
          ]),
        ],
      }),
    )
    api.controlRange.mockReset().mockResolvedValue(
      controlResponse({
        climate: [
          climateSeries([
            { timestamp: new Date('2026-08-02T11:00:00.000Z'), value: 22 },
            { timestamp: new Date('2026-08-02T11:00:05.000Z'), value: 22.5 },
            { timestamp: new Date('2026-08-02T11:00:10.000Z'), value: 23 },
          ]),
        ],
      }),
    )
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)

    const points = store.getSnapshot().data.controlHistory?.climate[0].points ?? []
    const timestamps = points.map((p) => p.timestamp.getTime())
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
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1000)

    expect(store.getSnapshot().data.controlHistory).not.toBeNull()

    await vi.advanceTimersByTimeAsync(5000)

    const points =
      store.getSnapshot().data.controlHistory?.climate[0].points ?? []
    const timestamps = points.map((p) => p.timestamp.getTime())
    expect(new Set(timestamps).size).toBe(timestamps.length)
    unsub()
  })

  it('never removes sensor history or relabels recorded data on automation failure', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().data.series).toHaveLength(1)
    expect(store.getSnapshot().data.statistics).toHaveLength(1)
    expect(store.getSnapshot().data.controlHistory).not.toBeNull()
    expect(store.getSnapshot().errors).toEqual([])

    await vi.advanceTimersByTimeAsync(1000)
    expect(store.getSnapshot().data.live).toHaveLength(1)

    api.controlRange.mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'automation down'),
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
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.sensorStats).toHaveBeenCalledTimes(1)
    const rangeFixture = controlResponse().range
    const fullSpan = rangeFixture.end.getTime() - rangeFixture.start.getTime()

    await vi.advanceTimersByTimeAsync(60000)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)
    expect(api.sensorStats).toHaveBeenCalledTimes(1)
    expect(api.controlTail.mock.calls.length).toBeGreaterThan(0)
    for (const [, startArg, endArg] of api.controlTail.mock.calls) {
      const span = Date.parse(endArg as string) - Date.parse(startArg as string)
      expect(span).toBeLessThanOrEqual(fullSpan)
    }
    unsub()
  })

  it('refetches range and statistics when live duration changes', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(api.sensorRange).toHaveBeenCalledTimes(2)
    expect(api.sensorStats).toHaveBeenCalledTimes(2)
    expect(api.controlRange.mock.calls.length).toBeGreaterThanOrEqual(2)
    unsub()
  })

  it('does not refetch fixed range when set to identical bounds', async () => {
    const api = makeApi()
    healthyDefaults(api)
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
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

  it('prevents overlapping range loads', async () => {
    const api = makeApi()
    healthyDefaults(api)
    let finishRange: (() => void) | undefined
    api.sensorRange.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishRange = () => resolve(sensorRangeResponse())
        }),
    )
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
    const unsub = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    store.setLiveRange(1800_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(1)

    finishRange?.()
    await vi.advanceTimersByTimeAsync(0)
    store.setLiveRange(900_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(api.sensorRange).toHaveBeenCalledTimes(2)
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
          new Promise((resolve) => {
            finishRange = () => resolve(sensorRangeResponse())
          }),
      )
      .mockResolvedValue(sensorRangeResponse())
    const store = new MonitoringStore('Flower Room', api, {
      now: () => new Date(),
    })
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

  it('does not expose the removed seekTo method', () => {
    const api = makeApi()
    const store = new MonitoringStore('Flower Room', api)
    expect('seekTo' in store).toBe(false)
  })
})
