/**
 * Lifecycle tests for the imperative uPlot adapter.
 *
 * uPlot is mocked so we can observe constructor/destroy/setData/setSize calls
 * without a real canvas. ResizeObserver is stubbed globally. The tests verify
 * that data updates call `setData` without recreating the instance, that a
 * theme change destroys and recreates while preserving data + series
 * visibility, and that React StrictMode double-mounting leaks no uPlot
 * instances or ResizeObservers.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import React from 'react'
import type uPlot from 'uplot'
import { ThemeProvider, useTheme } from '../../../../contexts/ThemeContext'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { UPlotChart } from '../UPlotChart'

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
  class MockUPlot {
    static instances = instances
    opts: uPlot.Options
    data: uPlot.AlignedData
    root: HTMLElement
    scales = { x: { min: 0, max: 100 } }
    setData = vi.fn((_data: uPlot.AlignedData, redraw?: boolean) => {
      if (redraw === false) return
      const drawHooks = this.opts.hooks?.draw
      drawHooks?.forEach((hook) => {
        if (hook !== undefined) hook(this as unknown as uPlot)
      })
    })
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

function makeData(x: number[], yRows: number[][]): AlignedData {
  return {
    x,
    series: yRows.map((y, i) => ({
      key: seriesKey('sensor', `s${i}`, 'mean'),
      label: `Series ${i}`,
      kind: 'sensor',
      source: 'sensor',
      metric: `s${i}`,
      family: 'temperature',
      role: 'mean',
      y,
      origin: 'recorded',
      quality: 'exact',
      isAggregated: false,
    })),
    bands: [],
    photoperiod: [],
    nowIndex: x.length - 1,
    aggregated: false,
  }
}

function toUPlot(data: AlignedData): uPlot.AlignedData {
  return [data.x, ...data.series.map((s) => s.y)]
}

function Harness({
  data,
  onZoom,
}: {
  data: AlignedData
  onZoom?: (range: { start: Date; end: Date }) => void
}) {
  const { setTheme } = useTheme()
  return (
    <>
      <UPlotChart data={data} onZoom={onZoom} />
      <button onClick={() => setTheme('control-room')}>switch</button>
    </>
  )
}

beforeEach(() => {
  instances.length = 0
  MockResizeObserver.instances.length = 0
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UPlotChart lifecycle', () => {
  it('updates data without recreation and recreates safely on theme', () => {
    const data1 = makeData([1000, 2000, 3000], [[20, 21, 22]])
    const data2 = makeData([1000, 2000, 3000, 4000], [[20, 21, 22, 23]])

    const { rerender } = render(
      <ThemeProvider>
        <Harness data={data1} />
      </ThemeProvider>,
    )

    expect(instances).toHaveLength(1)
    const first = instances[0]

    // Simulate a user toggling series 1 off through the setSeries hook.
    const setSeriesHook = first.opts.hooks?.setSeries?.[0]
    setSeriesHook?.(first as unknown as uPlot, 1, { show: false })

    // Data update must call setData without recreating the instance.
    rerender(
      <ThemeProvider>
        <Harness data={data2} />
      </ThemeProvider>,
    )
    expect(instances).toHaveLength(1)
    // resetScale=false keeps a fixed user zoom alive across live ticks.
    expect(first.setData).toHaveBeenLastCalledWith(toUPlot(data2), false)

    // Theme change must destroy the old instance and create a new one,
    // preserving data and the series visibility toggle.
    fireEvent.click(screen.getByText('switch'))
    expect(instances).toHaveLength(2)
    const second = instances[1]
    expect(first.destroy).toHaveBeenCalled()
    expect(second.data).toEqual(toUPlot(data2))
    expect(second.setSeries).toHaveBeenCalledWith(1, { show: false }, false)
  })

  it('leaks no canvas observer or listener across strict mounts', () => {
    const data = makeData([1000, 2000, 3000], [[20, 21, 22]])

    const { unmount } = render(
      <React.StrictMode>
        <ThemeProvider>
          <UPlotChart data={data} />
        </ThemeProvider>
      </React.StrictMode>,
    )

    // StrictMode double-mounts: two plots created, the first already destroyed.
    expect(instances).toHaveLength(2)
    expect(instances[0].destroy).toHaveBeenCalled()
    expect(instances[1].destroy).not.toHaveBeenCalled()

    unmount()

    // After unmount every created plot is destroyed.
    expect(instances[0].destroy).toHaveBeenCalled()
    expect(instances[1].destroy).toHaveBeenCalled()

    // Every ResizeObserver created is disconnected.
    const observers = MockResizeObserver.instances
    expect(observers.length).toBeGreaterThan(0)
    for (const obs of observers) {
      expect(obs.disconnect).toHaveBeenCalled()
    }
  })

  it('keeps the first legend entry for duplicate series keys', () => {
    const data = makeData([1000, 2000, 3000], [[20, 21, 22], [23, 24, 25]])
    data.series[1] = { ...data.series[0], label: 'Duplicate series' }

    render(
      <ThemeProvider>
        <UPlotChart data={data} />
      </ThemeProvider>,
    )

    expect(screen.getAllByRole('button', { name: 'Series 0' })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Duplicate series' })).toBeNull()
  })
})
