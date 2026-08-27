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
import { describe, expect, it, vi, beforeEach, afterAll, afterEach } from 'vitest'
import { act, render, screen, fireEvent } from '@testing-library/react'
import React, { useEffect, useRef } from 'react'
import type uPlot from 'uplot'
import { ThemeProvider, useTheme } from '../../../../contexts/ThemeContext'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { createMonitoringChartFeed } from '../MonitoringChartFeed'
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
  callback: ResizeObserverCallback
  observe = vi.fn()
  disconnect = vi.fn()
  unobserve = vi.fn()
  constructor(cb: ResizeObserverCallback) {
    this.callback = cb
    MockResizeObserver.instances.push(this)
  }
}

let frameWidth = 800
let frameHeight = 400
const clientWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')

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

const TEST_RANGE = { kind: 'live', duration: 3_600_000 } as const

function Harness({
  data,
  onZoom,
}: {
  data: AlignedData
  onZoom?: (range: { start: Date; end: Date }) => void
}) {
  const { setTheme } = useTheme()
  const feedRef = useRef<ReturnType<typeof createMonitoringChartFeed> | null>(null)
  if (feedRef.current === null) feedRef.current = createMonitoringChartFeed(data, TEST_RANGE)
  const feed = feedRef.current
  useEffect(() => {
    feed.publish(data, TEST_RANGE)
  }, [data, feed])
  return (
    <>
      <UPlotChart feed={feed} onZoom={onZoom} />
      <button onClick={() => setTheme('control-room')}>switch</button>
    </>
  )
}

beforeEach(() => {
  instances.length = 0
  MockResizeObserver.instances.length = 0
  frameWidth = 800
  frameHeight = 400
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => frameWidth,
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => frameHeight,
  })
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

