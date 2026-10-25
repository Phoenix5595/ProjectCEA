import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { ControlSnapshotResponse } from '../../services/api/devices'
import type { DeviceRegistryEntry } from '../../types/device'
import { HistoryOverviewView } from '../history-overview'
import { MonitoringApi, monitoringRequestContextFromSearchParams } from '../monitoring/api'
import type { MonitoringRequestContext } from '../monitoring/api'
import { alignPhotoperiod } from '../monitoring/data'
import type { PhotoperiodInterval } from '../monitoring/data'
import {
  MAX_RANGE_MS,
  TimeRangeToolbar,
  parseUrlRange,
  withSerializedRange,
} from '../monitoring/components'
import type { FixedRange, MonitoringRange } from '../monitoring/state'

import { logger } from '../../utils/logger'

import type { RelayTimelineResponse } from './contracts'
import {
  buildRelayLaneIntervals,
  buildRequestedOutputSegments,
  formatDuration,
  formatRequestedResolution,
  requestedOutputHoldSeconds,
  MAX_RELAY_TIMELINE_TRANSITIONS,
} from './intervals'
import { RelayTimelineChart } from './RelayTimelineChart'
import type { RelayTimelineChartHandle, RelayTimelineChartLane } from './RelayTimelineChart'

const LIVE_RANGE_DEFAULT_MS = 60 * 60 * 1000
const PAGE_LIMIT = 2_000
const LIVE_REFRESH_MS = 30_000
const SNAPSHOT_MAX_AGE_MS = 5_000
const PID_DEVICE_TYPES: Record<string, true> = { heating: true, cooling: true, co2: true }

interface RelayPidTimelineProps {
  readonly location: string
  readonly cluster: string
  readonly registry: readonly DeviceRegistryEntry[]
  readonly snapshot: ControlSnapshotResponse | null
  readonly snapshotLoading: boolean
}

interface RelayTimelineDetailProps {
  readonly location: string
  readonly cluster: string
  readonly registry: readonly DeviceRegistryEntry[]
  readonly snapshot: ControlSnapshotResponse | null
  readonly snapshotLoading: boolean
  readonly range: MonitoringRange
  readonly onRangeChange: (next: MonitoringRange) => void
  readonly requestContext: MonitoringRequestContext | undefined
}

interface CurrentRelayLane {
  readonly key: string
  readonly deviceId: number
  readonly deviceName: string
  readonly deviceType: string
  readonly displayName: string
  readonly location: string
  readonly cluster: string
  readonly channel: number
  readonly physicalRelay: number
}

interface LoadedTimeline {
  readonly viewKey: string
  readonly range: RelayTimelineResponse['range']
  readonly transitions: RelayTimelineResponse['transitions']
  readonly anchors: RelayTimelineResponse['anchors']
  readonly load: RelayTimelineResponse['load']
  readonly coverageComplete: boolean
  readonly lastHeartbeatAt: Date | null
  readonly watermark: number
  readonly pageComplete: boolean
  readonly residentTruncated: boolean
}

interface TimelineRequestError {
  readonly viewKey: string
  readonly message: string
}

function timestampText(timestamp: Date): string {
  return timestamp.toLocaleString(undefined, { timeZone: 'America/Toronto' })
}

function timelineErrorText(error: unknown): string {
  if (
    error &&
    typeof error === 'object' &&
    'message' in error &&
    typeof error.message === 'string'
  ) {
    return error.message
  }
  return 'Unable to load relay timeline history.'
}

function matchesAssignment(
  relay: ControlSnapshotResponse['relays'][number] | undefined,
  lane: CurrentRelayLane
): boolean {
  const assignment = relay?.assignment
  return (
    relay !== undefined &&
    assignment !== null &&
    assignment !== undefined &&
    relay.channel === lane.channel &&
    assignment.device_id === lane.deviceId &&
    assignment.device_name === lane.deviceName &&
    assignment.device_type === lane.deviceType &&
    assignment.location === lane.location &&
    assignment.cluster === lane.cluster
  )
}

function latestRequestedPoint(
  lane: CurrentRelayLane,
  points: RelayTimelineResponse['load'],
  edgeMs: number
): RelayTimelineResponse['load'][number] | null {
  let latest: RelayTimelineResponse['load'][number] | null = null
  for (const point of points) {
    const timestamp = point.time.getTime()
    if (
      point.device_id !== lane.deviceId ||
      point.device_name !== lane.deviceName ||
      timestamp > edgeMs
    ) {
      continue
    }
    if (latest === null || timestamp > latest.time.getTime()) latest = point
  }
  return latest
}

