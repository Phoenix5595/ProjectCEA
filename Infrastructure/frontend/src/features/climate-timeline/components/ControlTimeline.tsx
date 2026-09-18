import { useEffect, useMemo, useRef, useState } from 'react'
import type uPlot from 'uplot'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TimelinePhotoperiod } from '../state/timelineDraft'
import type { TimelineDraftController, TimelinePreviewState } from '../state/useTimelineDraft'
import { timeToMinutes, minutesToTime } from '../../../utils/timeMath'
import {
  buildEnvelopeSeries,
  envelopeSampleTimes,
  photoperiodIntervals,
} from '../charts/envelopeSeries'
import { timelineSeriesMeta } from '../charts/timelineOptions'
import { TimelineUPlot } from '../charts/TimelineUPlot'
import { TimelineWarningOverlay, timelineWarningLabel } from './TimelineWarningOverlay'

type TimelineMode = 'compact' | 'expanded'
type DragTarget = { readonly index: number; readonly edge: 'start' | 'end' }

export type ControlTimelineProps = {
  readonly mode: TimelineMode
  readonly controller: TimelineDraftController
  readonly onExpand?: () => void
  readonly onCollapse?: () => void
  readonly lockedPhotoperiodHours?: number | null
}

function changedPeriods(saved: readonly ClimatePeriod[], draft: readonly ClimatePeriod[]): string[] {
  return draft.flatMap((period, index) => {
    if (JSON.stringify(saved[index]) === JSON.stringify(period)) return []
    return [`Period ${index + 1}: ${period.period_name}`]
  })
}

function changedPhotoperiod(saved: TimelinePhotoperiod, draft: TimelinePhotoperiod): string[] {
  const changes: string[] = []
  if (saved.dayStartTime !== draft.dayStartTime) changes.push(`Day starts ${draft.dayStartTime}`)
  if (saved.nightStartTime !== draft.nightStartTime) changes.push(`Night starts ${draft.nightStartTime}`)
  if (saved.rampUpMinutes !== draft.rampUpMinutes) changes.push(`Ramp up ${draft.rampUpMinutes} min`)
  if (saved.rampDownMinutes !== draft.rampDownMinutes) changes.push(`Ramp down ${draft.rampDownMinutes} min`)
  return changes
}

function previewMessage(preview: TimelinePreviewState): string {
  switch (preview.kind) {
    case 'idle': return 'No review yet'
    case 'loading': return `Reviewing draft ${preview.draftRevision}`
    case 'ready': return preview.source === 'preview' ? `Reviewed draft ${preview.draftRevision}` : 'Saved trajectory refreshed'
    case 'failed': return `Review failed for draft ${preview.draftRevision}`
    default: return assertNever(preview)
  }
}

function assertNever(value: never): never {
  throw new Error(`Unexpected timeline preview state: ${JSON.stringify(value)}`)
}

