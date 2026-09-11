import { useEffect, useMemo, useState } from 'react'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TrajectorySegment } from '../api/contracts'
import type { TimelinePhotoperiod } from '../state/timelineDraft'
import type { TimelineDraftController, TimelinePreviewState } from '../state/useTimelineDraft'
import { timeToMinutes, minutesToTime } from '../../../utils/timeMath'

type TimelineMode = 'compact' | 'expanded'
type DragTarget = { readonly index: number; readonly edge: 'start' | 'end' }

export type ControlTimelineProps = {
  readonly mode: TimelineMode
  readonly controller: TimelineDraftController
  readonly onExpand?: () => void
  readonly lockedPhotoperiodHours?: number | null
}

function periodWidth(period: ClimatePeriod): { readonly left: number; readonly width: number } {
  const start = timeToMinutes(period.start_time)
  const end = timeToMinutes(period.end_time)
  const duration = end >= start ? end - start : 1440 - start + end
  return { left: (start / 1440) * 100, width: Math.max((duration / 1440) * 100, 2) }
}

function changedPeriods(saved: readonly ClimatePeriod[], draft: readonly ClimatePeriod[]): string[] {
  return draft.flatMap((period, index) => {
    if (JSON.stringify(saved[index]) === JSON.stringify(period)) return []
    return [`Period ${index + 1}: ${period.period_name}`]
  })
}

function changedPhotoperiod(
  saved: TimelinePhotoperiod,
  draft: TimelinePhotoperiod,
): string[] {
  const changes: string[] = []
  if (saved.dayStartTime !== draft.dayStartTime) changes.push(`Day starts ${draft.dayStartTime}`)
  if (saved.nightStartTime !== draft.nightStartTime) changes.push(`Night starts ${draft.nightStartTime}`)
  if (saved.rampUpMinutes !== draft.rampUpMinutes) changes.push(`Ramp up ${draft.rampUpMinutes} min`)
  if (saved.rampDownMinutes !== draft.rampDownMinutes) changes.push(`Ramp down ${draft.rampDownMinutes} min`)
  return changes
}

function segmentLabel(segment: TrajectorySegment): string {
  if (segment.shape === 'unavailable') return `${segment.metric} gap: ${segment.reason}`
  if (segment.shape === 'step') return `${segment.metric} ${segment.value} ${segment.unit}`
  return `${segment.metric} ${segment.start_value} to ${segment.end_value} ${segment.unit}`
}

function previewMessage(preview: TimelinePreviewState): string {
  switch (preview.kind) {
    case 'idle': return 'No review yet'
    case 'loading': return `Reviewing draft ${preview.draftRevision}`
    case 'ready': return `Reviewed draft ${preview.draftRevision}`
    case 'failed': return `Review failed for draft ${preview.draftRevision}`
    default: return assertNever(preview)
  }
}

function assertNever(value: never): never {
  throw new Error(`Unexpected timeline preview state: ${JSON.stringify(value)}`)
}

