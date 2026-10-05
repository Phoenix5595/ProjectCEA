import uPlot from 'uplot'

import type { AlignedData, SeriesKey } from '../../data'

/** Draw exact light segments independently of the shared, budgeted x grid. */
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
    let stroke: Path2D | undefined
    let previous: { readonly time: number; readonly y: number } | undefined

    for (const segment of segments) {
      if (
        segment.end <= segment.start ||
        segment.startValue === null ||
        segment.endValue === null
      ) {
        previous = undefined
        continue
      }

      const xStart = u.valToPos(segment.start, 'x', true)
      const xEnd = u.valToPos(segment.end, 'x', true)
      const yStart = u.valToPos(segment.startValue, scale, true)
      const yEnd = u.valToPos(segment.endValue, scale, true)
      if (stroke === undefined) stroke = new Path2D()
      if (previous !== undefined && previous.time === segment.start) {
        if (previous.y !== yStart) stroke.lineTo(xStart, yStart)
      } else {
        stroke.moveTo(xStart, yStart)
      }

      if (segment.shape === 'step') {
        stroke.lineTo(xEnd, yStart)
        if (yStart !== yEnd) stroke.lineTo(xEnd, yEnd)
      } else {
        stroke.lineTo(xEnd, yEnd)
      }
      previous = { time: segment.end, y: yEnd }
    }

    return stroke === undefined ? null : { stroke }
  }
}
