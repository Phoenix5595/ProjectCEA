import type uPlot from 'uplot'

import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { CurrentSnapshot, FutureProjection } from '../../monitoring/api'
import type { RichTrajectoryEnvelope } from '../api/contracts'

import { buildLocalScheduledSeries, type ValueMetric } from './dragInteraction'
import { buildEnvelopeSeries, envelopeSampleTimes, normalizeTimelineMetric, type EnvelopeSeriesKey } from './envelopeSeries'
import { scheduleOccurrences } from './scheduleClock'
import { timelineScaleForMetric, timelineSeriesMeta, type TimelineScale, type TimelineSeriesKey, type TimelineSeriesMeta, type TimelineSeriesRole, type TimelineWindowMs } from './timelineOptions'

export type TimelineSampleQuality = 'exact' | 'estimated' | 'unavailable'
export type TimelineSampleQualities = ReadonlyMap<TimelineSeriesKey, readonly TimelineSampleQuality[]>
export interface TimelineSources {
  readonly data: uPlot.AlignedData
  readonly meta: readonly TimelineSeriesMeta[]
  readonly qualities: TimelineSampleQualities
  readonly sampleTimes: readonly number[]
}
export interface TimelineSourcesOptions {
  readonly location: string
  readonly cluster: string
  readonly window: TimelineWindowMs
  readonly now: number
  readonly current: CurrentSnapshot | null
  readonly future: readonly FutureProjection[]
  readonly saved: RichTrajectoryEnvelope | null
  readonly draft: RichTrajectoryEnvelope | null
  readonly localDraft: readonly ClimatePeriod[] | null
  readonly selectedLabel: string
  readonly localSaved?: readonly ClimatePeriod[] | null
  readonly activeLabel: string | null
  /**
   * The editor holds unsaved changes. A draft source renders only when this is
   * true and at least one plotted draft value differs from Saved.
   */
  readonly draftDirty: boolean
}

/** Decimal places the tooltip renders for each unit family (see `formatTooltipValue`). */
const DISPLAYED_DECIMALS: Record<TimelineScale, number> = { temp: 1, vpd: 2, co2: 0 }

/** Round a plotted value to the precision its tooltip displays. */
export function displayedPrecisionValue(scale: TimelineScale, value: number): number {
  const factor = 10 ** DISPLAYED_DECIMALS[scale]
  return Math.round(value * factor) / factor
}

/**
 * Whether `candidate` holds any plotted value that differs from `reference` at
 * the same x after rounding to the tooltip's displayed precision. Two absent
 * samples are equal; a value against an absent reference differs.
 */
export function plottedValuesDiffer(
  scale: TimelineScale,
  reference: readonly (number | null)[],
  candidate: readonly (number | null)[]
): boolean {
  const length = Math.max(reference.length, candidate.length)
  for (let index = 0; index < length; index += 1) {
    const expected = reference[index] ?? null
    const actual = candidate[index] ?? null
    if (expected === null || actual === null) {
      if (expected !== actual) return true
      continue
    }
    if (displayedPrecisionValue(scale, expected) !== displayedPrecisionValue(scale, actual)) return true
  }
  return false
}

/** Exactly the observer's segment normalization; empty input is not an identifier. */
export function currentPublicationSegment(value: string): string | null {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  if (!normalized) return null
  return /^\d/.test(normalized) ? `v_${normalized}` : normalized
}

export function currentProfileSeriesId(location: string, cluster: string, kind: 'mode' | 'submode'): string | null {
  const room = currentPublicationSegment(location)
  const group = currentPublicationSegment(cluster)
  return room == null || group == null ? null : `${room}.${group}.setpoint.profile_${kind}_id`
}

export type TimelineSetpointMetric = 'heating_setpoint' | 'cooling_setpoint' | 'vpd_setpoint' | 'co2_setpoint'

export function currentSetpointSeriesId(location: string, cluster: string, metric: TimelineSetpointMetric): string | null {
  const room = currentPublicationSegment(location)
  const group = currentPublicationSegment(cluster)
  return room == null || group == null ? null : `${room}.${group}.setpoint.effective_${metric}`
}

const metrics: readonly ValueMetric[] = ['heating', 'cooling', 'vpd', 'co2']

