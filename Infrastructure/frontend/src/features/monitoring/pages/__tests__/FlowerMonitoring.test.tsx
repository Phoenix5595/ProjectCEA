/**
 * Shallow test for the Flower monitoring page.
 *
 * Mounts `FlowerMonitoring` under jsdom with a faked `MonitoringStore` binding
 * (no network) and a mocked uPlot so no real canvas is created. Asserts the
 * toolbar, both chart regions, and the canonical tables render, and that no
 * iframe or Grafana URL appears anywhere in the page.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type uPlot from 'uplot'
import { ThemeProvider } from '../../../../contexts/ThemeContext'
import FlowerMonitoring from '../../../../pages/FlowerMonitoring'

const { snapshot, store } = vi.hoisted(() => {
  const t = new Date('2026-08-02T12:00:00.000Z')
  const store = {
    setLiveRange: vi.fn(),
    setFixedRange: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
  }
  const snapshot = {
    range: { kind: 'live', duration: 3 * 3600_000 },
    isLive: true,
    data: {
      series: [
        {
          sensor: 'dry_bulb_f',
          node: 'front',
          unit_family: 'celsius',
          unit: '°C',
          points: [
            { timestamp: t, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
          ],
        },
        {
          sensor: 'dry_bulb_b',
          node: 'back',
          unit_family: 'celsius',
          unit: '°C',
          points: [
            { timestamp: t, average: 25.1, minimum: 24.7, maximum: 25.5, sample_count: 60 },
          ],
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
  return { snapshot, store }
})

vi.mock('../useMonitoringStore', () => ({
  FLOWER_DEFAULT_DURATION_MS: 3 * 3600_000,
  useMonitoringStore: () => ({ snapshot, store }),
}))

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
  MockResizeObserver.instances.length = 0
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <FlowerMonitoring />
      </ThemeProvider>
    </MemoryRouter>,
  )
}

describe('FlowerMonitoring page', () => {
  it('renders toolbar, chart regions, and tables without any iframe or Grafana URL', () => {
    const { container } = renderPage()

    expect(screen.getByRole('button', { name: 'Reset Zoom' })).toBeTruthy()
    expect(screen.getByText('LIVE')).toBeTruthy()

    expect(
      screen.getByRole('heading', { name: 'Flower climate conditions' }),
    ).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeTruthy()

    expect(screen.getByRole('table', { name: 'Averages' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Front Cluster' })).toBeTruthy()
    expect(screen.getByRole('table', { name: 'Back Cluster' })).toBeTruthy()
    expect(
      screen.getByRole('table', { name: 'Statistics - All Available Sensors' }),
    ).toBeTruthy()

    expect(container.querySelector('iframe')).toBeNull()
    expect(container.innerHTML).not.toMatch(/grafana/i)
    expect(container.innerHTML).not.toMatch(/iskraprojectcea/i)
    expect(container.innerHTML).not.toMatch(/:3001/)
  })

  it('renders live values in the Back Cluster table', () => {
    renderPage()
    const backTable = screen.getByRole('table', { name: 'Back Cluster' })
    expect(within(backTable).getByText('Dry Bulb')).toBeTruthy()
    expect(within(backTable).getByText('25.1°C')).toBeTruthy()
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
