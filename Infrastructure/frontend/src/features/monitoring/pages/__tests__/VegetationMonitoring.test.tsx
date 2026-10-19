/**
 * Shallow test for the Veg monitoring page.
 *
 * Mounts `VegetationMonitoring` under jsdom with a faked `MonitoringStore`
 * binding (no network) and a mocked uPlot so no real canvas is created. Asserts
 * the toolbar, chart regions, and canonical tables render without an iframe.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef } from 'react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import type uPlot from 'uplot'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { ThemeProvider } from '../../../../contexts/ThemeContext'
import VegetationMonitoring from '../../../../pages/VegetationMonitoring'

const {
  budgetReporters,
  pageRenderCount,
  snapshot,
  store,
  overviewViewProps,
  overviewSelection,
} = vi.hoisted(() => {
  const t = new Date('2026-08-02T12:00:00.000Z')
  const budgetReporters: Array<(budget: number) => void> = []
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
    setLiveRange: vi.fn(),
    setFixedRange: vi.fn(),
    setRangeBudget: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    sensorRange: vi.fn(),
    controlRange: vi.fn(),
  }
  const snapshot = {
    range: { kind: 'live', duration: 3600_000 },
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
  return { budgetReporters, pageRenderCount, snapshot, store, overviewViewProps, overviewSelection }
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
    pageRenderCount.current += 1
    return { snapshot, store }
  },
}))

vi.mock('../../charts', async importOriginal => {
  const actual = await importOriginal<typeof import('../../charts')>()
  return {
    ...actual,
    UPlotChart: forwardRef(function MockUPlotChart(
      { onRequestBudgetChange }: { readonly onRequestBudgetChange?: (budget: number) => void },
      _ref
    ) {
      if (onRequestBudgetChange) budgetReporters.push(onRequestBudgetChange)
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

beforeEach(() => {
  instances.length = 0
  budgetReporters.length = 0
  overviewViewProps.length = 0
  pageRenderCount.current = 0
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

  it('reports the widest current chart budget from both chart keys', () => {
    renderPage()

    expect(budgetReporters).toHaveLength(2)
    act(() => {
      budgetReporters[0](800)
      budgetReporters[1](1_200)
    })

    expect(store.setRangeBudget).toHaveBeenLastCalledWith(1_200)
  })

  it('recomputes the widest budget after the previously widest chart shrinks', () => {
    renderPage()

    act(() => {
      budgetReporters[0](1_200)
      budgetReporters[1](800)
      budgetReporters[0](600)
    })

    expect(store.setRangeBudget).toHaveBeenLastCalledWith(800)
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
  it('routes an initial long fixed URL to overview before mounting the detailed store', () => {
    renderPage('/?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z')

    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
    expect(screen.getByText('FIXED')).toBeInTheDocument()
    expect(pageRenderCount.current).toBe(0)
    expect(store.setLiveRange).not.toHaveBeenCalled()
    expect(store.setFixedRange).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Veg climate conditions' })).toBeNull()
    const view = overviewViewProps[0]
    expect(view?.room).toBe('Veg Room')
    expect(view?.range.start.toISOString()).toBe('2026-07-01T00:00:00.000Z')
    expect(view?.range.end.toISOString()).toBe('2026-08-01T00:00:00.000Z')
  })

  it('keeps exactly seven days detailed and switches to overview at seven days plus one millisecond', () => {
    const exact = renderPage('/?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.000Z')
    expect(pageRenderCount.current).toBeGreaterThan(0)
    expect(overviewViewProps).toHaveLength(0)
    exact.unmount()

    pageRenderCount.current = 0
    store.setFixedRange.mockClear()
    const plusOne = renderPage(
      '/?start=2026-07-15T10:00:00.000Z&end=2026-07-22T10:00:00.001Z'
    )
    expect(screen.getByTestId('history-overview-view')).toBeInTheDocument()
    expect(pageRenderCount.current).toBe(0)
    expect(store.setFixedRange).not.toHaveBeenCalled()
    plusOne.unmount()
  })

  it('hands overview detail selections to the detailed store and Now returns to live', () => {
    const overview = renderPage(
      '/?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Open detailed range' }))
    expect(pageRenderCount.current).toBeGreaterThan(0)
    expect(store.setFixedRange).toHaveBeenCalledWith(
      new Date('2026-07-23T00:00:00.000Z'),
      new Date('2026-07-26T00:00:00.000Z')
    )
    overview.unmount()

    const longView = renderPage(
      '/?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Now' }))
    expect(store.setLiveRange).toHaveBeenCalledWith(3600_000)
    longView.unmount()
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

  it('routes the five-day preset to Veg regular Monitoring with the fixture query preserved', () => {
    const router = renderRouted(
      '/vegetation/historical?start=2026-07-16T10:00:00.000Z&end=2026-07-22T10:00:00.000Z&scenario=absent&fixtureSession=veg-1'
    )

    fireEvent.click(screen.getByRole('button', { name: '5 days' }))

    expect(router.state.location.pathname).toBe('/vegetation/monitoring')
    const params = new URLSearchParams(router.state.location.search)
    expect(params.get('scenario')).toBe('absent')
    expect(params.get('fixtureSession')).toBe('veg-1')
    const start = new Date(params.get('start') ?? '')
    const end = new Date(params.get('end') ?? '')
    expect(end.getTime() - start.getTime()).toBe(5 * 24 * 3600_000)
    expect(store.setFixedRange).toHaveBeenLastCalledWith(start, end)
  })

  it('snaps a short historical overview selection to seven days and stays Historical', () => {
    const router = renderRouted(
      '/vegetation/historical?start=2026-07-01T00:00:00.000Z&end=2026-08-01T00:00:00.000Z'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open detailed range' }))

    expect(router.state.location.pathname).toBe('/vegetation/historical')
    expect(store.setFixedRange).toHaveBeenLastCalledWith(
      new Date('2026-07-21T00:00:00.000Z'),
      new Date('2026-07-28T00:00:00.000Z')
    )
  })

  it('never fetches the calendar from the regular Monitoring route', () => {
    renderPage('/')

    expect(screen.getByRole('button', { name: 'Now' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Current run' })).toBeNull()
    expect(getCalendarCurrentRun).not.toHaveBeenCalled()
  })
})
