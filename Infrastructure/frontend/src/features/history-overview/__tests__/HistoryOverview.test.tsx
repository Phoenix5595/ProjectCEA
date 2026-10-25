/**
 * Consumer-behavior tests for the history-overview feature.
 *
 * Covers the aligned builder's null-lattice/exact fraction semantics, the
 * atomic sensor+control hook generation (stale and abort behavior), the
 * overview view's canonical labels/error path, and the chart's click/zoom
 * drill-down mapping onto the original fixed overview request.
 */
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import type { Mock } from 'vitest'

import { ThemeProvider } from '../../../contexts/ThemeContext'
import { flowerManifest } from '../../monitoring/config'
import type { AlignedData } from '../../monitoring/data'
import { historyOverviewControlContract, historyOverviewSensorContract } from '../contracts'
import { buildHistoryOverviewAlignedData } from '../aligned'
import { HistoryOverviewChart, HistoryOverviewView } from '../HistoryOverviewChart'
import { useHistoryOverview } from '../useHistoryOverview'
import { seriesKey } from '@/features/monitoring/data/alignSeries.types'

type FixedRequestRange = { kind: 'fixed'; start: Date; end: Date }
type OverviewRole = 'sensor' | 'control' | 'climate'

/** Measured plot budget the view placeholder produces at clientWidth 1160. */
const TEST_MAX_POINTS = 1000

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS
const OVERVIEW_RANGE = { start: '2026-03-01T00:00:00.000Z', end: '2026-03-01T08:00:00.000Z' }
const TINY_RANGE = { start: '2026-04-01T00:00:00.000Z', end: '2026-04-01T08:00:00.000Z' }
const LONG_RANGE = {
  start: '2019-01-01T00:00:00.000Z',
  end: '2025-01-01T00:00:00.000Z',
}

// ---------------------------------------------------------------------------
// uPlot mock with an addressable `.u-over` overlay
// ---------------------------------------------------------------------------

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
  class MockUPlot {
    static instances = instances
    static paths = { stepped: () => () => null }
    opts: unknown
    data: unknown
    root: HTMLElement
    scales: { x: { min: number; max: number } } = { x: { min: 0, max: 100 } }
    select: Record<string, unknown> = { show: false, width: 0, left: 0, top: 0, height: 0 }
    setData = vi.fn()
    setSize = vi.fn()
    setSeries = vi.fn()
    destroy = vi.fn()
    setScale = vi.fn((key: string, scale: { min: number; max: number }) => {
      if (key === 'x') this.scales.x = scale
    })
    constructor(opts: unknown, data?: unknown, target?: HTMLElement) {
      this.opts = opts
      this.data = data ?? []
      this.root = document.createElement('div')
      const overlay = document.createElement('div')
      overlay.className = 'u-over'
      Object.defineProperty(overlay, 'getBoundingClientRect', {
        configurable: true,
        value: () => ({
          left: 0,
          top: 0,
          right: 800,
          bottom: 100,
          width: 800,
          height: 100,
        }),
      })
      this.root.appendChild(overlay)
      if (target !== undefined) target.appendChild(this.root)
      instances.push(this)
    }
    callSelectHook(): void {
      const hooks = (this.opts as { hooks?: { setSelect?: Array<(self: unknown) => void> } }).hooks
        ?.setSelect
      hooks?.forEach(hook => hook(this))
    }
    userDragZoom(min: number, max: number): void {
      this.select = { show: true, width: 40, left: 0, top: 0, height: 0 }
      this.callSelectHook()
      this.setScale('x', { min, max })
      const hooks = (
        this.opts as { hooks?: { setScale?: Array<(self: unknown, key: unknown) => void> } }
      ).hooks?.setScale
      hooks?.forEach(hook => hook(this, 'x'))
    }
  }
  return { MockUPlot, instances }
})

vi.mock('uplot', () => ({ default: MockUPlot }))

// ---------------------------------------------------------------------------
// Overview payload fixtures
// ---------------------------------------------------------------------------

