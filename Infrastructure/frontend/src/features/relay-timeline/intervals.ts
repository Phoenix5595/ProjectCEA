import type { RelayTimelineLoadPoint, RelayTimelineTransition } from './contracts'

export const HEARTBEAT_MAX_GAP_MS = 60_000
export const MAX_RELAY_TIMELINE_TRANSITIONS = 50_000

export type RelayIntervalState = 'on' | 'off' | 'unknown'

export interface RelayStateInterval {
  readonly start: number
  readonly end: number
  readonly state: RelayIntervalState
  readonly partialStart: boolean
  readonly partialEnd: boolean
  readonly reason: string | null
}

export interface RelayLaneIdentity {
  readonly deviceId: number
  readonly deviceName: string
  readonly deviceType: string
  readonly channel: number
  readonly location: string
  readonly cluster: string
}

export interface RelayIntervalSummary {
  readonly onTransitions: number
  readonly offTransitions: number
  readonly cyclesPerHour: number | null
  readonly knownObservationHours: number
  readonly knownPercent: number
  readonly completeOnDurationsSeconds: readonly number[]
  readonly completeOffDurationsSeconds: readonly number[]
  readonly meanOnDurationSeconds: number | null
  readonly meanOffDurationSeconds: number | null
  readonly onSeconds: number
  readonly offSeconds: number
  readonly coverageComplete: boolean
}

export interface RelayLaneIntervals {
  readonly intervals: readonly RelayStateInterval[]
  readonly summary: RelayIntervalSummary
}

export interface RelayTimelineWindow {
  readonly start: Date
  readonly end: Date
}

export interface RequestedOutputSegment {
  readonly start: number
  readonly end: number
  readonly requestedPercent: number
  readonly aggregated: boolean
  readonly intervalSeconds: number
}

const PID_DEVICE_TYPES: Record<string, true> = { heating: true, cooling: true, co2: true }
const VALID_BASELINE_REASONS: Record<string, true> = {
  initial: true,
  state_changed: true,
  recovered: true,
  assignment_changed: true,
}

function compareTransition(a: RelayTimelineTransition, b: RelayTimelineTransition): number {
  const timeDelta = a.observed_at.getTime() - b.observed_at.getTime()
  return timeDelta || a.observation_id - b.observation_id
}

function latestTransition(
  transitions: readonly RelayTimelineTransition[],
  predicate: (transition: RelayTimelineTransition) => boolean,
): RelayTimelineTransition | null {
  let latest: RelayTimelineTransition | null = null
  for (const transition of transitions) {
    if (predicate(transition) && (latest === null || compareTransition(latest, transition) < 0)) {
      latest = transition
    }
  }
  return latest
}

function belongsToLane(transition: RelayTimelineTransition, lane: RelayLaneIdentity): boolean {
  return transition.channel === lane.channel
    && transition.device_id === lane.deviceId
    && transition.device_name === lane.deviceName
    && transition.device_type === lane.deviceType
    && transition.location === lane.location
    && transition.cluster === lane.cluster
}

function mean(values: readonly number[]): number | null {
  if (values.length === 0) return null
  return values.reduce((total, value) => total + value, 0) / values.length
}

/**
 * Reconstruct only sample-time evidence supported by both a matching event-time
 * owner and recent board heartbeats. Any stale, recorder, session, or assignment
 * boundary ends the current run instead of carrying an ON/OFF state across it.
 */
