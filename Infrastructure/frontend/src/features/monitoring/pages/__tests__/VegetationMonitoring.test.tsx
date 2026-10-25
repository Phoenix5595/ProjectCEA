/**
 * Shallow test for the Veg monitoring page.
 *
 * Mounts `VegetationMonitoring` under jsdom with a faked `MonitoringStore`
 * binding (no network) and a mocked uPlot so no real canvas is created. Asserts
 * the toolbar, chart regions, and canonical tables render without an iframe.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef, useReducer } from 'react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import type uPlot from 'uplot'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { ThemeProvider } from '../../../../contexts/ThemeContext'
import VegetationMonitoring from '../../../../pages/VegetationMonitoring'
import type { MonitoringRange } from '../../state'

const {
  budgetReporters,
  pageRenderCount,
  snapshot,
  store,
  overviewViewProps,
  overviewSelection,
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
    lastLiveDurationMs: () => 3600_000,
    sensorRange: vi.fn(),
    controlRange: vi.fn(),
  }
  const snapshot = {
    range: { kind: 'live', duration: 3600_000 } as MonitoringRange,
    fulfilledRange: {
      range: { kind: 'live', duration: 3600_000 },
      start: new Date('2026-08-02T11:00:00.000Z'),
      end: t,
      revision: 1,
    },
    isLive: true,
    data: {
      series: [
        {
          sensor: 'dry_bulb_v',
          node: 'main',
          unit_family: 'celsius',
          unit: '°C',
          points: [{ timestamp: t, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 }],
        },
        {
          sensor: 'pressure_v',
          node: 'main',
          unit_family: 'hpa',
          unit: ' hPa',
          points: [
            { timestamp: t, average: 1013.2, minimum: 1012.7, maximum: 1013.7, sample_count: 60 },
          ],
        },
      ],
      statistics: [
        {
          sensor: 'dry_bulb_v',
          node: 'main',
          minimum: 24.1,
          maximum: 24.9,
          average: 24.5,
          stddev_samp: 0.2,
          sample_count: 60,
        },
      ],
      live: [
        { sensor: 'dry_bulb_v', value: 24.6, timestamp: t },
        { sensor: 'rh_v', value: 62, timestamp: t },
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
    zoomReporters,
    notify,
  }
})
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
        </div>
      )
    },
  }
})

const { getCalendarCurrentRun } = vi.hoisted(() => ({ getCalendarCurrentRun: vi.fn() }))

vi.mock('../../../../services/api', () => ({ apiClient: { getCalendarCurrentRun } }))

vi.mock('../useMonitoringStore', () => ({
  VEG_DEFAULT_DURATION_MS: 3600_000,
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
  snapshot.range = { kind: 'live', duration: 3600_000 }
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
        <VegetationMonitoring />
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
      { path: '/vegetation/monitoring', element: <VegetationMonitoring /> },
      { path: '/vegetation/historical', element: <VegetationMonitoring /> },
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

describe('VegetationMonitoring page', () => {
  it('renders toolbar, chart regions, and tables without an iframe', () => {
    const { container } = renderPage()

    expect(screen.getByText('LIVE')).toBeTruthy()

    expect(screen.getByRole('heading', { name: 'Veg climate conditions' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Veg atmosphere & equipment' })).toBeTruthy()

    expect(screen.getByRole('table', { name: 'Sensor Values' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Statistics - All Available Sensors' })).toBeTruthy()

    expect(container.querySelector('iframe')).toBeNull()
  })

  it('renders live values in the Sensor Values table', () => {
    renderPage()
    const valuesTable = screen.getByRole('table', { name: 'Sensor Values' })
    expect(within(valuesTable).getByText('Dry Bulb')).toBeTruthy()
    expect(within(valuesTable).getByText('24.6°C')).toBeTruthy()
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

    expect(container.querySelectorAll('.mon-chart__frame')).toHaveLength(2)
  })

  it('redirects an initial Monitoring fixed URL longer than seven days to Historical', () => {
    const router = renderRouted(
      '/vegetation/monitoring?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z&fixtureSession=long'
    )

    expect(router.state.location.pathname).toBe('/vegetation/historical')
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
      '/vegetation/monitoring?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
    )

    expect(pageRenderCount.current).toBeGreaterThan(0)
    expect(overviewViewProps).toHaveLength(0)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-15T10:00:00.000Z'),
      new Date('2026-07-22T10:00:00.000Z')
    )
  })

  it('navigates a custom Monitoring range longer than seven days to Historical with the exact range', () => {
    const router = renderRouted('/vegetation/monitoring?fixtureSession=custom')

    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-01T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-11T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))

    expect(router.state.location.pathname).toBe('/vegetation/historical')
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

  it('shows Now only on a fixed range and returns to the last live duration', () => {
    renderPage('/?start=2026-07-15T10:00:00.000Z&end=2026-07-15T12:00:00.000Z')

    fireEvent.click(screen.getByRole('button', { name: 'Now' }))

    expect(store.setLiveRange).toHaveBeenLastCalledWith(3600_000)
  })
})

describe('VegetationMonitoring historical route', () => {
  it('swaps to the historical presets and loads the Veg Room current run', async () => {
    renderRouted('/vegetation/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z')

    for (const label of ['5 days', '7 days', '2 weeks', '1 month', '3 months', 'Current run']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
    expect(screen.queryByRole('button', { name: 'Now' })).toBeNull()
    expect(overviewViewProps.at(-1)?.historicalDrillDown).toBe(true)

    await waitFor(() =>
      expect(getCalendarCurrentRun).toHaveBeenCalledWith(
        'Veg Room',
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      )
    )
  })

  it('keeps the five-day preset on Historical with its exact fixed range', () => {
    const router = renderRouted(
      '/vegetation/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z&scenario=absent&fixtureSession=veg-1'
    )

    fireEvent.click(screen.getByRole('button', { name: '5 days' }))

    expect(router.state.location.pathname).toBe('/vegetation/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('scenario')).toBe('absent')
    expect(params.get('fixtureSession')).toBe('veg-1')
    const start = new Date(params.get('start') ?? '')
    const end = new Date(params.get('end') ?? '')
    expect(end.getTime() - start.getTime()).toBe(5 * 24 * 3600_000)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(start, end)
  })

  it('zooms a short historical overview selection in place to the exact selection', () => {
    const router = renderRouted(
      '/vegetation/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open detailed range' }))

    expect(router.state.location.pathname).toBe('/vegetation/historical')
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-23T00:00:00.000Z'),
      new Date('2026-07-26T00:00:00.000Z')
    )
  })

  it('keeps a sub-seven-day zoom on Historical and writes the exact URL range', () => {
    const router = renderRouted(
      '/vegetation/historical?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z&fixtureSession=veg-9'
    )

    act(() => {
      zoomReporters.at(-1)?.({
        start: new Date('2026-07-16T00:00:00.000Z'),
        end: new Date('2026-07-22T00:00:00.000Z'),
      })
    })

    expect(router.state.location.pathname).toBe('/vegetation/historical')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('start')).toBe('2026-07-16T00:00:00.000Z')
    expect(params.get('end')).toBe('2026-07-22T00:00:00.000Z')
    expect(params.get('fixtureSession')).toBe('veg-9')
  })

  it('restores the previous range with Reset Zoom', () => {
    renderRouted(
      '/vegetation/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z'
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
    expect(reset).toBeEnabled()

    fireEvent.click(reset)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-16T10:00:00.000Z'),
      new Date('2026-07-22T10:00:00.000Z')
    )
    expect(reset).toBeDisabled()
  })

  it('never fetches the calendar from the regular Monitoring route', () => {
    renderPage('/')

    expect(screen.queryByRole('button', { name: 'Now' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Current run' })).toBeNull()
    expect(getCalendarCurrentRun).not.toHaveBeenCalled()
  })
})
