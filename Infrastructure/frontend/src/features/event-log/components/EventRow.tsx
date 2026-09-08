import { useState, useCallback, type KeyboardEvent } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { SEVERITY_LABELS } from '../presentation/severity'
import { formatRelativeTime, formatExactTime } from '../presentation/timeFormat'
import { EventDetails } from './EventDetails'

interface EventRowProps {
  entry: EventLogEntry
  now: Date
}

const SEVERITY_VISUAL: Record<string, string> = {
  critical: 'border-l-status-danger bg-status-danger-bg/20',
  warning: 'border-l-status-warning bg-status-warning-bg/20',
  info: 'border-l-border-emphasis bg-surface-secondary',
}

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-status-danger-bg text-status-danger-text border-status-danger-border',
  warning: 'bg-status-warning-bg text-status-warning-text border-status-warning-dim',
  info: 'bg-surface-tertiary text-text-default border-border-default',
}

export function EventRow({ entry, now }: EventRowProps) {
  const [expanded, setExpanded] = useState(false)
  const display = getEventDisplay(entry.type)
  const toggle = useCallback(() => setExpanded((prev) => !prev), [])

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        toggle()
      }
    },
    [toggle],
  )

  return (
    <li role="listitem" className={`border-l-2 ${SEVERITY_VISUAL[display.severity] ?? SEVERITY_VISUAL.info}`}>
      <div className="flex items-start gap-2 px-3 py-2">
        <span
          className={`shrink-0 inline-flex items-center px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider border ${SEVERITY_BADGE[display.severity] ?? SEVERITY_BADGE.info}`}
          aria-label={`Severity: ${SEVERITY_LABELS[display.severity]}`}
        >
          {SEVERITY_LABELS[display.severity]}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm text-text-default font-semibold truncate">{display.label}</span>
            <time
              dateTime={entry.occurredAt.toISOString()}
              title={formatExactTime(entry.occurredAt)}
              className="shrink-0 text-[11px] text-text-default tabular-nums"
            >
              {formatRelativeTime(entry.occurredAt, now)}
            </time>
          </div>
          <div className="text-[11px] text-text-default font-mono truncate">{entry.type}</div>
        </div>
        <button
          type="button"
          onClick={toggle}
          onKeyDown={handleKeyDown}
          aria-expanded={expanded}
          aria-label="Toggle details"
          className="shrink-0 w-6 h-6 flex items-center justify-center text-text-default hover:text-text-default border border-border-subtle hover:border-border-default bg-surface-secondary transition-colors"
        >
          <span aria-hidden="true" className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>&#9654;</span>
        </button>
      </div>
      <EventDetails payload={entry.payload} expanded={expanded} />
    </li>
  )
}
