import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ControlMonitoringResponse,
  MonitoringResponse,
  ProjectionPublicationResponse,
} from '../../api'
import { MonitoringApi, MonitoringHttpError } from '../../api'
import { MonitoringStore } from '../monitoringStore'

const NOW = new Date('2026-09-12T12:00:00.000Z')

function sensorResponse(average = 24): MonitoringResponse {
  return {
    metadata: {
      generated_at: NOW,
      tier: 'raw',
      range: { start: new Date(NOW.getTime() - 3600_000), end: NOW },
      room: { room: 'Flower Room', nodes: ['front'] },
    },
    series: [{
      sensor: 'dry_bulb',
      node: 'front',
      unit_family: 'celsius',
      unit: '°C',
      points: [{ timestamp: NOW, average, minimum: average, maximum: average, sample_count: 1 }],
    }],
    statistics: [{
      sensor: 'dry_bulb',
      node: 'front',
      minimum: average,
      maximum: average,
      average,
      stddev_samp: 0,
      sample_count: 1,
    }],
  }
}

function controlResponse(): ControlMonitoringResponse {
  return {
    range: { start: new Date(NOW.getTime() - 3600_000), end: NOW },
    runtime_snapshot_version: 1,
    cursors: [],
    flush_health: [],
    climate: [],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

function projectionResponse(): ProjectionPublicationResponse {
  return { quality: 'unavailable', value: [] }
}

function apiWithHealthyDefaults(): MonitoringApi {
  const api = new MonitoringApi()
  vi.spyOn(api, 'sensorRange').mockResolvedValue(sensorResponse())
  vi.spyOn(api, 'sensorLive').mockResolvedValue([])
  vi.spyOn(api, 'controlRange').mockResolvedValue(controlResponse())
  vi.spyOn(api, 'controlTail').mockResolvedValue(controlResponse())
  vi.spyOn(api, 'controlProjection').mockResolvedValue(projectionResponse())
  return api
}

describe('monitoring store recovery', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('tracks settled range results independently and projects only active source errors', async () => {
    const api = apiWithHealthyDefaults()
    vi.mocked(api.controlRange)
      .mockRejectedValueOnce(new MonitoringHttpError('monitoring', 503, 'control unavailable'))
      .mockResolvedValue(controlResponse())
    vi.mocked(api.controlProjection).mockRejectedValueOnce(
      new MonitoringHttpError('monitoring', 503, 'projection unavailable'),
    )
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsubscribe = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().sourceOutcomes['control-history'].status).toBe('failed')
    expect(store.getSnapshot().sourceOutcomes.projection.status).toBe('failed')
    expect(store.getSnapshot().errors).toEqual(['control unavailable', 'projection unavailable'])
    expect(store.getSnapshot().rangeErrorAt).toEqual(NOW)

    store.retry()
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().sourceOutcomes['control-history'].status).toBe('healthy')
    expect(store.getSnapshot().sourceOutcomes.projection.status).toBe('healthy')
    expect(store.getSnapshot().errors).toEqual([])
    unsubscribe()
  })

  it('keeps fixed ranges immutable across ticks until an explicit retry', async () => {
    const api = apiWithHealthyDefaults()
    vi.mocked(api.controlRange).mockRejectedValue(new MonitoringHttpError('monitoring', 503, 'control unavailable'))
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsubscribe = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    store.setFixedRange(new Date('2026-09-12T09:00:00.000Z'), new Date('2026-09-12T10:00:00.000Z'))
    await vi.advanceTimersByTimeAsync(0)
    const controlCalls = vi.mocked(api.controlRange).mock.calls.length
    const projectionCalls = vi.mocked(api.controlProjection).mock.calls.length

    await vi.advanceTimersByTimeAsync(10 * 60_000)

    expect(vi.mocked(api.controlRange)).toHaveBeenCalledTimes(controlCalls)
    expect(vi.mocked(api.controlProjection)).toHaveBeenCalledTimes(projectionCalls)
    expect(store.getSnapshot().sourceOutcomes['control-history'].status).toBe('failed')
    unsubscribe()
  })

  it('clamps live control tails to exactly two minutes for stale history', async () => {
    const api = apiWithHealthyDefaults()
    vi.mocked(api.controlTail).mockResolvedValue(controlResponse())
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsubscribe = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)

    const [, start, end] = vi.mocked(api.controlTail).mock.calls[0] ?? []
    expect(Date.parse(end ?? '') - Date.parse(start ?? '')).toBe(120_000)
    unsubscribe()
  })

  it('recovers sensor history autonomously after its retry cadence', async () => {
    const api = apiWithHealthyDefaults()
    vi.mocked(api.sensorRange)
      .mockResolvedValueOnce(sensorResponse())
      .mockRejectedValueOnce(new MonitoringHttpError('monitoring', 503, 'sensor unavailable'))
      .mockResolvedValue(sensorResponse(25))
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsubscribe = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(61_000)
    expect(store.getSnapshot().sourceOutcomes['sensor-history'].status).toBe('failed')

    await vi.advanceTimersByTimeAsync(61_000)
    expect(store.getSnapshot().sourceOutcomes['sensor-history'].status).toBe('healthy')
    expect(store.getSnapshot().data.series[0]?.points[0]?.average).toBe(25)
    unsubscribe()
  })

  it('recovers projection autonomously after its retry cadence', async () => {
    const api = apiWithHealthyDefaults()
    vi.mocked(api.controlProjection)
      .mockRejectedValue(new MonitoringHttpError('monitoring', 503, 'projection unavailable'))
    const store = new MonitoringStore('Flower Room', api, { now: () => new Date() })
    const unsubscribe = store.subscribe(() => {})
    await vi.advanceTimersByTimeAsync(0)

    expect(store.getSnapshot().sourceOutcomes.projection.status).toBe('failed')
    const failedCalls = vi.mocked(api.controlProjection).mock.calls.length
    vi.mocked(api.controlProjection).mockResolvedValue(projectionResponse())
    await vi.advanceTimersByTimeAsync(30_000)
    expect(vi.mocked(api.controlProjection)).toHaveBeenCalledTimes(failedCalls)

    await vi.advanceTimersByTimeAsync(1000)
    expect(store.getSnapshot().sourceOutcomes.projection.status).toBe('healthy')
    unsubscribe()
  })
})