/** All authorities share one half-open grid; rich effective is never current. */
export function buildTimelineSources(options: TimelineSourcesOptions): TimelineSources {
  const { window, now, location, cluster } = options
  const times = new Set(envelopeSampleTimes({ start: new Date(window.start), end: new Date(window.end) }))
  const addBoundary = (time: number) => {
    if (time >= window.start && time <= window.end) times.add(time)
    if (time - 1 >= window.start && time - 1 < window.end) times.add(time - 1)
  }
  const envelopes = [options.saved, options.draft].filter((value): value is RichTrajectoryEnvelope => value != null)
  for (const envelope of envelopes) {
    addBoundary(envelope.window.start.getTime())
    addBoundary(envelope.window.end.getTime())
    for (const segment of envelope.segments) {
      addBoundary(segment.start.getTime())
      addBoundary(segment.end.getTime())
    }
  }
  for (const interval of options.future) {
    addBoundary(interval.valid_from.getTime())
    addBoundary(interval.valid_until.getTime())
    for (const point of interval.series) {
      addBoundary(point.valid_from.getTime())
      addBoundary(point.valid_until.getTime())
    }
  }
  for (const point of options.current?.series ?? []) addBoundary(point.observed_at.getTime())
  for (const period of [...options.localDraft ?? [], ...options.localSaved ?? []]) {
    for (const clock of [period.start_time, period.end_time]) {
      for (const time of scheduleOccurrences(window.start - 36 * 3_600_000, window.end, clock)) {
        addBoundary(time)
        if (clock === period.start_time) addBoundary(time + period.ramp_minutes * 60_000)
      }
    }
  }
  const sampleTimes = [...times].sort((a, b) => a - b)
  const meta: TimelineSeriesMeta[] = []
  const values: (number | null)[][] = []
  const qualityBuffers: TimelineSampleQuality[][] = []
  const add = (key: EnvelopeSeriesKey, role: TimelineSeriesRole, label: string,
    samples: readonly (number | null)[], quality: readonly TimelineSampleQuality[]) => {
    const entry = timelineSeriesMeta([key], role, `${label} · `)[0]!
    meta.push(entry)
    values.push([...samples])
    qualityBuffers.push([...quality])
  }
  const current = options.current
  const roomSegment = currentPublicationSegment(location)
  const clusterSegment = currentPublicationSegment(cluster)
  const prefix = roomSegment == null || clusterSegment == null ? null : `${roomSegment}.${clusterSegment}.`
  const fresh = current != null && current.observed_at.getTime() <= now && now < current.valid_until.getTime() &&
    prefix != null && current.series.some(point => point.series_id.value.startsWith(prefix))
  for (const metric of metrics) {
    const point = fresh ? current.series.find(item => item.series_id.value === currentSetpointSeriesId(location, cluster, `${metric}_setpoint`)) : undefined
    const usable = point != null && point.quality !== 'unavailable' && point.observed_at.getTime() <= now &&
      now < point.valid_until.getTime() && point.value != null && Number.isFinite(point.value)
    const samples = sampleTimes.map(time => usable && time < window.end && time === point.observed_at.getTime() ? point.value : null)
    add(`${metric}_setpoint:effective`, 'active-current', 'Current effective', samples,
      samples.map(value => value === null ? 'unavailable' : point!.quality))
    const futureQualities: TimelineSampleQuality[] = []
    const futureSamples = sampleTimes.map(time => {
      let value: number | null = null
      let quality: TimelineSampleQuality = 'unavailable'
      let latest = -Infinity
      if (fresh && time < window.end) for (const interval of options.future) {
        if (interval.version.contract_version !== current.version.contract_version ||
          interval.version.config_version !== current.version.config_version || interval.version.revision !== current.version.revision ||
          interval.generated_at.getTime() > now || interval.valid_until.getTime() <= now ||
          time < interval.valid_from.getTime() || time >= interval.valid_until.getTime()) continue
        for (const item of interval.series) {
          if (item.series_id.value !== `climate.${metric}_setpoint_target` || item.valid_from.getTime() > time ||
            time >= item.valid_until.getTime() || item.valid_until.getTime() <= now ||
            item.valid_from.getTime() < interval.valid_from.getTime() || item.valid_until.getTime() > interval.valid_until.getTime() ||
            item.valid_from.getTime() < latest) continue
          latest = item.valid_from.getTime()
          value = item.quality === 'unavailable' || item.value == null || !Number.isFinite(item.value) ? null : item.value
          quality = value === null ? 'unavailable' : 'estimated'
        }
      }
      futureQualities.push(quality)
      return value
    })
    add(`${metric}_setpoint:effective`, 'active-future', 'Running forecast (estimated)', futureSamples, futureQualities)
  }
  const savedLabel = `Saved ${options.selectedLabel}${options.activeLabel === options.selectedLabel ? '' : ' (not active)'}`
  const savedSamples = new Map<string, readonly (number | null)[]>()
  if (options.saved != null) {
    const envelope = options.saved
    const built = buildEnvelopeSeries(envelope, sampleTimes)
    for (const key of built.keys.filter(candidate => candidate.endsWith(':scheduled'))) {
      const metric = key.slice(0, key.lastIndexOf(':'))
      const { samples, quality } = scheduledEnvelopeSamples(envelope, metric, built.series.get(key)!, sampleTimes, window.end)
      savedSamples.set(metric, samples)
      add(key, 'selected-saved', savedLabel, samples, quality)
    }
  }
  if (options.localSaved != null && options.saved == null) {
    const local = buildLocalScheduledSeries(options.localSaved, { start: new Date(window.start), end: new Date(window.end) }, sampleTimes)
    for (const metric of metrics) {
      const samples = local.series.get(metric)!
      savedSamples.set(`${metric}_setpoint`, samples)
      add(`${metric}_setpoint:scheduled`, 'selected-saved',
        `Saved ${options.selectedLabel} (local estimate${options.activeLabel === options.selectedLabel ? '' : ', not active'})`,
        samples, samples.map(value => value === null ? 'unavailable' : 'estimated'))
    }
  }
  const draftEntries: Array<{
    readonly key: EnvelopeSeriesKey
    readonly label: string
    readonly samples: readonly (number | null)[]
    readonly quality: readonly TimelineSampleQuality[]
  }> = []
  if (options.draft != null) {
    const envelope = options.draft
    const built = buildEnvelopeSeries(envelope, sampleTimes)
    for (const key of built.keys.filter(candidate => candidate.endsWith(':scheduled'))) {
      const metric = key.slice(0, key.lastIndexOf(':'))
      const { samples, quality } = scheduledEnvelopeSamples(envelope, metric, built.series.get(key)!, sampleTimes, window.end)
      draftEntries.push({ key, label: `Draft ${options.selectedLabel} (preview)`, samples, quality })
    }
  }
  if (options.localDraft != null) {
    const local = buildLocalScheduledSeries(options.localDraft, { start: new Date(window.start), end: new Date(window.end) }, sampleTimes)
    for (const metric of metrics) {
      const samples = local.series.get(metric)!
      draftEntries.push({
        key: `${metric}_setpoint:scheduled`,
        label: `Draft ${options.selectedLabel} (local estimate)`,
        samples,
        quality: samples.map(value => value === null ? 'unavailable' : 'estimated'),
      })
    }
  }
  // A draft is a hypothetical line, not a fact: it renders only for a dirty
  // editor and only when a plotted value really differs from Saved. Label- or
  // photoperiod-only edits change nothing plotted and add no Draft row.
  const draftDiffers = options.draftDirty && draftEntries.some(entry => {
    const metric = entry.key.slice(0, entry.key.lastIndexOf(':'))
    return plottedValuesDiffer(timelineScaleForMetric(metric), savedSamples.get(metric) ?? [], entry.samples)
  })
  if (draftDiffers) {
    for (const entry of draftEntries) {
      add(entry.key, 'selected-draft', entry.label, entry.samples, entry.quality)
    }
  }
  // When the inspected profile is the active one, the running forecast and
  // Saved describe the same future: keep the forecast only where it differs
  // from Saved at the tooltip's displayed precision.
  if (options.activeLabel != null && options.activeLabel === options.selectedLabel) {
    for (const [index, entry] of meta.entries()) {
      if (entry.role !== 'active-future') continue
      const savedIndex = meta.findIndex(candidate => candidate.role === 'selected-saved' && candidate.metric === entry.metric)
      if (savedIndex < 0) continue
      const saved = values[savedIndex]!
      const samples = values[index]!
      const quality = qualityBuffers[index]!
      const scale = timelineScaleForMetric(entry.metric)
      for (let sample = 0; sample < samples.length; sample += 1) {
        const forecast = samples[sample]
        const expected = saved[sample] ?? null
        if (forecast == null || expected == null) continue
        if (displayedPrecisionValue(scale, forecast) === displayedPrecisionValue(scale, expected)) {
          samples[sample] = null
          quality[sample] = 'unavailable'
        }
      }
    }
  }
  const qualities = new Map<TimelineSeriesKey, readonly TimelineSampleQuality[]>()
  for (const [index, entry] of meta.entries()) qualities.set(entry.key, qualityBuffers[index]!)
  return { sampleTimes, data: [sampleTimes.map(time => (time - window.start) / 60_000), ...values], meta, qualities }
}

