import uPlot from 'uplot'

import type { AlignedData, LightTrajectorySegment, SeriesKey } from '../../data'

/** Reused canvas dash buffer: 1px round dots separated by 5px at CSS scale. */
const PROJECTED_LIGHT_DASH = [1, 5]

/** Solid recorded and dotted projected geometry for one composed light series. */
export interface LightTrajectorySubpaths {
  readonly solid: Path2D | null
  readonly projected: Path2D | null
}


/**
 * Split one light's composed segments into two independent strokes. Recorded
 * coverage stays solid; projected coverage is stroked dotted by
 * `lightProjectedStrokePlugin`, so each subpath tracks its own continuity and
 * no connector is invented across the provenance boundary at the Now seam.
 * Unavailable coverage breaks both subpaths like any other gap.
 */
export function lightTrajectorySubpaths(
  segments: readonly LightTrajectorySegment[],
  u: uPlot,
  scale: string,
  origin?: LightTrajectorySegment['origin']
): LightTrajectorySubpaths {
  let solid: Path2D | undefined
  let projected: Path2D | undefined
  let solidTime: number | undefined
  let projectedTime: number | undefined
  let solidY: number | undefined
  let projectedY: number | undefined

  for (const segment of segments) {
    if (origin !== undefined && segment.origin !== origin) continue
    if (
      segment.end <= segment.start ||
      segment.startValue === null ||
      segment.endValue === null
    ) {
      solidTime = undefined
      projectedTime = undefined
      continue
    }

    const isProjected = segment.origin === 'projected'
    const path = (isProjected ? (projected ??= new Path2D()) : (solid ??= new Path2D()))
    const previousTime = isProjected ? projectedTime : solidTime
    const previousY = isProjected ? projectedY : solidY

    const xStart = u.valToPos(segment.start, 'x', true)
    const xEnd = u.valToPos(segment.end, 'x', true)
    const yStart = u.valToPos(segment.startValue, scale, true)
    const yEnd = u.valToPos(segment.endValue, scale, true)
    if (previousTime !== undefined && previousTime === segment.start) {
      if (previousY !== yStart) path.lineTo(xStart, yStart)
    } else {
      path.moveTo(xStart, yStart)
    }

    if (segment.shape === 'step') {
      path.lineTo(xEnd, yStart)
      if (yStart !== yEnd) path.lineTo(xEnd, yEnd)
    } else {
      path.lineTo(xEnd, yEnd)
    }
    if (isProjected) {
      projectedTime = segment.end
      projectedY = yEnd
    } else {
      solidTime = segment.end
      solidY = yEnd
    }
  }

  return { solid: solid ?? null, projected: projected ?? null }
}

/** Draw exact recorded light segments independently of the shared, budgeted x grid. */
export function lightTrajectoryPaths(
  key: SeriesKey,
  getData: () => AlignedData
): uPlot.Series.PathBuilder {
  return (u, seriesIndex, _idx0, _idx1) => {
    const series = getData().series.find(candidate => candidate.key === key)
    if (series === undefined) return null
    const segments = series.lightTrajectory
    if (segments === undefined || segments.length === 0) return null

    const scale = u.series[seriesIndex]?.scale ?? series.family
    const { solid } = lightTrajectorySubpaths(segments, u, scale, 'recorded')
    return solid === null ? null : { stroke: solid }
  }
}

/**
 * Stroke the projected subpath of every visible light series after uPlot
 * strokes its solid recorded geometry. The series keeps its own color and
 * width; only the dash pattern separates the estimated future from recorded
 * setpoints, so no second logical light, legend row, or tooltip row exists.
 */
export function lightProjectedStrokePlugin(getData: () => AlignedData): uPlot.Plugin {
  return {
    hooks: {
      drawSeries: (u, seriesIndex) => {
        const aligned = getData().series[seriesIndex - 1]
        if (aligned === undefined || aligned.source !== 'light') return
        const segments = aligned.lightTrajectory
        if (segments === undefined || segments.length === 0) return
        const target = u.series[seriesIndex]
        if (target === undefined || target.show === false) return

        const scale = target.scale ?? aligned.family
        const { projected } = lightTrajectorySubpaths(segments, u, scale, 'projected')
        if (projected === null) return
        strokeDotted(u, seriesIndex, projected)
      },
    },
  }
}

/** Stroke one projected subpath with the dotted pattern, mirroring uPlot's series clipping. */
function strokeDotted(u: uPlot, seriesIndex: number, path: Path2D): void {
  const series = u.series[seriesIndex]
  if (series === undefined) return
  const rawStroke = series.stroke
  const stroke = typeof rawStroke === 'function' ? rawStroke(u, seriesIndex) : rawStroke
  const width = series.width
  if (stroke === undefined || width === undefined || width <= 0) return

  const { ctx, bbox } = u
  const pxWidth = Math.round(width * uPlot.pxRatio * 1000) / 1000
  const offset = (pxWidth % 2) / 2
  ctx.save()
  try {
    // Match uPlot's own series clip: the plot rect widened by half the stroke,
    // so dotted coverage never paints over axes or legend space.
    ctx.beginPath()
    ctx.rect(
      bbox.left - pxWidth / 2,
      bbox.top - pxWidth / 2,
      bbox.width + pxWidth,
      bbox.height + pxWidth
    )
    ctx.clip()
    ctx.translate(offset, offset)
    ctx.strokeStyle = stroke
    ctx.lineWidth = pxWidth
    PROJECTED_LIGHT_DASH[0] = uPlot.pxRatio
    PROJECTED_LIGHT_DASH[1] = 5 * uPlot.pxRatio
    ctx.setLineDash(PROJECTED_LIGHT_DASH)
    ctx.lineCap = 'round'
    ctx.stroke(path)
  } finally {
    ctx.restore()
  }
}
