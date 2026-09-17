import { useEffect, useMemo, useState } from 'react'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TimelinePhotoperiod } from '../state/timelineDraft'
import type { TimelineDraftController, TimelinePreviewState } from '../state/useTimelineDraft'
import { timeToMinutes, minutesToTime } from '../../../utils/timeMath'
import { sampleMetricSeries } from '../../../utils/climatePeriodTimeline'
import { TimelineWarningOverlay, timelineWarningLabel } from './TimelineWarningOverlay'

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

interface TimeSegment {
  readonly startMin: number
  readonly endMin: number
}

function buildSegments(startMin: number, endMin: number): TimeSegment[] {
  if (startMin === endMin) return []
  if (endMin > startMin) return [{ startMin, endMin }]
  return [
    { startMin, endMin: 1440 },
    { startMin: 0, endMin },
  ]
}

function buildPolylineSegments(series: readonly (number | null)[], vmin: number, vmax: number): string[] {
  const segments: string[][] = []
  let current: string[] = []
  const span = Math.max(vmax - vmin, 1e-6)
  for (let minute = 0; minute <= 1440; minute += 1) {
    const value = minute === 1440 ? series[0] : series[minute]
    if (value == null) {
      if (current.length > 0) {
        segments.push(current)
        current = []
      }
      continue
    }
    const x = (minute / 1440) * 100
    const y = 100 * (1 - (value - vmin) / span)
    current.push(`${x},${y}`)
  }
  if (current.length > 0) segments.push(current)
  return segments.map((points) => points.join(' '))
}

