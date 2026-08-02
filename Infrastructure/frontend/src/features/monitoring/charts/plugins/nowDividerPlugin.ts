/**
 * "Now" divider plugin.
 *
 * Draws a dashed vertical line at the aligned `now` x value, separating
 * recorded history from the future projection.
 */
import uPlot from 'uplot'

/** Build a uPlot plugin that draws a vertical divider at `nowX`. */
export function nowDividerPlugin(nowX: number, color: string): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const { ctx, bbox } = u
        const x = u.valToPos(nowX, 'x', true)
        ctx.save()
        ctx.strokeStyle = color
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
