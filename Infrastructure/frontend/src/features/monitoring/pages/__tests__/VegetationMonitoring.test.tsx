/**
 * Shallow test for the Veg monitoring page.
 *
 * Mounts `VegetationMonitoring` under jsdom with a faked `MonitoringStore`
 * binding (no network) and a mocked uPlot so no real canvas is created. Asserts
 * the toolbar, both chart regions, and the canonical tables render, and that no
 * iframe or Grafana URL appears anywhere in the page.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import { forwardRef } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type uPlot from 'uplot'
import { ThemeProvider } from '../../../../contexts/ThemeContext'
import VegetationMonitoring from '../../../../pages/VegetationMonitoring'

const { budgetReporters, pageRenderCount, snapshot, store } = vi.hoisted(() => {
  const t = new Date('2026-08-02T12:00:00.000Z')
  const budgetReporters: Array<(budget: number) => void> = []
  const pageRenderCount = { current: 0 }
  const store = {
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
          points: [
            { timestamp: t, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
          ],
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
  return { budgetReporters, pageRenderCount, snapshot, store }
})

vi.mock('../useMonitoringStore', () => ({
  VEG_DEFAULT_DURATION_MS: 3600_000,
  useMonitoringStore: () => {
    pageRenderCount.current += 1
    return { snapshot, store }
  },
}))

vi.mock('../../charts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../charts')>()
  return {
    ...actual,
    UPlotChart: forwardRef(function MockUPlotChart(
      { onRequestBudgetChange }: { readonly onRequestBudgetChange?: (budget: number) => void },
      _ref,
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
  pageRenderCount.current = 0
  MockResizeObserver.instances.length = 0
  store.setRangeBudget.mockClear()
  store.sensorRange.mockClear()
  store.controlRange.mockClear()
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <VegetationMonitoring />
      </ThemeProvider>
    </MemoryRouter>,
  )
}

describe('VegetationMonitoring page', () => {
  it('renders toolbar, chart regions, and tables without any iframe or Grafana URL', () => {
    const { container } = renderPage()

    expect(screen.getByRole('button', { name: 'Reset Zoom' })).toBeTruthy()
    expect(screen.getByText('LIVE')).toBeTruthy()

    expect(
      screen.getByRole('heading', { name: 'Veg climate conditions' }),
    ).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Veg atmosphere & equipment' })).toBeTruthy()

    expect(screen.getByRole('table', { name: 'Sensor Values' })).toBeTruthy()
    expect(
      screen.getByRole('table', { name: 'Statistics - All Available Sensors' }),
    ).toBeTruthy()

    expect(container.querySelector('iframe')).toBeNull()
    expect(container.innerHTML).not.toMatch(/grafana/i)
    expect(container.innerHTML).not.toMatch(/iskraprojectcea/i)
    expect(container.innerHTML).not.toMatch(/:3001/)
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
      .map((element) => element.style.height)
      .filter((height) => height === '780px' || height === '640px')
    expect(chartSlots).toEqual(['780px', '640px'])
  })
})