/**
 * Samples and per-sample provenance of one scheduled metric from a rich
 * envelope. Every segment covers `[start, end)`; a value never carries past
 * its own coverage or outside the display window, and the latest-starting
 * segment wins at a shared boundary.
 */
function scheduledEnvelopeSamples(
  envelope: RichTrajectoryEnvelope,
  metric: string,
  samples: readonly (number | null)[],
  sampleTimes: readonly number[],
  windowEnd: number
): { samples: (number | null)[]; quality: TimelineSampleQuality[] } {
  const quality = sampleTimes.map(time => {
    let found: TimelineSampleQuality = 'unavailable'
    let latest = -Infinity
    if (time < windowEnd && time >= envelope.window.start.getTime() && time < envelope.window.end.getTime()) {
      for (const segment of envelope.segments) {
        if (normalizeTimelineMetric(segment.metric) === metric && segment.trajectory_kind === 'scheduled' &&
          segment.start.getTime() <= time && time < segment.end.getTime() && segment.start.getTime() >= latest) {
          latest = segment.start.getTime()
          found = segment.shape === 'unavailable' ? 'unavailable' : segment.quality
        }
      }
    }
    return found
  })
  return {
    samples: samples.map((value, index) => quality[index] === 'unavailable' ? null : value),
    quality,
  }
}
