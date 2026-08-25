import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MonitoringApi } from '../monitoringApi'
import {
  MonitoringAbortError,
  MonitoringNetworkError,
  MonitoringParseError,
  MonitoringTimeoutError,
} from '../errors'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const sensorRangePayload = {
  metadata: {
    generated_at: '2026-08-02T12:00:00.000Z',
    tier: 'raw',
    range: { start: '2026-08-02T11:00:00.000Z', end: '2026-08-02T12:00:00.000Z' },
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
          timestamp: '2026-08-02T11:00:00.000Z',
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

const sensorLivePayload = [
  { sensor: 'dry_bulb', value: 24.6, timestamp: '2026-08-02T12:00:00.000Z' },
]

const controlPayload = {
  range: { start: '2026-08-02T11:00:00.000Z', end: '2026-08-02T12:00:00.000Z' },
  runtime_snapshot_version: 7,
  cursors: [
    { source: 'effective_setpoints', cursor: '42', has_more: true },
    { source: 'automation_state', cursor: '10', has_more: false },
    { source: 'photoperiod_history', cursor: '3', has_more: false },
  ],
  flush_health: [
    {
      source: 'photoperiod_history',
      dropped_rows: 0,
      last_flushed_at: '2026-08-02T12:00:00.000Z',
      healthy: true,
    },
  ],
  climate: [
    {
      name: 'Heating Setpoint',
      provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
      projection: null,
      warnings: [],
      points: [
        {
          timestamp: '2026-08-02T11:00:00.000Z',
          value: 22,
          nominal_value: 22,
          metric: 'heating_setpoint',
          provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
        },
      ],
      steps: [],
      linear: [],
    },
  ],
  lights: [],
  devices: [],
  pid: [],
  photoperiod: [
    {
      timestamp: '2026-08-02T11:00:00.000Z',
      phase: 'SUN',
      provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
      runtime_snapshot_version: 7,
    },
  ],
}

const controlProjectionPayload = {
  quality: 'estimated',
  value: [
    {
      version: { contract_version: 1, config_version: 7, revision: '8f8c3db' },
      generated_at: '2026-08-02T12:00:00.000Z',
      valid_from: '2026-08-02T12:00:00.000Z',
      valid_until: '2026-08-02T13:00:00.000Z',
      series: [
        {
          series_id: { value: 'climate.heating_setpoint_target' },
          value: 22,
          quality: 'estimated',
          valid_from: '2026-08-02T12:00:00.000Z',
          valid_until: '2026-08-02T13:00:00.000Z',
        },
      ],
    },
  ],
}

describe('monitoring api boundary', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('parses sensor range, live, stats and control history, projection contracts', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorRangePayload))
    const range = await api.sensorRange('Flower Room')
    expect(range.metadata.tier).toBe('raw')
    expect(range.metadata.range.start).toBeInstanceOf(Date)
    expect(range.series[0].points[0].timestamp).toBeInstanceOf(Date)
    expect(range.statistics[0].stddev_samp).toBe(0.21)

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorLivePayload))
    const live = await api.sensorLive('Flower Room', 'front')
    expect(live[0].sensor).toBe('dry_bulb')
    expect(live[0].timestamp).toBeInstanceOf(Date)

    fetchMock.mockResolvedValueOnce(jsonResponse({ ...sensorRangePayload, series: [] }))
    const stats = await api.sensorStats('Flower Room')
    expect(stats.series).toHaveLength(0)
    expect(stats.statistics[0].sample_count).toBe(3600)

    fetchMock.mockResolvedValueOnce(jsonResponse(controlPayload))
    const controlRange = await api.controlRange('Flower Room')
    expect(controlRange.runtime_snapshot_version).toBe(7)
    expect(controlRange.cursors[0].has_more).toBe(true)
    expect(controlRange.climate[0].points[0].timestamp).toBeInstanceOf(Date)
    expect(controlRange.photoperiod[0].phase).toBe('SUN')

    fetchMock.mockResolvedValueOnce(jsonResponse(controlProjectionPayload))
    const projection = await api.controlProjection('Flower Room')
    expect(projection.quality).toBe('estimated')
    expect(projection.value[0].version.revision).toBe('8f8c3db')
    expect(projection.value[0].series[0].valid_from).toBeInstanceOf(Date)

  })

  it('uses unchanged Caddy monitoring paths', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorRangePayload))
    await api.sensorRange('Flower Room')
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/sensors/monitoring/range/Flower%20Room'),
      expect.any(Object),
    )

    fetchMock.mockResolvedValueOnce(jsonResponse(controlPayload))
    await api.controlRange('Flower Room')
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/monitoring/control/Flower%20Room/history'),
      expect.any(Object),
    )

    fetchMock.mockResolvedValueOnce(jsonResponse(controlProjectionPayload))
    await api.controlProjection('Flower Room')
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/monitoring/control/Flower%20Room/projection'),
      expect.any(Object),
    )
  })

  it('serializes optional point budgets for supported range routes', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()
    const start = '2026-08-02T11:00:00.000Z'
    const end = '2026-08-02T12:00:00.000Z'

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorRangePayload))
    await api.sensorRange('Flower Room', start, end, 2000)
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining(
        '/api/sensors/monitoring/range/Flower%20Room?start=2026-08-02T11%3A00%3A00.000Z&end=2026-08-02T12%3A00%3A00.000Z&max_points=2000',
      ),
      expect.any(Object),
    )

    fetchMock.mockResolvedValueOnce(jsonResponse({ ...sensorRangePayload, series: [] }))
    await api.sensorStats('Flower Room', start, end, 2000)
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining(
        '/api/sensors/monitoring/stats/Flower%20Room?start=2026-08-02T11%3A00%3A00.000Z&end=2026-08-02T12%3A00%3A00.000Z&max_points=2000',
      ),
      expect.any(Object),
    )

    fetchMock.mockResolvedValueOnce(jsonResponse(controlPayload))
    await api.controlRange('Flower Room', start, end, 1000)
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining(
        '/api/monitoring/control/Flower%20Room/history?start=2026-08-02T11%3A00%3A00.000Z&end=2026-08-02T12%3A00%3A00.000Z&max_points=1000',
      ),
      expect.any(Object),
    )

    fetchMock.mockResolvedValueOnce(jsonResponse(controlPayload))
    await api.controlTail('Flower Room', start, end)
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining(
        '/api/monitoring/control/Flower%20Room/tail?start=2026-08-02T11%3A00%3A00.000Z&end=2026-08-02T12%3A00%3A00.000Z&max_points=1000',
      ),
      expect.any(Object),
    )
  })

  it('parses legacy and additive point-budget response fields without changing unknown-key handling', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorRangePayload))
    const legacy = await api.sensorRange('Flower Room')
    expect(legacy.metadata.requested_max_points).toBeUndefined()
    expect(legacy.series[0].point_count).toBeUndefined()
    expect(legacy.statistics[0].stddev_quality).toBeUndefined()

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...sensorRangePayload,
        metadata: {
          ...sensorRangePayload.metadata,
          requested_max_points: 1000,
          interval_seconds: 600,
        },
        series: [{ ...sensorRangePayload.series[0], point_count: 1, sample_count_total: 60 }],
        statistics: [{ ...sensorRangePayload.statistics[0], stddev_quality: 'approximate' }],
        ignored_by_existing_schema: true,
      }),
    )
    const budgeted = await api.sensorRange('Flower Room')
    expect(budgeted.metadata.requested_max_points).toBe(1000)
    expect(budgeted.metadata.interval_seconds).toBe(600)
    expect(budgeted.series[0].sample_count_total).toBe(60)
    expect(budgeted.statistics[0].stddev_quality).toBe('approximate')
    expect('ignored_by_existing_schema' in budgeted).toBe(false)

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...controlPayload, requested_max_points: 1000, interval_seconds: 600 }),
    )
    const control = await api.controlRange('Flower Room')
    expect(control.requested_max_points).toBe(1000)
    expect(control.interval_seconds).toBe(600)
  })

  it('parses control history when backend budget metadata is null or absent', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...controlPayload,
        requested_max_points: null,
        interval_seconds: null,
      }),
    )
    const nullable = await api.controlRange('Flower Room')
    expect(nullable.requested_max_points).toBeNull()
    expect(nullable.interval_seconds).toBeNull()

    fetchMock.mockResolvedValueOnce(jsonResponse(controlPayload))
    const absent = await api.controlRange('Flower Room')
    expect(absent.requested_max_points).toBeUndefined()
    expect(absent.interval_seconds).toBeUndefined()
  })

  it('adds an immutable fixture context only to test requests', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi({
      scenario: 'backend-down',
      fixtureSession: 'test/session',
    })

    fetchMock.mockResolvedValueOnce(jsonResponse(sensorRangePayload))
    await api.sensorRange('Flower Room')

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('scenario=backend-down&fixtureSession=test%2Fsession'),
      expect.any(Object),
    )
  })

  it('rejects malformed timestamp provenance cursor and sample count', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...sensorRangePayload,
        metadata: { ...sensorRangePayload.metadata, generated_at: '2026-08-02T12:00:00' },
      }),
    )
    await expect(api.sensorRange('Flower Room')).rejects.toBeInstanceOf(MonitoringParseError)

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...controlPayload,
        climate: [
          {
            ...controlPayload.climate[0],
            provenance: { origin: 'fabricated', quality: 'exact', is_aggregated: false },
          },
        ],
      }),
    )
    await expect(api.controlRange('Flower Room')).rejects.toBeInstanceOf(MonitoringParseError)


    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...sensorRangePayload,
        series: [
          {
            ...sensorRangePayload.series[0],
            points: [
              {
                ...sensorRangePayload.series[0].points[0],
                sample_count: -1,
              },
            ],
          },
        ],
      }),
    )
    await expect(api.sensorRange('Flower Room')).rejects.toBeInstanceOf(MonitoringParseError)
  })

  it('attributes HTTP errors to the unified monitoring service', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, 503))
    await expect(api.sensorRange('Flower Room')).rejects.toMatchObject({
      kind: 'http',
      service: 'monitoring',
      status: 503,
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, 500))
    await expect(api.controlRange('Flower Room')).rejects.toMatchObject({
      kind: 'http',
      service: 'monitoring',
      status: 500,
    })
  })

  it('classifies timeout and network failures distinctly', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()

    fetchMock.mockImplementation(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        }),
    )
    await expect(api.sensorRange('Flower Room', undefined, undefined, undefined, { timeoutMs: 5 })).rejects.toBeInstanceOf(
      MonitoringTimeoutError,
    )

    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(api.sensorRange('Flower Room')).rejects.toBeInstanceOf(MonitoringNetworkError)
  })

  it('respects an external AbortSignal', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()
    const controller = new AbortController()
    let networkSignal: AbortSignal | undefined

    fetchMock.mockImplementation(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          networkSignal = init?.signal ?? undefined
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        }),
    )

    const pending = api.sensorRange('Flower Room', undefined, undefined, undefined, { signal: controller.signal })
    expect(networkSignal).toBeDefined()
    controller.abort()
    expect(networkSignal?.aborted).toBe(true)
    await expect(pending).rejects.toBeInstanceOf(MonitoringAbortError)
  })

  it('classifies an already-aborted external signal before the request starts', async () => {
    const fetchMock = vi.mocked(fetch)
    const api = new MonitoringApi()
    const controller = new AbortController()
    controller.abort()

    fetchMock.mockImplementation((_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.signal?.aborted) {
        return Promise.reject(new DOMException('Aborted', 'AbortError'))
      }
      return Promise.resolve(jsonResponse(sensorRangePayload))
    })

    await expect(
      api.sensorRange('Flower Room', undefined, undefined, undefined, {
        signal: controller.signal,
        timeoutMs: 1000,
      }),
    ).rejects.toBeInstanceOf(MonitoringAbortError)
  })
})
