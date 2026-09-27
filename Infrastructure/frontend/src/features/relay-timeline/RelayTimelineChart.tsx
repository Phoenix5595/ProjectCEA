import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import uPlot from 'uplot'

import 'uplot/dist/uPlot.min.css'
import type {
  RelayIntervalSummary,
  RelayStateInterval,
  RequestedOutputSegment,
  RelayTimelineWindow,
} from './intervals'
import { formatDuration, formatRequestedResolution, requestedOutputAt } from './intervals'

const LANE_HEIGHT = 58
const LEFT_LABEL_GUTTER = 154
const TOP_GUTTER = 8
const BOTTOM_GUTTER = 38
const ACCESSIBLE_INTERVAL_LIMIT = 100

export interface RelayTimelineChartLane {
  readonly key: string
  readonly label: string
  readonly deviceName: string
  readonly deviceType: string
  readonly channel: number
  readonly physicalRelay: number
  readonly intervals: readonly RelayStateInterval[]
  readonly requestedOutput: readonly RequestedOutputSegment[]
  readonly summary: RelayIntervalSummary
  readonly preview?: boolean
}

export interface RelayTimelineChartProps {
  readonly lanes: readonly RelayTimelineChartLane[]
  readonly range: RelayTimelineWindow
  readonly viewportKey: string
}

export interface RelayTimelineChartHandle {
  resetZoom: () => void
}

interface ActivePosition {
  readonly laneIndex: number
  readonly instant: number
}

function makeFocusStops(
  lanes: readonly RelayTimelineChartLane[],
  range: RelayTimelineWindow
): ActivePosition[] {
  const stops: ActivePosition[] = []
  lanes.forEach((lane, laneIndex) => {
    const instants = new Set<number>()
    for (const interval of lane.intervals) {
      if (interval.start >= range.start.getTime() && interval.start < range.end.getTime())
        instants.add(interval.start)
    }
    for (const segment of lane.requestedOutput) {
      if (segment.start >= range.start.getTime() && segment.start < range.end.getTime())
        instants.add(segment.start)
      if (segment.end > range.start.getTime() && segment.end < range.end.getTime())
        instants.add(segment.end)
    }
    for (const instant of instants) stops.push({ laneIndex, instant })
  })
  return stops.sort((a, b) => a.laneIndex - b.laneIndex || a.instant - b.instant)
}

function timelineSegmentAt<T extends { readonly start: number; readonly end: number }>(
  segments: readonly T[],
  instant: number
): T | null {
  let low = 0
  let high = segments.length - 1
  while (low <= high) {
    const middle = low + Math.floor((high - low) / 2)
    const segment = segments[middle]
    if (segment === undefined) return null
    if (instant < segment.start) high = middle - 1
    else if (instant >= segment.end) low = middle + 1
    else return segment
  }
  return null
}

function localTimestamp(instant: number): string {
  return new Date(instant).toLocaleString(undefined, { timeZone: 'America/Toronto' })
}

