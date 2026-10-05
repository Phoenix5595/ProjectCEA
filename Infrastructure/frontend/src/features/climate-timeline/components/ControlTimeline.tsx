import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { Popover, PopoverContent, PopoverTrigger } from '../../../components/ui/popover'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import { timeToMinutes, minutesToTime } from '../../../utils/timeMath'
import { dragHandlesPlugin } from '../charts/dragHandlesPlugin'
import {
  applyBoundary,
  applyValue,
  clampedBoundaryMinutes,
  createRafCoalescer,
  isValueInRange,
  rangeMessage,
  snapValue,
  type BoundaryEdge,
  type RafCoalescer,
  type ValueMetric,
} from '../charts/dragInteraction'
import {
  displayWindowFor,
  photoperiodIntervals,
} from '../charts/envelopeSeries'
import {
  periodLabelSegments,
  periodLabelsPlugin,
  type PeriodLabelSegment,
} from '../charts/periodLabelsPlugin'
import { buildTimelineSources } from '../charts/timelineSources'
import type { ActiveClimateProjectionState } from '../state/useActiveClimateProjection'
import { TimelineUPlot } from '../charts/TimelineUPlot'
import type {
  TimelinePhotoperiod,
  TimelineWindow,
} from '../state/timelineDraft'
import type { TimelineDraftController, TimelinePreviewState } from '../state/useTimelineDraft'

import { TimelineWarningOverlay, timelineWarningLabel } from './TimelineWarningOverlay'

type TimelineMode = 'compact' | 'expanded'

export type ControlTimelineProps = {
  readonly mode: TimelineMode
  readonly controller: TimelineDraftController
  readonly selectedLabel?: string
  readonly activeLabel?: string | null
  readonly operationalProjection?: ActiveClimateProjectionState
  readonly profileDetails?: ReactNode
  readonly profileWarning?: boolean
  readonly constantMode?: boolean
  readonly editingEnabled?: boolean
  readonly onExpand?: () => void
  readonly onCollapse?: () => void
  readonly lockedPhotoperiodHours?: number | null
  readonly forcedMoonPhase?: boolean
  /** Daily/rolling display-window change; invalidates review, keeps draft. */
  readonly onWindowChange?: (window: TimelineWindow) => void
}

function changedPeriods(
  saved: readonly ClimatePeriod[],
  draft: readonly ClimatePeriod[]
): string[] {
  return draft.flatMap((period, index) => {
    if (JSON.stringify(saved[index]) === JSON.stringify(period)) return []
    return [`Period ${index + 1}: ${period.period_name}`]
  })
}

function changedPhotoperiod(saved: TimelinePhotoperiod, draft: TimelinePhotoperiod): string[] {
  const changes: string[] = []
  if (saved.dayStartTime !== draft.dayStartTime) changes.push(`Day starts ${draft.dayStartTime}`)
  if (saved.nightStartTime !== draft.nightStartTime)
    changes.push(`Night starts ${draft.nightStartTime}`)
  if (saved.rampUpMinutes !== draft.rampUpMinutes)
    changes.push(`Ramp up ${draft.rampUpMinutes} min`)
  if (saved.rampDownMinutes !== draft.rampDownMinutes)
    changes.push(`Ramp down ${draft.rampDownMinutes} min`)
  return changes
}

function previewMessage(preview: TimelinePreviewState): string {
  switch (preview.kind) {
    case 'idle':
      return 'No review yet'
    case 'loading':
      return `Reviewing draft ${preview.draftRevision}`
    case 'ready':
      return `Reviewed draft ${preview.draftRevision}`
    case 'failed':
      return `Review failed for draft ${preview.draftRevision}`
    default:
      return assertNever(preview)
  }
}

export const AUTO_PREVIEW_DEBOUNCE_MS = 250

function assertNever(value: never): never {
  throw new Error(`Unexpected timeline preview state: ${JSON.stringify(value)}`)
}

