import { act } from '@testing-library/react'
import type uPlot from 'uplot'
import { describe, expect, it, vi } from 'vitest'

import {
  LABEL_EDGE_PADDING_PX,
  LABEL_FONT,
  LABEL_SHADOW_BLUR_PX,
  LABEL_SHADOW_COLOR,
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
  it('expands stored clocks onto the Toronto local dates inside the window', () => {
    // The UTC-day window covers Toronto 2025-12-31 19:00 → 2026-01-01 19:00
    // (EST = UTC-5): Day's tail, one continuous Night, Morning, then Day again.
    const segments = periodLabelSegments(periods, WINDOW.start, WINDOW.end)
    expect(segments).toEqual([
      { text: 'Day', startMs: DAY, endMs: DAY + 3 * 60 * MIN },
      { text: 'Night', startMs: DAY + 3 * 60 * MIN, endMs: DAY + 11 * 60 * MIN },
      { text: 'Morning shift', startMs: DAY + 11 * 60 * MIN, endMs: DAY + 17 * 60 * MIN },
      { text: 'Day', startMs: DAY + 17 * 60 * MIN, endMs: DAY + 24 * 60 * MIN },
    ])
  })

  it('expands an overnight period as one continuous segment across Toronto midnight', () => {
    const segments = periodLabelSegments([periods[0]], WINDOW.start, WINDOW.end)
    expect(segments).toEqual([
      { text: 'Night', startMs: DAY + 3 * 60 * MIN, endMs: DAY + 11 * 60 * MIN },
    ])
  })

  it('anchors a night begun the previous Toronto day at a mid-morning window start', () => {
    // Window starts 09:00Z = 04:00 Toronto: the previous day's Night (22:00 →
    // 06:00) is still running and must open the window.
    const segments = periodLabelSegments([periods[0]], DAY + 9 * 60 * MIN, DAY + 21 * 60 * MIN)
    expect(segments).toEqual([
      { text: 'Night', startMs: DAY + 9 * 60 * MIN, endMs: DAY + 11 * 60 * MIN },
    ])
  })

  it('tracks a draft time change in the segment extent', () => {
    const windowStart = DAY + 12 * 60 * MIN
    const windowEnd = DAY + 36 * 60 * MIN
    const before = periodLabelSegments(
      [{ period_name: 'Day', start_time: '12:00', end_time: '22:00' }],
      windowStart,
      windowEnd
    )
    const after = periodLabelSegments(
      [{ period_name: 'Day', start_time: '13:00', end_time: '22:00' }],
      windowStart,
      windowEnd
    )
    expect(before).toEqual([{ text: 'Day', startMs: DAY + 17 * 60 * MIN, endMs: DAY + 27 * 60 * MIN }])
    expect(after[0].startMs).toBe(before[0].startMs + 60 * MIN)
  })

  it('resolves the stored clocks to EDT instants on a summer window', () => {
    const summerDay = Date.parse('2026-07-01T00:00:00.000Z')
    const segments = periodLabelSegments(
      [{ period_name: 'Day', start_time: '06:00', end_time: '18:00' }],
      summerDay,
      summerDay + 86_400_000
    )
    expect(segments).toEqual([
      { text: 'Day', startMs: summerDay + 10 * 60 * MIN, endMs: summerDay + 22 * 60 * MIN },
    ])
  })

  it('resolves a folded start clock to its first occurrence', () => {
    // 01:00 on 2026-11-01 occurs twice (EDT then EST); the label anchors the
    // first (EDT) occurrence at 05:00Z, while 03:00 EST ends at 08:00Z.
    const foldDay = Date.parse('2026-11-01T00:00:00.000Z')
    const segments = periodLabelSegments(
      [{ period_name: 'Dawn', start_time: '01:00', end_time: '03:00' }],
      foldDay,
      foldDay + 86_400_000
    )
    expect(segments).toEqual([
      { text: 'Dawn', startMs: foldDay + 5 * 60 * MIN, endMs: foldDay + 8 * 60 * MIN },
    ])
  })

  it('places a gap-time segment at the forward-shifted start clock', () => {
    // 02:30 does not exist on 2026-03-08; the gap-forward shift places the
    // segment start at 07:30Z (03:30 EDT) and the end clock 06:00 is EDT too.
    const gapDay = Date.parse('2026-03-08T00:00:00.000Z')
    const segments = periodLabelSegments(
      [{ period_name: 'Early', start_time: '02:30', end_time: '06:00' }],
      gapDay,
      gapDay + 86_400_000
    )
    expect(segments).toEqual([
      { text: 'Early', startMs: gapDay + 7.5 * 60 * MIN, endMs: gapDay + 10 * 60 * MIN },
    ])
  })

  it('expands an equal start/end clock as the all-day constant label clipped to the window', () => {
    // The canonical constant row (00:00→00:00) is an all-day occurrence on
    // every Toronto local date; adjacent clipped occurrences merge into one.
    const segments = periodLabelSegments(
      [{ period_name: 'Constant', start_time: '00:00', end_time: '00:00' }],
      WINDOW.start,
      WINDOW.end
    )
    expect(segments).toEqual([{ text: 'Constant', startMs: DAY, endMs: DAY + 24 * 60 * MIN }])
  })

  it('returns no segments for an empty window', () => {
    expect(periodLabelSegments(periods, WINDOW.end, WINDOW.start)).toEqual([])
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
    const morning = labels.find(label => label.text === 'Morning shift')
    expect(morning?.clippedText).toBe('Morning shift')
    expect(morning?.startMs).toBe(DAY + 11 * 60 * MIN)
    expect(morning?.endMs).toBe(DAY + 17 * 60 * MIN)
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
      plotWidthPx
    )
    expect(labels).toEqual([])
  })
})