const SUN_COLOR = 'rgba(234, 179, 8, 0.45)'
const MOON_COLOR = 'rgba(168, 85, 247, 0.35)'
const HEAT_RANGE = { min: 15, max: 30 }
const VPD_RANGE = { min: 0.5, max: 2.0 }

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
  const trajectory = preview.kind === 'ready' ? preview.value : state.saved.trajectory
  const warnings = trajectory?.warnings ?? []
  const skippedWarning = warnings.find((warning) => warning.code === 'calendar.transition_skipped')
  const genericWarnings = warnings.filter((warning) => warning.code !== 'calendar.transition_skipped')

  const dayStartMin = timeToMinutes(state.draft.photoperiod.dayStartTime)
  const dayEndMin = timeToMinutes(state.draft.photoperiod.nightStartTime)
  const sunSegments = dayStartMin === dayEndMin ? [] : buildSegments(dayStartMin, dayEndMin)
  const moonSegments = dayStartMin === dayEndMin ? [{ startMin: 0, endMin: 1440 }] : buildSegments(dayEndMin, dayStartMin)
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes()
  const periods = useMemo(() => [...state.draft.periods], [state.draft.periods])
  const heatSeries = useMemo(() => sampleMetricSeries(periods, 'heating'), [periods])
  const coolSeries = useMemo(() => sampleMetricSeries(periods, 'cooling'), [periods])
  const vpdSeries = useMemo(() => sampleMetricSeries(periods, 'vpd'), [periods])

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
        {!isExpanded && onExpand && (
          <button type="button" onClick={onExpand} className="border border-accent-data px-2 py-1 text-[10px] font-bold uppercase text-accent-data hover:bg-accent-data/10">
            Expand editor
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-auto p-2">
        <div data-testid="control-timeline-plot" className="relative min-w-0 border border-border-default bg-surface-base p-1 pb-6">
          <div className="relative h-10">
            <div className="absolute inset-0 z-[1]">
              {moonSegments.map((segment, index) => (
                <div
                  key={`moon-${index}-${segment.startMin}-${segment.endMin}`}
                  className="absolute h-full"
                  style={{ left: `${(segment.startMin / 1440) * 100}%`, width: `${((segment.endMin - segment.startMin) / 1440) * 100}%`, backgroundColor: MOON_COLOR }}
                />
              ))}
              {sunSegments.map((segment, index) => (
                <div key={`sun-${index}-${segment.startMin}-${segment.endMin}`} className="absolute inset-y-0 z-[2]" style={{ left: `${(segment.startMin / 1440) * 100}%`, width: `${((segment.endMin - segment.startMin) / 1440) * 100}%`, backgroundColor: SUN_COLOR }}>
                  {state.draft.photoperiod.rampUpMinutes > 0 && (
                    <div className="absolute left-0 inset-y-0 pointer-events-none" style={{ width: `${(state.draft.photoperiod.rampUpMinutes / 1440) * 100}%`, background: 'linear-gradient(to right, rgba(234,179,8,0), rgba(234,179,8,0.45))' }} />
                  )}
                  {state.draft.photoperiod.rampDownMinutes > 0 && (
                    <div className="absolute right-0 inset-y-0 pointer-events-none" style={{ width: `${(state.draft.photoperiod.rampDownMinutes / 1440) * 100}%`, background: 'linear-gradient(to left, rgba(234,179,8,0), rgba(234,179,8,0.45))' }} />
                  )}
                </div>
              ))}
            </div>
            <span className="absolute top-0.5 left-1 z-[6] text-[8px] font-mono text-text-subtle uppercase">photoperiod</span>
          </div>

          <div className="relative mt-1 h-14 border-t border-border-default" data-testid="control-timeline-scheduled">
            <div className="absolute inset-x-0 top-0 bottom-0 z-[1]">
              {state.draft.periods.map((period, index) => {
                const position = periodWidth(period)
                return (
                  <div
                    key={`${period.period_name}-${period.start_time}-${period.end_time}`}
                    className="absolute top-1 bottom-0 border border-accent-setpoint bg-accent-setpoint/20"
                    style={{ left: `${position.left}%`, width: `${position.width}%` }}
                    title={`${period.period_name}: ${period.start_time}–${period.end_time}`}
                  >
                    <span className="pointer-events-none block truncate px-1 text-[10px] font-bold text-accent-setpoint" title={period.period_name}>{period.period_name}</span>
                    {isExpanded && <>{renderHandle(index, 'start')}{renderHandle(index, 'end')}</>}
                  </div>
                )
              })}
            </div>
            <div className="pointer-events-none absolute inset-0 z-[5]">
              <svg className="h-full w-full overflow-visible" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
                {buildPolylineSegments(heatSeries, HEAT_RANGE.min, HEAT_RANGE.max).map((points, index) => (
                  <polyline key={`heat-${index}`} fill="none" stroke="rgb(234 88 12)" strokeWidth={0.9} vectorEffect="non-scaling-stroke" points={points} />
                ))}
                {buildPolylineSegments(coolSeries, HEAT_RANGE.min, HEAT_RANGE.max).map((points, index) => (
                  <polyline key={`cool-${index}`} fill="none" stroke="rgb(59 130 246)" strokeWidth={0.9} vectorEffect="non-scaling-stroke" points={points} />
                ))}
                {buildPolylineSegments(vpdSeries, VPD_RANGE.min, VPD_RANGE.max).map((points, index) => (
                  <polyline key={`vpd-${index}`} fill="none" stroke="rgb(34 197 94)" strokeWidth={0.9} vectorEffect="non-scaling-stroke" points={points} />
                ))}
              </svg>
            </div>
            <div className="pointer-events-none absolute inset-0 z-[7] flex items-start justify-end gap-2 pr-1 pt-0.5">
              <span className="text-[8px] font-mono text-orange-600">heat</span>
              <span className="text-[8px] font-mono text-blue-500">cool</span>
              <span className="text-[8px] font-mono text-green-500">VPD</span>
            </div>
            {skippedWarning !== undefined && trajectory !== undefined && <TimelineWarningOverlay warning={skippedWarning} window={trajectory.window} />}
            <div className="absolute inset-y-0 z-10 w-0.5 bg-status-danger-vivid" aria-label="Current time" style={{ left: `${(nowMin / 1440) * 100}%` }} />
          </div>

          <div className="relative mt-2 h-4">
            {Array.from({ length: 13 }).map((_, index) => {
              const hour = index * 2
              return (
                <div key={`label-${index}`} className="absolute text-[10px] text-text-subtle font-medium font-mono tabular-nums" style={{ left: `${(hour / 24) * 100}%`, transform: 'translateX(-50%)' }}>
                  {String(hour).padStart(2, '0')}
                </div>
              )
            })}
          </div>

          {genericWarnings.map((warning, index) => (
            <p key={`timeline-generic-warning-${index}`} className="text-[10px] text-status-warning-text">
              {timelineWarningLabel(warning)}
            </p>
          ))}
          {genericWarnings.length === 0 && !skippedWarning && (
            <p className="text-[10px] text-text-subtle">Saved schedule authority; no runtime assumptions reported.</p>
          )}
          {editError && <p role="alert" className="text-[10px] text-status-danger-text">{editError}</p>}
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