/**
 * A fixed range beyond the detailed seven-day limit renders the bounded
 * all-history overview; live windows and short fixed ranges stay detailed.
 */
function isOverviewRange(range: MonitoringRange): range is FixedRange {
  return (
    range.kind === 'fixed' && range.end.getTime() - range.start.getTime() > MAX_RANGE_MS
  )
}

/** Parse the section URL before mounting any reader; 'none' falls back to live. */
function resolveRelayRange(searchParams: URLSearchParams): MonitoringRange {
  const parsed = parseUrlRange(searchParams, null)
  return parsed.kind === 'live'
    ? { kind: 'live', duration: parsed.duration }
    : parsed.kind === 'fixed'
      ? { kind: 'fixed', start: parsed.start, end: parsed.end }
      : { kind: 'live', duration: LIVE_RANGE_DEFAULT_MS }
}

export function RelayPidTimeline({
  location,
  cluster,
  registry,
  snapshot,
  snapshotLoading,
}: RelayPidTimelineProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(new URLSearchParams(searchParams.toString())),
    [searchParams]
  )
  // Parse the URL before mounting any reader: a long fixed range goes straight
  // to the overview branch and never triggers raw relay timeline pagination.
  const [range, setRange] = useState<MonitoringRange>(() => resolveRelayRange(searchParams))
  const rangeRef = useRef(range)
  rangeRef.current = range

  const applyRange = useCallback(
    (next: MonitoringRange): void => {
      // Switching into or out of the overview branch unmounts the branch-local
      // toolbar before its own URL effect can run, so the URL is kept in step
      // here. A write that only reproduces the current URL adds no history
      // entry.
      if (isOverviewRange(next) || isOverviewRange(rangeRef.current)) {
        const merged = withSerializedRange(searchParams, next)
        if (merged.toString() !== searchParams.toString()) setSearchParams(merged)
      }
      setRange(next)
    },
    [setSearchParams, searchParams]
  )

  if (isOverviewRange(range)) {
    return (
      <section
        className="min-w-0 rounded-lg border border-border-subtle bg-surface-primary p-2"
        aria-label="Relay and PID automation timeline"
      >
        <div className="mb-2">
          <h2 className="text-base font-bold text-text-default">
            Relay observations &amp; requested PID output
          </h2>
          <p className="mt-1 text-xs text-text-subtle">
            Recorded GPIO lane time aggregated into fixed buckets shows physical ON/OFF/unknown
            duration. Requested PID output and lights are summarized, not measured physical load.
          </p>
        </div>
        <TimeRangeToolbar
          maxRangeMs={null}
          suppressInitialUrlParse={true}
          range={range}
          isLive={false}
          onLive={duration => applyRange({ kind: 'live', duration })}
          onFixedRange={(start, end) => applyRange({ kind: 'fixed', start, end })}
          onPause={() => undefined}
          onResume={() => undefined}
          defaultDuration={LIVE_RANGE_DEFAULT_MS}
        />
        <HistoryOverviewView
          room={location}
          range={range}
          requestContext={requestContext}
          controlOnly={true}
          onDetail={(start, end) => applyRange({ kind: 'fixed', start, end })}
        />
      </section>
    )
  }
  return (
    <RelayTimelineDetail
      location={location}
      cluster={cluster}
      registry={registry}
      snapshot={snapshot}
      snapshotLoading={snapshotLoading}
      range={range}
      onRangeChange={applyRange}
      requestContext={requestContext}
    />
  )
}