export function ControlTimeline({ mode, controller, onExpand, onCollapse, lockedPhotoperiodHours = null }: ControlTimelineProps) {
  const [dragTarget, setDragTarget] = useState<DragTarget | null>(null)
  const [editError, setEditError] = useState<string | null>(null)
  const [windowMode, setWindowMode] = useState<'daily' | 'rolling'>('rolling')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const { state, preview } = controller
  const isExpanded = mode === 'expanded'

  useEffect(() => {
    const interval = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(interval)
  }, [])

  const changes = useMemo(
    () => [...changedPeriods(state.saved.periods, state.draft.periods), ...changedPhotoperiod(state.saved.photoperiod, state.draft.photoperiod)],
    [state.saved, state.draft],
  )
  const envelope = preview.kind === 'ready' ? preview.value : state.saved.trajectory
  const warnings = envelope?.warnings ?? []
  const skippedWarning = warnings.find((warning) => warning.code === 'calendar.transition_skipped')
  const genericWarnings = warnings.filter((warning) => warning.code !== 'calendar.transition_skipped')

  const windowStartMs = envelope?.window.start.getTime() ?? (state.saved.window ? Date.parse(state.saved.window.start) : nowMs)
  const windowEndMs = envelope?.window.end.getTime() ?? (state.saved.window ? Date.parse(state.saved.window.end) : nowMs + 24 * 60 * 60 * 1000)

  const chart = useMemo(() => {
    if (!envelope) return null
    const sampleTimes = envelopeSampleTimes(envelope.window)
    const built = buildEnvelopeSeries(envelope, sampleTimes)
    return {
      data: [sampleTimes.slice(), ...built.keys.map((key) => [...(built.series.get(key) ?? [])])] as uPlot.AlignedData,
      meta: timelineSeriesMeta(built.keys),
    }
  }, [envelope])

  const bands = useMemo(
    () => photoperiodIntervals(state.draft.photoperiod, windowStartMs, windowEndMs),
    [state.draft.photoperiod, windowStartMs, windowEndMs],
  )

  const nowX = nowMs >= windowStartMs && nowMs <= windowEndMs ? nowMs : null

  const dataRevision = useRef(0)
  const revisionInputsRef = useRef<{ envelope: typeof envelope; bands: typeof bands; nowBucket: number }>({ envelope, bands, nowBucket: 0 })
  const nowBucket = Math.floor(nowMs / 30_000)
  const revisionInputs = revisionInputsRef.current
  if (revisionInputs.envelope !== envelope || revisionInputs.bands !== bands || revisionInputs.nowBucket !== nowBucket) {
    revisionInputsRef.current = { envelope, bands, nowBucket }
    dataRevision.current += 1
  }

  useEffect(() => {
    if (!dragTarget) return
    const onMove = (event: MouseEvent): void => {
      const plot = document.querySelector('[data-testid="control-timeline-plot"]')
      if (!(plot instanceof HTMLElement)) return
      const bounds = plot.getBoundingClientRect()
      const minutes = Math.round(((event.clientX - bounds.left) / bounds.width) * 1440 / 5) * 5
      if (minutes < 0 || minutes > 1435) {
        setEditError('Timeline boundaries must stay within the 24-hour window.')
        return
      }
      const period = state.draft.periods[dragTarget.index]
      if (!period) return
      const field = dragTarget.edge === 'start' ? 'start_time' : 'end_time'
      controller.editPeriods(state.draft.periods.map((candidate, index) => index === dragTarget.index
        ? { ...candidate, [field]: minutesToTime(minutes) }
        : candidate))
      setEditError(null)
    }
    const onUp = (): void => setDragTarget(null)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [controller, dragTarget, state.draft.periods])

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
    controller.editPeriods(state.draft.periods.map((candidate, candidateIndex) => candidateIndex === index
      ? { ...candidate, [field]: minutesToTime(next) }
      : candidate))
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
      if (field === 'dayStartTime') next.nightStartTime = minutesToTime((minutes + lockedPhotoperiodHours * 60) % 1440)
      if (field === 'nightStartTime') next.dayStartTime = minutesToTime((minutes - lockedPhotoperiodHours * 60 + 1440) % 1440)
    }
    controller.editPhotoperiod(next)
    setEditError(null)
  }

  const renderHandle = (index: number, edge: 'start' | 'end') => (
    <button
      type="button"
      data-testid={`control-timeline-handle-${index}-${edge}`}
      aria-label={`Adjust ${state.draft.periods[index]?.period_name ?? `period ${index + 1}`} ${edge}`}
      className="border border-border-default bg-surface-secondary px-1 py-0.5 text-[10px] font-mono text-text-muted focus:outline-hidden focus:ring-2 focus:ring-accent-data"
      onMouseDown={() => setDragTarget({ index, edge })}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') adjustBoundary(index, edge, -5)
        if (event.key === 'ArrowRight') adjustBoundary(index, edge, 5)
      }}
    >
      {`${state.draft.periods[index]?.period_name ?? index + 1} ${edge}`}
    </button>
  )

  return (
    <section className="flex h-full min-h-0 flex-col bg-surface-primary text-text-default" aria-label="Climate control timeline">
      <header className="flex items-center justify-between gap-1 border-b border-border-subtle px-2 py-1">
        <div className="flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-text-muted">
          <fieldset className="flex items-center gap-1">
            <legend className="sr-only">Timeline window</legend>
            {(['rolling', 'daily'] as const).map((value) => (
              <button key={value} type="button" onClick={() => setWindowMode(value)} className={`border px-1.5 py-0.5 text-[10px] uppercase ${windowMode === value ? 'border-accent-data text-accent-data' : 'border-border-default text-text-subtle'}`}>
                {value}
              </button>
            ))}
          </fieldset>
          <span className="border border-border-default px-1 text-[10px] text-accent-data">{isExpanded ? 'EDITABLE' : 'READ ONLY'}</span>
        </div>
        <div className="flex items-center gap-1">
          {isExpanded && onCollapse && (
            <button type="button" onClick={onCollapse} data-testid="control-timeline-collapse" className="border border-border-default px-2 py-1 text-[10px] font-bold uppercase text-text-muted hover:bg-surface-secondary">
              Collapse editor
            </button>
          )}
          {!isExpanded && onExpand && (
            <button type="button" onClick={onExpand} className="border border-accent-data px-2 py-1 text-[10px] font-bold uppercase text-accent-data hover:bg-accent-data/10">
              Expand editor
            </button>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-auto p-2">
        <div
          data-testid="control-timeline-plot"
          className={`relative min-w-0 border border-border-default bg-surface-base ${isExpanded ? 'h-[60vh]' : 'h-64'}`}
        >
          {chart ? (
            <TimelineUPlot
              data={chart.data}
              meta={chart.meta}
              windowMs={{ start: windowStartMs, end: windowEndMs }}
              photoperiod={bands}
              nowX={nowX}
              revision={dataRevision.current}
              ariaLabel={`Climate control timeline plot, ${chart.meta.map((entry) => entry.label).join(', ')}`}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-[10px] text-text-subtle uppercase">No trajectory envelope available</div>
          )}
          <div className="pointer-events-none absolute inset-0 z-10">
            {skippedWarning !== undefined && envelope !== undefined && <TimelineWarningOverlay warning={skippedWarning} window={envelope.window} />}
          </div>
          <span className="sr-only" data-testid="control-timeline-period-legend">
            {state.draft.periods.map((period) => (
              <span key={`${period.period_name}-${period.start_time}-${period.end_time}`}>
                <span>{period.period_name}</span>
                {`: ${period.start_time}–${period.end_time}`}
              </span>
            ))}
          </span>
        </div>

        {isExpanded && (
          <div className="flex flex-wrap gap-1" data-testid="control-timeline-boundaries">
            {state.draft.periods.flatMap((_period, index) => [renderHandle(index, 'start'), renderHandle(index, 'end')])}
          </div>
        )}

        {genericWarnings.map((warning, index) => (
          <p key={`timeline-generic-warning-${index}`} className="text-[10px] text-status-warning-text">
            {timelineWarningLabel(warning)}
          </p>
        ))}
        {genericWarnings.length === 0 && !skippedWarning && (
          <p className="text-[10px] text-text-subtle">Saved schedule authority; no runtime assumptions reported.</p>
        )}
        {editError && <p role="alert" className="text-[10px] text-status-danger-text">{editError}</p>}

        {isExpanded && (
          <div className="grid grid-cols-2 gap-1 border border-border-subtle p-1 text-[10px] sm:grid-cols-4">
            {([
              ['Day start', 'dayStartTime'],
              ['Night start', 'nightStartTime'],
            ] as const).map(([label, field]) => (
              <label key={field} className="flex flex-col gap-0.5 text-text-muted">{label}
                <input aria-label={label} value={state.draft.photoperiod[field]} onChange={(event) => changePhotoperiod(field, event.target.value)} className="border border-border-default bg-surface-secondary px-1 py-0.5 text-text-input" />
              </label>
            ))}
            {([
              ['Ramp up (min)', 'rampUpMinutes'],
              ['Ramp down (min)', 'rampDownMinutes'],
            ] as const).map(([label, field]) => (
              <label key={field} className="flex flex-col gap-0.5 text-text-muted">{label}
                <input aria-label={label} type="number" min={0} value={state.draft.photoperiod[field]} onChange={(event) => controller.editPhotoperiod({ ...state.draft.photoperiod, [field]: Number(event.target.value) || 0 })} className="border border-border-default bg-surface-secondary px-1 py-0.5 text-text-input" />
              </label>
            ))}
          </div>
        )}

        {isExpanded && (
          <div className="flex flex-wrap items-center gap-1 border-t border-border-subtle pt-1">
            <span className={`text-[10px] ${state.status.kind === 'conflict' ? 'text-status-warning-text' : 'text-text-subtle'}`}>{state.status.kind === 'conflict' ? 'Conflict: saved revision changed. Draft preserved.' : previewMessage(preview)}</span>
            <button type="button" onClick={() => void controller.review()} disabled={!changes.length || preview.kind === 'loading'} className="ml-auto border border-accent-data px-2 py-1 text-[10px] font-bold uppercase text-accent-data disabled:cursor-not-allowed disabled:opacity-40">Review</button>
            <button type="button" onClick={() => void controller.apply()} disabled={state.status.kind !== 'reviewed' || preview.kind !== 'ready'} className="border border-status-success px-2 py-1 text-[10px] font-bold uppercase text-status-success disabled:cursor-not-allowed disabled:opacity-40">Apply</button>
            <button type="button" onClick={controller.discard} disabled={!changes.length} className="border border-border-default px-2 py-1 text-[10px] font-bold uppercase text-text-muted disabled:cursor-not-allowed disabled:opacity-40">Discard</button>
          </div>
        )}
        {isExpanded && changes.length > 0 && <div className="border border-accent-data/50 bg-accent-data/5 p-1 text-[10px] text-text-secondary"><strong>Draft changes:</strong> {changes.join(' · ')}</div>}
      </div>
    </section>
  )
}
