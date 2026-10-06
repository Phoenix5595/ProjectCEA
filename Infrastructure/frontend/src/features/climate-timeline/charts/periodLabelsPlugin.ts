import type uPlot from 'uplot'

import { timeToMinutes } from '../../../utils/timeMath'

import { scheduleClockInstant, scheduleLocalDates, scheduleNextLocalDate } from './scheduleClock'

import { readTimelineToken } from './tokens'

export const LABEL_FONT = '10px "JetBrains Mono", ui-monospace, monospace'
export const LABEL_EDGE_PADDING_PX = 2
export const LABEL_SHADOW_COLOR = 'rgba(0, 0, 0, 0.65)'
export const LABEL_SHADOW_BLUR_PX = 2

export interface PeriodLabelSegment {
  readonly text: string
  readonly startMs: number
  readonly endMs: number
}

export interface LaidOutLabel {
  readonly text: string
  readonly clippedText: string
  readonly startMs: number
  readonly endMs: number
}

const MINUTE_MS = 60_000

/**
 * Expand time-of-day periods into non-overlapping absolute segments across the
 * window. Periods are Toronto wall clocks, so each occurrence resolves through
 * the schedule clock on Toronto local dates — including the date preceding the
 * window start, so a night begun the previous day still opens the window. An
 * overnight period is one continuous segment from its start clock to its end
 * clock on the next local date; an equal start/end clock is an all-day
 * occurrence running to the same clock on the next local date (the canonical
 * constant all-day row). A segment that does not resolve (spring gap) is
 * skipped, clipped segments below one minute are dropped, and contiguous
 * same-name extents merge so an all-day constant profile reads as one label.
 */
export function periodLabelSegments(
  periods: readonly {
    readonly period_name: string
    readonly start_time: string
    readonly end_time: string
  }[],
  windowStartMs: number,
  windowEndMs: number
): PeriodLabelSegment[] {
  if (windowEndMs <= windowStartMs) return []
  const dates = scheduleLocalDates(windowStartMs, windowEndMs)
  const segments: PeriodLabelSegment[] = []
  for (const localDate of dates) {
    for (const period of periods) {
      const startMin = timeToMinutes(period.start_time)
      const endMin = timeToMinutes(period.end_time)
      const spansMidnight = startMin >= endMin
      const startMs = scheduleClockInstant(localDate, period.start_time)
      if (startMs === null) continue
      const endDate = spansMidnight ? scheduleNextLocalDate(localDate) : localDate
      if (endDate === null) continue
      const endMs = scheduleClockInstant(endDate, period.end_time)
      if (endMs === null) continue
      const clippedStart = Math.max(startMs, windowStartMs)
      const clippedEnd = Math.min(endMs, windowEndMs)
      if (clippedEnd - clippedStart >= MINUTE_MS) {
        segments.push({ text: period.period_name, startMs: clippedStart, endMs: clippedEnd })
      }
    }
  }
  segments.sort((left, right) => left.startMs - right.startMs)
  const merged: PeriodLabelSegment[] = []
  for (const segment of segments) {
    const previous = merged[merged.length - 1]
    if (previous && previous.text === segment.text && previous.endMs === segment.startMs) {
      merged[merged.length - 1] = { ...previous, endMs: Math.max(previous.endMs, segment.endMs) }
      continue
    }
    merged.push(segment)
  }
  return merged
}

/**
 * Place labels inside their own segment extents, truncating with an ellipsis
 * when the extent is too narrow. Segments never overlap, so laid-out labels
 * never overlap either.
 */
export function layoutPeriodLabels(
  segments: readonly PeriodLabelSegment[],
  windowMs: { readonly start: number; readonly end: number },
  plotWidthPx: number,
  charWidthPx = 6
): LaidOutLabel[] {
  const span = Math.max(windowMs.end - windowMs.start, 1)
  const labels: LaidOutLabel[] = []
  for (const segment of segments) {
    const fraction0 = (segment.startMs - windowMs.start) / span
    const fraction1 = (segment.endMs - windowMs.start) / span
    const x0 = fraction0 * plotWidthPx + LABEL_EDGE_PADDING_PX
    const x1 = fraction1 * plotWidthPx - LABEL_EDGE_PADDING_PX
    if (x1 - x0 < charWidthPx) continue
    const maxWidthChars = Math.floor((x1 - x0) / charWidthPx)
    const clippedText =
      maxWidthChars < segment.text.length
        ? `${segment.text.slice(0, Math.max(0, maxWidthChars - 1))}…`
        : segment.text
    if (clippedText.length === 0 || clippedText === '…') continue
    labels.push({
      text: segment.text,
      clippedText,
      startMs: segment.startMs,
      endMs: segment.endMs,
    })
  }
  return labels
}

/** Build a uPlot plugin that paints readable period names below the curve zone. */
export function periodLabelsPlugin(
  getSegments: () => readonly PeriodLabelSegment[],
  window: { readonly startMs: number; readonly endMs: number }
): uPlot.Plugin {
  const toWindowMinutes = (instantMs: number): number => (instantMs - window.startMs) / 60_000
  return {
    hooks: {
      drawClear: u => {
        const segments = getSegments()
        if (segments.length === 0) return
        const labels = layoutPeriodLabels(
          segments,
          { start: window.startMs, end: window.endMs },
          u.bbox.width
        )
        if (labels.length === 0) return
        const { ctx, bbox } = u
        ctx.save()
        ctx.font = LABEL_FONT
        ctx.fillStyle = readTimelineToken('label')
        ctx.shadowColor = LABEL_SHADOW_COLOR
        ctx.shadowBlur = LABEL_SHADOW_BLUR_PX
        ctx.textAlign = 'left'
        ctx.textBaseline = 'alphabetic'
        const baseline = bbox.top + bbox.height - LABEL_EDGE_PADDING_PX
        const rightEdge = bbox.left + bbox.width
        for (const label of labels) {
          const x0 = u.valToPos(toWindowMinutes(label.startMs), 'x', true)
          if (x0 >= rightEdge) continue
          ctx.fillText(label.clippedText, Math.max(x0, bbox.left) + LABEL_EDGE_PADDING_PX, baseline)
        }
        ctx.restore()
      },
    },
  }
}
