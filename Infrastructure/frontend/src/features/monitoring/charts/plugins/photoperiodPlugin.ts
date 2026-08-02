/**
 * Draw-under photoperiod plugin.
 *
 * Paints plot-wide SUN/MOON background rectangles behind the series lines so
 * day/night intervals read as a backdrop rather than fake y-series.
 */
import uPlot from 'uplot'
import type { PhotoperiodInterval } from '../../data'

export interface PhotoperiodColors {
  sunBg: string
  moonBg: string
}

/** Build a uPlot plugin that draws photoperiod intervals behind the series. */
export function photoperiodPlugin(
  intervals: PhotoperiodInterval[],
  colors: PhotoperiodColors,
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const { ctx, bbox } = u
        for (const interval of intervals) {
          const x0 = u.valToPos(interval.start, 'x', true)
          const x1 = u.valToPos(interval.end, 'x', true)
          const fill = interval.phase === 'SUN' ? colors.sunBg : colors.moonBg
          ctx.save()
          ctx.fillStyle = fill
          ctx.fillRect(x0, bbox.top, x1 - x0, bbox.height)
          ctx.restore()
        }
      },
    },
  }
}
