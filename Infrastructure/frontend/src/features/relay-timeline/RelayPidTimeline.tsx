import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { ControlSnapshotResponse } from '../../services/api/devices'
import type { DeviceRegistryEntry } from '../../types/device'
import { MonitoringApi, monitoringRequestContextFromSearchParams } from '../monitoring/api'
import { TimeRangeToolbar } from '../monitoring/components/TimeRangeToolbar'
import type { MonitoringRange } from '../monitoring/state'

import type { RelayTimelineResponse } from './contracts'
import {
  buildRelayLaneIntervals,
  buildRequestedOutputSegments,
  MAX_RELAY_TIMELINE_TRANSITIONS,
} from './intervals'
import type {
  RelayIntervalSummary,
  RelayStateInterval,
  RelayTimelineWindow,
  RequestedOutputSegment,
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

function makePreviewLane(range: RelayTimelineWindow): RelayTimelineChartLane {
  const start = range.start.getTime()
  const duration = range.end.getTime() - start
  const position = (fraction: number): number => start + Math.round(duration * fraction)
  const reason = 'Synthetic preview; not a sampled observation'
  const intervals: RelayStateInterval[] = [
    {
      start: position(0),
      end: position(0.08),
      state: 'unknown',
      partialStart: true,
      partialEnd: false,
      reason,
    },
    {
      start: position(0.08),
      end: position(0.24),
      state: 'off',
      partialStart: true,
      partialEnd: false,
      reason,
    },
    {
      start: position(0.24),
      end: position(0.44),
      state: 'on',
      partialStart: false,
      partialEnd: false,
      reason,
    },
    {
      start: position(0.44),
      end: position(0.59),
      state: 'off',
      partialStart: false,
      partialEnd: false,
      reason,
    },
    {
      start: position(0.59),
      end: position(0.82),
      state: 'on',
      partialStart: false,
      partialEnd: true,
      reason,
    },
    {
      start: position(0.82),
      end: position(1),
      state: 'unknown',
      partialStart: false,
      partialEnd: true,
      reason,
    },
  ]
  const requestedOutput: RequestedOutputSegment[] = [
    {
      start: position(0),
      end: position(0.3),
      requestedPercent: 25,
      aggregated: false,
      intervalSeconds: (duration * 0.3) / 1000,
    },
    {
      start: position(0.3),
      end: position(0.55),
      requestedPercent: 50,
      aggregated: false,
      intervalSeconds: (duration * 0.25) / 1000,
    },
    {
      start: position(0.55),
      end: position(0.82),
      requestedPercent: 75,
      aggregated: false,
      intervalSeconds: (duration * 0.27) / 1000,
    },
    {
      start: position(0.82),
      end: position(1),
      requestedPercent: 35,
      aggregated: false,
      intervalSeconds: (duration * 0.18) / 1000,
    },
  ]
  const knownHours = (duration * 0.74) / 3_600_000
  const completeOnSeconds = (duration * 0.2) / 1000
  const completeOffSeconds = (duration * 0.15) / 1000
  const summary: RelayIntervalSummary = {
    onTransitions: 2,
    offTransitions: 1,
    cyclesPerHour: knownHours > 0 ? 2 / knownHours : null,
    knownObservationHours: knownHours,
    knownPercent: 74,
    completeOnDurationsSeconds: [completeOnSeconds],
    completeOffDurationsSeconds: [completeOffSeconds],
    meanOnDurationSeconds: completeOnSeconds,
    meanOffDurationSeconds: completeOffSeconds,
    onSeconds: (duration * 0.43) / 1000,
    offSeconds: (duration * 0.31) / 1000,
    coverageComplete: false,
  }

  return {
    key: 'preview:heating',
    label: 'Heating relay',
    deviceName: 'preview-heating-relay',
    deviceType: 'heating',
    channel: 0,
    physicalRelay: 0,
    preview: true,
    intervals,
    requestedOutput,
    summary,
  }
}

export function RelayPidTimeline({
  location,
  cluster,
  registry,
  snapshot,
  snapshotLoading,
}: RelayPidTimelineProps) {
  const [searchParams] = useSearchParams()
  const scenario = searchParams.get('scenario') ?? undefined
  const fixtureSession = searchParams.get('fixtureSession') ?? undefined
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(new URLSearchParams(searchParams.toString())),
    [searchParams]
  )
  const monitoringApi = useMemo(() => new MonitoringApi(requestContext), [requestContext])
  const chartRef = useRef<RelayTimelineChartHandle>(null)
  const requestId = useRef(0)
  const [range, setRange] = useState<MonitoringRange>({
    kind: 'live',
    duration: LIVE_RANGE_DEFAULT_MS,
  })
  const [paused, setPaused] = useState(false)
  const [previewEnabled, setPreviewEnabled] = useState(true)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [queryNowMs, setQueryNowMs] = useState(() => Date.now())
  const [history, setHistory] = useState<LoadedTimeline | null>(null)
  const [loadingViewKey, setLoadingViewKey] = useState<string | null>(null)
  const [requestError, setRequestError] = useState<TimelineRequestError | null>(null)

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
  const historyLoading = loadingViewKey === viewKey
  const sourceCoverageComplete =
    currentHistory !== null &&
    currentHistory.coverageComplete &&
    currentHistory.pageComplete &&
    !currentHistory.residentTruncated

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
  const previewLane = useMemo(() => makePreviewLane(displayRange), [displayRange])
  const showPreview =
    previewEnabled && currentLanes.length === 0 && !snapshotLoading && snapshot !== null

  const outputAvailable = chartLanes.some(lane => lane.requestedOutput.length > 0)
  const onLive = (duration: number): void => {
    const now = Date.now()
    setPaused(false)
    setRange({ kind: 'live', duration })
    setNowMs(now)
    setQueryNowMs(now)
  }
  const onFixedRange = (start: Date, end: Date): void => {
    setPaused(false)
    setRange({ kind: 'fixed', start, end })
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
          Sample-time GPIO facts show physical ON/OFF duration. The centered waveform is requested
          PID output, not measured watts or hardware power.
        </p>
      </div>
      <TimeRangeToolbar
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
        <>
          <div
            className="mt-3 rounded border border-border-subtle bg-surface-secondary px-3 py-4 text-sm text-text-subtle"
            role="status"
          >
            {snapshotLoading && snapshot === null
              ? 'Loading current relay assignments…'
              : 'No assigned non-light relays are available in this room/main cluster.'}
          </div>
          {cluster === 'main' && !snapshotLoading && snapshot !== null && (
            <button
              type="button"
              aria-pressed={previewEnabled}
              className="mt-2 rounded border border-border-subtle px-2 py-1 text-xs text-text-default hover:bg-surface-hover"
              onClick={() => setPreviewEnabled(enabled => !enabled)}
            >
              {previewEnabled ? 'Disable beta synthetic example' : 'Enable beta synthetic example'}
            </button>
          )}
          {showPreview && (
            <div
              className="mt-3 rounded border border-accent-subtle bg-surface-secondary p-2"
              role="group"
              aria-label="Illustrative relay timeline preview"
            >
              <p className="mb-2 text-xs text-text-subtle">
                <strong>Example preview — synthetic data.</strong> This illustrative heating lane is
                not assigned hardware, a relay observation, or recorded PID output.
              </p>
              <div className="min-w-0 overflow-auto">
                <RelayTimelineChart
                  ref={chartRef}
                  lanes={[previewLane]}
                  range={displayRange}
                  viewportKey={`${viewKey}:preview`}
                />
              </div>
            </div>
          )}
        </>
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
          <div className="mt-2 min-w-0 overflow-auto">
            <RelayTimelineChart
              ref={chartRef}
              lanes={chartLanes}
              range={displayRange}
              viewportKey={viewKey}
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