describe('periodLabelsPlugin', () => {
  it('carries the font and shadow constants in one place', () => {
    expect(LABEL_FONT).toContain('JetBrains Mono')
    expect(LABEL_SHADOW_BLUR_PX).toBeGreaterThan(0)
    expect(LABEL_SHADOW_COLOR).toContain('rgba(0, 0, 0')
  })

  it('draws the layout with fillText after the photoperiod bands and before series strokes', () => {
    const plugin = periodLabelsPlugin(
      () => [{ text: 'Day', startMs: DAY + 12 * 60 * MIN, endMs: DAY + 22 * 60 * MIN }],
      { startMs: WINDOW.start, endMs: WINDOW.end }
    )
    const calls: string[] = []
    const ctx = {
      save: () => calls.push('save'),
      restore: () => calls.push('restore'),
      fillText: (text: string, x: number, y: number) => calls.push(`fillText:${text}:${x}:${y}`),
      valToPos: undefined,
      font: '',
      fillStyle: '',
      shadowColor: '',
      shadowBlur: 0,
      textAlign: '',
      textBaseline: '',
    } as unknown as CanvasRenderingContext2D
    const u = {
      ctx,
      bbox: { left: 0, top: 0, width: 1440, height: 400 },
      valToPos: (valueMinutes: number) => (valueMinutes / 1440) * 1440,
    } as unknown as uPlot

    act(() => {
      const drawClear = plugin.hooks.drawClear
      const hooks = Array.isArray(drawClear) ? drawClear : drawClear ? [drawClear] : []
      hooks.forEach(hook => (hook as (u: uPlot) => void)(u))
    })

    expect(calls).toContain(
      `fillText:Day:${12 * 60 + LABEL_EDGE_PADDING_PX}:${400 - LABEL_EDGE_PADDING_PX}`
    )
    expect(calls[0]).toBe('save')
    expect(ctx.font).toBe(LABEL_FONT)
    expect(ctx.fillStyle).toBe('#e2e8f0')
    expect(ctx.shadowColor).toBe(LABEL_SHADOW_COLOR)
    expect(ctx.shadowBlur).toBe(LABEL_SHADOW_BLUR_PX)
    expect(calls.at(-1)).toBe('restore')
  })

  it('draws nothing when the layout is empty', () => {
    const plugin = periodLabelsPlugin(() => [], { startMs: WINDOW.start, endMs: WINDOW.end })
    const fillText = vi.fn()
    const ctx = { save: vi.fn(), restore: vi.fn(), fillText } as unknown as CanvasRenderingContext2D
    const u = {
      ctx,
      bbox: { left: 0, top: 0, width: 100, height: 100 },
      valToPos: () => 0,
    } as unknown as uPlot
    const drawClear = plugin.hooks.drawClear
    const hooks = Array.isArray(drawClear) ? drawClear : drawClear ? [drawClear] : []
    hooks.forEach(hook => (hook as (u: uPlot) => void)(u))
    expect(fillText).not.toHaveBeenCalled()
  })
})
