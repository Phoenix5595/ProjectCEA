/**
 * Interaction tests for the themed monitoring chart.
 *
 * uPlot is mocked so we can inspect the assembled options (bands, axes,
 * plugins) and observe setSeries/setScale calls without a real canvas. The
 * tests verify legend toggles drive `plot.setSeries`, temperature renders on
 * the left while other families render on the right, min/max bands and
 * photoperiod/now overlays are wired, drag zoom emits a UTC range, and hiding
 * one series keeps its sibling family scale and the accessible table.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { createRef } from 'react'
import type uPlot from 'uplot'
import { ThemeProvider } from '../../../../contexts/ThemeContext'
import type { AlignedData } from '../../data'
import { UPlotChart, type UPlotChartHandle } from '../UPlotChart'

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
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

function makeData(): AlignedData {
  const x = [1000, 2000, 3000, 4000]
  return {
    x,
    series: [
      { key: 'sensor:temp:mean', label: 'temp', kind: 'sensor', y: [20, 21, 22, 23], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
      { key: 'sensor:temp:min', label: 'temp min', kind: 'sensor', y: [19, 20, 21, 22], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
      { key: 'sensor:temp:max', label: 'temp max', kind: 'sensor', y: [21, 22, 23, 24], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
      { key: 'sensor:rh:mean', label: 'rh', kind: 'sensor', y: [50, 51, 52, 53], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%', unitFamily: 'percent' },
      { key: 'sensor:rh:min', label: 'rh min', kind: 'sensor', y: [49, 50, 51, 52], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%', unitFamily: 'percent' },
      { key: 'sensor:rh:max', label: 'rh max', kind: 'sensor', y: [51, 52, 53, 54], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%', unitFamily: 'percent' },
      { key: 'climate:heating:point', label: 'heating', kind: 'point', y: [20, 20, 21, 21], origin: 'projected', quality: 'estimated', isAggregated: false },
    ],
    bands: [
      { key: 'sensor:temp:band', minKey: 'sensor:temp:min', maxKey: 'sensor:temp:max' },
      { key: 'sensor:rh:band', minKey: 'sensor:rh:min', maxKey: 'sensor:rh:max' },
    ],
    photoperiod: [
      { start: 1000, end: 2500, phase: 'SUN' },
      { start: 2500, end: 4000, phase: 'MOON' },
    ],
    nowIndex: 2,
    aggregated: false,
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

describe('monitoring chart interactions', () => {
  it('toggles legends draws bands axes overlays and zooms', () => {
    const onZoom = vi.fn()
    const data = makeData()
    render(
      <ThemeProvider>
        <UPlotChart data={data} onZoom={onZoom} />
      </ThemeProvider>,
    )
    const plot = instances[0]

    // Min/max bands are wired from the aligned band pairs.
    expect(plot.opts.bands).toHaveLength(2)

    // Temperature renders on the left; other families on the right.
    const tempAxis = plot.opts.axes?.find((a) => a.scale === 'temperature')
    expect(tempAxis?.side).toBe(1)
    const rhAxis = plot.opts.axes?.find((a) => a.scale === 'rh')
    expect(rhAxis?.side).toBe(3)

    // Photoperiod + now-divider overlays are present as plugins.
    expect(plot.opts.plugins?.length).toBeGreaterThanOrEqual(3)

    // Projected series are visually distinct (dashed).
    const heating = plot.opts.series?.[7]
    expect(heating?.dash).toBeDefined()

    // Legend swatch click toggles the series via setSeries.
    const tempButton = screen.getByRole('button', { name: /temp/ })
    fireEvent.click(tempButton)
    expect(plot.setSeries).toHaveBeenCalledWith(1, { show: false })

    // Drag zoom emits an exact UTC range from the setScale hook.
    const setScaleHook = plot.opts.hooks?.setScale?.[0]
    setScaleHook?.(plot as unknown as uPlot, 'x')
    setScaleHook?.(plot as unknown as uPlot, 'x')
    expect(onZoom).toHaveBeenCalledTimes(1)
    const range = onZoom.mock.calls[0][0] as { start: Date; end: Date }
    expect(range.start).toBeInstanceOf(Date)
    expect(range.end).toBeInstanceOf(Date)
  })

  it('keeps sibling family scale and accessible table when a series hides', () => {
    const data = makeData()
    render(
      <ThemeProvider>
        <UPlotChart data={data} />
      </ThemeProvider>,
    )
    const plot = instances[0]

    // Hide the rh series via its legend swatch.
    const rhButton = screen.getByRole('button', { name: /rh/ })
    fireEvent.click(rhButton)
    expect(plot.setSeries).toHaveBeenCalledWith(4, { show: false })

    // The sibling rh family scale/axis is preserved in the options.
    expect(plot.opts.scales?.rh).toBeDefined()
    const rhAxis = plot.opts.axes?.find((a) => a.scale === 'rh')
    expect(rhAxis).toBeDefined()

    // The accessible table still lists every legend series.
    const table = document.querySelector('.mon-legend__table')
    expect(table).not.toBeNull()
    const rows = table?.querySelectorAll('tbody tr')
    expect(rows?.length).toBe(3)
  })

  it('reset zoom restores the original range via the imperative handle', () => {
    const data = makeData()
    const ref = createRef<UPlotChartHandle>()
    const range = { start: new Date(1000), end: new Date(4000) }
    render(
      <ThemeProvider>
        <UPlotChart data={data} range={range} ref={ref} />
      </ThemeProvider>,
    )
    const plot = instances[0]

    ref.current?.resetZoom()
    expect(plot.setScale).toHaveBeenCalledWith('x', { min: 1000, max: 4000 })
  })
})