afterAll(() => {
  if (clientWidthDescriptor !== undefined) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientWidthDescriptor)
  if (clientHeightDescriptor !== undefined) Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeightDescriptor)
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
            <Harness data={data} />
        </ThemeProvider>
      </React.StrictMode>,
    )

    expect(instances).toHaveLength(1)
    expect(instances[0].destroy).not.toHaveBeenCalled()

    unmount()

    expect(instances[0].destroy).toHaveBeenCalled()

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
        <Harness data={data} />
      </ThemeProvider>,
    )

    expect(screen.getAllByRole('button', { name: 'Series 0' })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Duplicate series' })).toBeNull()
  })

  it('draws 120 live feed revisions without rendering its parent or recreating uPlot', () => {
    const feed = createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)
    let parentRenders = 0
    function CountedParent() {
      parentRenders += 1
      return <UPlotChart feed={feed} />
    }

    const { unmount } = render(
      <ThemeProvider>
        <CountedParent />
      </ThemeProvider>,
    )
    const plot = instances[0]
    plot.setData.mockClear()

    act(() => {
      for (let tick = 0; tick < 120; tick += 1) {
        feed.publish(makeData([1000, 2000, 3000], [[20, 21, tick]]), TEST_RANGE)
      }
    })

    expect(parentRenders).toBe(1)
    expect(instances).toHaveLength(1)
    expect(plot.setData).toHaveBeenCalledTimes(120)
    expect(plot.setData).toHaveBeenLastCalledWith(toUPlot(makeData([1000, 2000, 3000], [[20, 21, 119]])), false)

    unmount()
    plot.setData.mockClear()
    act(() => feed.publish(makeData([1000, 2000, 3000], [[20, 21, 120]]), TEST_RANGE))
    expect(plot.setData).not.toHaveBeenCalled()
  })

  it('applies the fulfilled viewport data extent without recreating', () => {
    const data = makeData([1000, 2000, 3000], [[20, 21, 22]])
    const feed = createMonitoringChartFeed(data, TEST_RANGE)
    render(
      <ThemeProvider>
        <UPlotChart feed={feed} />
      </ThemeProvider>,
    )
    const plot = instances[0]
    plot.setScale.mockClear()

    const fixedRange = { kind: 'fixed', start: new Date(1250), end: new Date(2750) } as const
    act(() => feed.publish(makeData([1000, 2000, 3000], [[20, 21, 22]]), fixedRange))
    expect(instances).toHaveLength(1)
    expect(plot.setData).toHaveBeenCalledWith(toUPlot(makeData([1000, 2000, 3000], [[20, 21, 22]])), false)
    expect(plot.setScale).toHaveBeenCalledWith('x', { min: 1000, max: 3000 })

    act(() => feed.publish(makeData([1000, 2000, 3000], [[20, 21, 22], [30, 31, 32]]), fixedRange))
    expect(instances).toHaveLength(2)
    expect(instances[0]?.destroy).toHaveBeenCalledTimes(1)
  })

  it('keeps a user zoom when a same live-range tick arrives', () => {
    const feed = createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)
    render(
      <ThemeProvider>
        <UPlotChart feed={feed} />
      </ThemeProvider>,
    )
    const plot = instances[0]

    // Simulate a user drag zoom via the setScale hook.
    const setScaleHook = plot.opts.hooks?.setScale?.[0]
    setScaleHook?.(plot as unknown as uPlot, 'x')

    plot.setData.mockClear()
    plot.setScale.mockClear()

    act(() => feed.publish(makeData([1000, 2000, 3000, 4000], [[20, 21, 22, 23]]), TEST_RANGE))

    expect(instances).toHaveLength(1)
    expect(plot.setData).toHaveBeenCalledTimes(1)
    expect(plot.setData).toHaveBeenLastCalledWith(toUPlot(makeData([1000, 2000, 3000, 4000], [[20, 21, 22, 23]])), false)
    expect(plot.setScale).not.toHaveBeenCalled()
  })

  it('coalesces a resize storm into one handling per animation frame', () => {
    const data = makeData([1000, 2000, 3000], [[20, 21, 22]])
    const animationFrames: Array<(timestamp: number) => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)

    render(
      <ThemeProvider>
        <Harness data={data} />
      </ThemeProvider>,
    )

    const observer = MockResizeObserver.instances[0]
    const plot = instances[0]
    const container = observer?.observe.mock.calls[0]?.[0] as HTMLElement
    expect(observer).toBeDefined()
    expect(plot).toBeDefined()
    expect(container).toBeDefined()
    Object.defineProperty(container, 'clientWidth', { value: 800, configurable: true, writable: true })
    Object.defineProperty(container, 'clientHeight', { value: 400, configurable: true, writable: true })
    for (let index = 0; index < 10; index += 1) observer?.callback([], observer)

    expect(plot?.setSize).not.toHaveBeenCalled()
    animationFrames[0]?.(0)
    expect(plot?.setSize).toHaveBeenCalledTimes(1)
  })

  it('ignores a zero-size resize observation and applies the next valid observation', () => {
    const data = makeData([1000, 2000, 3000], [[20, 21, 22]])
    const animationFrames: Array<(timestamp: number) => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)

    render(
      <ThemeProvider>
        <Harness data={data} />
      </ThemeProvider>,
    )

    const observer = MockResizeObserver.instances[0]
    const plot = instances[0]
    const container = observer?.observe.mock.calls[0]?.[0] as HTMLElement
    expect(container).toBeDefined()
    expect(plot).toBeDefined()

    // Simulate browser flattening the container to zero during fullscreen transition.
    const originalWidth = container.clientWidth
    const originalHeight = container.clientHeight
    Object.defineProperty(container, 'clientWidth', { value: 0, configurable: true, writable: true })
    Object.defineProperty(container, 'clientHeight', { value: 0, configurable: true, writable: true })
    observer?.callback([], observer)
    animationFrames.at(-1)?.(0)
    expect(plot?.setSize).not.toHaveBeenCalled()

    // Restore valid dimensions and trigger another resize; it must apply.
    Object.defineProperty(container, 'clientWidth', { value: originalWidth || 800, configurable: true, writable: true })
    Object.defineProperty(container, 'clientHeight', { value: originalHeight || 400, configurable: true, writable: true })
    observer?.callback([], observer)
    animationFrames.at(-1)?.(0)
    expect(plot?.setSize).toHaveBeenCalledTimes(1)
    expect(plot?.setSize).toHaveBeenLastCalledWith({ width: originalWidth || 800, height: originalHeight || 400 })
  })

  it('creates only after a zero-size frame becomes positive', () => {
    const animationFrames: Array<(timestamp: number) => void> = []
    frameWidth = 0
    frameHeight = 0
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)

    render(
      <ThemeProvider>
        <UPlotChart feed={createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)} />
      </ThemeProvider>,
    )

    const observer = MockResizeObserver.instances[0]
    expect(instances).toHaveLength(0)
    frameWidth = 800
    frameHeight = 400
    observer?.callback([], observer)
    animationFrames[0]?.(0)

    expect(instances).toHaveLength(1)
    expect(instances[0]?.opts.width).toBe(800)
    expect(instances[0]?.opts.height).toBe(400)
  })

  it('measures the relative frame instead of the fixed-width uPlot host child', () => {
    render(
      <ThemeProvider>
        <UPlotChart feed={createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)} />
      </ThemeProvider>,
    )

    const host = screen.getByRole('img')
    const frame = host.parentElement
    const observer = MockResizeObserver.instances[0]
    instances[0]?.root.style.setProperty('width', '1600px')
    expect(frame).not.toBeNull()
    expect(frame?.clientWidth).toBe(800)
    expect(observer?.observe).toHaveBeenCalledWith(frame)
    expect(host).toHaveStyle({ position: 'absolute', inset: '0' })
  })

  it('resizes one stable instance through breakpoint-like widths', () => {
    const animationFrames: Array<(timestamp: number) => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    render(
      <ThemeProvider>
        <UPlotChart feed={createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)} />
      </ThemeProvider>,
    )
    const plot = instances[0]
    const observer = MockResizeObserver.instances[0]
    plot?.setSize.mockClear()

    for (const width of [600, 800, 1090, 1280]) {
      frameWidth = width
      observer?.callback([], observer)
      animationFrames.at(-1)?.(0)
    }

    expect(instances).toHaveLength(1)
    expect(plot?.destroy).not.toHaveBeenCalled()
    expect(plot?.setSize).toHaveBeenCalledTimes(4)
    expect(plot?.setSize).toHaveBeenLastCalledWith({ width: 1280, height: 400 })
  })

  it('sets data before scaling each newly fulfilled viewport revision', () => {
    const feed = createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)
    render(<ThemeProvider><UPlotChart feed={feed} /></ThemeProvider>)
    const plot = instances[0]
    plot?.setData.mockClear()
    plot?.setScale.mockClear()

    act(() => feed.publish(makeData([1000, 2000, 3000, 4000], [[20, 21, 22, 23]]), TEST_RANGE, 0))
    expect(plot?.setData).toHaveBeenCalledTimes(1)
    expect(plot?.setScale).not.toHaveBeenCalled()

    act(() => feed.publish(makeData([1000, 2000, 3000, 5000], [[20, 21, 22, 24]]), TEST_RANGE, 1))
    expect(plot?.setScale).toHaveBeenCalledOnce()
    expect(plot?.setScale).toHaveBeenCalledWith('x', { min: 1000, max: 5000 })
    expect(plot?.setData.mock.invocationCallOrder[1]).toBeLessThan(plot?.setScale.mock.invocationCallOrder[0] ?? Infinity)
  })

  it('cancels a queued frame and disconnects its observer on unmount', () => {
    const animationFrames: Array<(timestamp: number) => void> = []
    const cancelAnimationFrame = vi.fn()
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', cancelAnimationFrame)
    const { unmount } = render(
      <ThemeProvider>
        <UPlotChart feed={createMonitoringChartFeed(makeData([1000, 2000, 3000], [[20, 21, 22]]), TEST_RANGE)} />
      </ThemeProvider>,
    )
    const observer = MockResizeObserver.instances[0]
    observer?.callback([], observer)

    expect(animationFrames).toHaveLength(1)
    expect(() => unmount()).not.toThrow()
    expect(cancelAnimationFrame).toHaveBeenCalledWith(1)
    expect(observer?.disconnect).toHaveBeenCalledOnce()
  })
})
