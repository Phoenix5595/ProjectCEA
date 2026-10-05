/**
 * Align store-shaped monitoring data onto a shared uPlot x grid.
 *
 * This module is the public orchestrator: it derives the visible window,
 * chooses the shared x grid, aligns every sensor and control series, and
 * returns bands and photoperiod intervals. Detail work lives in sibling
 * modules so this file stays focused on the high-level pipeline.
 */
import type { LiveSensorValue, SensorSeries } from '../api'

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
import {
  mergeControlSeries,
  mergeDeviceSeries,
  mergeLightSeries,
  mergePidSeries,
} from './alignSeries.control'
import { collectTimestamps, coarsenedGrid, indexOfNow, windowBounds } from './alignSeries.grid'
import { alignPhotoperiod, alignSensor } from './alignSeries.series'
import type { AlignInput, AlignedBand, AlignedData, AlignedSeries } from './alignSeries.types'
import { seriesKey } from './alignSeries.types'
import { composeLightTrajectory, lightValueAt } from './lightTrajectory'
import { MAX_BUDGET } from './pointBudget'

export interface BaseAlignment {
  readonly data: AlignedData
  readonly rangeKind: AlignInput['range']['kind']
}

type BaseAlignInput = Omit<AlignInput, 'live'>

/** Build the static historical grid and series for one panel. */
export function alignSeriesBase(input: BaseAlignInput): BaseAlignment {
  const maxPoints = boundedMaxPoints(input.maxPoints)
  const bounds = windowBounds(input.range, input.now)
  const start = bounds.start
  const end = bounds.end
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
    series.push(
      ...buildSensorSeries(
        raw.sensor,
        family,
        mean,
        min,
        max,
        aggregated,
        node,
        raw.unit,
        raw.unit_family,
        presentation
      )
    )
    bands.push(bandForSensor(raw.sensor))
  }

  for (const cs of mergeControlSeries(input.controlHistory, input.projectionHistory)) {
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, cs.name))
    series.push(...buildControlSeries(cs, x, start, end, aggregated, presentation))
  }

  const recordedEnd = input.controlHistory?.range.end.getTime() ?? Number.NEGATIVE_INFINITY
  const projectedEnd = input.projectionHistory?.range.end.getTime() ?? Number.NEGATIVE_INFINITY
  for (const light of mergeLightSeries(input.controlHistory, input.projectionHistory)) {
    const trajectory = composeLightTrajectory(
      light.history,
      light.projection,
      recordedEnd,
      projectedEnd
    )
    if (trajectory.length === 0) continue
    const hasRecordedFiniteCoverage = trajectory.some(
      segment =>
        segment.origin === 'recorded' &&
        segment.startValue !== null &&
        segment.endValue !== null
    )
    const origin =
      hasRecordedFiniteCoverage || light.projection === undefined
        ? (light.history?.seriesOrigin ?? 'recorded')
        : 'projected'
    const quality = hasRecordedFiniteCoverage
      ? 'exact'
      : (light.projection?.seriesQuality ??
        trajectory[0]?.quality ??
        light.history?.seriesQuality ??
        'unavailable')
    const displayName = input.lightRegistry
      ?.find(registry => registry.device_name === light.deviceName)
      ?.display_name?.trim()
    const label = `${displayName || light.deviceName} - Intensity`
    const basePresentation = presentationFromSpec(findSpec(input.seriesSpecs, light.deviceName))
    const presentation =
      basePresentation === undefined ? undefined : Object.freeze({ ...basePresentation, label })
    series.push({
      key: seriesKey('light', light.deviceName, 'linear'),
      label,
      kind: 'linear',
      source: 'light',
      metric: light.deviceName,
      family: 'light',
      role: 'linear',
      y: x.map(timestamp => lightValueAt(trajectory, timestamp)),
      origin,
      quality,
      isAggregated:
        aggregated ||
        (light.history?.seriesIsAggregated ?? false) ||
        (light.projection?.seriesIsAggregated ?? false),
      unit: '%',
      unitFamily: 'percent',
      lightTrajectory: trajectory,
      ...(presentation === undefined ? {} : { presentation }),
    })
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
      scaleDefaults: input.scaleDefaults,
    },
    rangeKind: input.range.kind,
  }
}

