import { describe, expect, it, vi } from 'vitest'
import { act } from '@testing-library/react'
import type uPlot from 'uplot'
import {
  LABEL_ALPHA,
  LABEL_EDGE_PADDING_PX,
  LABEL_FONT,
  layoutPeriodLabels,
  periodLabelSegments,
  periodLabelsPlugin,
} from '../periodLabelsPlugin'

const DAY = Date.parse('2026-01-01T00:00:00.000Z')
const WINDOW = { start: DAY, end: DAY + 86_400_000 }
const MIN = 60_000

const periods = [
  { period_name: 'Night', start_time: '22:00', end_time: '06:00' },
  { period_name: 'Morning shift', start_time: '06:00', end_time: '12:00' },
  { period_name: 'Day', start_time: '12:00', end_time: '22:00' },
]

describe('periodLabelSegments', () => {
  it('expands a wrap-around period into two segments inside the window', () => {
    const segments = periodLabelSegments([periods[0]], WINDOW.start, WINDOW.end)
    expect(segments).toEqual([
      { text: 'Night', startMs: DAY, endMs: DAY + 6 * 60 * MIN },
      { text: 'Night', startMs: DAY + 22 * 60 * MIN, endMs: DAY + 24 * 60 * MIN },
    ])
  })

  it('tracks a draft time change in the segment extent', () => {
    const before = periodLabelSegments([{ period_name: 'Day', start_time: '12:00', end_time: '22:00' }], WINDOW.start, WINDOW.end)
    const after = periodLabelSegments([{ period_name: 'Day', start_time: '13:00', end_time: '22:00' }], WINDOW.start, WINDOW.end)
    expect(after[0].startMs).toBe(before[0].startMs + 60 * MIN)
  })
})

describe('layoutPeriodLabels', () => {
  const plotWidthPx = 1440

  it('places labels within their period extents without overlap', () => {
    const segments = periodLabelSegments(periods, WINDOW.start, WINDOW.end)
    const labels = layoutPeriodLabels(segments, WINDOW, plotWidthPx)
    for (let index = 1; index < labels.length; index += 1) {
      expect(labels[index].startMs).toBeGreaterThanOrEqual(labels[index - 1].endMs - MIN)
    }
    const morning = labels.find((label) => label.text === 'Morning shift')
    expect(morning?.clippedText).toBe('Morning shift')
    expect(morning?.startMs).toBe(DAY + 6 * 60 * MIN)
    expect(morning?.endMs).toBe(DAY + 12 * 60 * MIN)
  })

  it('truncates a narrow period with an ellipsis and drops degenerate extents', () => {
    const segments = [
      { text: 'Very long period name', startMs: DAY, endMs: DAY + 30 * MIN },
      { text: 'Wide enough name', startMs: DAY + 30 * MIN, endMs: DAY + 4 * 60 * MIN },
    ]
    const labels = layoutPeriodLabels(segments, WINDOW, plotWidthPx)
    expect(labels[0].clippedText).toBe('Ver…')
    expect(labels[0].clippedText.endsWith('…')).toBe(true)
    expect(labels[1].clippedText).toBe('Wide enough name')
  })

  it('drops a segment too narrow to hold even one visible character', () => {
    const labels = layoutPeriodLabels(
      [{ text: 'Blip', startMs: DAY, endMs: DAY + 2 * MIN }],
      WINDOW,
      plotWidthPx,
    )
    expect(labels).toEqual([])
  })
})

describe('periodLabelsPlugin', () => {
  it('carries the alpha and font constants in one place', () => {
    expect(LABEL_ALPHA).toBeGreaterThanOrEqual(0.15)
    expect(LABEL_ALPHA).toBeLessThanOrEqual(0.3)
    expect(LABEL_FONT).toContain('JetBrains Mono')
  })

  it('draws the layout with fillText after the photoperiod bands and before series strokes', () => {
    const plugin = periodLabelsPlugin(
      () => [{ text: 'Day', startMs: DAY + 12 * 60 * MIN, endMs: DAY + 22 * 60 * MIN }],
      WINDOW,
    )
    const calls: string[] = []
    const ctx = {
      save: () => calls.push('save'),
      restore: () => calls.push('restore'),
      fillText: (text: string, x: number, y: number) => calls.push(`fillText:${text}:${x}:${y}`),
      valToPos: undefined,
      globalAlpha: 1,
      font: '',
      fillStyle: '',
      textAlign: '',
      textBaseline: '',
    } as unknown as CanvasRenderingContext2D
    const u = {
      ctx,
      bbox: { left: 0, top: 0, width: 1440, height: 400 },
      valToPos: (value: number) => ((value - WINDOW.start) / (WINDOW.end - WINDOW.start)) * 1440,
    } as unknown as uPlot

    act(() => {
      const drawClear = plugin.hooks.drawClear
      const hooks = Array.isArray(drawClear) ? drawClear : drawClear ? [drawClear] : []
      hooks.forEach((hook) => (hook as (u: uPlot) => void)(u))
    })

    expect(calls).toContain(`fillText:Day:${(12 * 60 * MIN / (24 * 60 * MIN)) * 1440 + LABEL_EDGE_PADDING_PX}:${400 - LABEL_EDGE_PADDING_PX}`)
    expect(calls[0]).toBe('save')
    expect(ctx.globalAlpha).toBe(LABEL_ALPHA)
    expect(ctx.font).toBe(LABEL_FONT)
    expect(calls.at(-1)).toBe('restore')
  })

  it('draws nothing when the layout is empty', () => {
    const plugin = periodLabelsPlugin(() => [], WINDOW)
    const fillText = vi.fn()
    const ctx = { save: vi.fn(), restore: vi.fn(), fillText } as unknown as CanvasRenderingContext2D
    const u = { ctx, bbox: { left: 0, top: 0, width: 100, height: 100 }, valToPos: () => 0 } as unknown as uPlot
    const drawClear = plugin.hooks.drawClear
    const hooks = Array.isArray(drawClear) ? drawClear : drawClear ? [drawClear] : []
    hooks.forEach((hook) => (hook as (u: uPlot) => void)(u))
    expect(fillText).not.toHaveBeenCalled()
  })
})
