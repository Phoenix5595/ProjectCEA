import type { DayButtonProps } from '@daypicker/react'
import { format, subDays, addDays } from 'date-fns'
import { fr } from 'date-fns/locale'
import { useEffect, useRef } from 'react'
import type { CalendarEventDto } from '../../types/calendar'
import { EVENT_TYPE_COLORS } from '../../types/calendar'
import { calendarEventOccursOnDay, isGrowPlanPhase } from '../../utils/calendarDayMarkers'

import { useCalendarMarkers } from './CalendarMarkersContext'

interface CalendarDayButtonProps extends DayButtonProps {
  maxVisibleEvents?: 1 | 3
  showRoomLabels?: boolean
}

const ROOM_PREFIX: Record<string, string> = { 'Flower Room': 'F', 'Veg Room': 'V', Lab: 'L' }
const SYNC_LABEL: Record<string, string> = {
  pending_push: 'Saved locally; awaiting sync', pending_delete: 'Delete awaiting sync',
  synced: 'Synced', error: 'Sync error', conflict: 'Sync conflict',
}

function dayPhasePosition(ev: CalendarEventDto, day: Date): string {
  const coversBefore = calendarEventOccursOnDay(ev, subDays(day, 1))
  const coversAfter = calendarEventOccursOnDay(ev, addDays(day, 1))
  if (!coversBefore && !coversAfter) return 'single'
  if (!coversBefore) return 'start'
  if (!coversAfter) return 'end'
  return 'middle'
}

export function CalendarDayButton({
  day, modifiers, className = '', children, maxVisibleEvents = 3,
  showRoomLabels = false, ...buttonProps
}: CalendarDayButtonProps) {
  const ref = useRef<HTMLButtonElement>(null)
  const markers = useCalendarMarkers(day.date)
  useEffect(() => {
    if (modifiers.focused) ref.current?.focus()
  }, [modifiers.focused])

  const visible = markers.events.slice(0, maxVisibleEvents)
  const hiddenCount = markers.events.length - visible.length
  const dateName = buttonProps['aria-label'] ?? format(day.date, 'PPP', { locale: fr })
  const accessibleLabel = [dateName, ...markers.events.map(ev => {
    const dates = ev.end && ev.end !== ev.start ? `${ev.start} through ${ev.end}` : ev.start
    const note = ev.notes?.trim() ? `Notes: ${ev.notes.trim()}` : ''
    const sync = ev.sync_status ? `${SYNC_LABEL[ev.sync_status] ?? ev.sync_status}${ev.sync_error_code ? ` (${ev.sync_error_code})` : ''}` : ''
    return [showRoomLabels ? `${ROOM_PREFIX[ev.location] ?? ''} ${ev.location}` : ev.location, ev.title, dates, note, sync].filter(Boolean).join(', ')
  }), hiddenCount > 0 ? `${hiddenCount} more events` : ''].filter(Boolean).join('. ')

  return (
    <button
      ref={ref} type="button" {...buttonProps}
      className={['grow-cal-day-btn', className,
        modifiers.today ? 'grow-cal-day-btn--today' : '',
        modifiers.selected ? 'grow-cal-day-btn--selected' : '',
        modifiers.outside ? 'grow-cal-day-btn--outside' : '',
        markers.events.length ? 'grow-cal-day-btn--marked' : '',
      ].filter(Boolean).join(' ')}
      aria-label={accessibleLabel}
      title={markers.events.map(ev => `${ev.title} · ${showRoomLabels ? `${ROOM_PREFIX[ev.location] ?? ''} ` : ''}${ev.location} · ${ev.start}${ev.end && ev.end !== ev.start ? ` through ${ev.end}` : ''}${ev.notes?.trim() ? ` · Notes: ${ev.notes.trim()}` : ''}${ev.sync_status ? ` · ${SYNC_LABEL[ev.sync_status] ?? ev.sync_status}${ev.sync_error_code ? ` (${ev.sync_error_code})` : ''}` : ''}`).join('\n')}
    >
      <span className="grow-cal-day-num">
        <span>{children}</span>
        {hiddenCount > 0 && <span className="grow-cal-more" aria-hidden="true">+{hiddenCount}</span>}
      </span>
      {visible.length > 0 && (
        <span className="grow-cal-day-markers" aria-hidden="true">
          {visible.map(ev => {
            const prefix = ROOM_PREFIX[ev.location]
            const status = ev.sync_status ? SYNC_LABEL[ev.sync_status] ?? ev.sync_status : ''
            const phase = isGrowPlanPhase(ev)
            const position = dayPhasePosition(ev, day.date)
            const weekEdge = day.date.getDay() === 0 ? ' grow-cal-phase--week-start' : day.date.getDay() === 6 ? ' grow-cal-phase--week-end' : ''
            return (
              <span key={ev.id}
                className={`grow-cal-event-chip ${phase ? `grow-cal-event-chip--phase grow-cal-phase--${position}${weekEdge}` : ''} ${status ? `grow-cal-event-chip--${ev.sync_status}` : ''}`}
                title={`${ev.title} · ${ev.location} · ${ev.start}${ev.end && ev.end !== ev.start ? ` through ${ev.end}` : ''}${ev.notes?.trim() ? ` · Notes: ${ev.notes.trim()}` : ''}${status ? ` · ${status}${ev.sync_error_code ? ` (${ev.sync_error_code})` : ''}` : ''}`}
              >
                <span className={`grow-cal-event-accent ${EVENT_TYPE_COLORS[ev.eventType] ?? 'bg-slate-500'}`} />
                {showRoomLabels && <span className="grow-cal-room-prefix">{prefix ?? ev.location}</span>}
                <span className="grow-cal-event-title">{ev.title}</span>
                {ev.notes?.trim() && <span className="grow-cal-event-indicator" title="Has notes" aria-label="Has notes">✎</span>}
                {status && <span className="grow-cal-event-sync" title={status}>{ev.sync_status === 'conflict' ? '!' : ev.sync_status === 'error' ? '×' : ev.sync_status === 'synced' ? '✓' : '…'}</span>}
              </span>
            )
          })}
        </span>
      )}
    </button>
  )
}
