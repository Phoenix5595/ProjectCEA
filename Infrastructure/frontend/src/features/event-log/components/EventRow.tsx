import { useState, useCallback, type KeyboardEvent } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import { sourcePartsFor } from '../presentation/eventSourceParts'
import { formatLocalTime } from '../presentation/timeFormat'
import { EventDetails } from './EventDetails'
import { EventSourceLine, EventTimestamps } from './EventFragments'

interface EventRowProps {
  entry: EventLogEntry
  now: Date
  formatAbsolute?: (date: Date, now: Date) => string
}

const SEVERITY_VISUAL: Record<SeverityLevel, string> = {
  critical: 'border-l-status-danger-vivid border-l-4 bg-status-danger-bg/20',
  warning: 'border-l-status-warning bg-status-warning-bg/20',
  error: 'border-l-status-danger bg-surface-secondary',
  info: 'border-l-border-emphasis bg-surface-secondary',
}

const SEVERITY_BADGE: Record<SeverityLevel, string> = {
  critical: 'bg-status-danger-vivid text-status-danger-text border-status-danger-vivid font-bold',
  warning: 'bg-status-warning-bg text-status-warning-text border-status-warning-dim',
  error: 'bg-surface-secondary text-status-danger-text border-status-danger-border',
  info: 'bg-surface-tertiary text-text-default border-border-default',
}

export function EventRow({ entry, now, formatAbsolute = formatLocalTime }: EventRowProps) {
  const [expanded, setExpanded] = useState(false)
  const display = getEventDisplay(entry.type)
  const severity = entry.severity
  const toggle = useCallback(() => setExpanded((prev) => !prev), [])
  const sourceParts = sourcePartsFor(entry)

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
    <li role="listitem" className={`border-l-2 ${SEVERITY_VISUAL[severity]}`}>
      <div className="flex items-start gap-2 px-3 py-2">
        <span
          className={`shrink-0 inline-flex items-center px-1.5 py-0.5 text-10 font-bold uppercase tracking-wider border ${SEVERITY_BADGE[severity]}`}
          aria-label={`Severity: ${SEVERITY_LABELS[severity]}`}
        >
          {SEVERITY_LABELS[severity]}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm text-text-default font-semibold truncate">{display.label}</span>
            <EventTimestamps occurredAt={entry.occurredAt} now={now} formatAbsolute={formatAbsolute} />
          </div>
          <div className="text-11 text-text-default font-mono truncate">{entry.type}</div>
          {sourceParts.length > 0 && <EventSourceLine parts={sourceParts} />}
          {entry.reasonText !== null && (
            <div className="text-11 text-text-default italic truncate" title={entry.reasonText}>
              {entry.reasonText}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={toggle}
          onKeyDown={handleKeyDown}
          aria-expanded={expanded}
          aria-label="Toggle details"
          className="shrink-0 size-6 flex items-center justify-center text-text-default hover:text-text-default border border-border-subtle hover:border-border-default bg-surface-secondary transition-colors"
        >
          <span aria-hidden="true" className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>&#9654;</span>
        </button>
      </div>
      <EventDetails payload={entry.payload} expanded={expanded} />
    </li>
  )
}