function drawLaneTimeline(plot: uPlot, lanes: readonly RelayTimelineChartLane[]): void {
  if (lanes.length === 0) return
  const { ctx, bbox } = plot
  const rowHeight = bbox.height / lanes.length
  const rangeMin = plot.scales.x.min ?? 0
  const rangeMax = plot.scales.x.max ?? rangeMin + 1
  const xPosition = (instant: number): number => plot.valToPos(instant / 1000, 'x', true)
  const laneLabelX = Math.max(6, bbox.left - LEFT_LABEL_GUTTER + 8)

  ctx.save()
  ctx.beginPath()
  ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height)
  ctx.clip()

  lanes.forEach((lane, laneIndex) => {
    const rowTop = bbox.top + laneIndex * rowHeight
    const rowBottom = rowTop + rowHeight
    const backgroundTop = rowTop
    const backgroundHeight = rowHeight

    for (const interval of lane.intervals) {
      const left = Math.max(bbox.left, xPosition(Math.max(interval.start, rangeMin * 1000)))
      const right = Math.min(
        bbox.left + bbox.width,
        xPosition(Math.min(interval.end, rangeMax * 1000))
      )
      if (right <= left) continue
      if (interval.state === 'on') {
        ctx.fillStyle = 'rgba(34, 197, 94, 0.34)'
        ctx.fillRect(left, backgroundTop, right - left, backgroundHeight)
      } else if (interval.state === 'off') {
        ctx.fillStyle = 'rgba(100, 116, 139, 0.20)'
        ctx.fillRect(left, backgroundTop, right - left, backgroundHeight)
      } else {
        ctx.fillStyle = 'rgba(148, 163, 184, 0.08)'
        ctx.fillRect(left, backgroundTop, right - left, backgroundHeight)
        ctx.save()
        ctx.beginPath()
        ctx.rect(left, backgroundTop, right - left, backgroundHeight)
        ctx.clip()
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.38)'
        ctx.lineWidth = 1
        for (let hatch = left - backgroundHeight; hatch < right; hatch += 9) {
          ctx.beginPath()
          ctx.moveTo(hatch, backgroundTop + backgroundHeight)
          ctx.lineTo(hatch + backgroundHeight, backgroundTop)
          ctx.stroke()
        }
        ctx.restore()
      }
    }

    const centerY = rowTop + rowHeight / 2
    const maxAmplitude = backgroundHeight / 2
    const outputs = lane.requestedOutput
    for (let index = 0; index < outputs.length; index += 1) {
      const output = outputs[index]
      if (output === undefined) continue
      const next = outputs[index + 1]
      const hasNextSample = next !== undefined && output.end === next.start
      const visibleStart = Math.max(output.start, rangeMin * 1000)
      const visibleEnd = Math.min(output.end, rangeMax * 1000)
      const left = xPosition(visibleStart)
      const right = xPosition(visibleEnd)
      if (right <= left) continue

      const startPercent =
        requestedOutputAt(outputs, visibleStart)?.percent ?? output.requestedPercent
      const endPercent =
        requestedOutputAt(outputs, visibleEnd)?.percent ??
        (hasNextSample ? next.requestedPercent : output.requestedPercent)
      if (startPercent <= 0 && endPercent <= 0) continue
      const startAmplitude = maxAmplitude * (startPercent / 100)
      const endAmplitude = maxAmplitude * (endPercent / 100)
      const controlOffset = (right - left) / 3
      ctx.fillStyle = output.aggregated ? 'rgba(56, 189, 248, 0.62)' : 'rgba(14, 165, 233, 0.72)'
      ctx.beginPath()
      ctx.moveTo(left, centerY)
      ctx.lineTo(left, centerY - startAmplitude)
      ctx.bezierCurveTo(
        left + controlOffset,
        centerY - startAmplitude,
        right - controlOffset,
        centerY - endAmplitude,
        right,
        centerY - endAmplitude
      )
      ctx.lineTo(right, centerY + endAmplitude)
      ctx.bezierCurveTo(
        right - controlOffset,
        centerY + endAmplitude,
        left + controlOffset,
        centerY + startAmplitude,
        left,
        centerY + startAmplitude
      )
      ctx.closePath()
      ctx.fill()
    }

    ctx.strokeStyle = 'rgba(148, 163, 184, 0.28)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(bbox.left, rowBottom)
    ctx.lineTo(bbox.left + bbox.width, rowBottom)
    ctx.stroke()
  })

  ctx.restore()
  lanes.forEach((lane, laneIndex) => {
    const centerY = bbox.top + laneIndex * rowHeight + rowHeight / 2
    ctx.fillStyle = '#e2e8f0'
    ctx.font = '12px system-ui, sans-serif'
    ctx.textBaseline = 'middle'
    const label = lane.preview
      ? `EXAMPLE · ${lane.label}`
      : `R${lane.physicalRelay} · ${lane.label}`
    ctx.fillText(label, laneLabelX, centerY, LEFT_LABEL_GUTTER - 12)
  })
}

const relayIntervalDate = new Intl.DateTimeFormat(undefined, {
  timeZone: 'America/Toronto',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})