export function ControlTimeline({ mode, controller, onExpand, lockedPhotoperiodHours = null }: ControlTimelineProps) {
  const [dragTarget, setDragTarget] = useState<DragTarget | null>(null)
  const [editError, setEditError] = useState<string | null>(null)
  const [windowMode, setWindowMode] = useState<'daily' | 'rolling'>('rolling')
  const { state, preview } = controller
  const isExpanded = mode === 'expanded'
  const changes = useMemo(
    () => [...changedPeriods(state.saved.periods, state.draft.periods), ...changedPhotoperiod(state.saved.photoperiod, state.draft.photoperiod)],
    [state.saved, state.draft],
  )
  const segments = state.saved.trajectory?.segments ?? []

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
      className="absolute top-0 z-10 h-full w-2 cursor-ew-resize border-0 bg-accent-data/70 p-0 focus:outline-hidden focus:ring-2 focus:ring-accent-data"
      style={{ [edge]: '-0.25rem' }}
      onMouseDown={() => setDragTarget({ index, edge })}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') adjustBoundary(index, edge, -5)
        if (event.key === 'ArrowRight') adjustBoundary(index, edge, 5)
      }}
    />
  )

  return (
    <section className="flex h-full min-h-0 flex-col bg-surface-primary text-text-default" aria-label="Climate control timeline">
      <header className="flex flex-wrap items-start justify-between gap-1 border-b border-border-subtle px-2 py-1">
        <div>
          <div className="flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-text-muted">
            <span>Control timeline</span>
            <span className="border border-border-default px-1 text-[10px] text-accent-data">{isExpanded ? 'EDITABLE' : 'READ ONLY'}</span>
          </div>
          <p className="text-[10px] text-text-subtle">UTC window {state.saved.window?.start ?? 'rolling now'} → {state.saved.window?.end ?? 'next 24h'} · {state.saved.window?.timezone ?? 'UTC'}</p>
        </div>
        {!isExpanded && onExpand && (
          <button type="button" onClick={onExpand} className="border border-accent-data px-2 py-1 text-[10px] font-bold uppercase text-accent-data hover:bg-accent-data/10">
            Expand editor
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-auto p-2">
        <div className="flex flex-wrap items-center justify-between gap-1 text-[10px] text-text-muted">
          <div className="flex items-center gap-1" role="group" aria-label="Timeline window">
            {(['rolling', 'daily'] as const).map((value) => (
              <button key={value} type="button" onClick={() => setWindowMode(value)} className={`border px-1.5 py-0.5 uppercase ${windowMode === value ? 'border-accent-data text-accent-data' : 'border-border-default text-text-subtle'}`}>
                {value}
              </button>
            ))}
          </div>
          <span>{windowMode === 'rolling' ? 'Now + 24 elapsed hours' : 'Toronto calendar day'}</span>
        </div>

        <div data-testid="control-timeline-plot" className="relative min-h-20 border border-border-default bg-surface-base p-1">
          <div className="pointer-events-none absolute inset-x-1 top-1 flex justify-between text-[9px] text-text-subtle"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
          <div className="relative mt-4 h-12">
            {state.draft.periods.map((period, index) => {
              const position = periodWidth(period)
              return (
                <div key={`${period.period_name}-${index}`} className="absolute top-2 h-8 border border-accent-setpoint bg-accent-setpoint/20" style={{ left: `${position.left}%`, width: `${position.width}%` }} title={`${period.period_name}: ${period.start_time}–${period.end_time}`}>
                  <span className="pointer-events-none block truncate px-1 text-[10px] font-bold text-accent-setpoint" title={period.period_name}>{period.period_name}</span>
                  {isExpanded && <>{renderHandle(index, 'start')}{renderHandle(index, 'end')}</>}
                </div>
              )
            })}
            <div className="absolute inset-y-0 left-1/2 border-l border-dashed border-timeline-now" aria-label="Current time" />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-1 text-[10px] sm:grid-cols-3">
          <div className="border border-border-subtle bg-surface-secondary/40 p-1"><strong className="text-accent-setpoint">Scheduled</strong><span className="ml-1 text-text-subtle">draft values · period boundaries · ramps</span></div>
          <div className="border border-border-subtle bg-surface-secondary/40 p-1"><strong className="text-accent-data">Effective</strong><span className="ml-1 text-text-subtle">runtime-aware values</span></div>
          <div className="border border-border-subtle bg-surface-secondary/40 p-1"><strong className="text-text-muted">Photoperiod</strong><span className="ml-1 text-text-subtle">{state.draft.photoperiod.dayStartTime}–{state.draft.photoperiod.nightStartTime}</span></div>
        </div>

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

        <div className="grid grid-cols-1 gap-1 text-[10px] md:grid-cols-2">
          <div className="border border-border-subtle p-1"><span className="font-bold text-text-muted">Runtime effective</span><p className="text-status-warning-text">{segments.length > 0 ? segments.filter((segment) => segment.trajectory_kind === 'effective').map(segmentLabel).join(' · ') || 'No effective segment in this window.' : 'Unavailable gap: runtime observation is not available.'}</p></div>
          <div className="border border-border-subtle p-1"><span className="font-bold text-text-muted">Provenance and assumptions</span><p className="text-text-subtle">{state.saved.trajectory?.assumptions.join(' · ') || 'Saved schedule authority; no runtime assumptions reported.'}</p></div>
        </div>

        {isExpanded && (
          <div className="flex flex-wrap items-center gap-1 border-t border-border-subtle pt-1">
            <span className={`text-[10px] ${state.status.kind === 'conflict' ? 'text-status-warning-text' : 'text-text-subtle'}`}>{state.status.kind === 'conflict' ? 'Conflict: saved revision changed. Draft preserved.' : previewMessage(preview)}</span>
            <button type="button" onClick={() => void controller.review()} disabled={!changes.length || preview.kind === 'loading'} className="ml-auto border border-accent-data px-2 py-1 text-[10px] font-bold uppercase text-accent-data disabled:cursor-not-allowed disabled:opacity-40">Review</button>
            <button type="button" onClick={() => void controller.apply()} disabled={state.status.kind !== 'reviewed' || preview.kind !== 'ready'} className="border border-status-success px-2 py-1 text-[10px] font-bold uppercase text-status-success disabled:cursor-not-allowed disabled:opacity-40">Apply</button>
            <button type="button" onClick={controller.discard} disabled={!changes.length} className="border border-border-default px-2 py-1 text-[10px] font-bold uppercase text-text-muted disabled:cursor-not-allowed disabled:opacity-40">Discard</button>
          </div>
        )}
        {isExpanded && changes.length > 0 && <div className="border border-accent-data/50 bg-accent-data/5 p-1 text-[10px] text-text-secondary"><strong>Draft changes:</strong> {changes.join(' · ')}</div>}
        {editError && <p role="alert" className="text-[10px] text-status-danger-text">{editError}</p>}
      </div>
    </section>
  )
}
