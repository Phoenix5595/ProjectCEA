import { useRef, useEffect } from 'react';
import type { DayButtonProps } from 'react-day-picker';

import {
  MARKER_LIMITS,
  noteMarkerLabel,
  truncateMarkerLabel,
} from '../../utils/calendarDayMarkers';
import { useCalendarMarkers } from './CalendarMarkersContext';

export function CalendarDayButton({
  day,
  modifiers,
  className = '',
  children,
  ...buttonProps
}: DayButtonProps) {
  const ref = useRef<HTMLButtonElement>(null);
  const markers = useCalendarMarkers(day.date);

  useEffect(() => {
    if (modifiers.focused) {
      ref.current?.focus();
    }
  }, [modifiers.focused]);

  const tasks = markers.tasks.slice(0, MARKER_LIMITS.tasks);
  const notes = markers.notes.slice(0, MARKER_LIMITS.notes);
  const bars = markers.scheduleBars.slice(0, MARKER_LIMITS.scheduleBars);
  const extraTasks = markers.tasks.length - tasks.length;
  const extraNotes = markers.notes.length - notes.length;
  const extraBars = markers.scheduleBars.length - bars.length;
  const hasMarkers = tasks.length + notes.length + bars.length > 0;

  return (
    <button
      ref={ref}
      type="button"
      {...buttonProps}
      className={[
        'grow-cal-day-btn',
        className,
        modifiers.today ? 'grow-cal-day-btn--today' : '',
        modifiers.selected ? 'grow-cal-day-btn--selected' : '',
        modifiers.outside ? 'grow-cal-day-btn--outside' : '',
        hasMarkers ? 'grow-cal-day-btn--marked' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <span className="grow-cal-day-num">{children}</span>

      {(tasks.length > 0 || notes.length > 0 || bars.length > 0) && (
        <span className="grow-cal-day-markers">
          {tasks.map((ev) => (
            <span key={ev.id} className="grow-cal-marker grow-cal-marker--task" title={ev.title}>
              <span className="grow-cal-marker-dot grow-cal-marker-dot--task" aria-hidden />
              <span className="grow-cal-marker-text">{truncateMarkerLabel(ev.title)}</span>
            </span>
          ))}
          {extraTasks > 0 && <span className="grow-cal-more">+{extraTasks} tasks</span>}

          {notes.map((ev) => {
            const label = noteMarkerLabel(ev);
            return (
              <span
                key={`note-${ev.id}`}
                className="grow-cal-marker grow-cal-marker--note"
                title={ev.notes?.trim() || ev.title}
              >
                <span className="grow-cal-marker-dot grow-cal-marker-dot--note" aria-hidden />
                <span className="grow-cal-marker-text">{label}</span>
              </span>
            );
          })}
          {extraNotes > 0 && <span className="grow-cal-more">+{extraNotes} notes</span>}

          {bars.map((bar) => (
            <span
              key={`${bar.eventId}-${bar.location}`}
              className={`grow-cal-phase ${bar.colorClass}`}
              title={`${bar.location}: ${bar.title}`}
            >
              <span className="grow-cal-phase-text">{truncateMarkerLabel(bar.title)}</span>
            </span>
          ))}
          {extraBars > 0 && <span className="grow-cal-more">+{extraBars} phases</span>}
        </span>
      )}
    </button>
  );
}
