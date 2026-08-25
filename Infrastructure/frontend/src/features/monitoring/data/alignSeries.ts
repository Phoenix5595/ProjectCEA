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
import { MAX_BUDGET } from './pointBudget'
import { injectMonitoringAlignmentDelay, PERFORMANCE_MARKS_ENABLED } from '../perfMarks'

export interface BaseAlignment {
  readonly data: AlignedData
  readonly rangeKind: AlignInput['range']['kind']
}

type BaseAlignInput = Omit<AlignInput, 'live'>

/** Build the static historical grid and series for one panel. */
export function alignSeriesBase(input: BaseAlignInput): BaseAlignment {
  if (PERFORMANCE_MARKS_ENABLED) injectMonitoringAlignmentDelay()
  const maxPoints = boundedMaxPoints(input.maxPoints)
  const { start, end } = windowBounds(input.range, input.now)
  const now = input.now.getTime()

  const timestamps = collectTimestamps(input, start, end, now)
  const aggregated = timestamps.length > maxPoints
  const x = aggregated ? coarsenedGrid(start, end, maxPoints) : timestamps

  const series: AlignedSeries[] = []
  const bands: AlignedBand[] = []

  for (const raw of input.series) {
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
    data: {
      x,
      series,
      bands,
      photoperiod: alignPhotoperiod(input.photoperiod, start, end),
      nowIndex: indexOfNow(x, now),
      aggregated,
    },
    rangeKind: input.range.kind,
  }
}

/** Apply the current live values without sorting or rebuilding historical grids. */
export function applyLiveTail(base: BaseAlignment, live: LiveSensorValue[], now: Date): AlignedData {
  const { data } = base
  if (live.length === 0) return data

  const nowMs = now.getTime()
  const lastIndex = data.x.length - 1
  const last = data.x[lastIndex]
  if (last === undefined) return data

  let tailIndex = data.x.indexOf(nowMs)
  let x = data.x
  let appended = false

  if (tailIndex < 0) {
    if (base.rangeKind === 'fixed' || nowMs < last) return data
    if (data.x.length < MAX_BUDGET) {
      tailIndex = data.x.length
      x = [...data.x, nowMs]
      appended = true
    } else {
      tailIndex = lastIndex
      x = data.x.slice()
      x[tailIndex] = nowMs
    }
  }

  const valuesBySensor = new Map(live.map((value) => [value.sensor, value.value]))
  const series = data.series.map((aligned) => {
    const y = appended ? [...aligned.y, null] : aligned.y.slice()
    const liveValue = aligned.source === 'sensor' ? valuesBySensor.get(aligned.metric) : undefined
    if (liveValue !== undefined && (appended || y[tailIndex] === null)) y[tailIndex] = liveValue
    return { ...aligned, y }
  })

  return { ...data, x, series, nowIndex: tailIndex }
}

/** Preserve the legacy whole-input API while enforcing the client safety ceiling. */
export function alignSeries(input: AlignInput): AlignedData {
  const base = alignSeriesBase({ ...input, series: withLive(input.series, input.live, input.now.getTime()) })
  return base.data
}

function boundedMaxPoints(maxPoints: number | undefined): number {
  const requested = maxPoints ?? DEFAULT_MAX_POINTS
  return Math.min(MAX_BUDGET, Math.max(2, Math.floor(requested)))
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
