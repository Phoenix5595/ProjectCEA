import { format } from 'date-fns';
import { fr } from 'date-fns/locale';

import type { CalendarEventDto } from '../../types/calendar';
import { ROOM_CALENDAR_COLORS } from '../../types/calendar';
import { truncateMarkerLabel } from '../../utils/calendarDayMarkers';

interface CalendarDayDetailProps {
  date: Date;
  events: CalendarEventDto[];
  onClose: () => void;
}

export default function CalendarDayDetail({ date, events, onClose }: CalendarDayDetailProps) {
  return (
    <div className="grow-cal-day-detail">
      <div className="flex justify-between items-center gap-0 mb-2 pb-2 border-b border-border-emphasis">
        <span className="font-bold text-text-default">
          {format(date, 'EEEE d MMMM yyyy', { locale: fr })}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="grow-cal-detail-close"
          aria-label="Close day detail"
        >
          ×
        </button>
      </div>
      {events.length === 0 ? (
        <p className="text-sm text-text-muted">No events on this day</p>
      ) : (
        <ul className="space-y-1.5 max-h-44 overflow-y-auto">
          {events.map((ev) => {
            const roomClass = ROOM_CALENDAR_COLORS[ev.location] ?? 'bg-slate-500';
            return (
              <li
                key={ev.id}
                className="flex items-start gap-0 text-sm bg-surface-base border border-border-default rounded-sm px-2 py-1.5"
              >
                <span className={`grow-cal-bar ${roomClass} shrink-0 mt-0.5 max-w-[5rem]`}>
                  <span className="grow-cal-bar-text">{truncateMarkerLabel(ev.title, 12)}</span>
                </span>
                <span className="min-w-0">
                  <span className="block font-semibold text-text-default">{ev.title}</span>
                  <span className="block text-xs text-text-muted mt-0.5">
                    {ev.location}
                    {ev.end && ev.end !== ev.start ? ` · ${ev.start} → ${ev.end}` : ''}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
