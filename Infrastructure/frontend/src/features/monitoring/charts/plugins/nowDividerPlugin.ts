/**
 * "Now" divider plugin.
 *
 * Draws a dashed vertical line at the aligned `now` x value, separating
 * recorded history from the future projection. The x value is read on every
 * draw so live data updates move the divider instead of pinning it to the
 * timestamp visible when the chart was first built.
 */
import uPlot from 'uplot'

/** Build a uPlot plugin that draws a vertical divider at the current `nowX`. */
export function nowDividerPlugin(getNowX: () => number | null, color: string): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const nowX = getNowX()
        if (nowX === null) return
        const { ctx, bbox } = u
        const x = u.valToPos(nowX, 'x', true)
        ctx.save()
        ctx.strokeStyle = color
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
