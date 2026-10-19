import { useState } from 'react'
import { format } from 'date-fns'
import { fr } from 'date-fns/locale'

import type { CalendarEventDto } from '../../types/calendar'
import { EVENT_TYPE_COLORS } from '../../types/calendar'

interface CalendarDayDetailProps {
  date: Date
  events: CalendarEventDto[]
  onClose: () => void
}

const EVENTS_PER_PAGE = 3

export default function CalendarDayDetail({ date, events, onClose }: CalendarDayDetailProps) {
  const [page, setPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(events.length / EVENTS_PER_PAGE))
  const shown = events.slice(page * EVENTS_PER_PAGE, (page + 1) * EVENTS_PER_PAGE)
  return (
    <div className="grow-cal-day-detail">
      <div className="flex justify-between items-center gap-0 mb-2 pb-2 border-b border-border-emphasis">
        <span className="font-bold text-text-default">{format(date, 'EEEE d MMMM yyyy', { locale: fr })}</span>
        <button type="button" onClick={onClose} className="grow-cal-detail-close" aria-label="Close day detail">×</button>
      </div>
      {events.length === 0 ? <p className="text-sm text-text-muted">No events on this day</p> : (
        <>
          <ul className="grow-cal-detail-events">
            {shown.map(ev => {
              const sync = ev.sync_status ? ` · ${ev.sync_error_code ?? ev.sync_status}` : ''
              const range = ev.end && ev.end !== ev.start ? `${ev.start} → ${ev.end}` : ev.start
              return (
                <li key={ev.id} className="grow-cal-detail-event">
                  <span className={`grow-cal-detail-accent ${EVENT_TYPE_COLORS[ev.eventType] ?? 'bg-slate-500'}`} aria-hidden="true" />
                  <span className="min-w-0">
                    <span className="block font-semibold text-text-default">{ev.title}</span>
                    <span className="block text-xs text-text-muted">{ev.location} · {range}{sync}</span>
                    {ev.notes?.trim() && <span className="block text-xs text-text-secondary">{ev.notes.trim()}</span>}
                  </span>
                </li>
              )
            })}
          </ul>
          {pageCount > 1 && (
            <nav className="grow-cal-detail-pagination" aria-label="Events on selected day">
              <button type="button" onClick={() => setPage(current => Math.max(0, current - 1))} disabled={page === 0}>Previous</button>
              <span aria-live="polite">{page + 1} / {pageCount} · {events.length} events</span>
              <button type="button" onClick={() => setPage(current => Math.min(pageCount - 1, current + 1))} disabled={page + 1 === pageCount}>Next</button>
            </nav>
          )}
        </>
      )}
    </div>
  )
}
