import uPlot from 'uplot'
import type { TimelinePhotoperiodInterval } from './envelopeSeries'
import { readTimelineToken } from './tokens'

/** Owner's original timeline colors — the two documented hardcoded exceptions. */
const SUN_BG = 'rgba(234, 179, 8, 0.45)'
const MOON_BG = 'rgba(168, 85, 247, 0.35)'

export { SUN_BG, MOON_BG }

export function toWindowMinutes(instantMs: number, windowStartMs: number): number {
  return (instantMs - windowStartMs) / 60_000
}

/** Full-height sun/moon bands behind the series, in window-minute x units. */
export function timelinePhotoperiodPlugin(
  getIntervals: () => readonly TimelinePhotoperiodInterval[],
  windowStartMs: number,
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const { ctx, bbox } = u
        for (const interval of getIntervals()) {
          const x0 = u.valToPos(toWindowMinutes(interval.start, windowStartMs), 'x', true)
          const x1 = u.valToPos(toWindowMinutes(interval.end, windowStartMs), 'x', true)
          ctx.save()
          ctx.fillStyle = interval.phase === 'SUN' ? SUN_BG : MOON_BG
          ctx.fillRect(x0, bbox.top, x1 - x0, bbox.height)
          ctx.restore()
        }
      },
    },
  }
}

/** Dashed "now" divider at the current instant, in window-minute x space. */
export function timelineNowDividerPlugin(
  getNowInstant: () => number | null,
  windowStartMs: number,
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const nowInstant = getNowInstant()
        if (nowInstant === null) return
        const { ctx, bbox } = u
        const x = u.valToPos(toWindowMinutes(nowInstant, windowStartMs), 'x', true)
        ctx.save()
        ctx.strokeStyle = readTimelineToken('now')
        ctx.lineWidth = 1
        ctx.globalAlpha = 0.25
        ctx.setLineDash([4, 4])
        ctx.beginPath()
        ctx.moveTo(x, bbox.top)
        ctx.lineTo(x, bbox.top + bbox.height)
        ctx.stroke()
        ctx.restore()
      },
    },
  }
}
