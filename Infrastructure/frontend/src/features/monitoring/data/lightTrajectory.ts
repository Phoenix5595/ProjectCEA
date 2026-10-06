import type { LightTrajectorySegment, NormControlSeries, NormLinear } from './alignSeries.types'

/**
 * Compose one physical light's recorded coverage and selected projected path.
 *
 * `recordedEnd` is the known end of recorded coverage and `projectedEnd` the
 * forecast validity boundary. `now` confines projected coverage strictly to the
 * future side of the actual Now instant: a forecast never fills a missing
 * historical interval, never draws outside its validity window, and a ramp
 * crossing Now splits at Now with the interpolated projected value.
 */
export function composeLightTrajectory(
  history: NormControlSeries | undefined,
  projection: NormControlSeries | undefined,
  recordedEnd: number,
  projectedEnd: number,
  now: number
): readonly LightTrajectorySegment[] {
  const recorded = sourceTrajectory(history, Math.min(recordedEnd, now), Number.NEGATIVE_INFINITY)
  const projected = sourceTrajectory(projection, projectedEnd, now)
  if (recorded.length === 0 && projected.length === 0) return []

  const boundaries = new Set<number>()
  for (const segment of [...recorded, ...projected]) {
    boundaries.add(segment.start)
    boundaries.add(segment.end)
  }
  const ordered = [...boundaries].sort((left, right) => left - right)
  const output: LightTrajectorySegment[] = []
  for (let index = 0; index + 1 < ordered.length; index += 1) {
    const start = ordered[index]
    const end = ordered[index + 1]
    if (start === undefined || end === undefined || end <= start) continue
    // Recorded facts win wherever both authorities cover the interval. The
    // projected timeline starts no earlier than `now`, so it can never
    // backfill a historical gap ahead of it; an explicit recorded null keeps
    // its own coverage unavailable instead of becoming forecast-filled.
    const source = lightSegmentAt(recorded, start) ?? lightSegmentAt(projected, start)
    if (source !== undefined) output.push(sliceSegment(source, start, end))
  }
  return output
}

/** Find the segment active at `timestamp` in a sorted, non-overlapping timeline. */
export function lightSegmentAt(
  segments: readonly LightTrajectorySegment[],
  timestamp: number
): LightTrajectorySegment | undefined {
  let low = 0
  let high = segments.length
  while (low < high) {
    const middle = (low + high) >>> 1
    const segment = segments[middle]
    if (segment !== undefined && segment.start <= timestamp) low = middle + 1
    else high = middle
  }
  const candidate = segments[low - 1]
  return candidate !== undefined && timestamp < candidate.end ? candidate : undefined
}

/** Evaluate one light's effective intensity without extrapolating across gaps. */
export function lightValueAt(
  segments: readonly LightTrajectorySegment[],
  timestamp: number
): number | null {
  const segment = lightSegmentAt(segments, timestamp)
  if (segment === undefined || segment.startValue === null || segment.endValue === null) return null
  if (segment.shape === 'step') return segment.startValue
  const duration = segment.end - segment.start
  if (duration <= 0) return null
  const fraction = (timestamp - segment.start) / duration
  return segment.startValue + (segment.endValue - segment.startValue) * fraction
}

interface LightChange {
  readonly value: number | null
  readonly origin: LightTrajectorySegment['origin']
  readonly quality: LightTrajectorySegment['quality']
}