interface DeferredResponse {
  promise: Promise<Response>
  resolve: (value: Response) => void
}

function deferred(): DeferredResponse {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>(executor => {
    resolve = executor
  })
  return { promise, resolve }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface RequestedOverview {
  readonly start: string
  readonly end: string
  readonly maxPoints: number
}

/**
 * Request echo (range + budget) so request-bound parse passes on every
 * generation: response metadata must follow the actual query, never a
 * hardcoded budget.
 */
function requestedOverviewFromUrl(url: string): RequestedOverview {
  const parsed = new URL(url, 'http://fixture.local')
  return {
    start: parsed.searchParams.get('start') ?? OVERVIEW_RANGE.start,
    end: parsed.searchParams.get('end') ?? OVERVIEW_RANGE.end,
    maxPoints: Number(parsed.searchParams.get('max_points') ?? TEST_MAX_POINTS),
  }
}

function bucketPoint(
  range: { start: string; end: string },
  index: number,
  overrides: Record<string, unknown> = {}
) {
  const startMs = new Date(range.start).getTime() + index * HOUR_MS
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(startMs + HOUR_MS).toISOString(),
    ...overrides,
  }
}
function sampledPoint(
  range: { start: string; end: string },
  index: number,
  value: number,
  count: number
) {
  return {
    ...bucketPoint(range, index),
    average: value,
    minimum: value - 0.5,
    maximum: value + 0.5,
    sample_count: count,
    partial: false,
  }
}

function zeroPoint(range: { start: string; end: string }, index: number) {
  return {
    ...bucketPoint(range, index),
    average: null,
    minimum: null,
    maximum: null,
    sample_count: 0,
    partial: false,
  }
}

function sensorPayload(request: RequestedOverview, seriesName: string) {
  return {
    metadata: {
      generated_at: '2026-03-01T08:00:01.000Z',
      range: { start: request.start, end: request.end },
      room: { room: 'Flower Room', nodes: ['front'] },
      requested_max_points: request.maxPoints,
      bucket: { kind: 'fixed', seconds: 3600 },
      source_coverage: 'ready',
      all_aggregated: true,
    },
    series: [
      {
        sensor: seriesName,
        node: 'front',
        unit: '°C',
        unit_family: 'celsius',
        points: [sampledPoint(request, 0, 20.5, 17), zeroPoint(request, 1)],
      },
    ],
    statistics: [
      {
        sensor: seriesName,
        node: 'front',
        sample_count: 17,
        minimum: 20,
        maximum: 21,
        average: 20.5,
        stddev_samp: 0.3,
        stddev_quality: 'exact',
      },
    ],
  }
}

function controlPayload(request: RequestedOverview, channel: number) {
  return {
    metadata: {
      generated_at: '2026-03-01T08:00:01.000Z',
      range: { start: request.start, end: request.end },
      room: { room: 'Flower Room', nodes: ['front'] },
      requested_max_points: request.maxPoints,
      bucket: { kind: 'fixed', seconds: 3600 },
      source_coverage: 'ready',
      all_aggregated: true,
    },
    lights: [],
    requested_pid: [
      {
        location: 'Flower Room',
        cluster: 'front',
        device_name: 'Heater Flower',
        points: [sampledPoint(request, 0, 12.5, 2), zeroPoint(request, 1)],
      },
    ],
    relays: [
      {
        location: 'Flower Room',
        cluster: 'front',
        channel,
        device_id: 11,
        device_name: 'Exhaust Fan',
        device_type: 'fan',
        points: [
          {
            ...bucketPoint(request, 0),
            on_seconds: 3000,
            off_seconds: 600,
            unknown_seconds: 0,
            coverage_complete: true,
            partial: false,
          },
          {
            ...bucketPoint(request, 1),
            on_seconds: 0,
            off_seconds: 0,
            unknown_seconds: 3600,
            coverage_complete: false,
            partial: false,
          },
        ],
      },
    ],
  }
}

