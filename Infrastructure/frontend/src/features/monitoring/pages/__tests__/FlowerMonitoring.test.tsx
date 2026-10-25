/**
 * Shallow test for the Flower monitoring page.
 *
 * Mounts `FlowerMonitoring` under jsdom with a faked `MonitoringStore` binding
 * (no network) and a mocked uPlot so no real canvas is created. Asserts the
 * toolbar, chart regions, and canonical tables render without an iframe.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef, useReducer } from 'react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import type uPlot from 'uplot'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { ThemeProvider } from '../../../../contexts/ThemeContext'
import FlowerMonitoring from '../../../../pages/FlowerMonitoring'
import type { MonitoringRange } from '../../state'
import { torontoDateString, torontoMidnightUtc } from '../../../../utils/torontoWallTime'

const {
  budgetReporters,
  pageRenderCount,
  snapshot,
  store,
  overviewViewProps,
  overviewSelection,
  overviewWideSelection,
  zoomReporters,
  notify,
} = vi.hoisted(() => {
  const t = new Date('2026-08-02T12:00:00.000Z')
  const notify: { current: (() => void) | null } = { current: null }
  const budgetReporters: Array<(budget: number) => void> = []
  const zoomReporters: Array<(range: { start: Date; end: Date }) => void> = []
  const pageRenderCount = { current: 0 }
  const overviewViewProps: Array<{
    room: string
    range: { start: Date; end: Date }
    onDetail: (start: Date, end: Date) => void
    historicalDrillDown?: boolean
  }> = []
  const overviewSelection = {
    start: new Date('2026-07-23T00:00:00.000Z'),
    end: new Date('2026-07-26T00:00:00.000Z'),
  }
  const overviewWideSelection = {
    start: new Date('2026-07-02T00:00:00.000Z'),
    end: new Date('2026-07-25T00:00:00.000Z'),
  }
  const store = {
    getSnapshot: () => snapshot,
    setLiveRange: vi.fn((duration: number) => {
      snapshot.range = { kind: 'live', duration }
      snapshot.isLive = true
      notify.current?.()
    }),
    setFixedRange: vi.fn((start: Date, end: Date) => {
      snapshot.range = { kind: 'fixed', start, end }
      snapshot.isLive = false
      notify.current?.()
    }),
    setRangeBudget: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    retry: vi.fn(),
    lastLiveDurationMs: () => 3 * 3600_000,
    sensorRange: vi.fn(),
    controlRange: vi.fn(),
  }
  const snapshot = {
    range: { kind: 'live', duration: 3 * 3600_000 } as MonitoringRange,
    fulfilledRange: {
      range: { kind: 'live', duration: 3 * 3600_000 },
      start: new Date('2026-08-02T09:00:00.000Z'),
      end: t,
      revision: 1,
    },
    isLive: true,
    data: {
      series: [
        {
          sensor: 'dry_bulb_f',
          node: 'front',
          unit_family: 'celsius',
          unit: '°C',
          points: [{ timestamp: t, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 }],
        },
        {
          sensor: 'dry_bulb_b',
          node: 'back',
          unit_family: 'celsius',
          unit: '°C',
          points: [{ timestamp: t, average: 25.1, minimum: 24.7, maximum: 25.5, sample_count: 60 }],
        },
      ],
      statistics: [
        {
          sensor: 'dry_bulb_f',
          node: 'front',
          minimum: 24.1,
          maximum: 24.9,
          average: 24.5,
          stddev_samp: 0.2,
          sample_count: 60,
        },
      ],
      live: [
        { sensor: 'dry_bulb_f', value: 24.6, timestamp: t },
        { sensor: 'dry_bulb_b', value: 25.1, timestamp: t },
        { sensor: 'rh_f', value: 62, timestamp: t },
        { sensor: 'rh_b', value: 58, timestamp: t },
      ],
      controlHistory: null,
      projectionHistory: null,
      photoperiod: [],
      cursors: [],
      projectionRevision: null,
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
  }
  return {
    budgetReporters,
    pageRenderCount,
    snapshot,
    store,
    overviewViewProps,
    overviewSelection,
    overviewWideSelection,
    zoomReporters,
    notify,
  }
})

const { getCalendarCurrentRun } = vi.hoisted(() => ({ getCalendarCurrentRun: vi.fn() }))

vi.mock('../../../../services/api', () => ({ apiClient: { getCalendarCurrentRun } }))

vi.mock('../../../history-overview', async importOriginal => {
  const actual = await importOriginal<typeof import('../../../history-overview')>()
  return {
    ...actual,
    HistoryOverviewView: (props: {
      room: string
      range: { start: Date; end: Date }
      onDetail: (start: Date, end: Date) => void
      historicalDrillDown?: boolean
    }) => {
      overviewViewProps.push(props)
      return (
        <div data-testid="history-overview-view">
          <button
            type="button"
            onClick={() => props.onDetail(overviewSelection.start, overviewSelection.end)}
          >
            Open detailed range
          </button>
          <button
            type="button"
            onClick={() => props.onDetail(overviewWideSelection.start, overviewWideSelection.end)}
          >
            Open wide history range
          </button>
        </div>
      )
    },
  }
})

vi.mock('../useMonitoringStore', () => ({
  FLOWER_DEFAULT_DURATION_MS: 3 * 3600_000,
  useMonitoringStore: () => {
    const [, force] = useReducer((count: number) => count + 1, 0)
    notify.current = force
    pageRenderCount.current += 1
    return { snapshot, store }
  },
}))

vi.mock('../../charts', async importOriginal => {
  const actual = await importOriginal<typeof import('../../charts')>()
  return {
    ...actual,
    UPlotChart: forwardRef(function MockUPlotChart(
      {
        onRequestBudgetChange,
        onZoom,
      }: {
        readonly onRequestBudgetChange?: (budget: number) => void
        readonly onZoom?: (range: { start: Date; end: Date }) => void
      },
      _ref
    ) {
      if (onRequestBudgetChange) budgetReporters.push(onRequestBudgetChange)
      if (onZoom) zoomReporters.push(onZoom)
      return <div />
    }),
  }
})

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: unknown[] = []
  class MockUPlot {
    static instances = instances
    opts: uPlot.Options
    data: uPlot.AlignedData
    root: HTMLElement
    scales = { x: { min: 0, max: 100 } }
    setData = vi.fn()
    setSize = vi.fn()
    setScale = vi.fn()
    setSeries = vi.fn()
    destroy = vi.fn()
    constructor(opts: uPlot.Options, data?: uPlot.AlignedData, target?: HTMLElement) {
      this.opts = opts
      this.data = data ?? []
      this.root = document.createElement('div')
      if (target) target.appendChild(this.root)
      instances.push(this)
    }
  }
  return { MockUPlot, instances }
})

vi.mock('uplot', () => ({ default: MockUPlot }))

class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  observe = vi.fn()
  disconnect = vi.fn()
  unobserve = vi.fn()
  constructor(_cb: ResizeObserverCallback) {
    MockResizeObserver.instances.push(this)
  }
}

const INITIAL_SENSOR_SERIES = structuredClone(snapshot.data.series)

beforeEach(() => {
  instances.length = 0
  budgetReporters.length = 0
  zoomReporters.length = 0
  overviewViewProps.length = 0
  pageRenderCount.current = 0
  snapshot.range = { kind: 'live', duration: 3 * 3600_000 }
  snapshot.isLive = true
  snapshot.data.series = structuredClone(INITIAL_SENSOR_SERIES)
  MockResizeObserver.instances.length = 0
  store.setLiveRange.mockClear()
  store.setFixedRange.mockClear()
  store.setRangeBudget.mockClear()
  store.sensorRange.mockClear()
  store.controlRange.mockClear()
  getCalendarCurrentRun.mockReset()
  getCalendarCurrentRun.mockResolvedValue(null)
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

function renderPage(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ThemeProvider>
        <FlowerMonitoring />
      </ThemeProvider>
    </MemoryRouter>
  )
}

/**
 * Route-aware harness: navigating between the Historical and regular
 * Monitoring paths remounts the page the same way the app router does, so the
 * URL a handoff writes is what the next surface parses.
 */