/** Apply the current live values without rebuilding historical grids. */
export function applyLiveTail(
  base: BaseAlignment,
  live: LiveSensorValue[],
  now: Date
): AlignedData {
  const { data } = base
  if (live.length === 0) return data

  const nowMs = now.getTime()
  if (data.x.length === 0) return data

  let tailIndex = data.x.indexOf(nowMs)
  let x = data.x
  let inserted = false
  let droppedOldest = false

  if (tailIndex < 0) {
    if (base.rangeKind === 'fixed') return data
    const nextIndex = data.x.findIndex(timestamp => timestamp > nowMs)
    if (data.x.length < MAX_BUDGET) {
      tailIndex = nextIndex < 0 ? data.x.length : nextIndex
      x = [...data.x.slice(0, tailIndex), nowMs, ...data.x.slice(tailIndex)]
      inserted = true
    } else {
      const first = data.x[0]
      if (first === undefined || nowMs < first) return data
      const retained = data.x.slice(1)
      const retainedNextIndex = retained.findIndex(timestamp => timestamp > nowMs)
      tailIndex = retainedNextIndex < 0 ? retained.length : retainedNextIndex
      x = [...retained.slice(0, tailIndex), nowMs, ...retained.slice(tailIndex)]
      inserted = true
      droppedOldest = true
    }
  }

  const valuesBySensor = new Map(
    live
      .filter(value => value.timestamp.getTime() <= nowMs)
      .map(value => [value.sensor, value.value])
  )
  const series = data.series.map(aligned => {
    const retained = droppedOldest ? aligned.y.slice(1) : aligned.y
    const insertedValue =
      aligned.lightTrajectory !== undefined
        ? lightValueAt(aligned.lightTrajectory, nowMs)
        : aligned.kind === 'step'
          ? (retained[tailIndex - 1] ?? null)
          : null
    const y = inserted
      ? [...retained.slice(0, tailIndex), insertedValue, ...retained.slice(tailIndex)]
      : retained.slice()
    const liveValue = aligned.source === 'sensor' ? valuesBySensor.get(aligned.metric) : undefined
    if (liveValue !== undefined) y[tailIndex] = liveValue
    return { ...aligned, y }
  })

  return { ...data, x, series, nowIndex: tailIndex }
}

export const LEGACY_SAFETY_CEILING = 20_000

/** Preserve the legacy whole-input API while enforcing the client safety ceiling. */
export function alignSeries(input: AlignInput): AlignedData {
  const base = alignSeriesBase({
    ...input,
    series: withLive(input.series, input.live, input.now.getTime()),
  })
  return base.data
}

function boundedMaxPoints(maxPoints: number | undefined): number {
  // Unbudgeted (legacy-server) payloads are hard-capped at the safety ceiling;
  // explicit budgets are honored up to MAX_BUDGET since the server pre-thins.
  if (maxPoints === undefined) return LEGACY_SAFETY_CEILING
  return Math.min(MAX_BUDGET, Math.max(2, Math.floor(maxPoints)))
}

/** Merge live values into their matching sensor series at `now`. */
function withLive(series: SensorSeries[], live: LiveSensorValue[], now: number): SensorSeries[] {
  const bySensor = new Map(live.map(v => [v.sensor, v]))
  return series.map(s => {
    const lv = bySensor.get(s.sensor)
    if (!lv || s.points.some(p => p.timestamp.getTime() === now)) return s
    return {
      ...s,
      points: [
        ...s.points,
        {
          timestamp: new Date(now),
          average: lv.value,
          minimum: lv.value,
          maximum: lv.value,
          sample_count: 1,
        },
      ],
    }
  })
}