const relayIntervalTime = new Intl.DateTimeFormat(undefined, {
  timeZone: 'America/Toronto',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

function intervalTimeRange(interval: RelayStateInterval): string {
  const start = new Date(interval.start)
  const end = new Date(interval.end)
  const startDate = relayIntervalDate.format(start)
  const endDate = relayIntervalDate.format(end)
  const startLabel = `${startDate} ${relayIntervalTime.format(start)}`
  const endLabel =
    startDate === endDate
      ? relayIntervalTime.format(end)
      : `${endDate} ${relayIntervalTime.format(end)}`
  return `${startLabel}–${endLabel} · ${formatDuration((interval.end - interval.start) / 1000)}`
}
function intervalNote(interval: RelayStateInterval): string | null {
  const partial = interval.partialStart || interval.partialEnd
  if (partial && interval.reason) return `Partial interval · ${interval.reason}`
  if (partial) return 'Partial interval'
  return interval.reason
}

export const RelayTimelineChart = forwardRef<RelayTimelineChartHandle, RelayTimelineChartProps>(
  function RelayTimelineChart({ lanes, range, viewportKey }, forwardedRef) {
    const frameRef = useRef<HTMLDivElement>(null)
    const plotElementRef = useRef<HTMLDivElement>(null)
    const plotRef = useRef<uPlot | null>(null)
    const lanesRef = useRef(lanes)
    const rangeRef = useRef(range)
    const viewportKeyRef = useRef(viewportKey)
    const programmaticScaleRef = useRef(false)
    const userZoomedRef = useRef(false)
    const [active, setActive] = useState<ActivePosition | null>(null)
    lanesRef.current = lanes
    rangeRef.current = range

    const focusStops = useMemo(() => makeFocusStops(lanes, range), [lanes, range])
    const plotHeight = Math.max(180, lanes.length * LANE_HEIGHT + TOP_GUTTER + BOTTOM_GUTTER)
    const data = useMemo<uPlot.AlignedData>(
      () => [
        [range.start.getTime() / 1000, range.end.getTime() / 1000],
        [0, 0],
      ],
      [range.start, range.end]
    )
    const dataRef = useRef(data)
    dataRef.current = data

    const resetZoom = (): void => {
      const plot = plotRef.current
      if (plot === null) return
      userZoomedRef.current = false
      programmaticScaleRef.current = true
      plot.setScale('x', {
        min: rangeRef.current.start.getTime() / 1000,
        max: rangeRef.current.end.getTime() / 1000,
      })
    }
    useImperativeHandle(forwardedRef, () => ({ resetZoom }), [])

    useEffect(() => {
      const frame = frameRef.current
      const element = plotElementRef.current
      if (frame === null || element === null || lanesRef.current.length === 0) return
      let scheduledFrame: number | null = null
      const resize = (): void => {
        const width = frame.clientWidth
        const height = frame.clientHeight
        if (width <= 0 || height <= 0) return
        if (plotRef.current !== null) {
          plotRef.current.setSize({ width, height })
          return
        }
        const currentRange = rangeRef.current
        const plugin: uPlot.Plugin = {
          hooks: {
            drawClear: plot => drawLaneTimeline(plot, lanesRef.current),
            setSelect: () => {
              userZoomedRef.current = true
            },
            setScale: () => {
              if (programmaticScaleRef.current || plotRef.current === null) {
                programmaticScaleRef.current = false
                return
              }
              userZoomedRef.current = true
            },
          },
        }
        plotRef.current = new uPlot(
          {
            width,
            height,
            padding: [TOP_GUTTER, 12, BOTTOM_GUTTER, LEFT_LABEL_GUTTER],
            scales: {
              x: {
                time: true,
                auto: false,
                range: () => [
                  rangeRef.current.start.getTime() / 1000,
                  rangeRef.current.end.getTime() / 1000,
                ],
                min: currentRange.start.getTime() / 1000,
                max: currentRange.end.getTime() / 1000,
              },
              y: { auto: false, min: 0, max: 1 },
            },
            axes: [
              {
                scale: 'x',
                stroke: '#94a3b8',
                grid: { show: false },
                ticks: { stroke: 'rgba(148,163,184,0.35)', width: 1 },
                values: (_plot, values) =>
                  values.map(value =>
                    new Date(value * 1000).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                      timeZone: 'America/Toronto',
                    })
                  ),
              },
              { scale: 'y', show: false },
            ],
            series: [
              {},
              { show: true, stroke: 'rgba(0, 0, 0, 0)', width: 0, points: { show: false } },
            ],
            legend: { show: false },
            cursor: {
              points: { show: false },
              drag: { x: true, y: false, setScale: true },
            },
            plugins: [plugin],
          },
          dataRef.current,
          element
        )
        const initialPlot = plotRef.current
        if (initialPlot !== null) {
          userZoomedRef.current = false
          programmaticScaleRef.current = true
          initialPlot.setScale('x', {
            min: currentRange.start.getTime() / 1000,
            max: currentRange.end.getTime() / 1000,
          })
        }
      }
      const scheduleResize = (): void => {
        if (scheduledFrame !== null) return
        scheduledFrame = requestAnimationFrame(() => {
          scheduledFrame = null
          resize()
        })
      }
      const observer = new ResizeObserver(scheduleResize)
      observer.observe(frame)
      resize()
      return () => {
        if (scheduledFrame !== null) cancelAnimationFrame(scheduledFrame)
        observer.disconnect()
        plotRef.current?.destroy()
        plotRef.current = null
      }
    }, [lanes.length])

    useEffect(() => {
      const plot = plotRef.current
      if (plot === null) return
      const viewportChanged = viewportKeyRef.current !== viewportKey
      viewportKeyRef.current = viewportKey
      if (viewportChanged) userZoomedRef.current = false
      plot.setData(data, false)
      if (!userZoomedRef.current) {
        programmaticScaleRef.current = true
        plot.setScale('x', { min: range.start.getTime() / 1000, max: range.end.getTime() / 1000 })
      }
      plot.redraw()
    }, [data, range, viewportKey])
    useEffect(() => {
      plotRef.current?.redraw()
    }, [lanes])

    const selectedLane = active === null ? null : (lanes[active.laneIndex] ?? null)
    const selectedInterval =
      selectedLane === null || active === null
        ? null
        : timelineSegmentAt(selectedLane.intervals, active.instant)
    const selectedOutput =
      selectedLane === null || active === null
        ? null
        : requestedOutputAt(selectedLane.requestedOutput, active.instant)

    const moveFocus = (direction: -1 | 1): void => {
      if (focusStops.length === 0) return
      if (active === null) {
        setActive(
          direction > 0 ? (focusStops[0] ?? null) : (focusStops[focusStops.length - 1] ?? null)
        )
        return
      }
      let next: ActivePosition | undefined
      if (direction > 0) {
        next = focusStops.find(
          stop =>
            stop.laneIndex > active.laneIndex ||
            (stop.laneIndex === active.laneIndex && stop.instant > active.instant)
        )
      } else {
        for (let index = focusStops.length - 1; index >= 0; index -= 1) {
          const stop = focusStops[index]
          if (
            stop &&
            (stop.laneIndex < active.laneIndex ||
              (stop.laneIndex === active.laneIndex && stop.instant < active.instant))
          ) {
            next = stop
            break
          }
        }
      }
      if (next) setActive(next)
    }

    const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>): void => {
      const plot = plotRef.current
      const frame = frameRef.current
      if (plot === null || frame === null || lanes.length === 0) return
      const bounds = frame.getBoundingClientRect()
      const pixelRatio = plot.ctx.canvas.width / Math.max(1, plot.root.clientWidth)
      const plotLeft = plot.bbox.left / pixelRatio
      const plotTop = plot.bbox.top / pixelRatio
      const plotWidth = plot.bbox.width / pixelRatio
      const plotHeight = plot.bbox.height / pixelRatio
      const plotX = event.clientX - bounds.left - plotLeft
      const plotY = event.clientY - bounds.top - plotTop
      if (plotX < 0 || plotX > plotWidth || plotY < 0 || plotY > plotHeight) return
      const laneIndex = Math.min(lanes.length - 1, Math.floor((plotY / plotHeight) * lanes.length))
      const min = plot.scales.x.min ?? 0
      const max = plot.scales.x.max ?? min + 1
      const instant = (min + (plotX / plotWidth) * (max - min)) * 1000
      setActive({ laneIndex, instant })
    }

    return (
      <section
        className="min-w-0 rounded-lg border border-border-subtle bg-surface-primary p-2"
        aria-label="Relay state timeline"
      >
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div
            className="flex items-center gap-2 text-xs text-text-subtle"
            aria-label="Requested PID output waveform key"
          >
            <span className="font-semibold text-text-default">Requested PID output</span>
            <span>0%</span>
            <span
              className="inline-block h-3 w-3 border-x-2 border-accent-data"
              aria-hidden="true"
            />
            <span>50%</span>
            <span
              className="inline-block h-5 w-3 border-x-2 border-accent-data"
              aria-hidden="true"
            />
            <span>100%</span>
            <span className="ml-1">0% center · 100% reaches both row edges</span>
          </div>
          <span className="text-xs text-text-muted">
            {lanes.some(lane => lane.preview)
              ? 'Illustrative synthetic preview; no relay observations are being shown.'
              : 'Smooth symmetric curve interpolates requested PID samples; gaps remain broken. Output is not measured load.'}
          </span>
        </div>

        <div
          ref={frameRef}
          role="application"
          tabIndex={0}
          aria-label={
            lanes.some(lane => lane.preview)
              ? 'Illustrative synthetic relay chart. Use left and right arrow keys to inspect intervals and requested PID output.'
              : 'Relay interval chart. Use left and right arrow keys to inspect intervals and requested PID output.'
          }
          aria-describedby="relay-timeline-selected-detail"
          className="relative w-full focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-accent-vivid"
          style={{ height: plotHeight }}
          onPointerMove={handlePointerMove}
          onPointerLeave={() => setActive(null)}
          onFocus={() => {
            if (active === null && focusStops.length > 0) setActive(focusStops[0] ?? null)
          }}
          onKeyDown={event => {
            if (event.key === 'ArrowRight') {
              event.preventDefault()
              moveFocus(1)
            } else if (event.key === 'ArrowLeft') {
              event.preventDefault()
              moveFocus(-1)
            } else if (event.key === 'Home' && focusStops.length > 0) {
              event.preventDefault()
              setActive(focusStops[0] ?? null)
            } else if (event.key === 'End' && focusStops.length > 0) {
              event.preventDefault()
              setActive(focusStops[focusStops.length - 1] ?? null)
            }
          }}
        >
          <div ref={plotElementRef} aria-hidden="true" className="absolute inset-0" />
        </div>

        <div
          id="relay-timeline-selected-detail"
          role="status"
          aria-live="polite"
          className="mt-2 rounded border border-border-subtle bg-surface-secondary px-2 py-2 text-xs text-text-default"
        >
          {selectedLane && active ? (
            <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-3">
              <div className="min-w-0">
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Relay state
                </dt>
                <dd className="mt-0.5 flex flex-wrap items-center gap-1.5">
                  <span className="font-semibold">{selectedLane.label}</span>
                  {selectedLane.preview && (
                    <span className="rounded border border-border-subtle px-1.5 py-0.5 text-[10px] font-semibold">
                      Example
                    </span>
                  )}
                  <span className="rounded border border-border-subtle px-1.5 py-0.5 text-[10px] font-semibold">
                    {selectedInterval?.state === 'on'
                      ? 'ON'
                      : selectedInterval?.state === 'off'
                        ? 'OFF'
                        : 'UNKNOWN'}
                  </span>
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Interval
                </dt>
                <dd className="mt-0.5 break-words">
                  {selectedInterval === null ? (
                    'No relay interval at this time'
                  ) : (
                    <>
                      <span>{intervalTimeRange(selectedInterval)}</span>
                      {(selectedInterval.partialStart ||
                        selectedInterval.partialEnd ||
                        selectedInterval.reason) && (
                        <span className="mt-0.5 block text-text-muted">
                          {intervalNote(selectedInterval)}
                        </span>
                      )}
                    </>
                  )}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  PID requested
                </dt>
                <dd className="mt-0.5">
                  {selectedOutput === null ? (
                    <>
                      <span className="block font-semibold">Unavailable</span>
                      <span className="block text-text-muted">
                        {selectedLane.preview
                          ? 'No synthetic sample at this time'
                          : 'No recorded sample at this time'}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="block font-semibold">
                        {selectedOutput.percent.toFixed(1)}%
                      </span>
                      <span className="block text-text-muted">
                        {selectedOutput.interpolated ? 'Smoothed' : 'Sample'} ·{' '}
                        {selectedLane.preview
                          ? 'synthetic only'
                          : formatRequestedResolution(selectedOutput.segment)}
                      </span>
                    </>
                  )}
                </dd>
              </div>
            </dl>
          ) : (
            'Focus the chart and use arrow keys to inspect sample intervals and requested PID output.'
          )}
        </div>

        <details className="mt-2 rounded border border-border-subtle bg-surface-secondary px-2 py-1">
          <summary className="cursor-pointer text-xs font-semibold text-text-default">
            Accessible interval summaries
          </summary>
          <div className="overflow-x-auto pt-2">
            <table className="min-w-full text-left text-xs">
              <caption className="sr-only">
                Relay interval counts, complete durations, cycles per hour, and coverage
              </caption>
              <thead>
                <tr className="text-text-muted">
                  <th className="px-2 py-1">Relay</th>
                  <th className="px-2 py-1">ON / OFF changes</th>
                  <th className="px-2 py-1">ON / OFF duration</th>
                  <th className="px-2 py-1">Mean complete ON / OFF</th>
                  <th className="px-2 py-1">Cycles/hour</th>
                  <th className="px-2 py-1">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {lanes.map(lane => {
                  const summary = lane.summary
                  return (
                    <tr key={lane.key} className="border-t border-border-subtle text-text-default">
                      <th scope="row" className="px-2 py-1 font-medium">
                        {lane.preview
                          ? `${lane.label} (synthetic example)`
                          : `${lane.label} (R${lane.physicalRelay})`}
                      </th>
                      <td className="px-2 py-1">
                        {summary.onTransitions} ON / {summary.offTransitions} OFF
                      </td>
                      <td className="px-2 py-1">
                        {summary.coverageComplete
                          ? `${formatDuration(summary.onSeconds)} / ${formatDuration(summary.offSeconds)}`
                          : 'Suppressed while coverage is incomplete'}
                      </td>
                      <td className="px-2 py-1">
                        {formatDuration(summary.meanOnDurationSeconds)} /{' '}
                        {formatDuration(summary.meanOffDurationSeconds)}
                      </td>
                      <td className="px-2 py-1">
                        {summary.cyclesPerHour === null
                          ? 'Unavailable'
                          : summary.cyclesPerHour.toFixed(2)}
                      </td>
                      <td className="px-2 py-1">
                        {summary.coverageComplete
                          ? 'Complete'
                          : `Incomplete (${summary.knownPercent.toFixed(1)}% known)`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {lanes.map(lane => (
            <section key={`${lane.key}-interval-text`} className="mt-2 text-xs text-text-subtle">
              <h3 className="font-semibold text-text-default">
                {lane.label}: known interval details
              </h3>
              <ol className="list-inside list-decimal">
                {lane.intervals.slice(0, ACCESSIBLE_INTERVAL_LIMIT).map((interval, index) => (
                  <li key={`${interval.start}-${index}`}>
                    {interval.state.toUpperCase()} · {localTimestamp(interval.start)}–
                    {localTimestamp(interval.end)} ·{' '}
                    {formatDuration((interval.end - interval.start) / 1000)}
                    {interval.partialStart || interval.partialEnd ? ' · partial boundary' : ''}
                    {interval.reason ? ` · ${interval.reason}` : ''}
                  </li>
                ))}
              </ol>
              {lane.intervals.length > ACCESSIBLE_INTERVAL_LIMIT && (
                <p>
                  Showing the first {ACCESSIBLE_INTERVAL_LIMIT}; focus the chart and use arrow keys
                  to inspect every state and requested-output sample.
                </p>
              )}
            </section>
          ))}
        </details>
      </section>
    )
  }
)
