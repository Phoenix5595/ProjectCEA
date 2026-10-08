/**
 * Draw-under photoperiod plugin.
 *
 * Paints plot-wide SUN/MOON background rectangles behind the series lines so
 * day/night intervals read as a backdrop rather than fake y-series. The fills
 * are fixed semantic colors that never follow the active theme; UNKNOWN spans
 * stay unpainted so missing evidence reads as an honest gap.
 */
import uPlot from 'uplot'

import type { PhotoperiodInterval } from '../../data'

const SUN_BACKGROUND = 'rgba(251, 191, 36, 0.12)'
const MOON_BACKGROUND = 'rgba(129, 140, 248, 0.12)'

/** Build a uPlot plugin that draws photoperiod intervals behind the series. */
export function photoperiodPlugin(
  getIntervals: () => readonly PhotoperiodInterval[]
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: u => {
        const { ctx, bbox } = u
        const bboxRight = bbox.left + bbox.width
        for (const interval of getIntervals()) {
          if (interval.phase === 'UNKNOWN') continue
          const x0 = Math.min(Math.max(u.valToPos(interval.start, 'x', true), bbox.left), bboxRight)
          const x1 = Math.min(Math.max(u.valToPos(interval.end, 'x', true), bbox.left), bboxRight)
          if (x1 <= x0) continue
          ctx.save()
          ctx.fillStyle = interval.phase === 'SUN' ? SUN_BACKGROUND : MOON_BACKGROUND
          ctx.fillRect(x0, bbox.top, x1 - x0, bbox.height)
          ctx.restore()
        }
      },
    },
  }
}
