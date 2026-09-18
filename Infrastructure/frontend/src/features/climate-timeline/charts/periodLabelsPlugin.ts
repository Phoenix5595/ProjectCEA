import type uPlot from 'uplot'
import { timeToMinutes } from '../../../utils/timeMath'
import { readTimelineToken } from './tokens'

export const LABEL_ALPHA = 0.25
export const LABEL_FONT = '10px "JetBrains Mono", ui-monospace, monospace'
export const LABEL_EDGE_PADDING_PX = 2

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

const DAY_MS = 86_400_000
const MINUTE_MS = 60_000

/** Expand time-of-day periods into non-overlapping absolute segments across the window. */
export function periodLabelSegments(
  periods: readonly { readonly period_name: string; readonly start_time: string; readonly end_time: string }[],
  windowStartMs: number,
  windowEndMs: number,
): PeriodLabelSegment[] {
  if (windowEndMs <= windowStartMs) return []
  const origin = new Date(windowStartMs)
  const dayOrigin = Date.UTC(origin.getUTCFullYear(), origin.getUTCMonth(), origin.getUTCDate())
  const segments: PeriodLabelSegment[] = []
  const firstDay = 0
  const lastDay = Math.ceil((windowEndMs - dayOrigin) / DAY_MS)
  for (let dayOffset = firstDay; dayOffset <= lastDay; dayOffset += 1) {
    for (const period of periods) {
      const startMin = timeToMinutes(period.start_time)
      const endMin = timeToMinutes(period.end_time)
      const pieces: Array<[number, number]> = startMin < endMin
        ? [[startMin, endMin]]
        : startMin > endMin
          ? [[startMin, 1440], [0, endMin]]
          : []
      for (const [fromMin, toMin] of pieces) {
        const startMs = dayOrigin + dayOffset * DAY_MS + fromMin * MINUTE_MS
        const endMs = dayOrigin + dayOffset * DAY_MS + toMin * MINUTE_MS
        const clippedStart = Math.max(startMs, windowStartMs)
        const clippedEnd = Math.min(endMs, windowEndMs)
        if (clippedEnd - clippedStart >= MINUTE_MS) {
          segments.push({ text: period.period_name, startMs: clippedStart, endMs: clippedEnd })
        }
      }
    }
  }
  return segments.sort((left, right) => left.startMs - right.startMs)
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
  charWidthPx = 6,
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
    const clippedText = maxWidthChars < segment.text.length
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

/** Build a uPlot plugin that paints faint period names below the curve zone. */
export function periodLabelsPlugin(
  getSegments: () => readonly PeriodLabelSegment[],
  windowMs: { readonly start: number; readonly end: number },
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const segments = getSegments()
        if (segments.length === 0) return
        const labels = layoutPeriodLabels(segments, windowMs, u.bbox.width)
        if (labels.length === 0) return
        const { ctx, bbox } = u
        ctx.save()
        ctx.globalAlpha = LABEL_ALPHA
        ctx.font = LABEL_FONT
        ctx.fillStyle = readTimelineToken('axis')
        ctx.textAlign = 'left'
        ctx.textBaseline = 'alphabetic'
        const baseline = bbox.top + bbox.height - LABEL_EDGE_PADDING_PX
        const rightEdge = bbox.left + bbox.width
        for (const label of labels) {
          const x0 = u.valToPos(label.startMs, 'x', true)
          if (x0 >= rightEdge) continue
          ctx.fillText(label.clippedText, Math.max(x0, bbox.left) + LABEL_EDGE_PADDING_PX, baseline)
        }
        ctx.restore()
      },
    },
  }
}