function sourceTrajectory(
  series: NormControlSeries | undefined,
  coverageEnd: number,
  coverageStart: number
): LightTrajectorySegment[] {
  if (series === undefined || !Number.isFinite(coverageEnd)) return []

  const points = new Map<number, LightChange>()
  const steps = new Map<number, LightChange>()
  const rampStarts = new Map<number, NormLinear>()
  const rampEnds = new Map<number, NormLinear>()
  const zeroLengthRamps = new Map<number, LightChange>()
  const boundaries = new Set<number>([coverageEnd])
  let firstTime = coverageEnd

  for (const point of series.points) {
    if (point.t >= coverageEnd) continue
    points.set(point.t, point)
    boundaries.add(point.t)
    firstTime = Math.min(firstTime, point.t)
  }
  for (const step of series.steps) {
    if (step.t >= coverageEnd) continue
    steps.set(step.t, step)
    boundaries.add(step.t)
    firstTime = Math.min(firstTime, step.t)
  }
  for (const original of series.linear) {
    if (original.start >= coverageEnd || original.end < original.start) continue
    const ramp = { ...original, end: Math.min(original.end, coverageEnd) }
    firstTime = Math.min(firstTime, ramp.start)
    boundaries.add(ramp.start)
    boundaries.add(ramp.end)
    if (ramp.end === ramp.start) {
      zeroLengthRamps.set(ramp.start, {
        value: ramp.endValue,
        origin: ramp.origin,
        quality: ramp.quality,
      })
    } else {
      rampStarts.set(ramp.start, ramp)
      rampEnds.set(ramp.end, ramp)
    }
  }
  if (firstTime >= coverageEnd) return []

  const ordered = [...boundaries].sort((left, right) => left - right)
  const output: LightTrajectorySegment[] = []
  let held: LightChange | undefined
  let activeRamp: NormLinear | undefined

  for (let index = 0; index + 1 < ordered.length; index += 1) {
    const start = ordered[index]
    const end = ordered[index + 1]
    if (start === undefined || end === undefined || end <= start) continue

    const step = steps.get(start)
    const point = points.get(start)
    const pointUnavailable = point !== undefined && point.value === null
    const zeroLengthRamp = zeroLengthRamps.get(start)
    const endingRamp = rampEnds.get(start)
    const startingRamp = rampStarts.get(start)

    if (step !== undefined) {
      held = step
      activeRamp = undefined
    } else if (pointUnavailable) {
      held = point
      activeRamp = undefined
    } else if (zeroLengthRamp !== undefined) {
      held = zeroLengthRamp
      activeRamp = undefined
    } else if (point !== undefined) {
      held = point
      activeRamp = undefined
    } else if (endingRamp !== undefined) {
      held = {
        value: endingRamp.endValue,
        origin: endingRamp.origin,
        quality: endingRamp.quality,
      }
      activeRamp = undefined
    }

    if (
      startingRamp !== undefined &&
      step === undefined &&
      !pointUnavailable &&
      zeroLengthRamp === undefined
    ) {
      activeRamp = startingRamp
    }

    if (activeRamp !== undefined && activeRamp.end >= end) {
      output.push({
        start,
        end,
        shape: 'linear',
        startValue: rampValueAt(activeRamp, start),
        endValue: rampValueAt(activeRamp, end),
        origin: activeRamp.origin,
        quality: activeRamp.quality,
      })
    } else if (held !== undefined) {
      output.push({
        start,
        end,
        shape: 'step',
        startValue: held.value,
        endValue: held.value,
        origin: held.origin,
        quality: held.quality,
      })
    }
  }
  // `held` carries state built across the whole source timeline, so the
  // coverage-start clip runs on the finished output: intervals before it are
  // dropped and a crossing ramp is re-sliced with its interpolated value.
  return coverageStart === Number.NEGATIVE_INFINITY
    ? output
    : sliceFromCoverageStart(output, coverageStart)
}

/** Confine built segments to `[coverageStart, …)`, splitting the first crossing segment. */
function sliceFromCoverageStart(
  segments: LightTrajectorySegment[],
  coverageStart: number
): LightTrajectorySegment[] {
  const index = segments.findIndex(segment => segment.end > coverageStart)
  const head = segments[index]
  if (index < 0 || head === undefined) return []
  const start = Math.max(head.start, coverageStart)
  const slicedHead = start === head.start ? head : sliceSegment(head, start, head.end)
  return [slicedHead, ...segments.slice(index + 1)]
}

function rampValueAt(ramp: NormLinear, timestamp: number): number {
  const duration = ramp.end - ramp.start
  if (duration <= 0 || timestamp <= ramp.start) return ramp.startValue
  if (timestamp >= ramp.end) return ramp.endValue
  return ramp.startValue + ((ramp.endValue - ramp.startValue) * (timestamp - ramp.start)) / duration
}

function sliceSegment(
  segment: LightTrajectorySegment,
  start: number,
  end: number
): LightTrajectorySegment {
  if (segment.shape === 'step') return { ...segment, start, end }
  return {
    ...segment,
    start,
    end,
    startValue: segmentValueAt(segment, start),
    endValue: segmentValueAt(segment, end),
  }
}

function segmentValueAt(segment: LightTrajectorySegment, timestamp: number): number | null {
  if (segment.startValue === null || segment.endValue === null) return null
  const duration = segment.end - segment.start
  if (duration <= 0 || timestamp <= segment.start) return segment.startValue
  if (timestamp >= segment.end) return segment.endValue
  return segment.startValue + ((segment.endValue - segment.startValue) * (timestamp - segment.start)) / duration
}
