import type { RichTrajectoryEnvelope } from '../api/contracts'
import { isMinuteInPeriod, timeToMinutes } from '../../../utils/climatePeriodTimeline'

export type TrajectoryKind = 'scheduled' | 'effective'
export type EnvelopeSeriesKey = `${string}:${TrajectoryKind}`

export interface EnvelopeSeriesSet {
  readonly sampleTimes: readonly number[]
  readonly keys: readonly EnvelopeSeriesKey[]
  readonly series: ReadonlyMap<EnvelopeSeriesKey, readonly (number | null)[]>
  readonly units: ReadonlyMap<string, string>
}

const TIMELINE_METRIC_ORDER = ['heating_setpoint', 'cooling_setpoint', 'vpd_setpoint', 'co2_setpoint'] as const

export function normalizeTimelineMetric(metric: string): string | null {
  if (metric.startsWith('light.')) return null
  const normalized = metric.endsWith('_setpoint') ? metric : `${metric}_setpoint`
  return (TIMELINE_METRIC_ORDER as readonly string[]).includes(normalized) ? normalized : null
}

interface NormStep {
  readonly t: number
  readonly value: number | null
}

interface NormLinear {
  readonly start: number
  readonly end: number
  readonly startValue: number
  readonly endValue: number
}

interface MetricGroup {
  readonly metric: string
  readonly kind: TrajectoryKind
  readonly unit: string
  readonly steps: NormStep[]
  readonly linear: NormLinear[]
}

export function envelopeSampleTimes(
  window: { readonly start: Date; readonly end: Date },
  stepMs = 60_000,
): number[] {
  const times: number[] = []
  const startMs = window.start.getTime()
  const endMs = window.end.getTime()
  for (let t = startMs; t < endMs; t += stepMs) times.push(t)
  times.push(endMs)
  return times
}

function metricOrder(metric: string): number {
  const index = (TIMELINE_METRIC_ORDER as readonly string[]).indexOf(metric)
  return index === -1 ? TIMELINE_METRIC_ORDER.length : index
}

function kindOrder(kind: TrajectoryKind): number {
  return kind === 'scheduled' ? 0 : 1
}

export function groupEnvelopeSegments(envelope: RichTrajectoryEnvelope): MetricGroup[] {
  const groups = new Map<string, MetricGroup>()
  for (const segment of envelope.segments) {
    const metric = normalizeTimelineMetric(segment.metric)
    if (metric === null) continue
    const key = `${metric}:${segment.trajectory_kind}` as EnvelopeSeriesKey
    const existing = groups.get(key)
    const group = existing ?? {
      metric,
      kind: segment.trajectory_kind,
      unit: segment.unit,
      steps: [],
      linear: [],
    }
    if (segment.shape === 'step') {
      group.steps.push({ t: segment.start.getTime(), value: segment.value })
    } else if (segment.shape === 'linear') {
      group.linear.push({
        start: segment.start.getTime(),
        end: segment.end.getTime(),
        startValue: segment.start_value,
        endValue: segment.end_value,
      })
    } else {
      group.steps.push({ t: segment.start.getTime(), value: null })
    }
    groups.set(key, group)
  }
  return [...groups.values()].sort((left, right) => {
    const metricDiff = metricOrder(left.metric) - metricOrder(right.metric)
    return metricDiff !== 0 ? metricDiff : kindOrder(left.kind) - kindOrder(right.kind)
  })
}

function sampleGroup(group: MetricGroup, t: number): number | null {
  for (const ln of group.linear) {
    if (t >= ln.start && t <= ln.end) {
      const frac = ln.end === ln.start ? 0 : (t - ln.start) / (ln.end - ln.start)
      return ln.startValue + frac * (ln.endValue - ln.startValue)
    }
  }
  let value: number | null = null
  for (const step of group.steps) {
    if (step.t <= t) value = step.value
    else break
  }
  return value
}

export function buildEnvelopeSeries(
  envelope: RichTrajectoryEnvelope,
  sampleTimes: readonly number[],
): EnvelopeSeriesSet {
  const groups = groupEnvelopeSegments(envelope)
  const series = new Map<EnvelopeSeriesKey, readonly (number | null)[]>()
  const units = new Map<string, string>()
  for (const group of groups) {
    const key = `${group.metric}:${group.kind}` as EnvelopeSeriesKey
    series.set(key, sampleTimes.map((t) => sampleGroup(group, t)))
    units.set(group.metric, group.unit)
  }
  return {
    sampleTimes,
    keys: groups.map((group) => `${group.metric}:${group.kind}` as EnvelopeSeriesKey),
    series,
    units,
  }
}

export interface TimelinePhotoperiodInput {
  readonly dayStartTime: string
  readonly nightStartTime: string
}

export interface TimelinePhotoperiodInterval {
  readonly start: number
  readonly end: number
  readonly phase: 'SUN' | 'MOON'
}

const DAY_MS = 86_400_000
const MINUTE_MS = 60_000

export function photoperiodIntervals(
  photoperiod: TimelinePhotoperiodInput,
  windowStartMs: number,
  windowEndMs: number,
): TimelinePhotoperiodInterval[] {
  const dayStart = timeToMinutes(photoperiod.dayStartTime)
  const nightStart = timeToMinutes(photoperiod.nightStartTime)
  if (windowEndMs <= windowStartMs) return []
  if (dayStart === nightStart) {
    return [{ start: windowStartMs, end: windowEndMs, phase: 'MOON' }]
  }

  const windowStart = new Date(windowStartMs)
  const dayOrigin = Date.UTC(
    windowStart.getUTCFullYear(),
    windowStart.getUTCMonth(),
    windowStart.getUTCDate(),
  )
  const boundaries: number[] = []
  for (let dayOffset = -1; dayOffset <= Math.ceil((windowEndMs - dayOrigin) / DAY_MS); dayOffset += 1) {
    for (const boundaryMinute of [dayStart, nightStart]) {
      const candidate = dayOrigin + dayOffset * DAY_MS + boundaryMinute * MINUTE_MS
      if (candidate > windowStartMs && candidate < windowEndMs) boundaries.push(candidate)
    }
  }
  boundaries.sort((left, right) => left - right)

  const phaseAt = (t: number): 'SUN' | 'MOON' => {
    const minuteOfDay = Math.floor((t - dayOrigin) / MINUTE_MS) % 1440
    return isMinuteInPeriod(minuteOfDay, dayStart, nightStart) ? 'SUN' : 'MOON'
  }

  const intervals: TimelinePhotoperiodInterval[] = []
  let cursor = windowStartMs
  let phase = phaseAt(cursor)
  for (const boundary of boundaries) {
    if (boundary <= cursor) continue
    intervals.push({ start: cursor, end: boundary, phase })
    cursor = boundary
    phase = phase === 'SUN' ? 'MOON' : 'SUN'
  }
  intervals.push({ start: cursor, end: windowEndMs, phase })
  return intervals
}