/**
 * Lights-scope overview body: sampled canonical light identity with empty
 * PID/relay groups, mirroring the strict scope contract a room overview read
 * must satisfy.
 */
function lightsControlPayload(request: RequestedOverview) {
  return {
    ...controlPayload(request, 3),
    lights: [
      {
        location: 'Flower Room',
        cluster: 'front',
        device_name: 'light_f_1',
        points: [sampledPoint(request, 0, 80, 3), zeroPoint(request, 1)],
      },
    ],
    requested_pid: [],
    relays: [],
  }
}

function climateHistoryPayload(request: RequestedOverview) {
  const provenance = { origin: 'recorded', quality: 'exact', is_aggregated: false } as const
  return {
    range: { start: request.start, end: request.end },
    runtime_snapshot_version: 1,
    requested_max_points: request.maxPoints,
    interval_seconds: null,
    cursors: [],
    flush_health: [],
    climate: [
      {
        name: 'heating_setpoint',
        metric: 'heating_setpoint',
        trajectory_kind: 'effective',
        provenance,
        warnings: [],
        points: [
          {
            time: request.start,
            metric: 'heating_setpoint',
            value: 22,
            nominal_value: 22,
            provenance,
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
        time: request.start,
        phase: 'SUN',
        provenance,
      },
    ],
  }
}

const OVERVIEW_REQUEST: RequestedOverview = {
  start: OVERVIEW_RANGE.start,
  end: OVERVIEW_RANGE.end,
  maxPoints: TEST_MAX_POINTS,
}

/** One payload per endpoint role, echoing the actual request range/budget. */
function payloadFor(
  role: OverviewRole,
  scope: RequestedOverviewScope,
  request: RequestedOverview
): unknown {
  if (role === 'sensor') return sensorPayload(request, 'dry_bulb_f')
  if (role === 'climate') return climateHistoryPayload(request)
  return scope === 'lights' ? lightsControlPayload(request) : controlPayload(request, 3)
}
const parsedSensor = historyOverviewSensorContract({
  range: { start: new Date(OVERVIEW_RANGE.start), end: new Date(OVERVIEW_RANGE.end) },
  maxPoints: TEST_MAX_POINTS,
}).parse(sensorPayload(OVERVIEW_REQUEST, 'dry_bulb_f'))
const parsedControl = historyOverviewControlContract({
  range: { start: new Date(OVERVIEW_RANGE.start), end: new Date(OVERVIEW_RANGE.end) },
  maxPoints: TEST_MAX_POINTS,
}).parse(controlPayload(OVERVIEW_REQUEST, 3))

function fixedRange(range: { start: string; end: string }): FixedRequestRange {
  return { kind: 'fixed', start: new Date(range.start), end: new Date(range.end) }
}

const SENSOR_URL_MARKER = '/api/sensors/monitoring/overview/'
type RequestedOverviewScope = 'lights' | 'all' | 'climate'

/** Stub fetch by endpoint role with URL-parameter-aware payload echoes. */
function stubOverviewFetch(
  fetchMock: Mock,
  handler: (
    role: OverviewRole,
    callIndex: number,
    request: RequestedOverview,
    scope: RequestedOverviewScope
  ) => Promise<Response> | Response
): void {
  let callIndex = 0
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    const path = new URL(url, 'http://fixture.local')
    const role: OverviewRole = url.includes(SENSOR_URL_MARKER)
      ? 'sensor'
      : path.pathname.endsWith('/overview')
        ? 'control'
        : 'climate'
    const requestedScope = path.searchParams.get('scope')
    const scope: RequestedOverviewScope =
      requestedScope === 'lights' ? 'lights' : requestedScope === 'climate' ? 'climate' : 'all'
    const resolved = handler(role, callIndex, requestedOverviewFromUrl(url), scope)
    callIndex += 1
    return Promise.resolve(resolved)
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => 1160,
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => 400,
  })
  vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
    callback(0)
    return 0
  })
  instances.length = 0
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('buildHistoryOverviewAlignedData', () => {
  it('lattices sampled values onto exact midpoints with honest gaps', () => {
    const aligned = buildHistoryOverviewAlignedData({
      sensor: parsedSensor,
      control: parsedControl,
      manifest: flowerManifest,
      includeAutomation: true,
    })
    const bucketStart = new Date(OVERVIEW_RANGE.start).getTime()
    expect(aligned.x).toHaveLength(8)
    for (const [index, value] of aligned.x.entries()) {
      expect(value).toBe(bucketStart + index * HOUR_MS + HOUR_MS / 2)
    }
    expect(aligned.overview?.buckets).toHaveLength(8)
    expect(aligned.overview?.buckets[0]?.start).toBe(bucketStart)

    const mean = aligned.series.find(series => series.key === 'sensor:dry_bulb_f:mean')
    expect(mean?.y[0]).toBeCloseTo(20.5, 9)
    for (const index of [1, 2, 3, 4, 5, 6, 7]) {
      expect(mean?.y[index]).toBeNull()
    }
    expect(mean?.preserveGaps).toBe(true)
    expect(mean?.origin).toBe('derived')
    expect(mean?.isAggregated).toBe(true)
    expect(mean?.label).toBe('Dry Bulb (°C) - Front')
    expect(mean?.unit).toBe('°C')

    const sensorBand = aligned.bands.find(band => band.key === 'sensor:dry_bulb_f:band')
    expect(sensorBand?.minKey).toBe('sensor:dry_bulb_f:min')
    expect(sensorBand?.maxKey).toBe('sensor:dry_bulb_f:max')

    const laneOn = aligned.series.find(series => series.key === 'device:Exhaust Fan R4·on:mean')
    expect(laneOn?.y[0]).toBeCloseTo((3000 / 3600) * 100, 6)
    expect(laneOn?.y[1]).toBeCloseTo(0, 9)
    const laneUnknown = aligned.series.find(
      series => series.key === 'device:Exhaust Fan R4·unknown:mean'
    )
    expect(laneUnknown?.y[0]).toBeCloseTo(0, 9)
    expect(laneUnknown?.y[1]).toBeCloseTo(100, 9)
    expect(aligned.series.some(series => series.label === 'Exhaust Fan R4 Unknown time')).toBe(true)
    expect(aligned.nowIndex).toBe(-1)
    expect(aligned.photoperiod).toHaveLength(0)
    expect(aligned.aggregated).toBe(true)
  })

  it('keeps an empty overview response as an honest empty aligned grid', () => {
    const aligned = buildHistoryOverviewAlignedData({
      sensor: { ...parsedSensor, series: [], statistics: [] },
      control: { ...parsedControl, lights: [], requested_pid: [], relays: [] },
      includeAutomation: false,
    })
    expect(aligned.series).toHaveLength(0)
    expect(aligned.bands).toHaveLength(0)
    expect(aligned.x).toHaveLength(8)
    expect(aligned.overview?.buckets).toHaveLength(8)
  })

  it('emits no PID or relay curves for a monitoring chart fed a full automation payload', () => {
    // A full-scope response must never repopulate room Monitoring/Historical
    // graphs: the automation groups exist only behind `includeAutomation`.
    const mixedControlPayload = historyOverviewControlContract({
      range: { start: new Date(OVERVIEW_RANGE.start), end: new Date(OVERVIEW_RANGE.end) },
      maxPoints: TEST_MAX_POINTS,
    }).parse({
      ...controlPayload(OVERVIEW_REQUEST, 3),
      lights: [
        {
          location: 'Flower Room',
          cluster: 'front',
          device_name: 'light_f_1',
          points: [sampledPoint(OVERVIEW_REQUEST, 0, 80, 3), zeroPoint(OVERVIEW_REQUEST, 1)],
        },
      ],
    })
    const aligned = buildHistoryOverviewAlignedData({
      sensor: parsedSensor,
      control: mixedControlPayload,
      manifest: flowerManifest,
      includeAutomation: false,
    })

    expect(aligned.series.some(series => series.source === 'pid')).toBe(false)
    expect(aligned.series.some(series => series.source === 'device')).toBe(false)
    expect(
      aligned.series.some(
        series => series.label.includes('PID Output') || series.label.includes('ON time')
      )
    ).toBe(false)
    expect(aligned.series.some(series => series.key === 'light:light_f_1:mean')).toBe(true)
    expect(aligned.series.some(series => series.key === 'sensor:dry_bulb_f:mean')).toBe(true)
  })
})