function RelayTimelineDetail({
  location,
  cluster,
  registry,
  snapshot,
  snapshotLoading,
  range,
  onRangeChange,
  requestContext,
}: RelayTimelineDetailProps) {
  const [searchParams] = useSearchParams()
  const scenario = searchParams.get('scenario') ?? undefined
  const fixtureSession = searchParams.get('fixtureSession') ?? undefined
  const monitoringApi = useMemo(() => new MonitoringApi(requestContext), [requestContext])
  const chartRef = useRef<RelayTimelineChartHandle>(null)
  const requestId = useRef(0)
  const [paused, setPaused] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [queryNowMs, setQueryNowMs] = useState(() => Date.now())
  const [history, setHistory] = useState<LoadedTimeline | null>(null)
  const [loadingViewKey, setLoadingViewKey] = useState<string | null>(null)
  const [requestError, setRequestError] = useState<TimelineRequestError | null>(null)
  const [photoperiod, setPhotoperiod] = useState<PhotoperiodInterval[]>([])

  const isLive = range.kind === 'live'
  const liveDuration = isLive ? range.duration : 0
  const fixedStartMs = range.kind === 'fixed' ? range.start.getTime() : 0
  const fixedEndMs = range.kind === 'fixed' ? range.end.getTime() : 0
  const contextKey = `${scenario ?? ''}|${fixtureSession ?? ''}`
  const viewKey = `${location}|${cluster}|${contextKey}|${isLive ? `live:${liveDuration}` : `fixed:${fixedStartMs}:${fixedEndMs}`}`

  const displayRange = useMemo(
    () =>
      isLive
        ? { start: new Date(nowMs - liveDuration), end: new Date(nowMs) }
        : { start: new Date(fixedStartMs), end: new Date(fixedEndMs) },
    [fixedEndMs, fixedStartMs, isLive, liveDuration, nowMs]
  )

  useEffect(() => {
    if (!isLive || paused) return
    const timer = setInterval(() => setNowMs(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [isLive, paused, liveDuration])

  useEffect(() => {
    if (!isLive || paused) return
    const timer = setInterval(() => {
      const now = Date.now()
      setQueryNowMs(now)
      setNowMs(now)
    }, LIVE_REFRESH_MS)
    return () => clearInterval(timer)
  }, [isLive, paused, liveDuration])

  useEffect(() => {
    const startMs = isLive ? queryNowMs - liveDuration : fixedStartMs
    const endMs = isLive ? queryNowMs : fixedEndMs
    if (!(startMs < endMs)) return
    const requestedStart = new Date(startMs)
    const requestedEnd = new Date(endMs)
    const controller = new AbortController()
    const id = ++requestId.current
    const isCurrent = (): boolean => requestId.current === id && !controller.signal.aborted
    const transitions: RelayTimelineResponse['transitions'][number][] = []
    let firstResponse: RelayTimelineResponse | null = null
    const seenObservationIds = new Set<number>()
    const seenCursors = new Set<string>()

    setLoadingViewKey(viewKey)
    setRequestError(null)

    const publish = (pageComplete: boolean, residentTruncated: boolean): void => {
      if (!isCurrent() || firstResponse === null) return
      setHistory({
        viewKey,
        range: firstResponse.range,
        transitions: [...transitions],
        anchors: firstResponse.anchors,
        load: firstResponse.load,
        coverageComplete: firstResponse.coverage_complete,
        lastHeartbeatAt: firstResponse.last_heartbeat_at,
        watermark: firstResponse.watermark,
        pageComplete,
        residentTruncated,
      })
    }

    void (async () => {
      let cursor: string | null = null
      let residentTruncated = false
      try {
        while (firstResponse === null || cursor !== null) {
          const page = await monitoringApi.relayTimeline(
            location,
            requestedStart.toISOString(),
            requestedEnd.toISOString(),
            PAGE_LIMIT,
            cursor,
            { signal: controller.signal }
          )
          if (!isCurrent()) return
          if (firstResponse === null) {
            firstResponse = page
          } else if (
            page.watermark !== firstResponse.watermark ||
            page.range.start.getTime() !== firstResponse.range.start.getTime() ||
            page.range.end.getTime() !== firstResponse.range.end.getTime() ||
            page.coverage_complete !== firstResponse.coverage_complete
          ) {
            throw new Error(
              'Relay timeline pages did not preserve their requested range and watermark.'
            )
          }

          for (const transition of page.transitions) {
            if (seenObservationIds.has(transition.observation_id)) continue
            if (transitions.length >= MAX_RELAY_TIMELINE_TRANSITIONS) {
              residentTruncated = true
              break
            }
            seenObservationIds.add(transition.observation_id)
            transitions.push(transition)
          }

          if (transitions.length >= MAX_RELAY_TIMELINE_TRANSITIONS && page.has_more)
            residentTruncated = true
          if (residentTruncated) {
            publish(false, true)
            break
          }

          if (!page.has_more) {
            publish(true, false)
            break
          }

          const nextCursor = page.next_cursor
          if (nextCursor === null || seenCursors.has(nextCursor)) {
            throw new Error('Relay timeline returned an invalid or repeating page cursor.')
          }
          seenCursors.add(nextCursor)
          cursor = nextCursor
          publish(false, false)
        }
      } catch (error: unknown) {
        if (!isCurrent()) return
        setRequestError({ viewKey, message: timelineErrorText(error) })
      } finally {
        if (isCurrent()) setLoadingViewKey(null)
      }
    })()

    return () => {
      controller.abort()
      if (requestId.current === id) requestId.current += 1
    }
  }, [fixedEndMs, fixedStartMs, isLive, liveDuration, location, monitoringApi, queryNowMs, viewKey])

  const currentLanes = useMemo<CurrentRelayLane[]>(() => {
    if (cluster !== 'main' || snapshot === null) return []
    const lanes: CurrentRelayLane[] = []
    for (const device of registry) {
      const channel = device.channel
      if (
        device.location !== location ||
        device.cluster !== 'main' ||
        device.device_type === 'light' ||
        channel === null ||
        channel === undefined
      )
        continue
      const snapshotRelay = snapshot.relays.find(relay => relay.channel === channel)
      const assignment = snapshotRelay?.assignment
      if (
        snapshotRelay === undefined ||
        assignment === null ||
        assignment === undefined ||
        assignment.device_id !== device.device_id ||
        assignment.device_name !== device.device_name ||
        assignment.device_type !== device.device_type ||
        assignment.location !== device.location ||
        assignment.cluster !== device.cluster
      )
        continue
      lanes.push({
        key: `${device.device_id}:${channel}`,
        deviceId: device.device_id,
        deviceName: device.device_name,
        deviceType: device.device_type,
        displayName: device.display_name ?? device.device_name,
        location: device.location,
        cluster: device.cluster,
        channel,
        physicalRelay: snapshotRelay.physical_relay,
      })
    }
    return lanes.sort((a, b) => a.physicalRelay - b.physicalRelay || a.deviceId - b.deviceId)
  }, [cluster, location, registry, snapshot])

  const currentHistory = history?.viewKey === viewKey ? history : null
  const currentError = requestError?.viewKey === viewKey ? requestError.message : null
  const loadedRangeStartMs = currentHistory?.range.start.getTime()
  const loadedRangeEndMs = currentHistory?.range.end.getTime()

  // R5: the sun/moon overlay comes only from recorded photoperiod for the
  // same loaded window; relay pages never derive it from climate periods.
  useEffect(() => {
    if (loadedRangeStartMs === undefined || loadedRangeEndMs === undefined) return
    const controller = new AbortController()
    void monitoringApi
      .controlRange(
        location,
        new Date(loadedRangeStartMs).toISOString(),
        new Date(loadedRangeEndMs).toISOString(),
        4_000,
        'photoperiod',
        { signal: controller.signal }
      )
      .then(response => {
        if (controller.signal.aborted) return
        setPhotoperiod(
          alignPhotoperiod(response.photoperiod, loadedRangeStartMs, loadedRangeEndMs)
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        logger.error('Relay photoperiod fetch failed', error)
      })
    return () => controller.abort()
  }, [loadedRangeStartMs, loadedRangeEndMs, location, monitoringApi])

  const historyLoading = loadingViewKey === viewKey
  const sourceCoverageComplete =
    currentHistory !== null &&
    currentHistory.coverageComplete &&
    currentHistory.pageComplete &&
    !currentHistory.residentTruncated

  const outputReferenceMs = isLive && !paused ? nowMs : displayRange.end.getTime()
  const latestOutputRows = useMemo(() => {
    if (currentHistory === null) return []
    return currentLanes
      .filter(lane => PID_DEVICE_TYPES[lane.deviceType] === true)
      .map(lane => {
        const point = latestRequestedPoint(lane, currentHistory.load, outputReferenceMs)
        return {
          lane,
          point,
          ageSeconds:
            point === null
              ? null
              : Math.max(0, (outputReferenceMs - point.time.getTime()) / 1000),
        }
      })
  }, [currentHistory, currentLanes, outputReferenceMs])

  const chartLanes = useMemo<RelayTimelineChartLane[]>(() => {
    if (currentHistory === null) return []
    return currentLanes.map(lane => {
      const heartbeatAt = currentHistory.lastHeartbeatAt?.getTime() ?? null
      const sampleAt =
        snapshot?.sampled_at === null || snapshot?.sampled_at === undefined
          ? null
          : Date.parse(snapshot.sampled_at)
      const nowAtRangeEnd = displayRange.end.getTime()
      const heartbeatRecent =
        heartbeatAt !== null &&
        heartbeatAt <= nowAtRangeEnd &&
        nowAtRangeEnd - heartbeatAt <= 60_000
      const snapshotRecent =
        snapshot?.freshness === 'FRESH' &&
        sampleAt !== null &&
        Number.isFinite(sampleAt) &&
        sampleAt <= nowAtRangeEnd &&
        nowAtRangeEnd - sampleAt <= SNAPSHOT_MAX_AGE_MS
      const assignedSnapshotRelay = snapshot?.relays.find(relay => relay.channel === lane.channel)
      const assignmentStillMatches = matchesAssignment(assignedSnapshotRelay, lane)
      const observedState = assignedSnapshotRelay?.observed_state ?? null
      const liveEdge =
        isLive &&
        !paused &&
        heartbeatRecent &&
        snapshotRecent &&
        assignmentStillMatches &&
        observedState !== null
          ? { timestamp: new Date(sampleAt ?? nowAtRangeEnd), state: observedState }
          : undefined
      const laneIntervals = buildRelayLaneIntervals({
        lane,
        range: displayRange,
        dataEnd: currentHistory.range.end,
        liveEdge,
        anchors: currentHistory.anchors,
        transitions: currentHistory.transitions,
        sourceCoverageComplete,
      })
      const loadEnd = Math.min(displayRange.end.getTime(), currentHistory.range.end.getTime())
      const requestedOutput = buildRequestedOutputSegments({
        deviceType: lane.deviceType,
        deviceName: lane.deviceName,
        deviceId: lane.deviceId,
        range: {
          start: displayRange.start,
          end: new Date(Math.max(displayRange.start.getTime(), loadEnd)),
        },
        points: currentHistory.load,
      })
      return {
        key: lane.key,
        label: lane.displayName,
        deviceName: lane.deviceName,
        deviceType: lane.deviceType,
        channel: lane.channel,
        physicalRelay: lane.physicalRelay,
        intervals: laneIntervals.intervals,
        requestedOutput,
        summary: laneIntervals.summary,
      }
    })
  }, [currentHistory, currentLanes, displayRange, isLive, paused, snapshot, sourceCoverageComplete])

  const outputAvailable = chartLanes.some(lane => lane.requestedOutput.length > 0)
  const onLive = (duration: number): void => {
    const now = Date.now()
    setPaused(false)
    setNowMs(now)
    setQueryNowMs(now)
    onRangeChange({ kind: 'live', duration })
  }
  const onFixedRange = (start: Date, end: Date): void => {
    setPaused(false)
    onRangeChange({ kind: 'fixed', start, end })
  }
  const onPause = (): void => {
    const now = Date.now()
    setPaused(true)
    setNowMs(now)
    setQueryNowMs(now)
  }

  const onResume = (): void => {
    const now = Date.now()
    setPaused(false)
    setNowMs(now)
    setQueryNowMs(now)
  }

  return (
    <section
      className="min-w-0 rounded-lg border border-border-subtle bg-surface-primary p-2"
      aria-label="Relay and PID automation timeline"
    >
      <div className="mb-2">
        <h2 className="text-base font-bold text-text-default">
          Relay observations &amp; requested PID output
        </h2>
        <p className="mt-1 text-xs text-text-subtle">
          Sample-time GPIO facts show physical ON/OFF duration. Requested PID output steps hold
          each sample until the next; it is not measured physical load.
        </p>
      </div>
      <TimeRangeToolbar
        maxRangeMs={null}
        suppressInitialUrlParse={true}
        range={range}
        isLive={isLive}
        onLive={onLive}
        onFixedRange={onFixedRange}
        onPause={onPause}
        onResume={onResume}
        onResetZoom={() => chartRef.current?.resetZoom()}
        now={() => new Date(nowMs)}
        defaultDuration={LIVE_RANGE_DEFAULT_MS}
      />

      {historyLoading && (
        <p role="status" className="mt-2 text-xs text-text-subtle">
          Loading relay observation history…
        </p>
      )}
      {currentError && (
        <div
          role="alert"
          className="mt-2 rounded border border-status-error-border bg-status-error-bg/30 px-2 py-1 text-sm text-status-error-text"
        >
          History request incomplete: {currentError}. Returned observations remain visible; unknown
          coverage is not drawn as OFF.
        </div>
      )}
      {currentHistory?.residentTruncated && (
        <div
          role="alert"
          className="mt-2 rounded border border-status-warning-border bg-status-warning-bg/30 px-2 py-1 text-sm text-status-warning-text"
        >
          The 50,000-transition resident limit was reached. This window is incomplete and exact
          whole-window totals are suppressed.
        </div>
      )}
      {currentHistory && !currentHistory.coverageComplete && (
        <div
          role="status"
          className="mt-2 rounded border border-status-warning-border bg-status-warning-bg/30 px-2 py-1 text-sm text-status-warning-text"
        >
          The recorder reports incomplete coverage. Gaps are shown as unknown; exact whole-window
          totals are suppressed while known subrange transition counts remain available.
        </div>
      )}
      {currentHistory &&
        !currentHistory.pageComplete &&
        !currentHistory.residentTruncated &&
        !currentError &&
        !historyLoading && (
          <div
            role="status"
            className="mt-2 rounded border border-status-warning-border bg-status-warning-bg/30 px-2 py-1 text-sm text-status-warning-text"
          >
            Not all transition pages are available. Known subranges are retained and whole-window
            totals are suppressed.
          </div>
        )}
      {currentHistory?.lastHeartbeatAt === null && currentHistory && (
        <div
          role="status"
          className="mt-2 rounded border border-status-warning-border bg-status-warning-bg/30 px-2 py-1 text-sm text-status-warning-text"
        >
          No persisted recorder heartbeat supports continuous observation in this range.
        </div>
      )}

      {currentLanes.length === 0 ? (
        <div
          className="mt-3 rounded border border-border-subtle bg-surface-secondary px-3 py-4 text-sm text-text-subtle"
          role="status"
        >
          {snapshotLoading && snapshot === null
            ? 'Loading current relay assignments…'
            : 'No assigned non-light relays are available in this room/main cluster.'}
        </div>
      ) : currentHistory ? (
        <>
          {chartLanes.some(lane => lane.intervals[0]?.state === 'unknown') && (
            <p className="mt-2 text-xs text-text-muted">
              History begins here for one or more relays; unsupported earlier intervals remain
              unknown.
            </p>
          )}
          {!outputAvailable &&
            currentLanes.some(lane => PID_DEVICE_TYPES[lane.deviceType] === true) && (
              <p className="mt-2 text-xs text-text-muted">
                No requested PID output samples are recorded for these PID-capable lanes in the
                selected range.
              </p>
            )}
          {latestOutputRows.length > 0 && (
            <section
              className="mb-2 rounded border border-border-subtle bg-surface-secondary p-2"
              role="group"
              aria-label="Latest requested PID output"
            >
              <h3 className="text-xs font-semibold text-text-default">
                {isLive && !paused
                  ? 'Latest requested PID output'
                  : 'Requested PID output at selected range end'}
              </h3>
              <p className="mt-1 text-xs text-text-muted">
                Controller demand, not measured electrical or thermal load.
              </p>
              <ul className="mt-2 flex flex-wrap gap-2">
                {latestOutputRows.map(({ lane, point, ageSeconds }) => (
                  <li
                    key={lane.key}
                    className="flex min-w-52 flex-col rounded border border-border-subtle px-2 py-1"
                  >
                    <span className="text-xs text-text-subtle">{lane.displayName}</span>
                    {point !== null && point.requested_percent !== null ? (
                      <>
                        <strong className="text-lg text-text-default">
                          {point.requested_percent.toFixed(0)}%
                        </strong>
                        <span className="text-10 text-text-muted">
                          {ageSeconds !== null &&
                          ageSeconds <= requestedOutputHoldSeconds(point)
                            ? 'Fresh'
                            : 'Stale'}
                          {' · sampled '}
                          {formatDuration(ageSeconds ?? 0)} ago ·{' '}
                          {formatRequestedResolution({
                            aggregated: point.aggregated,
                            intervalSeconds: point.interval_seconds,
                          })}
                        </span>
                      </>
                    ) : (
                      <div className="flex flex-col">
                        <strong className="text-lg text-text-muted">Unavailable</strong>
                        <span className="text-10 text-text-muted">
                          Latest requested output sample is null or absent.
                        </span>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="mt-2 min-w-0 overflow-auto">
            <RelayTimelineChart
              ref={chartRef}
              lanes={chartLanes}
              range={displayRange}
              viewportKey={viewKey}
              photoperiod={photoperiod}
            />
          </div>
        </>
      ) : historyLoading ? null : currentError ? null : (
        <p className="mt-3 text-sm text-text-subtle">
          No recorded relay observations are available for this range.
        </p>
      )}

      {currentHistory && (
        <p className="mt-2 text-right text-10 text-text-muted">
          Observation watermark {currentHistory.watermark} · heartbeat{' '}
          {currentHistory.lastHeartbeatAt
            ? timestampText(currentHistory.lastHeartbeatAt)
            : 'unavailable'}
        </p>
      )}
    </section>
  )
}
