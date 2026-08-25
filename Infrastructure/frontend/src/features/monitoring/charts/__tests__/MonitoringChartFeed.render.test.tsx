import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { ThemeProvider } from '../../../../contexts/ThemeContext'
import type { AlignedData } from '../../data'
import { createMonitoringChartFeed } from '../MonitoringChartFeed'
import { UPlotChart } from '../UPlotChart'

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: any[] = []
  class MockUPlot {
    static instances = instances
    opts: any
    data: any
    root: HTMLElement
    scales = { x: { min: 0, max: 100 } }
    setData = vi.fn()
    setSize = vi.fn()
    setScale = vi.fn((k: string, r: any) => {
      if (k !== 'x') return
      this.scales.x = r
      this.opts.hooks?.setScale?.forEach((h: any) => h?.(this))
    })
    setSeries = vi.fn()
    destroy = vi.fn()
    constructor(opts: any, data?: any, target?: HTMLElement) {
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

class RO { observe = vi.fn(); disconnect = vi.fn(); unobserve = vi.fn(); constructor(_cb: any) {} }

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
  beforeEach(() => { instances.length = 0; vi.stubGlobal('ResizeObserver', RO) })
  afterEach(() => vi.unstubAllGlobals())
  it('counts setData calls across rerender', () => {
    const range = { kind: 'live', duration: 3_600_000 } as const
    const feed = createMonitoringChartFeed(makeData(), range)
    const { rerender } = render(
      <ThemeProvider><UPlotChart feed={feed} /></ThemeProvider>,
    )
    expect(instances.length).toBe(1)
    act(() => feed.publish(makeData(3), range))
    rerender(<ThemeProvider><UPlotChart feed={feed} /></ThemeProvider>)
    expect(instances[0].setData.mock.calls.length).toBeGreaterThan(0)
  })
})
