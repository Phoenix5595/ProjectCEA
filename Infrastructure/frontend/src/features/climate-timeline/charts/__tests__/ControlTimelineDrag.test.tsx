import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type uPlot from 'uplot'
import { ControlTimeline } from '../../components/ControlTimeline'
import { useTimelineDraft } from '../../state/useTimelineDraft'
import { RichTrajectoryEnvelope } from '../../api/contracts'
import type { TimelinePublicationPort } from '../../api/timelinePublicationPort'
import type { TimelineSavedBaseline } from '../../state/timelineDraft'

const { MockUPlot, instances } = vi.hoisted(() => {
  const instances: MockUPlot[] = []
  class MockUPlot {
    static instances = instances
    opts: uPlot.Options
    data: uPlot.AlignedData
    root: HTMLElement
    bbox = { left: 0, top: 0, width: 800, height: 400 }
    over = document.createElement('div')
    cursor = { left: -1, top: -1, idx: null }
    scales: Record<string, { min: number; max: number }> = {
      x: { min: 0, max: 1441 },
      temp: { min: 10, max: 35 },
      vpd: { min: 0, max: 5 },
      co2: { min: 400, max: 2000 },
    }
    setData = vi.fn((_data: uPlot.AlignedData, redraw?: boolean) => {
      if (_data) this.data = _data
      void redraw
    })
    redraw = vi.fn(() => {
      this.fireDraw()
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
      this.fireInit()
      this.fireDraw()
    }
    mergedHooks(): Record<string, unknown> {
      const merged: Record<string, unknown> = { ...(this.opts.hooks ?? {}) }
      for (const plugin of this.opts.plugins ?? []) {
        for (const [name, pluginHooks] of Object.entries(plugin.hooks ?? {})) {
          const list = Array.isArray(merged[name]) ? merged[name] as unknown[] : merged[name] ? [merged[name]] : []
          if (Array.isArray(pluginHooks)) list.push(...pluginHooks)
          else if (pluginHooks) list.push(pluginHooks)
          merged[name] = list
        }
      }
      return merged
    }
    private fireInit(): void {
      for (const hook of (this.mergedHooks().init as Array<(u: uPlot) => void> | undefined) ?? []) hook?.(this as unknown as uPlot)
    }
    private fireDraw(): void {
      for (const hook of (this.mergedHooks().draw as Array<(u: uPlot) => void> | undefined) ?? []) hook?.(this as unknown as uPlot)
    }
    valToPos(value: number, scaleKey: string, _canvasPx?: boolean): number {
      const scale = this.scales[scaleKey] ?? this.scales.temp
      const span = scale.max - scale.min
      if (scaleKey === 'x') return ((value - scale.min) / span) * this.bbox.width
      return this.bbox.height - ((value - scale.min) / span) * this.bbox.height
    }
    posToVal(px: number, scaleKey: string): number {
      const scale = this.scales[scaleKey] ?? this.scales.temp
      const span = scale.max - scale.min
      if (scaleKey === 'x') return scale.min + (px / this.bbox.width) * span
      return scale.max - (px / this.bbox.height) * span
    }
  }
  return { MockUPlot, instances }
})

vi.mock('uplot', () => ({ default: MockUPlot }))



const savedBaseline = (): TimelineSavedBaseline => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: 'config-1',
  modeId: 1,
  submodeId: null,
  window: { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'UTC' },
  periods: [
    { period_name: 'Day', start_time: '06:00', end_time: '12:00', ramp_minutes: 0, heating_setpoint: 22, cooling_setpoint: 28, vpd_setpoint: 1.1, co2_setpoint: 900, details: '' },
    { period_name: 'Night', start_time: '12:00', end_time: '22:00', ramp_minutes: 0, heating_setpoint: 18, cooling_setpoint: 24, vpd_setpoint: 0.8, co2_setpoint: 700, details: '' },
  ],
  photoperiod: { dayStartTime: '06:00', nightStartTime: '18:00', rampUpMinutes: 0, rampDownMinutes: 0 },
  trajectory: envelopeFixture(),
})

function envelopeFixture(): RichTrajectoryEnvelope {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Flower Room',
    generated_at: '2026-01-01T00:00:00.000Z',
    window: { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'UTC' },
    revision_scope: 'saved',
    base_config_revision: 'config-1',
    draft_revision: null,
    segments: [
      scheduledStep('heating_setpoint', 22),
      scheduledStep('cooling_setpoint', 28),
      scheduledStep('vpd_setpoint', 1.1),
      scheduledStep('co2_setpoint', 900),
    ],
    assumptions: [],
    warnings: [],
  })
}

function scheduledStep(metric: string, value: number) {
  return {
    shape: 'step',
    value,
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    metric,
    unit: metric === 'co2_setpoint' ? 'ppm' : metric === 'vpd_setpoint' ? 'kPa' : 'C',
    trajectory_kind: 'scheduled',
    quality: 'exact',
    source: {
      mode: 'flower',
      submode: null,
      period: { period_id: 'p1', label: 'Day' },
      config_revision: 'config-1',
      draft_revision: null,
    },
  }
}

function port(): TimelinePublicationPort {
  return {
    preview: async () => {
      throw new Error('preview not expected in drag tests')
    },
    apply: async () => savedBaseline(),
  }
}

function valueY(value: number): number {
  return 400 - ((value - 10) / 25) * 400
}

function timeX(minuteOfDay: number): number {
  return (minuteOfDay / 1440) * 800
}

let animationFrames: Array<(timestamp: number) => void> = []

