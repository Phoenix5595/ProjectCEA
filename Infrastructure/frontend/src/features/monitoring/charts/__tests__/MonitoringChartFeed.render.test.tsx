import { act, render } from '@testing-library/react'
import type uPlot from 'uplot'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { ThemeProvider } from '../../../../contexts/ThemeContext'
import type { AlignedData } from '../../data'
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
    setData = vi.fn()
    setSize = vi.fn()
    setScale = vi.fn((k: string, r: { min: number; max: number }) => {
      if (k !== 'x') return
      this.scales.x = r
      this.opts.hooks?.setScale?.forEach(hook => hook?.(this as unknown as uPlot, k))
    })
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

class RO {
  callback: ResizeObserverCallback
  observe = vi.fn((target: HTMLElement) => {
    Object.defineProperty(target, 'clientWidth', { configurable: true, value: 800 })
    Object.defineProperty(target, 'clientHeight', { configurable: true, value: 400 })
    this.callback([], this)
  })
  disconnect = vi.fn()
  unobserve = vi.fn()
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
  }
}

function makeData(nowIndex = 2): AlignedData {
  return {
    x: [1000, 2000, 3000, 4000],
    series: [],
    bands: [],
    photoperiod: [],
    nowIndex,
    aggregated: false,
  } as unknown as AlignedData
}

describe('debug', () => {
  beforeEach(() => {
    instances.length = 0
    vi.stubGlobal('ResizeObserver', RO)
  })
  afterEach(() => vi.unstubAllGlobals())
  it('counts setData calls across rerender', () => {
    const range = { kind: 'live', duration: 3_600_000 } as const
    const feed = createMonitoringChartFeed(makeData(), range)
    const { rerender } = render(
      <ThemeProvider>
        <UPlotChart feed={feed} />
      </ThemeProvider>
    )
    expect(instances.length).toBe(1)
    act(() => feed.publish(makeData(3), range))
    rerender(
      <ThemeProvider>
        <UPlotChart feed={feed} />
      </ThemeProvider>
    )
    expect(instances[0].setData.mock.calls.length).toBeGreaterThan(0)
  })
})