export function ControlTimeline({
  mode,
  controller,
  selectedLabel = 'profile',
  activeLabel = null,
  operationalProjection,
  profileDetails,
  profileWarning = false,
  constantMode = false,
  editingEnabled = true,
  onExpand,
  onCollapse,
  lockedPhotoperiodHours = null,
  forcedMoonPhase = false,
  onWindowChange,
}: ControlTimelineProps) {
  const [editError, setEditError] = useState<string | null>(null)
  const [windowMode, setWindowMode] = useState<'daily' | 'rolling'>('daily')
  const [dragActive, setDragActive] = useState(false)
  const [warningPage, setWarningPage] = useState(0)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const { state, preview } = controller
  const isExpanded = mode === 'expanded'
  const draftPeriods = state.draft.periods
  const draftPeriodsRef = useRef(draftPeriods)
  draftPeriodsRef.current = draftPeriods
  const controllerRef = useRef(controller)
  controllerRef.current = controller
  const windowModeRef = useRef(windowMode)
  windowModeRef.current = windowMode
  const isExpandedRef = useRef(isExpanded)
  isExpandedRef.current = isExpanded
  const constantModeRef = useRef(constantMode)
  constantModeRef.current = constantMode
  const editingEnabledRef = useRef(editingEnabled && state.saved.baseConfigRevision.length > 0)
  editingEnabledRef.current = editingEnabled && state.saved.baseConfigRevision.length > 0

  useEffect(() => {
    const interval = setInterval(() => setNowMs(Date.now()), 1_000)
    return () => clearInterval(interval)
  }, [])

  const changes = useMemo(
    () => [
      ...changedPeriods(state.saved.periods, state.draft.periods),
      ...changedPhotoperiod(state.saved.photoperiod, state.draft.photoperiod),
    ],
    [state.saved, state.draft]
  )
  const operationalWarnings = operationalProjection?.trajectory?.warnings ?? []

  const nowBucket = Math.floor(nowMs / 30_000)
  const displayWindow = useMemo(() => displayWindowFor(windowMode, nowBucket * 30_000), [windowMode, nowBucket])
  const windowStartMs = displayWindow.start
  const windowEndMs = displayWindow.end

  const editorDirty = changes.length > 0
  const requestedWindow = { start: new Date(windowStartMs).toISOString(), end: new Date(windowEndMs).toISOString(), timezone: state.saved.window?.timezone ?? 'UTC' }
  const windowMatches = (window: TimelineWindow) => Date.parse(window.start) === windowStartMs &&
    Date.parse(window.end) === windowEndMs && window.timezone === requestedWindow.timezone
  const publishedWindowRef = useRef('')
  useEffect(() => {
    const key = `${state.saved.room.location}:${state.saved.room.cluster}:${windowStartMs}:${windowEndMs}:${requestedWindow.timezone}`
    if (publishedWindowRef.current === key) return
    publishedWindowRef.current = key
    if (state.saved.window != null && Date.parse(state.saved.window.start) === windowStartMs &&
      Date.parse(state.saved.window.end) === windowEndMs && state.saved.window.timezone === requestedWindow.timezone) return
    controllerRef.current.setWindow(requestedWindow)
    onWindowChange?.(requestedWindow)
  }, [state.saved.room.location, state.saved.room.cluster, windowStartMs, windowEndMs, requestedWindow.timezone,
    state.saved.window, onWindowChange])
  const previewMatchesDraft = preview.kind === 'ready' &&
    preview.draftRevision === state.draftRevision && preview.result.draftRevision === state.draftRevision &&
    preview.request.draftRevision === state.draftRevision &&
    preview.request.room.location === state.saved.room.location && preview.request.room.cluster === state.saved.room.cluster &&
    preview.request.expectedConfigRevision === state.saved.baseConfigRevision &&
    preview.result.expectedConfigRevision === state.saved.baseConfigRevision &&
    preview.request.modeId === state.saved.modeId && preview.result.modeId === state.saved.modeId &&
    preview.request.submodeId === (state.saved.submodeId ?? null) && preview.result.submodeId === (state.saved.submodeId ?? null) &&
    preview.request.requestId === preview.result.requestId && windowMatches(preview.request.window) &&
    windowMatches(preview.result.window) && JSON.stringify(preview.request.values) === JSON.stringify(state.draft) &&
    (preview.result.trajectory == null || (preview.result.trajectory.room === state.saved.room.location &&
      preview.result.trajectory.base_config_revision === state.saved.baseConfigRevision &&
      preview.result.trajectory.draft_revision === String(state.draftRevision) &&
      preview.result.trajectory.window.start.getTime() === windowStartMs &&
      preview.result.trajectory.window.end.getTime() === windowEndMs &&
      preview.result.trajectory.window.timezone === requestedWindow.timezone &&
      preview.result.trajectory.segments.every(segment => segment.source.mode === String(state.saved.modeId) &&
        segment.source.submode === (state.saved.submodeId == null ? null : String(state.saved.submodeId)))))
  const envelope = previewMatchesDraft && preview.kind === 'ready' ? preview.result.trajectory : null
  const savedEnvelope = state.saved.trajectory != null &&
    state.saved.trajectory.room === state.saved.room.location &&
    state.saved.trajectory.revision_scope === 'saved' &&
    state.saved.trajectory.base_config_revision === state.saved.baseConfigRevision &&
    state.saved.trajectory.window.start.getTime() === windowStartMs &&
    state.saved.trajectory.window.end.getTime() === windowEndMs &&
    state.saved.trajectory.window.timezone === requestedWindow.timezone ? state.saved.trajectory : null
  const warnings = (envelope ?? savedEnvelope)?.warnings ?? []
  const detailWarnings = [...warnings, ...operationalWarnings].filter(warning => warning.code !== 'calendar.transition_skipped')
  const warningPages = Math.max(1, Math.ceil(detailWarnings.length / 4))
  const visibleWarningPage = Math.min(warningPage, warningPages - 1)
  const localMode = dragActive || !previewMatchesDraft
  const dragEnabled = editingEnabled && state.saved.baseConfigRevision.length > 0 && isExpanded && (constantMode || windowMode === 'daily')
  const lastAutoPreviewRevisionRef = useRef<number | null>(null)

  useEffect(() => {
    if (!isExpanded || !editingEnabled || !state.saved.baseConfigRevision) return
    if (!editorDirty) {
      lastAutoPreviewRevisionRef.current = null
      return
    }
    if (state.status.kind !== 'editing') return
    if (preview.kind === 'loading') return
    if (previewMatchesDraft) return
    if (lastAutoPreviewRevisionRef.current === state.draftRevision) return
    const timer = window.setTimeout(() => {
      lastAutoPreviewRevisionRef.current = state.draftRevision
      void controllerRef.current.review()
    }, AUTO_PREVIEW_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [
    isExpanded,
    editorDirty,
    preview.kind,
    previewMatchesDraft,
    state.draftRevision,
    state.status.kind,
    editingEnabled,
    state.saved.baseConfigRevision,
  ])
  const chart = useMemo(() => buildTimelineSources({
    location: state.saved.room.location,
    cluster: state.saved.room.cluster,
    window: { start: windowStartMs, end: windowEndMs },
    // Publications can arrive after the last one-second display tick.
    now: Date.now(),
    current: operationalProjection?.current ?? null,
    future: operationalProjection?.future ?? [],
    saved: savedEnvelope,
    localSaved: savedEnvelope == null && state.saved.baseConfigRevision ? state.saved.periods : null,
    draft: localMode ? null : envelope,
    localDraft: localMode && state.saved.baseConfigRevision ? draftPeriods : null,
    selectedLabel,
    activeLabel,
  }), [state.saved.room.location, state.saved.room.cluster, windowStartMs, windowEndMs, nowMs,
    operationalProjection, state.saved.baseConfigRevision, state.saved.periods, savedEnvelope, envelope, localMode, draftPeriods, selectedLabel, activeLabel])

  // Selected draft bands remain hypothetical; live phase is separately named.
  const bands = useMemo(
    () => !state.saved.baseConfigRevision ? [] :
      forcedMoonPhase
        ? [{ start: windowStartMs, end: windowEndMs, phase: 'MOON' as const }]
        : photoperiodIntervals(state.draft.photoperiod, windowStartMs, windowEndMs),
    [forcedMoonPhase, state.saved.baseConfigRevision, state.draft.photoperiod, windowStartMs, windowEndMs]
  )

  const nowX =
    nowMs >= windowStartMs && nowMs <= windowEndMs ? (nowMs - windowStartMs) / 60_000 : null

  const labelSegments = useMemo(
    () => periodLabelSegments(state.draft.periods, windowStartMs, windowEndMs),
    [state.draft.periods, windowStartMs, windowEndMs]
  )

  const dataRevision = useRef(0)
  const revisionInputsRef = useRef<{
    envelope: typeof envelope
    chart: typeof chart
    bands: typeof bands
    nowBucket: number
    labelSegments: typeof labelSegments
    draftPeriods: typeof draftPeriods
    dragEnabled: boolean
  }>({ envelope, chart, bands, nowBucket: 0, labelSegments, draftPeriods, dragEnabled })
  const revisionInputs = revisionInputsRef.current
  if (
    revisionInputs.envelope !== envelope ||
    revisionInputs.chart !== chart ||
    revisionInputs.bands !== bands ||
    revisionInputs.nowBucket !== nowBucket ||
    revisionInputs.labelSegments !== labelSegments ||
    revisionInputs.draftPeriods !== draftPeriods ||
    revisionInputs.dragEnabled !== dragEnabled
  ) {
    revisionInputsRef.current = {
      envelope,
      chart,
      bands,
      nowBucket,
      labelSegments,
      draftPeriods,
      dragEnabled,
    }
    dataRevision.current += 1
  }

  const labelSegmentsRef = useRef<readonly PeriodLabelSegment[]>(labelSegments)
  labelSegmentsRef.current = labelSegments
  const labelsPlugin = useMemo(
    () =>
      periodLabelsPlugin(() => labelSegmentsRef.current, {
        startMs: windowStartMs,
        endMs: windowEndMs,
      }),
    [windowStartMs, windowEndMs]
  )

  const boundaryCoalescerRef = useRef<RafCoalescer<{
    index: number
    edge: BoundaryEdge
    rawMinutes: number
  }> | null>(null)
  if (boundaryCoalescerRef.current === null) {
    boundaryCoalescerRef.current = createRafCoalescer(({ index, edge, rawMinutes }) => {
      const clamped = clampedBoundaryMinutes(draftPeriodsRef.current, index, edge, rawMinutes)
      controllerRef.current.editPeriods(
        applyBoundary(draftPeriodsRef.current, index, edge, clamped)
      )
    })
  }
  const valueCoalescerRef = useRef<RafCoalescer<{
    index: number
    metric: ValueMetric
    value: number
  }> | null>(null)
  if (valueCoalescerRef.current === null) {
    valueCoalescerRef.current = createRafCoalescer(({ index, metric, value }) => {
      controllerRef.current.editPeriods(applyValue(draftPeriodsRef.current, index, metric, value))
    })
  }

  const dragHandles = useMemo(
    () =>
      dragHandlesPlugin({
        getPeriods: () => draftPeriodsRef.current,
        getWindow: () => ({ start: windowStartMs, end: windowEndMs }),
        isDragEnabled: () => editingEnabledRef.current && isExpandedRef.current && (constantModeRef.current || windowModeRef.current === 'daily'),
        isBoundaryEnabled: () => !constantModeRef.current,
        onDragStart: () => setDragActive(true),
        onDragEnd: () => {
          boundaryCoalescerRef.current?.flush()
          valueCoalescerRef.current?.flush()
          setDragActive(false)
        },
        pushBoundary: (index, edge, rawMinutes) =>
          boundaryCoalescerRef.current?.push({ index, edge, rawMinutes }),
        pushValue: (index, metric, rawValue) => {
          const value = snapValue(metric, rawValue)
          if (!isValueInRange(metric, value)) {
            setEditError(rangeMessage(metric))
            return
          }
          setEditError(null)
          valueCoalescerRef.current?.push({ index, metric, value })
        },
        commitBoundaryKey: (index, edge, deltaMinutes) => {
          const period = draftPeriodsRef.current[index]
          if (!period) return
          const current = timeToMinutes(edge === 'start' ? period.start_time : period.end_time)
          const clamped = clampedBoundaryMinutes(
            draftPeriodsRef.current,
            index,
            edge,
            current + deltaMinutes
          )
          controllerRef.current.editPeriods(
            applyBoundary(draftPeriodsRef.current, index, edge, clamped)
          )
          setEditError(null)
        },
        commitValueKey: (index, metric, deltaValue) => {
          const period = draftPeriodsRef.current[index]
          if (!period) return
          const field =
            metric === 'heating'
              ? 'heating_setpoint'
              : metric === 'cooling'
                ? 'cooling_setpoint'
                : metric === 'vpd'
                  ? 'vpd_setpoint'
                  : 'co2_setpoint'
          const current = period[field]
          if (current == null) return
          const value = snapValue(metric, current + deltaValue)
          if (!isValueInRange(metric, value)) {
            setEditError(rangeMessage(metric))
            return
          }
          controllerRef.current.editPeriods(
            applyValue(draftPeriodsRef.current, index, metric, value)
          )
          setEditError(null)
        },
      }),
    [windowStartMs, windowEndMs]
  )

  useEffect(
    () => () => {
      boundaryCoalescerRef.current?.cancel()
      valueCoalescerRef.current?.cancel()
    },
    []
  )

  const adjustBoundary = (index: number, edge: 'start' | 'end', delta: number): void => {
    const period = state.draft.periods[index]
    if (!period) return
    const current = timeToMinutes(edge === 'start' ? period.start_time : period.end_time)
    const next = current + delta
    if (next < 0 || next > 1435) {
      setEditError('Timeline boundaries must stay within the 24-hour window.')
      return
    }
    const field = edge === 'start' ? 'start_time' : 'end_time'
    controller.editPeriods(
      state.draft.periods.map((candidate, candidateIndex) =>
        candidateIndex === index ? { ...candidate, [field]: minutesToTime(next) } : candidate
      )
    )
    setEditError(null)
  }

  const changePhotoperiod = (field: 'dayStartTime' | 'nightStartTime', value: string): void => {
    if (field !== 'dayStartTime' && field !== 'nightStartTime') return
    if (!/^\d{2}:\d{2}$/.test(value)) {
      setEditError('Photoperiod times must use HH:MM.')
      controller.editPhotoperiod({ ...state.draft.photoperiod, [field]: value })
      return
    }
    const next = { ...state.draft.photoperiod, [field]: value }
    if (lockedPhotoperiodHours != null) {
      const minutes = timeToMinutes(value)
      if (field === 'dayStartTime')
        next.nightStartTime = minutesToTime((minutes + lockedPhotoperiodHours * 60) % 1440)
      if (field === 'nightStartTime')
        next.dayStartTime = minutesToTime((minutes - lockedPhotoperiodHours * 60 + 1440) % 1440)
    }
    controller.editPhotoperiod(next)
    setEditError(null)
  }

  const renderHandle = (index: number, edge: 'start' | 'end') => (
    <button
      disabled={constantMode || !editingEnabled || !state.saved.baseConfigRevision}
      type="button"
      data-testid={`control-timeline-handle-${index}-${edge}`}
      aria-label={`Adjust ${state.draft.periods[index]?.period_name ?? `period ${index + 1}`} ${edge}`}
      className="border border-border-default bg-surface-secondary px-1 py-0.5 text-10 font-mono text-text-muted focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-accent-data"
      onKeyDown={event => {
        if (event.key === 'ArrowLeft') adjustBoundary(index, edge, -5)
        if (event.key === 'ArrowRight') adjustBoundary(index, edge, 5)
      }}
    >
      {`${state.draft.periods[index]?.period_name ?? index + 1} ${edge}`}
    </button>
  )

  return (
    <section
      className="flex h-full min-h-0 flex-col bg-surface-primary text-text-default"
      aria-label="Climate control timeline"
    >
      <header className="flex items-center justify-between gap-1 border-b border-border-subtle px-2 py-1">
        <div className="flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-text-muted">
          <fieldset className="flex items-center gap-1">
            <legend className="sr-only">Timeline window</legend>
            {(['rolling', 'daily'] as const).map(value => (
              <button
                key={value}
                type="button"
                onClick={() => setWindowMode(value)}
                className={`border px-1.5 py-0.5 text-10 uppercase ${windowMode === value ? 'border-accent-data text-accent-data' : 'border-border-default text-text-subtle'}`}
              >
                {value}
              </button>
            ))}
          </fieldset>
          <span className="border border-border-default px-1 text-10 text-accent-data">
            {isExpanded ? 'EDITABLE' : 'READ ONLY'}
          </span>
          {forcedMoonPhase && <span className="text-10">MOON · 24h</span>}
          <span
            data-testid="control-timeline-clock-label"
            className="text-10 text-text-subtle"
          >
            Chart times UTC · Schedule times America/Toronto
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Popover>
            <PopoverTrigger asChild>
              <button type="button" aria-label="Timeline sources" data-testid="control-timeline-details"
                className="border border-border-default px-2 py-1 text-10 text-text-subtle hover:bg-surface-secondary">
                Current · Forecast · Saved · Draft{profileWarning || detailWarnings.length > 0 ? ' · !' : ''}
              </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[36rem] max-w-[calc(100vw-2rem)] space-y-2 p-3 text-xs break-words"
              aria-label="Timeline source and profile details">
              <div className="flex flex-wrap gap-2 text-10 text-text-subtle">
                <span>Current effective · observation marker</span>
                <span>Running forecast (estimated) · dashed</span>
                <span>Saved {selectedLabel}{!state.saved.baseConfigRevision ? ' (unavailable)' :
                  `${activeLabel === selectedLabel ? '' : ' (not active)'}${savedEnvelope == null ? ' (local estimate)' : ''}`} · solid</span>
                <span>Draft {selectedLabel} ({!state.saved.baseConfigRevision ? 'unavailable' : localMode ? 'local estimate' : 'preview'}) · dotted</span>
              </div>
              <p className="text-10 text-text-subtle" aria-live="polite">
                {chart.meta.some((entry, index) => entry.role === 'active-current' && chart.data[index + 1]?.some(value => value != null))
                  ? 'Current effective · fresh completed tick' : 'Current effective unavailable'}
                {' · '}{chart.meta.some((entry, index) => entry.role === 'active-future' && chart.data[index + 1]?.some(value => value != null))
                  ? 'Running forecast available (estimated)' : 'Running projection unavailable / updating'}
                {operationalProjection?.current?.photoperiod != null &&
                  operationalProjection.current.photoperiod.quality !== 'unavailable' &&
                  operationalProjection.current.photoperiod.observed_at.getTime() <= Date.now() &&
                  Date.now() < operationalProjection.current.photoperiod.valid_until.getTime() &&
                  ` · Live photoperiod ${operationalProjection.current.photoperiod.phase}`}
              </p>
              {profileDetails}
              {detailWarnings.slice(visibleWarningPage * 4, (visibleWarningPage + 1) * 4).map((warning, index) => (
                <p key={`timeline-generic-warning-${visibleWarningPage}-${index}`} className="text-10 text-status-warning-text">
                  {timelineWarningLabel(warning)}
                </p>
              ))}
              {warningPages > 1 && (
                <div className="flex items-center gap-2">
                  <button type="button" disabled={visibleWarningPage === 0} onClick={() => setWarningPage(visibleWarningPage - 1)}
                    className="border border-border-default px-2 py-1 disabled:opacity-40">Previous warnings</button>
                  <span>Warnings {visibleWarningPage + 1} / {warningPages}</span>
                  <button type="button" disabled={visibleWarningPage === warningPages - 1} onClick={() => setWarningPage(visibleWarningPage + 1)}
                    className="border border-border-default px-2 py-1 disabled:opacity-40">Next warnings</button>
                </div>
              )}
            </PopoverContent>
          </Popover>
          {isExpanded && onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              data-testid="control-timeline-collapse"
              className="border border-border-default px-2 py-1 text-10 font-bold uppercase text-text-muted hover:bg-surface-secondary"
            >
              Collapse editor
            </button>
          )}
          {!isExpanded && onExpand && (
            <button
              type="button"
              onClick={onExpand}
              className="border border-accent-data px-2 py-1 text-10 font-bold uppercase text-accent-data hover:bg-accent-data/10"
            >
              Expand editor
            </button>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-1 p-2">
        <div
          data-testid="control-timeline-plot"
          className={`relative min-w-0 border border-border-default bg-surface-base ${isExpanded ? 'h-[60vh]' : 'h-64'}`}
        >
          <TimelineUPlot
            data={chart.data}
            meta={chart.meta}
            qualities={chart.qualities}
            windowMs={{ start: windowStartMs, end: windowEndMs }}
            photoperiod={bands}
            nowX={nowX}
            revision={dataRevision.current}
            plugins={[labelsPlugin, dragHandles]}
            ariaLabel={`Climate control timeline plot, ${chart.meta.map(entry => entry.label).join(', ')}`}
          />
          <div className="pointer-events-none absolute inset-0 z-10">
            {warnings.filter(warning => warning.code === 'calendar.transition_skipped').map((warning, index) =>
              <TimelineWarningOverlay key={`selected-${index}`} warning={warning} window={{ start: new Date(windowStartMs), end: new Date(windowEndMs) }} />
            )}
            {operationalWarnings.filter(warning => warning.code === 'calendar.transition_skipped').map((warning, index) =>
              <TimelineWarningOverlay key={`running-${index}`} warning={warning} window={{ start: new Date(windowStartMs), end: new Date(windowEndMs) }} />
            )}
          </div>
          <span className="sr-only" data-testid="control-timeline-period-legend">
            {state.draft.periods.map(period => (
              <span key={`${period.period_name}-${period.start_time}-${period.end_time}`}>
                <span>{period.period_name}</span>
                {`: ${period.start_time}–${period.end_time}`}
              </span>
            ))}
          </span>
          {dragActive && (
            <span
              data-testid="control-timeline-local-estimate"
              className="pointer-events-none absolute left-1 top-1 z-20 border border-accent-data bg-surface-primary/80 px-1 text-9 font-bold uppercase text-accent-data"
            >
              Local draft estimate
            </span>
          )}
          {localMode && !dragActive && state.saved.baseConfigRevision && (
            <span
              className="pointer-events-none absolute right-1 top-1 z-20 border border-status-warning-border bg-surface-primary/80 px-1 text-9 font-bold uppercase text-status-warning-text"
            >
              Local draft estimate{preview.kind === 'failed' ? ' — review failed' : preview.kind === 'loading' ? ' — preview pending' : ''}
            </span>
          )}
        </div>
        {state.draft.periods.length === 0 && (
          <p className="text-10 text-text-subtle">
            No climate periods configured; setpoints are unset.
          </p>
        )}

        {isExpanded && (
          <div className="flex flex-wrap gap-1" data-testid="control-timeline-boundaries">
            {state.draft.periods.map((period, index) => (
              <Fragment key={`boundary-controls-${period.period_name}-${index}`}>
                {renderHandle(index, 'start')}
                {renderHandle(index, 'end')}
              </Fragment>
            ))}
          </div>
        )}

        {editError && (
          <p role="alert" className="text-10 text-status-danger-text">
            {editError}
          </p>
        )}

        {isExpanded && (
          <div className="grid grid-cols-4 gap-1 border border-border-subtle p-1 text-10">
            {(
              [
                ['Day start', 'dayStartTime'],
                ['Night start', 'nightStartTime'],
              ] as const
            ).map(([label, field]) => (
              <label key={field} className="flex flex-col gap-0.5 text-text-muted">
                {label}
                <input
                  aria-label={label}
                  value={state.draft.photoperiod[field]}
                  disabled={constantMode || forcedMoonPhase || !editingEnabled || !state.saved.baseConfigRevision}
                  onChange={event => changePhotoperiod(field, event.target.value)}
                  className="border border-border-default bg-surface-secondary px-1 py-0.5 text-text-input"
                />
              </label>
            ))}
            {(
              [
                ['Ramp up (min)', 'rampUpMinutes'],
                ['Ramp down (min)', 'rampDownMinutes'],
              ] as const
            ).map(([label, field]) => (
              <label key={field} className="flex flex-col gap-0.5 text-text-muted">
                {label}
                <input
                  aria-label={label}
                  type="number"
                  min={0}
                  value={state.draft.photoperiod[field]}
                  disabled={constantMode || forcedMoonPhase || !editingEnabled || !state.saved.baseConfigRevision}
                  onChange={event =>
                    controller.editPhotoperiod({
                      ...state.draft.photoperiod,
                      [field]: Number(event.target.value) || 0,
                    })
                  }
                  className="border border-border-default bg-surface-secondary px-1 py-0.5 text-text-input"
                />
              </label>
            ))}
          </div>
        )}

        {isExpanded && (
          <div className="flex flex-wrap items-center gap-1 border-t border-border-subtle pt-1">
            <span
              className={`text-10 ${state.status.kind === 'conflict' ? 'text-status-warning-text' : 'text-text-subtle'}`}
            >
              {state.status.kind === 'conflict'
                ? 'Conflict: saved revision changed. Draft preserved.'
                : previewMessage(preview)}
            </span>
            <button
              type="button"
              onClick={() => void controller.review()}
              disabled={!editingEnabled || !state.saved.baseConfigRevision || (!changes.length && state.saved.parametersConfigured) || preview.kind === 'loading'}
              className="ml-auto border border-accent-data px-2 py-1 text-10 font-bold uppercase text-accent-data disabled:cursor-not-allowed disabled:opacity-40"
            >
              Review
            </button>
            <button
              type="button"
              onClick={() => void controller.apply()}
              disabled={!editingEnabled || !state.saved.baseConfigRevision || state.status.kind !== 'reviewed' || !previewMatchesDraft}
              className="border border-status-success px-2 py-1 text-10 font-bold uppercase text-status-success disabled:cursor-not-allowed disabled:opacity-40"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={controller.discard}
              disabled={!editingEnabled || !changes.length}
              className="border border-border-default px-2 py-1 text-10 font-bold uppercase text-text-muted disabled:cursor-not-allowed disabled:opacity-40"
            >
              Discard
            </button>
          </div>
        )}
        {isExpanded && changes.length > 0 && (
          <div className="border border-accent-data/50 bg-accent-data/5 p-1 text-10 text-text-secondary">
            <strong>Draft changes:</strong> {changes.join(' · ')}
          </div>
        )}
      </div>
    </section>
  )
}
