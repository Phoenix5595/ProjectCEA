import type { TimelineWarning } from '../api/contracts'

type WarningWindow = {
  readonly start: Date
  readonly end: Date
}

function warningPosition(
  warning: TimelineWarning,
  window: WarningWindow,
): { readonly left: number; readonly width: number } | null {
  if (warning.start === undefined || warning.end === undefined) return null
  const duration = window.end.getTime() - window.start.getTime()
  const left = ((warning.start.getTime() - window.start.getTime()) / duration) * 100
  const right = ((warning.end.getTime() - window.start.getTime()) / duration) * 100
  const boundedLeft = Math.max(0, Math.min(100, left))
  const boundedRight = Math.max(0, Math.min(100, right))
  return boundedRight <= boundedLeft
    ? null
    : { left: boundedLeft, width: boundedRight - boundedLeft }
}

export function timelineWarningLabel(warning: TimelineWarning): string {
  return warning.code === 'calendar.transition_skipped'
    ? `Calendar transition skipped: ${warning.reason ?? warning.detail}`
    : `Timeline warning: ${warning.detail}`
}

export function TimelineWarningOverlay({
  warning,
  window,
}: {
  readonly warning: TimelineWarning
  readonly window: WarningWindow
}) {
  const position = warningPosition(warning, window)
  if (position === null) return null

  return (
    <div
      data-testid="calendar-transition-skipped-overlay"
      role="img"
      aria-label={timelineWarningLabel(warning)}
      className="pointer-events-none absolute inset-y-0 z-20 overflow-hidden border border-dashed border-status-danger bg-status-danger-bg/30 text-status-danger-text"
      style={{ left: `${position.left}%`, width: `${position.width}%` }}
    >
      <span className="block truncate px-1 text-[9px] font-bold uppercase">Skipped transition</span>
    </div>
  )
}