beforeEach(() => {
  instances.length = 0
  animationFrames = []
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 400 })
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  })
  vi.stubGlobal('requestAnimationFrame', (callback: (timestamp: number) => void) => {
    animationFrames.push(callback)
    return animationFrames.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function renderDailyEditor() {
  const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port() }))
  const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
  fireEvent.click(screen.getByRole('button', { name: 'daily' }))
  await waitFor(() => expect(screen.getByTestId('control-timeline-boundary-grip-0-start')).toBeInTheDocument())
  return { hook, view }
}

function flushFrame(): void {
  const frame = animationFrames.shift()
  act(() => {
    frame?.(16)
  })
}

describe('drag editing on the uPlot timeline', () => {
  it('coalesces a 60-frame value drag burst into exactly one editPeriods commit per frame', async () => {
    const { hook } = await renderDailyEditor()
    const grip = screen.getByTestId('control-timeline-value-grip-0-heating')
    fireEvent.mouseDown(grip, { clientY: valueY(22) })

    for (let index = 0; index < 60; index += 1) {
      fireEvent.mouseMove(window, { clientY: valueY(24) })
    }
    expect(hook.result.current.state.draftRevision).toBe(0)

    flushFrame()
    expect(hook.result.current.state.draftRevision).toBe(1)
    expect(hook.result.current.state.draft.periods[0]?.heating_setpoint).toBe(24)

    flushFrame()
    expect(hook.result.current.state.draftRevision).toBe(1)

    for (let index = 0; index < 30; index += 1) {
      fireEvent.mouseMove(window, { clientY: valueY(23) })
    }
    flushFrame()
    fireEvent.mouseUp(window)
    expect(hook.result.current.state.draftRevision).toBe(2)
    expect(hook.result.current.state.draft.periods[0]?.heating_setpoint).toBe(23)
  })

  it('visibly rejects out-of-range values without changing the draft', async () => {
    const { hook } = await renderDailyEditor()
    const grip = screen.getByTestId('control-timeline-value-grip-0-heating')
    fireEvent.mouseDown(grip, { clientY: valueY(22) })
    fireEvent.mouseMove(window, { clientY: valueY(40) })

    expect(screen.getByRole('alert')).toHaveTextContent(/Heating setpoint must stay within 10–35/)
    expect(hook.result.current.state.draftRevision).toBe(0)

    fireEvent.mouseMove(window, { clientY: valueY(25) })
    flushFrame()
    expect(hook.result.current.state.draft.periods[0]?.heating_setpoint).toBe(25)
    fireEvent.mouseUp(window)
  })

  it('snaps boundary drags to 5 minutes and clamps between neighbouring periods', async () => {
    const { hook } = await renderDailyEditor()
    const grip = screen.getByTestId('control-timeline-boundary-grip-1-start')

    fireEvent.mouseDown(grip, { clientX: timeX(720) })
    fireEvent.mouseMove(window, { clientX: timeX(690) })
    flushFrame()
    expect(hook.result.current.state.draft.periods[1]?.start_time).toBe('12:00')

    fireEvent.mouseMove(window, { clientX: timeX(813) })
    flushFrame()
    expect(hook.result.current.state.draft.periods[1]?.start_time).toBe('13:35')
    fireEvent.mouseUp(window)
  })

  it('steps value and boundary grips from the keyboard', async () => {
    const { hook } = await renderDailyEditor()
    fireEvent.keyDown(screen.getByTestId('control-timeline-value-grip-0-heating'), { key: 'ArrowUp' })
    expect(hook.result.current.state.draft.periods[0]?.heating_setpoint).toBe(22.1)

    fireEvent.keyDown(screen.getByTestId('control-timeline-value-grip-0-co2'), { key: 'ArrowDown' })
    expect(hook.result.current.state.draft.periods[0]?.co2_setpoint).toBe(890)

    fireEvent.keyDown(screen.getByTestId('control-timeline-boundary-grip-0-start'), { key: 'ArrowLeft' })
    expect(hook.result.current.state.draft.periods[0]?.start_time).toBe('05:55')
  })

  it('renders grips in the daily default and clears them once rolling is selected', async () => {
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port() }))
    const compact = render(<ControlTimeline mode="compact" controller={hook.result.current} />)
    expect(screen.queryByTestId('control-timeline-value-grip-0-heating')).not.toBeInTheDocument()
    compact.unmount()

    const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    const triggerDraw = () => {
      for (const instance of instances) {
        const mergedHooks = instance.mergedHooks()
        for (const drawHook of (mergedHooks.draw as Array<(u: unknown) => void> | undefined) ?? []) {
          drawHook?.(instance)
        }
      }
    }
    await waitFor(() => expect(screen.getByTestId('control-timeline-boundary-grip-0-start')).toBeInTheDocument())
    triggerDraw()

    fireEvent.click(screen.getByRole('button', { name: 'rolling' }))
    triggerDraw()
    await waitFor(() => expect(screen.queryByTestId('control-timeline-value-grip-0-heating')).not.toBeInTheDocument())
    view.unmount()
  })

  it('exposes keyboard-accessible grips with aria labels and no effective-series grips', async () => {
    const { view } = await renderDailyEditor()
    expect(screen.getByTestId('control-timeline-value-grip-0-heating')).toHaveAttribute('aria-label', 'Adjust Day heating setpoint')
    const allGrips = Array.from(document.querySelectorAll('[data-testid*="-grip-"]'))
    expect(allGrips.length).toBe(12)
    expect(allGrips.some((grip) => grip.getAttribute('data-testid')?.includes('effective'))).toBe(false)
    view.unmount()
  })
})