describe('useHistoryOverview', () => {
  it('publishes the atomic pair and never lets a stale generation repaint', async () => {
    const fetchMock = vi.mocked(fetch)
    const staleSensor = deferred()
    stubOverviewFetch(fetchMock, (role, callIndex, request, scope) => {
      if (callIndex === 0 && role === 'sensor') return staleSensor.promise
      return jsonResponse(payloadFor(role, scope, request))
    })

    const initialResult = renderHook(
      ({ range }: { range: FixedRequestRange }) =>
        useHistoryOverview({ room: 'Flower Room', range, maxPoints: TEST_MAX_POINTS }),
      { initialProps: { range: fixedRange(OVERVIEW_RANGE) } }
    )
    expect(initialResult.result.current.loading).toBe(true)

    // A different range starts the next generation without resolving the first.
    await act(async () => {
      initialResult.rerender({ range: fixedRange(TINY_RANGE) })
    })

    await waitFor(() => expect(initialResult.result.current.loading).toBe(false))
    expect(initialResult.result.current.error).toBeNull()
    const publishedSensor = initialResult.result.current.sensor
    const publishedControl = initialResult.result.current.control
    expect(publishedSensor).not.toBeNull()
    expect(publishedControl).not.toBeNull()

    // A late first-generation resolution may not overwrite the newer pair.
    await act(async () => {
      staleSensor.resolve(jsonResponse(sensorPayload(OVERVIEW_REQUEST, 'dry_bulb_f')))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(initialResult.result.current.sensor).toBe(publishedSensor)
    // Two reads for the superseded generation (its climate read is skipped on
    // abort) plus three for the current generation.
    expect(fetchMock).toHaveBeenCalledTimes(5)

    // The superseded generation's requests were aborted.
    const firstCall = fetchMock.mock.calls[0]
    expect(firstCall?.[1]?.signal?.aborted).toBe(true)
  })

  it('surfaces a sensor failure atomically and retries the full pair', async () => {
    const fetchMock = vi.mocked(fetch)
    let sensorFailureCount = 0
    stubOverviewFetch(fetchMock, (role, _callIndex, request, scope) => {
      if (role === 'sensor') {
        sensorFailureCount += 1
        if (sensorFailureCount === 1) return jsonResponse({ detail: 'boom' }, 500)
        return jsonResponse(sensorPayload(request, 'dry_bulb_f'))
      }
      return jsonResponse(payloadFor(role, scope, request))
    })

    const probe = renderHook(() =>
      useHistoryOverview({
        room: 'Flower Room',
        range: fixedRange(OVERVIEW_RANGE),
        maxPoints: TEST_MAX_POINTS,
      })
    )
    await waitFor(() => expect(probe.result.current.error).not.toBeNull())
    expect(probe.result.current.sensor).toBeNull()
    expect(probe.result.current.control).toBeNull()

    act(() => {
      probe.result.current.retry()
    })
    await waitFor(() => expect(probe.result.current.sensor).not.toBeNull())
    expect(probe.result.current.control).not.toBeNull()
    expect(probe.result.current.error).toBeNull()
  })

  it('requests the measured one-point-per-pixel budget on every read', async () => {
    const fetchMock = vi.mocked(fetch)
    stubOverviewFetch(fetchMock, (role, _callIndex, request, scope) =>
      jsonResponse(payloadFor(role, scope, request))
    )
    const probe = renderHook(() =>
      useHistoryOverview({
        room: 'Flower Room',
        range: fixedRange(OVERVIEW_RANGE),
        maxPoints: TEST_MAX_POINTS,
      })
    )
    await waitFor(() => expect(probe.result.current.loading).toBe(false))
    expect(probe.result.current.error).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    for (const call of fetchMock.mock.calls) {
      const url = new URL(String(call[0]), 'http://fixture.local')
      expect(url.searchParams.get('max_points')).toBe(String(TEST_MAX_POINTS))
    }
  })

  it('rejects an unfit range and budget before issuing any overview fetch', async () => {
    const fetchMock = vi.mocked(fetch)
    const probe = renderHook(() =>
      useHistoryOverview({ room: 'Flower Room', range: fixedRange(LONG_RANGE), maxPoints: 10 })
    )
    await waitFor(() => expect(probe.result.current.error).not.toBeNull())
    expect(probe.result.current.error).toBe(
      'history range and point budget cannot fit the maximum 12-hour bucket size'
    )
    expect(probe.result.current.loading).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('HistoryOverviewView rendering', () => {
  it('shows sensor statistics without relay, PID or light summary rows', async () => {
    const onDetail = vi.fn()
    const fetchMock = vi.mocked(fetch)
    stubOverviewFetch(fetchMock, (role, _callIndex, request, scope) =>
      jsonResponse(payloadFor(role, scope, request))
    )
    render(
      <ThemeProvider>
        <HistoryOverviewView
          room="Flower Room"
          range={fixedRange(OVERVIEW_RANGE)}
          manifest={flowerManifest}
          onDetail={onDetail}
        />
      </ThemeProvider>
    )
    await waitFor(() => {
      const summary = screen.getByRole('table', { name: 'Selected range summary' })
      expect(within(summary).getByText('Dry Bulb (°C) - Front')).toBeDefined()
      expect(within(summary).queryByRole('row', { name: /Exhaust Fan R4/ })).toBeNull()
      expect(within(summary).queryByRole('row', { name: /PID Output/ })).toBeNull()
      expect(within(summary).queryByRole('row', { name: /Chilled Front/ })).toBeNull()
    })
    expect(screen.queryByText('Exhaust Fan R4 ON time')).toBeNull()
    expect(screen.queryByText('Exhaust Fan R4 OFF time')).toBeNull()
    expect(screen.queryByText('Exhaust Fan R4 Unknown time')).toBeNull()
  })

  it('shows the control-only aggregated relay view without sensor requests', async () => {
    const fetchMock = vi.mocked(fetch)
    stubOverviewFetch(fetchMock, (role, _callIndex, request, scope) =>
      jsonResponse(payloadFor(role, scope, request))
    )
    render(
      <ThemeProvider>
        <HistoryOverviewView
          room="Flower Room"
          range={fixedRange(OVERVIEW_RANGE)}
          manifest={flowerManifest}
          controlOnly
          onDetail={vi.fn()}
        />
      </ThemeProvider>
    )
    await waitFor(() => expect(screen.getByText('Aggregated relay time')).toBeDefined())
    expect(fetchMock.mock.calls.every(call => !String(call[0]).includes(SENSOR_URL_MARKER))).toBe(
      true
    )
    expect(screen.getByText('Exhaust Fan R4 OFF time')).toBeDefined()
    expect(screen.queryByRole('region', { name: 'Selected range summary' })).toBeNull()
  })

  it('surfaces errors with a retry that republishes a fresh pair', async () => {
    const fetchMock = vi.mocked(fetch)
    let sensorFailureCount = 0
    stubOverviewFetch(fetchMock, (role, _callIndex, request, scope) => {
      if (role === 'sensor') {
        sensorFailureCount += 1
        if (sensorFailureCount === 1) return jsonResponse({ detail: 'unavailable' }, 503)
        return jsonResponse(sensorPayload(request, 'dry_bulb_f'))
      }
      return jsonResponse(payloadFor(role, scope, request))
    })
    render(
      <ThemeProvider>
        <HistoryOverviewView
          room="Flower Room"
          range={fixedRange(OVERVIEW_RANGE)}
          manifest={flowerManifest}
          onDetail={vi.fn()}
        />
      </ThemeProvider>
    )
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('History overview failed')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      const summary = screen.getByRole('table', { name: 'Selected range summary' })
      expect(within(summary).getByText('Dry Bulb (°C) - Front')).toBeDefined()
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('HistoryOverviewChart drill-down', () => {
  function mountChart(onDetail: (start: Date, end: Date) => void): void {
    const aligned: AlignedData = {
      x: [new Date(OVERVIEW_RANGE.start).getTime() + HOUR_MS / 2],
      series: [
        {
          key: seriesKey('sensor', 'dry_bulb_f', 'mean'),
          label: 'Dry Bulb (°C) - Front',
          kind: 'sensor',
          source: 'sensor',
          metric: 'dry_bulb_f',
          family: 'temperature',
          role: 'mean',
          y: [20.5],
          origin: 'derived',
          quality: 'exact',
          isAggregated: true,
          preserveGaps: true,
          unit: '°C',
          unitFamily: 'celsius',
        },
      ],
      bands: [],
      photoperiod: [],
      nowIndex: -1,
      nowMs: Date.now(),
      sensorIntervalSeconds: 3600,
      aggregated: true,
      overview: {
        buckets: [
          {
            start: new Date(OVERVIEW_RANGE.start).getTime(),
            end: new Date(OVERVIEW_RANGE.end).getTime(),
          },
        ],
      },
    }
    render(
      <ThemeProvider>
        <HistoryOverviewChart
          data={aligned}
          range={fixedRange(LONG_RANGE)}
          onDetail={onDetail}
          height={320}
          label="Aggregated history"
        />
      </ThemeProvider>
    )
    expect(instances).toHaveLength(1)
  }

  it('maps a plain click onto a centered one-day detail window', () => {
    const onDetail = vi.fn<(start: Date, end: Date) => void>()
    mountChart(onDetail)
    const chart = screen.getByRole('img', { name: 'Aggregated history' })
    fireEvent.pointerDown(chart, { clientX: 398, clientY: 50 })
    fireEvent.click(chart, { clientX: 400, clientY: 50 })
    expect(onDetail).toHaveBeenCalledTimes(1)
    const [start, end] = onDetail.mock.calls[0]
    const clickMs = (new Date(LONG_RANGE.start).getTime() + new Date(LONG_RANGE.end).getTime()) / 2
    expect(start.getTime()).toBe(clickMs - 12 * HOUR_MS)
    expect(end.getTime()).toBe(clickMs + 12 * HOUR_MS)
  })

  it('lets drag gestures be handled by the scale event, not the click fallback', () => {
    const onDetail = vi.fn<(start: Date, end: Date) => void>()
    mountChart(onDetail)
    const chart = screen.getByRole('img', { name: 'Aggregated history' })
    fireEvent.pointerDown(chart, { clientX: 100, clientY: 50 })
    fireEvent.click(chart, { clientX: 440, clientY: 50 })
    expect(onDetail).toHaveBeenCalledTimes(0)
  })

  it('routes drag-zoom scale changes through the drill-down clamp', () => {
    const onDetail = vi.fn<(start: Date, end: Date) => void>()
    mountChart(onDetail)
    const chart = instances[0]
    if (chart === undefined) throw new Error('mock uPlot instance missing')
    const clickMs = new Date(LONG_RANGE.start).getTime() + 12 * DAY_MS
    chart.userDragZoom(clickMs, clickMs + 30 * DAY_MS)
    expect(onDetail).toHaveBeenCalledTimes(1)
    const [start] = onDetail.mock.calls[0]
    expect(start.getTime()).toBe(clickMs + 15 * DAY_MS - Math.round((7 / 2) * DAY_MS))
  })
})