export function buildRelayLaneIntervals(input: {
  readonly lane: RelayLaneIdentity
  readonly range: RelayTimelineWindow
  readonly anchors: readonly RelayTimelineTransition[]
  readonly transitions: readonly RelayTimelineTransition[]
  readonly sourceCoverageComplete: boolean
  readonly dataEnd?: Date
  readonly liveEdge?: { readonly timestamp: Date; readonly state: boolean }
}): RelayLaneIntervals {
  const start = input.range.start.getTime()
  const end = input.range.end.getTime()
  const dataEnd = Math.max(start, Math.min(end, input.dataEnd?.getTime() ?? end))
  if (!(start < end)) {
    return {
      intervals: [],
      summary: {
        onTransitions: 0,
        offTransitions: 0,
        cyclesPerHour: null,
        knownObservationHours: 0,
        knownPercent: 0,
        completeOnDurationsSeconds: [],
        completeOffDurationsSeconds: [],
        meanOnDurationSeconds: null,
        meanOffDurationSeconds: null,
        onSeconds: 0,
        offSeconds: 0,
        coverageComplete: false,
      },
    }
  }

  const anchorCandidates = [...input.anchors, ...input.transitions.filter((transition) => transition.observed_at.getTime() < start)]
  const channelAnchor = latestTransition(
    anchorCandidates,
    (transition) => transition.channel === input.lane.channel,
  )
  const heartbeatAnchor = latestTransition(
    anchorCandidates,
    (transition) => transition.reason === 'heartbeat' && transition.observed_at.getTime() <= start,
  )
  const anchorMatches = channelAnchor !== null
    && belongsToLane(channelAnchor, input.lane)
    && channelAnchor.observed_state !== null
    && VALID_BASELINE_REASONS[channelAnchor.reason] === true
  const heartbeatSupportsAnchor = anchorMatches
    && heartbeatAnchor !== null
    && heartbeatAnchor.session_id === channelAnchor.session_id
    && heartbeatAnchor.observed_at.getTime() <= start
    && start - heartbeatAnchor.observed_at.getTime() <= HEARTBEAT_MAX_GAP_MS

  let cursor = start
  let state: boolean | null = heartbeatSupportsAnchor ? channelAnchor?.observed_state ?? null : null
  let sessionId: string | null = heartbeatAnchor?.session_id ?? channelAnchor?.session_id ?? null
  let lastSupportAt = heartbeatSupportsAnchor ? heartbeatAnchor?.observed_at.getTime() ?? null : null
  let partialStart = state !== null
  let activeReason: string | null = state === null ? 'no supported baseline' : null
  let onTransitions = 0
  let offTransitions = 0
  let offToOnCycles = 0
  const intervals: RelayStateInterval[] = []

  const append = (until: number, partialEnd: boolean, reason: string | null = activeReason): void => {
    const clippedEnd = Math.min(until, end)
    if (clippedEnd <= cursor) return
    const interval: RelayStateInterval = {
      start: cursor,
      end: clippedEnd,
      state: state === null ? 'unknown' : state ? 'on' : 'off',
      partialStart,
      partialEnd,
      reason: state === null ? reason : null,
    }
    const previous = intervals[intervals.length - 1]
    if (previous && previous.state === 'unknown' && interval.state === 'unknown' && previous.end === interval.start) {
      intervals[intervals.length - 1] = {
        ...previous,
        end: interval.end,
        partialEnd: interval.partialEnd,
        reason: previous.reason === interval.reason ? previous.reason : 'coverage gap',
      }
    } else {
      intervals.push(interval)
    }
    cursor = clippedEnd
  }

  const expireBefore = (instant: number): void => {
    if (state === null || lastSupportAt === null) return
    const expiry = lastSupportAt + HEARTBEAT_MAX_GAP_MS
    if (instant <= expiry) return
    append(expiry, true, 'heartbeat gap')
    state = null
    partialStart = false
    activeReason = 'heartbeat gap'
    lastSupportAt = null
  }

  const enterUnknown = (instant: number, reason: string): void => {
    expireBefore(instant)
    if (state !== null) append(instant, true, reason)
    else append(instant, false, reason)
    state = null
    partialStart = false
    activeReason = reason
    lastSupportAt = null
  }

  const enterBaseline = (instant: number, nextState: boolean, reason: string): void => {
    if (state !== null) append(instant, true, reason)
    else append(instant, false, activeReason)
    cursor = Math.max(cursor, instant)
    state = nextState
    partialStart = true
    activeReason = null
    lastSupportAt = instant
  }

  const observations = input.transitions
    .filter((transition) => {
      const instant = transition.observed_at.getTime()
      return instant >= start && instant < dataEnd
        && (transition.reason === 'heartbeat' || transition.channel === input.lane.channel)
    })
    .slice()
    .sort(compareTransition)

  for (const transition of observations) {
    const instant = transition.observed_at.getTime()
    expireBefore(instant)

    if (sessionId !== null && transition.session_id !== sessionId) {
      enterUnknown(instant, 'process session changed')
      sessionId = transition.session_id
    } else if (sessionId === null) {
      sessionId = transition.session_id
    }

    if (transition.reason === 'heartbeat') {
      if (state !== null) lastSupportAt = instant
      continue
    }

    if (transition.reason === 'stale' || transition.reason === 'recording_gap') {
      enterUnknown(instant, transition.reason === 'stale' ? 'stale observation' : 'recording gap')
      continue
    }

    const isCurrentOwner = belongsToLane(transition, input.lane)
    if (!isCurrentOwner) {
      enterUnknown(instant, 'relay assignment changed')
      continue
    }

    if (transition.reason === 'assignment_changed') {
      if (transition.observed_state === null) {
        enterUnknown(instant, 'relay assignment changed')
      } else {
        enterBaseline(instant, transition.observed_state, 'relay assignment changed')
      }
      continue
    }

    if (transition.reason === 'initial' || transition.reason === 'recovered') {
      if (transition.observed_state === null) {
        enterUnknown(instant, transition.reason)
      } else {
        enterBaseline(instant, transition.observed_state, transition.reason)
      }
      continue
    }

    if (transition.observed_state === null) {
      enterUnknown(instant, 'unknown observation')
      continue
    }

    if (state === null) {
      if (transition.observed_state) onTransitions += 1
      else offTransitions += 1
      enterBaseline(instant, transition.observed_state, 'fresh observation baseline')
    } else if (state === transition.observed_state) {
      lastSupportAt = instant
    } else {
      append(instant, false)
      if (transition.observed_state) {
        onTransitions += 1
        if (state === false) offToOnCycles += 1
      } else {
        offTransitions += 1
      }
      state = transition.observed_state
      partialStart = false
      activeReason = null
      lastSupportAt = instant
    }
  }

  expireBefore(dataEnd)
  if (input.liveEdge && state !== null && state === input.liveEdge.state) {
    append(end, true)
  } else {
    append(dataEnd, true)
    state = null
    partialStart = false
    activeReason = input.liveEdge ? 'waiting for fresh live snapshot' : 'outside queried history'
    const liveEdgeAt = input.liveEdge?.timestamp.getTime()
    if (liveEdgeAt !== undefined && liveEdgeAt >= dataEnd && liveEdgeAt < end) {
      append(liveEdgeAt, false, activeReason)
      state = input.liveEdge?.state ?? null
      partialStart = true
      activeReason = null
      append(end, true)
    } else {
      append(end, true, activeReason)
    }
  }

  let knownMs = 0
  let onMs = 0
  let offMs = 0
  const completeOnDurationsSeconds: number[] = []
  const completeOffDurationsSeconds: number[] = []
  for (const interval of intervals) {
    const duration = interval.end - interval.start
    if (interval.state === 'unknown') continue
    knownMs += duration
    if (interval.state === 'on') {
      onMs += duration
      if (!interval.partialStart && !interval.partialEnd) completeOnDurationsSeconds.push(duration / 1000)
    } else {
      offMs += duration
      if (!interval.partialStart && !interval.partialEnd) completeOffDurationsSeconds.push(duration / 1000)
    }
  }

  const knownObservationHours = knownMs / 3_600_000
  const coverageComplete = input.sourceCoverageComplete
    && knownMs === end - start
    && intervals.every((interval) => interval.state !== 'unknown')
  return {
    intervals,
    summary: {
      onTransitions,
      offTransitions,
      cyclesPerHour: knownObservationHours > 0 ? offToOnCycles / knownObservationHours : null,
      knownObservationHours,
      knownPercent: (knownMs / (end - start)) * 100,
      completeOnDurationsSeconds,
      completeOffDurationsSeconds,
      meanOnDurationSeconds: mean(completeOnDurationsSeconds),
      meanOffDurationSeconds: mean(completeOffDurationsSeconds),
      onSeconds: onMs / 1000,
      offSeconds: offMs / 1000,
      coverageComplete,
    },
  }
}

