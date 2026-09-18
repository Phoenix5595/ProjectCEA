import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import type uPlot from 'uplot'
import { TimelineUPlot, SUN_BG, MOON_BG } from '../TimelineUPlot'
import type { TimelineSeriesMeta } from '../timelineOptions'

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
  class MockUPlot {
    static instances = instances
    opts: uPlot.Options
    data: uPlot.AlignedData
    root: HTMLElement
    scales = { x: { min: 0, max: 100 } }
    setData = vi.fn()
    redraw = vi.fn()
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

const meta: TimelineSeriesMeta[] = [
  { key: 'heating_setpoint:scheduled', label: 'Heating (scheduled)', metric: 'heating_setpoint', scale: 'temp', stroke: '#ea580c', dash: [] },
  { key: 'heating_setpoint:effective', label: 'Heating (effective)', metric: 'heating_setpoint', scale: 'temp', stroke: '#ea580c', dash: [6, 4] },
]

const WINDOW = { start: Date.parse('2026-01-01T00:00:00.000Z'), end: Date.parse('2026-01-02T00:00:00.000Z') }

const BANDS = [
  { start: WINDOW.start, end: Date.parse('2026-01-01T06:00:00.000Z'), phase: 'MOON' as const },
  { start: Date.parse('2026-01-01T06:00:00.000Z'), end: Date.parse('2026-01-01T18:00:00.000Z'), phase: 'SUN' as const },
]

function Harness({ data, revision }: { data: uPlot.AlignedData; revision: number }) {
  return (
    <TimelineUPlot
      data={data}
      meta={meta}
      windowMs={WINDOW}
      photoperiod={BANDS}
      nowX={null}
      revision={revision}
      ariaLabel="Test plot"
    />
  )
}

beforeEach(() => {
  instances.length = 0
  MockResizeObserver.instances.length = 0
  frameWidth = 800
  frameHeight = 400
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => frameWidth })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => frameHeight })
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TimelineUPlot mount', () => {
  it('creates one instance with static window, family series and photoperiod/now plugins', () => {
    const data: uPlot.AlignedData = [[0, 1440], [22, 22], [21, 21]]
    render(<Harness data={data} revision={1} />)

    expect(instances).toHaveLength(1)
    const plot = instances[0]
    expect(plot.data).toBe(data)
    expect(plot.opts.scales?.x).toMatchObject({ time: false })
    const xRange = (plot.opts.scales?.x as { range?: () => [number, number] }).range?.() ?? []
    expect(xRange).toEqual([0, 1441])
    const scaleKeys = Object.keys(plot.opts.scales ?? {})
    expect(scaleKeys).toEqual(expect.arrayContaining(['temp', 'vpd', 'co2']))
    expect(plot.opts.series).toHaveLength(3)
    expect(plot.opts.series[1]).toMatchObject({ label: 'Heating (scheduled)', scale: 'temp', spanGaps: false })
    expect(plot.opts.series[2]).toMatchObject({ label: 'Heating (effective)', dash: [6, 4] })

    const pluginCount = plot.opts.plugins?.length ?? 0
    expect(pluginCount).toBeGreaterThanOrEqual(2)
    expect(plot.setData).not.toHaveBeenCalled()
  })

  it('applies a newer revision through setData(false)+redraw without recreating the instance', () => {
    const data1: uPlot.AlignedData = [[WINDOW.start, WINDOW.end], [22, 22], [21, 21]]
    const { rerender } = render(<Harness data={data1} revision={1} />)
    const plot = instances[0]

    const data2: uPlot.AlignedData = [[0, 1440], [23, 23], [21, 21]]
    rerender(<Harness data={data2} revision={2} />)

    expect(instances).toHaveLength(1)
    expect(plot.destroy).not.toHaveBeenCalled()
    expect(plot.setData).toHaveBeenCalledTimes(1)
    expect(plot.setData).toHaveBeenCalledWith(data2, false)
    expect(plot.redraw).toHaveBeenCalledTimes(1)
  })

  it('ignores a stale revision and keeps the last applied data', () => {
    const data1: uPlot.AlignedData = [[WINDOW.start, WINDOW.end], [22, 22], [21, 21]]
    const { rerender } = render(<Harness data={data1} revision={5} />)
    const plot = instances[0]
    plot.setData.mockClear()

    rerender(<Harness data={data1} revision={5} />)
    expect(plot.setData).not.toHaveBeenCalled()

    const data2: uPlot.AlignedData = [[0, 1440], [19, 19], [21, 21]]
    rerender(<Harness data={data2} revision={4} />)
    expect(plot.setData).not.toHaveBeenCalled()
  })

  it('passes the owner photoperiod colors to the band plugin and draws full-height bands', () => {
    const data: uPlot.AlignedData = [[0, 1440], [22, 22], [21, 21]]
    render(<Harness data={data} revision={1} />)
    const plot = instances[0]
    const photoperiodPluginInstance = plot.opts.plugins?.find((plugin) =>
      plugin.hooks !== undefined && plugin.hooks.drawClear != null)
    expect(photoperiodPluginInstance).toBeDefined()

    const fillStyleCalls: string[] = []
    const ctx = {
      save: () => undefined,
      restore: () => undefined,
      set fillStyle(value: string) { fillStyleCalls.push(value) },
      fillRect: () => undefined,
    } as unknown as CanvasRenderingContext2D
    const fakeU = {
      ctx,
      bbox: { top: 0, height: 100 },
      valToPos: (value: number) => (value - WINDOW.start) / (WINDOW.end - WINDOW.start),
    } as unknown as uPlot

    const drawClear = photoperiodPluginInstance?.hooks?.drawClear
    const drawHooks = Array.isArray(drawClear) ? drawClear : drawClear ? [drawClear] : []
    act(() => {
      drawHooks.forEach((hook) => (hook as (u: uPlot) => void)(fakeU))
    })

    expect(fillStyleCalls).toContain(SUN_BG)
    expect(fillStyleCalls).toContain(MOON_BG)
    expect(SUN_BG).toBe('rgba(234, 179, 8, 0.45)')
    expect(MOON_BG).toBe('rgba(168, 85, 247, 0.35)')
  })

  it('coalesces a resize storm into one setSize per animation frame without recreation', () => {
    const animationFrames: Array<(timestamp: number) => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
      animationFrames.push(callback)
      return animationFrames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)

    const data: uPlot.AlignedData = [[0, 1440], [22, 22], [21, 21]]
    render(<Harness data={data} revision={1} />)
    const plot = instances[0]
    const observer = MockResizeObserver.instances[0]
    const container = observer?.observe.mock.calls[0]?.[0] as HTMLElement
    Object.defineProperty(container, 'clientWidth', { value: 640, configurable: true, writable: true })
    Object.defineProperty(container, 'clientHeight', { value: 320, configurable: true, writable: true })

    for (let index = 0; index < 10; index += 1) observer?.callback([], observer)
    expect(plot.setSize).not.toHaveBeenCalled()
    animationFrames[0]?.(0)
    expect(plot.setSize).toHaveBeenCalledTimes(1)
    expect(plot.setSize).toHaveBeenLastCalledWith({ width: 640, height: 320 })
    expect(instances).toHaveLength(1)
  })

  it('destroys its instance and disconnects the observer on unmount', () => {
    const data: uPlot.AlignedData = [[0, 1440], [22, 22], [21, 21]]
    const { unmount } = render(<Harness data={data} revision={1} />)
    const plot = instances[0]
    const observer = MockResizeObserver.instances[0]
    unmount()
    expect(plot.destroy).toHaveBeenCalled()
    expect(observer.disconnect).toHaveBeenCalledOnce()
  })
})
