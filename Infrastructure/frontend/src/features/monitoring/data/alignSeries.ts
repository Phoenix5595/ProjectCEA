/**
 * Align store-shaped monitoring data onto a shared uPlot x grid.
 *
 * This module is the public orchestrator: it derives the visible window,
 * chooses the shared x grid, aligns every sensor and control series, and
 * returns bands and photoperiod intervals. Detail work lives in sibling
 * modules so this file stays focused on the high-level pipeline.
 */
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
import { alignSensor, composePhotoperiod } from './alignSeries.series'
import type { AlignInput, AlignedBand, AlignedData, AlignedSeries } from './alignSeries.types'
import { seriesKey } from './alignSeries.types'
import { composeLightTrajectory, lightValueAt } from './lightTrajectory'
import { MAX_POINTS_PER_LINE } from './pointBudget'

export interface BaseAlignment {
  readonly data: AlignedData
  readonly rangeKind: AlignInput['range']['kind']
}

type BaseAlignInput = Omit<AlignInput, 'live' | 'liveTail'>

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
    const buckets = alignSensor(raw, x, start, end, aggregated)
    const family = familyForUnit(raw.unit_family)
    const node = nodeFromSensor(raw.sensor)
    const presentation = presentationFromSpec(findSpec(input.seriesSpecs, raw.sensor))
    series.push(
      ...buildSensorSeries(
        raw.sensor,
        family,
        buckets,
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
    const built = buildControlSeries(cs, x, start, end, aggregated, presentation)
    series.push(...built.series)
    bands.push(...built.bands)
  }

  const recordedEnd = input.controlHistory?.range.end.getTime() ?? Number.NEGATIVE_INFINITY
  const projectedEnd = input.projectionHistory?.range.end.getTime() ?? Number.NEGATIVE_INFINITY
  const projectedStart =
    input.projectionHistory?.range.start.getTime() ?? Number.POSITIVE_INFINITY
  for (const light of mergeLightSeries(input.controlHistory, input.projectionHistory)) {
    const trajectory = composeLightTrajectory(
      light.history,
      light.projection,
      recordedEnd,
      projectedEnd,
      now
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
      kind: trajectory.some(segment => segment.shape === 'linear') ? 'linear' : 'step',
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
    const built = buildPidSeries(ps, x, start, end, aggregated, presentation)
    series.push(...built.series)
    bands.push(...built.bands)
  }

  return {
    data: {
      x,
      series,
      bands,
      photoperiod: composePhotoperiod(
        input.photoperiod,
        input.projectionHistory?.photoperiod ?? [],
        recordedEnd,
        projectedStart,
        projectedEnd,
        start,
        end,
        now
      ),
      nowIndex: indexOfNow(x, now),
      nowMs: now,
      sensorIntervalSeconds: input.sensorIntervalSeconds ?? null,
      aggregated,
      scaleDefaults: input.scaleDefaults,
    },
    rangeKind: input.range.kind,
  }
}

type TailSample = { readonly t: number; readonly v: number }

/**
 * Raw tail points per sensor. The buffer is the live path; the legacy `live`
 * array contributes a single point at `now` for callers that have no buffer.
 */
function tailSamples(
  input: Pick<AlignInput, 'live' | 'liveTail'>,
  nowMs: number
): Map<string, TailSample[]> {
  const bySensor = new Map<string, TailSample[]>()
  if (input.liveTail !== undefined) {
    for (const [sensor, points] of input.liveTail.entries()) {
      if (points.length > 0) bySensor.set(sensor, [...points])
    }
    if (bySensor.size > 0) return bySensor
  }
  for (const value of input.live) {
    if (value.timestamp.getTime() > nowMs) continue
    bySensor.set(value.sensor, [{ t: nowMs, v: value.value }])
  }
  return bySensor
}

/**
 * Apply the raw live tail without rebuilding the historical grid (R4).
 *
 * Tail points are appended at their own timestamps, so a current live value
 * always sits at the right edge at its real observation time. Recorded slots,
 * bands and bucket detail are untouched; raw slots carry no min/max/sample
 * count and are marked in the sensor's `liveTail` flags. `now` only moves the
 * x-window consumers; the grid itself is never re-keyed on it.
 */
export function applyLiveTail(
  base: BaseAlignment,
  input: Pick<AlignInput, 'live' | 'liveTail'>,
  now: Date
): AlignedData {
  const { data } = base
  const nowMs = now.getTime()
  if (data.x.length === 0) return { ...data, nowMs, nowIndex: -1 }

  const tails = tailSamples(input, nowMs)
  if (tails.size === 0 || base.rangeKind === 'fixed') {
    return { ...data, nowMs, nowIndex: indexOfNow(data.x, nowMs) }
  }

  const first = data.x[0]
  const last = data.x[data.x.length - 1]
  if (first === undefined || last === undefined) return { ...data, nowMs, nowIndex: -1 }
  const tailTimes = [
    ...new Set(
      [...tails.values()]
        .flat()
        .map(point => point.t)
        .filter(t => t >= first && t <= last)
    ),
  ].sort((left, right) => left - right)
  if (tailTimes.length === 0) return { ...data, nowMs, nowIndex: indexOfNow(data.x, nowMs) }

  const x: number[] = []
  const inserted: boolean[] = []
  const tailByTime = new Map<number, Map<string, number>>()
  for (const [sensor, points] of tails) {
    for (const point of points) {
      let values = tailByTime.get(point.t)
      if (values === undefined) {
        values = new Map()
        tailByTime.set(point.t, values)
      }
      values.set(sensor, point.v)
    }
  }

  let sourceIndex = 0
  for (const t of tailTimes) {
    while (sourceIndex < data.x.length && (data.x[sourceIndex] ?? Number.POSITIVE_INFINITY) < t) {
      x.push(data.x[sourceIndex] ?? 0)
      inserted.push(false)
      sourceIndex++
    }
    if (data.x[sourceIndex] === t) {
      x.push(t)
      inserted.push(false)
      sourceIndex++
      continue
    }
    x.push(t)
    inserted.push(true)
  }
  while (sourceIndex < data.x.length) {
    x.push(data.x[sourceIndex] ?? 0)
    inserted.push(false)
    sourceIndex++
  }

  const series = data.series.map(aligned => {
    const isSensorMean = aligned.source === 'sensor' && aligned.role === 'mean'
    const y: (number | null)[] = new Array(x.length).fill(null)
    const minimum =
      aligned.minimum === undefined ? undefined : new Array<number | null>(x.length).fill(null)
    const maximum =
      aligned.maximum === undefined ? undefined : new Array<number | null>(x.length).fill(null)
    const sampleCount =
      aligned.sampleCount === undefined ? undefined : new Array<number | null>(x.length).fill(null)
    const liveTail = isSensorMean
      ? new Array<boolean>(x.length).fill(false)
      : aligned.liveTail === undefined
        ? undefined
        : new Array<boolean>(x.length).fill(false)
    let source = 0
    for (let index = 0; index < x.length; index++) {
      const t = x[index] ?? 0
      if (inserted[index] === true) {
        const tailValue = isSensorMean ? tailByTime.get(t)?.get(aligned.metric) : undefined
        y[index] =
          tailValue ??
          (aligned.lightTrajectory !== undefined
            ? lightValueAt(aligned.lightTrajectory, t)
            : aligned.kind === 'step'
              ? (y[index - 1] ?? null)
              : null)
        if (liveTail !== undefined && tailValue !== undefined) liveTail[index] = true
        continue
      }
      y[index] = aligned.y[source] ?? null
      if (minimum !== undefined) minimum[index] = aligned.minimum?.[source] ?? null
      if (maximum !== undefined) maximum[index] = aligned.maximum?.[source] ?? null
      if (sampleCount !== undefined) sampleCount[index] = aligned.sampleCount?.[source] ?? null
      if (liveTail !== undefined) liveTail[index] = aligned.liveTail?.[source] ?? false
      // A live sample can share a recorded slot's instant; the raw sample wins
      // for that slot and the bucket detail no longer describes a bucket.
      const tailValue = isSensorMean ? tailByTime.get(t)?.get(aligned.metric) : undefined
      if (tailValue !== undefined) {
        y[index] = tailValue
        if (liveTail !== undefined) liveTail[index] = true
        if (minimum !== undefined) minimum[index] = null
        if (maximum !== undefined) maximum[index] = null
        if (sampleCount !== undefined) sampleCount[index] = null
      }
      source++
    }
    return {
      ...aligned,
      y,
      ...(minimum === undefined ? {} : { minimum }),
      ...(maximum === undefined ? {} : { maximum }),
      ...(sampleCount === undefined ? {} : { sampleCount }),
      ...(liveTail === undefined ? {} : { liveTail }),
    }
  })

  return { ...data, x, series, nowMs, nowIndex: indexOfNow(x, nowMs) }
}

/** Align one complete input (base grid plus raw live tail). */
export function alignSeries(input: AlignInput): AlignedData {
  return applyLiveTail(alignSeriesBase(input), input, input.now)
}

function boundedMaxPoints(maxPoints: number | undefined): number {
  // R1: every displayed line stays at or below the per-line maximum.
  if (maxPoints === undefined) return MAX_POINTS_PER_LINE
  return Math.min(MAX_POINTS_PER_LINE, Math.max(2, Math.floor(maxPoints)))
}