/**
 * Make piecewise-constant requested PID output segments. Output is a requested
 * controller percentage, never a measurement of electrical or thermal power.
 */
export function buildRequestedOutputSegments(input: {
  readonly deviceType: string
  readonly deviceId: number
  readonly deviceName: string
  readonly range: RelayTimelineWindow
  readonly points: readonly RelayTimelineLoadPoint[]
}): readonly RequestedOutputSegment[] {
  if (PID_DEVICE_TYPES[input.deviceType] !== true) return []

  const start = input.range.start.getTime()
  const end = input.range.end.getTime()
  const points = input.points
    .filter((point) => point.device_id === input.deviceId
      && point.device_name === input.deviceName
      && point.timestamp.getTime() < end)
    .slice()
    .sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
  const segments: RequestedOutputSegment[] = []

  for (let index = 0; index < points.length; index += 1) {
    const point = points[index]
    if (!point || point.requested_percent === null) continue
    const pointAt = point.timestamp.getTime()
    const next = points[index + 1]
    const nextAt = next?.timestamp.getTime()
    const maxHoldEnd = pointAt + point.interval_seconds * 2_000
    const segmentEnd = nextAt !== undefined && nextAt <= maxHoldEnd
      ? nextAt
      : maxHoldEnd
    const clippedStart = Math.max(start, pointAt)
    const clippedEnd = Math.min(end, segmentEnd)
    if (clippedEnd <= clippedStart) continue
    segments.push({
      start: clippedStart,
      end: clippedEnd,
      requestedPercent: point.requested_percent,
      aggregated: point.aggregated,
      intervalSeconds: point.interval_seconds,
    })
  }

  return segments
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return 'Unavailable'
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  if (seconds < 3_600) return `${(seconds / 60).toFixed(1)} min`
  return `${(seconds / 3_600).toFixed(2)} h`
}

export function formatRequestedResolution(segment: Pick<RequestedOutputSegment, 'aggregated' | 'intervalSeconds'>): string {
  const resolution = formatDuration(segment.intervalSeconds)
  return segment.aggregated ? `aggregated, ${resolution} resolution` : `raw, ${resolution} interval`
}