function renderRouted(initialEntry: string) {
  const router = createMemoryRouter(
    [
      { path: '/flower/monitoring', element: <FlowerMonitoring /> },
      { path: '/flower/historical', element: <FlowerMonitoring /> },
    ],
    { initialEntries: [initialEntry] }
  )
  render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  )
  return router
}

describe('FlowerMonitoring page', () => {
  it('renders toolbar, chart regions, and tables without an iframe', () => {
    const { container } = renderPage()

    expect(screen.getByText('LIVE')).toBeTruthy()

    expect(screen.getByRole('heading', { name: 'Flower climate conditions' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeTruthy()

    expect(screen.getByRole('table', { name: 'Averages' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Front Cluster' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Back Cluster' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Statistics - All Available Sensors' })).toBeTruthy()

    expect(container.querySelector('iframe')).toBeNull()
  })

  it('renders live values in the Back Cluster table', () => {
    renderPage()
    const backTable = screen.getByRole('table', { name: 'Back Cluster' })
    expect(within(backTable).getByText('Dry Bulb')).toBeTruthy()
    expect(within(backTable).getByText('25.1°C')).toBeTruthy()
  })

  it('reports the narrowest current chart budget from both chart keys', () => {
    renderPage()

    expect(budgetReporters.length).toBeGreaterThanOrEqual(2)
    const [climate, device] = budgetReporters.slice(-2)
    act(() => {
      climate(800)
      device(1_200)
    })

    expect(store.setRangeBudget).toHaveBeenLastCalledWith(800)
  })

  it('recomputes the narrowest budget after a chart narrows further', () => {
    renderPage()

    const [climate, device] = budgetReporters.slice(-2)
    act(() => {
      climate(1_200)
      device(800)
      climate(600)
    })

    expect(store.setRangeBudget).toHaveBeenLastCalledWith(600)
  })

  it('does not issue range requests when charts report budgets', () => {
    renderPage()

    act(() => {
      budgetReporters[0](800)
      budgetReporters[1](1_200)
    })

    expect(store.sensorRange).not.toHaveBeenCalled()
    expect(store.controlRange).not.toHaveBeenCalled()
  })

  it('does not rerender the page when charts report budgets', () => {
    renderPage()
    const rendersBeforeBudgetReports = pageRenderCount.current

    act(() => {
      budgetReporters[0](800)
      budgetReporters[1](1_200)
    })

    expect(pageRenderCount.current).toBe(rendersBeforeBudgetReports)
  })

  it('preserves chart space without mounting charts before sensor series load', () => {
    snapshot.data.series = []
    const { container } = renderPage()

    expect(instances).toHaveLength(0)
    const chartSlots = [...container.querySelectorAll('div')]
      .map(element => element.style.height)
      .filter(height => height === '780px' || height === '320px')
    expect(chartSlots).toEqual(['780px', '320px'])
  })
  it('renders the budget placeholder frame on the first chart panel', () => {
    snapshot.data.series = []
    const { container } = renderPage()

    const frames = [...container.querySelectorAll('.mon-chart__frame')]
    expect(frames).toHaveLength(2)
  })

  it('redirects an initial Monitoring fixed URL longer than seven days to Historical', () => {
    const router = renderRouted(
      '/flower/monitoring?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z&fixtureSession=long'
    )

    expect(router.state.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('start')).toBe('2026-07-01T00:00:00.000Z')
    expect(params.get('end')).toBe('2026-08-01T00:00:00.000Z')
    expect(params.get('fixtureSession')).toBe('long')
    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
    expect(pageRenderCount.current).toBe(0)
    expect(store.setFixedRange).not.toHaveBeenCalled()
  })

  it('keeps exactly seven days detailed on Monitoring', () => {
    renderRouted(
      '/flower/monitoring?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
    )

    expect(pageRenderCount.current).toBeGreaterThan(0)
    expect(overviewViewProps).toHaveLength(0)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-15T10:00:00.000Z'),
      new Date('2026-07-22T10:00:00.000Z')
    )
  })

  it('navigates a custom Monitoring range longer than seven days to Historical with the exact range', () => {
    const router = renderRouted('/flower/monitoring?fixtureSession=custom')

    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-01T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-11T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('start')).toBe('2026-07-01T14:00:00.000Z')
    expect(params.get('end')).toBe('2026-07-11T16:00:00.000Z')
    expect(params.get('fixtureSession')).toBe('custom')
    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
  })

  it('turns a drag selection that reaches now into a live range with the selected duration', () => {
    renderPage()
    store.setLiveRange.mockClear()
    store.setFixedRange.mockClear()
    const now = Date.now()

    act(() => {
      zoomReporters.at(-1)?.({
        start: new Date(now - 20 * 60_000),
        end: new Date(now + 5 * 60_000),
      })
    })

    const [duration] = store.setLiveRange.mock.calls.at(-1) ?? []
    expect(duration).toBeGreaterThanOrEqual(20 * 60_000)
    expect(duration).toBeLessThanOrEqual(20 * 60_000 + 5_000)
    expect(store.setFixedRange).not.toHaveBeenCalled()
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })

  it('turns a past drag selection into a fixed range', () => {
    renderPage()
    store.setLiveRange.mockClear()
    store.setFixedRange.mockClear()
    const now = Date.now()
    const start = new Date(now - 40 * 60_000)
    const end = new Date(now - 20 * 60_000)

    act(() => {
      zoomReporters.at(-1)?.({ start, end })
    })

    expect(store.setFixedRange).toHaveBeenLastCalledWith(start, end)
    expect(store.setLiveRange).not.toHaveBeenCalled()
  })

  it('keeps a fully-future drag selection fixed for a projection-only view', () => {
    renderPage()
    store.setLiveRange.mockClear()
    store.setFixedRange.mockClear()
    const now = Date.now()
    const start = new Date(now + 10 * 60_000)
    const end = new Date(now + 20 * 60_000)

    act(() => {
      zoomReporters.at(-1)?.({ start, end })
    })

    expect(store.setFixedRange).toHaveBeenLastCalledWith(start, end)
    expect(store.setLiveRange).not.toHaveBeenCalled()
  })

  it('shows Now only on a fixed range and returns to the last live duration', () => {
    renderPage('/?start=2026-07-15T10:00:00.000Z&end=2026-07-15T12:00:00.000Z')

    expect(screen.getByText('FIXED')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Now' }))

    expect(store.setLiveRange).toHaveBeenLastCalledWith(3 * 3600_000)
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })

  it('hides Now while the range is live', () => {
    renderPage()

    expect(screen.queryByRole('button', { name: 'Now' })).toBeNull()
  })
})

describe('FlowerMonitoring historical route', () => {
  it('swaps to the historical presets and loads the room current run', async () => {
    renderRouted('/flower/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z')

    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
    for (const label of ['5 days', '7 days', '2 weeks', '1 month', '3 months', 'Current run']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
    expect(screen.queryByRole('button', { name: 'Now' })).toBeNull()
    expect(screen.queryByRole('button', { name: '1h' })).toBeNull()

    expect(overviewViewProps.at(-1)?.historicalDrillDown).toBe(true)
    await waitFor(() =>
      expect(getCalendarCurrentRun).toHaveBeenCalledWith(
        'Flower Room',
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      )
    )
  })

  it('keeps the regular Monitoring route on the live presets and never fetches the calendar', () => {
    renderPage('/')

    expect(screen.getByRole('button', { name: '1h' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Now' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Current run' })).toBeNull()
    expect(getCalendarCurrentRun).not.toHaveBeenCalled()
  })

  it('keeps the seven-day preset on the Historical detailed reader', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
    )
    expect(pageRenderCount.current).toBeGreaterThan(0)

    const before = Date.now()
    fireEvent.click(screen.getByRole('button', { name: '7 days' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    const call = store.setFixedRange.mock.calls.at(-1)
    if (call === undefined) throw new Error('expected a fixed-range apply')
    const [start, end] = call
    expect(end.getTime() - start.getTime()).toBe(7 * 24 * 3600_000)
    expect(end.getTime()).toBeGreaterThanOrEqual(before)
    expect(end.getTime()).toBeLessThanOrEqual(Date.now())
  })

  it('switches the Historical page to the overview for the two-week preset', () => {
    renderRouted('/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z')

    fireEvent.click(screen.getByRole('button', { name: '2 weeks' }))

    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
    const view = overviewViewProps.at(-1)
    expect((view?.range.end.getTime() ?? 0) - (view?.range.start.getTime() ?? 0)).toBe(
      14 * 24 * 3600_000
    )
  })

  it('keeps the five-day preset on Historical with its exact fixed range', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z&scenario=no-current&fixtureSession=hist-7'
    )

    fireEvent.click(screen.getByRole('button', { name: '5 days' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('scenario')).toBe('no-current')
    expect(params.get('fixtureSession')).toBe('hist-7')
    const start = new Date(params.get('start') ?? '')
    const end = new Date(params.get('end') ?? '')
    expect(end.getTime() - start.getTime()).toBe(5 * 24 * 3600_000)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(start, end)
  })

  it('keeps a sub-five-day absolute Historical range on Historical exactly', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-15T00:00:00.000Z&end=2026-07-22T00:00:00.000Z&fixtureSession=hist-2'
    )

    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-20T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-20T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(router.state.location.search)
    // The exact two-hour range is preserved — never stretched or handed off.
    expect(params.get('start')).toBe('2026-07-20T14:00:00.000Z')
    expect(params.get('end')).toBe('2026-07-20T16:00:00.000Z')
    expect(params.get('fixtureSession')).toBe('hist-2')
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-20T14:00:00.000Z'),
      new Date('2026-07-20T16:00:00.000Z')
    )
  })

  it('keeps a wide overview selection on the Historical page with its actual span', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open wide history range' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    const view = overviewViewProps.at(-1)
    expect(view?.range.start.toISOString()).toBe('2026-07-02T00:00:00.000Z')
    expect(view?.range.end.toISOString()).toBe('2026-07-25T00:00:00.000Z')
  })

  it('zooms a short overview selection in place to the exact selection', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open detailed range' }))

    expect(router.state.location.pathname).toBe('/flower/historical')
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-23T00:00:00.000Z'),
      new Date('2026-07-26T00:00:00.000Z')
    )
    expect(pageRenderCount.current).toBeGreaterThan(0)
  })

  it('keeps a sub-seven-day zoom on Historical and writes the exact URL range', () => {
    const router = renderRouted(
      '/flower/historical?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z&fixtureSession=hist-9'
    )
    expect(pageRenderCount.current).toBeGreaterThan(0)

    act(() => {
      zoomReporters.at(-1)?.({
        start: new Date('2026-07-16T00:00:00.000Z'),
        end: new Date('2026-07-22T00:00:00.000Z'),
      })
    })

    expect(router.state.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('start')).toBe('2026-07-16T00:00:00.000Z')
    expect(params.get('end')).toBe('2026-07-22T00:00:00.000Z')
    expect(params.get('fixtureSession')).toBe('hist-9')
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-16T00:00:00.000Z'),
      new Date('2026-07-22T00:00:00.000Z')
    )
  })

  it('floors a Historical zoom to five minutes by extending the end', () => {
    renderRouted(
      '/flower/historical?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
    )
    const start = new Date('2026-07-16T09:00:00.000Z')
    const end = new Date('2026-07-16T09:01:00.000Z')

    act(() => {
      zoomReporters.at(-1)?.({ start, end })
    })

    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      start,
      new Date(start.getTime() + 5 * 60_000)
    )
  })

  it('restores the previous ranges with Reset Zoom and disables it when the stack is empty', () => {
    renderRouted(
      '/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
    )
    const reset = screen.getByRole('button', { name: 'Reset Zoom' })
    expect(reset).toBeDisabled()

    const first = {
      start: new Date('2026-07-18T00:00:00.000Z'),
      end: new Date('2026-07-19T00:00:00.000Z'),
    }
    act(() => {
      zoomReporters.at(-1)?.(first)
    })
    const second = {
      start: new Date('2026-07-18T06:00:00.000Z'),
      end: new Date('2026-07-18T12:00:00.000Z'),
    }
    act(() => {
      zoomReporters.at(-1)?.(second)
    })
    expect(reset).toBeEnabled()

    fireEvent.click(reset)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(first.start, first.end)

    fireEvent.click(reset)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-16T10:00:00.000Z'),
      new Date('2026-07-22T10:00:00.000Z')
    )
    expect(reset).toBeDisabled()
  })

  it('disables the Current run action with the honest reason when the calendar has no run', async () => {
    renderRouted('/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z')

    const button = screen.getByRole('button', { name: 'Current run' })
    await waitFor(() =>
      expect(button).toHaveAttribute(
        'title',
        'No active current run in the Flower Room calendar'
      )
    )
    expect(button).toBeDisabled()
  })

  it('applies the unique current run from Toronto-midnight calendar start to now', async () => {
    const startDate = torontoDateString(Date.now() - 10 * 24 * 3600_000)
    getCalendarCurrentRun.mockResolvedValue({
      location: 'Flower Room',
      grow_plan_id: 'plan-1',
      start_date: startDate,
      end_date: '2026-12-31',
    })
    renderRouted('/flower/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z')

    const button = screen.getByRole('button', { name: 'Current run' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)

    const view = overviewViewProps.at(-1)
    expect(view?.range.start.toISOString()).toBe(torontoMidnightUtc(startDate)?.toISOString())
    expect(Math.abs((view?.range.end.getTime() ?? 0) - Date.now())).toBeLessThan(10_000)
  })
})
