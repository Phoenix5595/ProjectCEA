import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RelayTimelineChart } from '../RelayTimelineChart'
import type { RelayTimelineChartLane } from '../RelayTimelineChart'
import type { RelayIntervalSummary, RelayTimelineWindow } from '../intervals'

const { MockUPlot, instances, pathCommands } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
  const pathCommands: Array<{ method: string; args: number[] }> = []
  const context = {
    save: () => undefined,
    restore: () => undefined,
    beginPath: () => undefined,
    rect: () => undefined,
    clip: () => undefined,
    fillRect: () => undefined,
    fillText: () => undefined,
    moveTo: (x: number, y: number) => pathCommands.push({ method: 'moveTo', args: [x, y] }),
    lineTo: (x: number, y: number) => pathCommands.push({ method: 'lineTo', args: [x, y] }),
    closePath: () => pathCommands.push({ method: 'closePath', args: [] }),
    fill: () => undefined,
    stroke: () => undefined,
  } as unknown as CanvasRenderingContext2D

  class MockUPlot {
    readonly opts: {
      plugins?: Array<{
        hooks?: {
          drawClear?: ((plot: MockUPlot) => void) | Array<(plot: MockUPlot) => void>
        }
      }>
    }
    readonly ctx = context
    readonly bbox = { left: 0, top: 0, width: 100, height: 58 }
    scales = { x: { min: 0, max: 20 } }
    readonly valToPos = (value: number): number => value * 5

    constructor(opts: MockUPlot['opts']) {
      this.opts = opts
      instances.push(this)
    }

    setScale = (_key: string, scale: { min: number; max: number }): void => {
      this.scales.x = scale
    }
    setData = (): void => undefined
    setSize = (): void => undefined
    redraw = (): void => this.drawClear()
    destroy = (): void => undefined

    drawClear(): void {
      for (const plugin of this.opts.plugins ?? []) {
        const drawClear = plugin.hooks?.drawClear
        if (typeof drawClear === 'function') drawClear(this)
        else for (const hook of drawClear ?? []) hook(this)
      }
    }
  }

  return { MockUPlot, instances, pathCommands }
})

vi.mock('uplot', () => ({ default: MockUPlot }))

const summary: RelayIntervalSummary = {
  onTransitions: 0,
  offTransitions: 0,
  cyclesPerHour: null,
  knownObservationHours: 0,
  knownPercent: 0,
  completeOnDurationsSeconds: [],
  completeOffDurationsSeconds: [],
  meanOnDurationSeconds: null,
  meanOffDurationSeconds: null,
  onSeconds: 0,
  offSeconds: 0,
  coverageComplete: false,
}

const lane: RelayTimelineChartLane = {
  key: 'heater:101:0',
  label: 'Heater',
  deviceName: 'flower_heater',
  deviceType: 'heating',
  channel: 0,
  physicalRelay: 1,
  intervals: [],
  requestedOutput: [
    { start: 0, end: 10_000, requestedPercent: 20, aggregated: false, intervalSeconds: 10 },
    { start: 10_000, end: 20_000, requestedPercent: 80, aggregated: false, intervalSeconds: 10 },
  ],
  summary,
}

const range: RelayTimelineWindow = {
  start: new Date(0),
  end: new Date(20_000),
}

const clientWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
const clientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')

beforeEach(() => {
  instances.length = 0
  pathCommands.length = 0
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    value: 800,
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    value: 180,
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      disconnect(): void {}
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  if (clientWidthDescriptor) {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientWidthDescriptor)
  }
  if (clientHeightDescriptor) {
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeightDescriptor)
  }
})

describe('RelayTimelineChart requested PID waveform', () => {
  it('draws flat sample steps and jumps only at the next sample', () => {
    render(<RelayTimelineChart lanes={[lane]} range={range} viewportKey="step-test" />)
    const plot = instances[0]
    expect(plot).toBeDefined()

    act(() => plot?.drawClear())

    const lineTos = pathCommands
      .filter(command => command.method === 'lineTo')
      .map(command => command.args)
    const lineAt = (x: number, y: number): boolean =>
      lineTos.some(
        ([lineX, lineY]) =>
          lineX === x && lineY !== undefined && Math.abs(lineY - y) < 1e-9
      )

    expect(lineAt(0, 23.2)).toBe(true)
    expect(lineAt(50, 23.2)).toBe(true)
    expect(lineAt(50, 5.8)).toBe(true)
    expect(lineAt(100, 5.8)).toBe(true)
    expect(pathCommands.some(command => command.method === 'bezierCurveTo')).toBe(false)
  })
})
