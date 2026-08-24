/**
 * Align store-shaped monitoring data onto a shared uPlot x grid.
 *
 * This module is the public orchestrator: it derives the visible window,
 * chooses the shared x grid, aligns every sensor and control series, and
 * returns bands and photoperiod intervals. Detail work lives in sibling
 * modules so this file stays focused on the high-level pipeline.
 */
import type { LiveSensorValue, SensorSeries } from '../api'
import { alignPhotoperiod, alignSensor } from './alignSeries.series'
import type { AlignInput, AlignedBand, AlignedData, AlignedSeries } from './alignSeries.types'
import {
  bandForSensor,
  buildControlSeries,
  buildDeviceSeries,
  buildPidSeries,
  buildSensorSeries,
  familyForUnit,
  findSpec,
  nodeFromSensor,
  presentationFromSpec,
} from './alignSeries.builder'
import { mergeControlSeries, mergeDeviceSeries, mergePidSeries } from './alignSeries.control'
import { collectTimestamps, coarsenedGrid, DEFAULT_MAX_POINTS, indexOfNow, windowBounds } from './alignSeries.grid'
import { injectMonitoringAlignmentDelay, PERFORMANCE_MARKS_ENABLED } from '../perfMarks'

/** Align store-shaped monitoring data onto a shared uPlot x grid. */
export function alignSeries(input: AlignInput): AlignedData {
  if (PERFORMANCE_MARKS_ENABLED) injectMonitoringAlignmentDelay()
  const maxPoints = input.maxPoints ?? DEFAULT_MAX_POINTS
  const { start, end } = windowBounds(input.range, input.now)
  const now = input.now.getTime()

  const timestamps = collectTimestamps(input, start, end, now)
  const aggregated = timestamps.length > maxPoints
  const x = aggregated ? coarsenedGrid(start, end, maxPoints) : timestamps

  const series: AlignedSeries[] = []
  const bands: AlignedBand[] = []

  for (const raw of withLive(input.series, input.live, now)) {
    const { mean, min, max } = alignSensor(raw, x, start, end, aggregated)
    const family = familyForUnit(raw.unit_family)
    const node = nodeFromSensor(raw.sensor)
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, raw.sensor))
    series.push(...buildSensorSeries(raw.sensor, family, mean, min, max, aggregated, node, raw.unit, raw.unit_family, presentation))
    bands.push(bandForSensor(raw.sensor))
  }

  for (const cs of mergeControlSeries(input.controlHistory, input.projectionHistory)) {
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, cs.name))
    series.push(...buildControlSeries(cs, x, start, end, aggregated, presentation))
  }

  for (const ds of mergeDeviceSeries(input.controlHistory, input.projectionHistory)) {
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, ds.name))
    series.push(...buildDeviceSeries(ds, x, start, end, aggregated, presentation))
  }

  for (const ps of mergePidSeries(input.controlHistory, input.projectionHistory)) {
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, ps.name))
    series.push(...buildPidSeries(ps, x, start, end, aggregated, presentation))
  }

  return {
    x,
    series,
    bands,
    photoperiod: alignPhotoperiod(input.photoperiod, start, end),
    nowIndex: indexOfNow(x, now),
    aggregated,
  }
}

/** Merge live values into their matching sensor series at `now`. */
function withLive(series: SensorSeries[], live: LiveSensorValue[], now: number): SensorSeries[] {
  const bySensor = new Map(live.map((v) => [v.sensor, v]))
  return series.map((s) => {
    const lv = bySensor.get(s.sensor)
    if (!lv || s.points.some((p) => p.timestamp.getTime() === now)) return s
    return {
      ...s,
      points: [
        ...s.points,
        { timestamp: new Date(now), average: lv.value, minimum: lv.value, maximum: lv.value, sample_count: 1 },
      ],
    }
  })
}
