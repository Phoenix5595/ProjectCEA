import { useMemo, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import { fr } from 'react-day-picker/locale';
import { format, parseISO } from 'date-fns';
import { fr as dateFnsFr } from 'date-fns/locale';
import { toZonedTime } from 'date-fns-tz';

import type { CalendarEventDto } from '../../types/calendar';
import { ROOM_CALENDAR_COLORS } from '../../types/calendar';
import { buildCalendarDayMarkers } from '../../utils/calendarDayMarkers';
import { CALENDAR_TZ } from '../../utils/flowerGrowPlan';
import { CalendarDayButton } from './CalendarDayButton';
import { CalendarMarkersContext } from './CalendarMarkersContext';
import CalendarDayDetail from './CalendarDayDetail';

import 'react-day-picker/style.css';

export interface GrowCalendarProps {
  variant: 'compact' | 'full';
  viewMode?: 'unified' | 'room';
  location?: string;
  events: CalendarEventDto[];
  loading?: boolean;
  onMonthChange?: (month: Date) => void;
  onRefresh?: () => void;
  showAddTask?: boolean;
  onAddTask?: () => void;
  /** Scale month grid to fill parent width (e.g. dashboard column up to 50%). */
  fillWidth?: boolean;
}

function eventOnDay(ev: CalendarEventDto, day: Date): boolean {
  const start = parseISO(ev.start);
  const end = parseISO(ev.end ?? ev.start);
  const d = toZonedTime(day, CALENDAR_TZ);
  return d >= toZonedTime(start, CALENDAR_TZ) && d <= toZonedTime(end, CALENDAR_TZ);
}

export default function GrowCalendar({
  variant,
  viewMode = 'unified',
  location,
  events,
  loading,
  onMonthChange,
  onRefresh,
  showAddTask,
  onAddTask,
  fillWidth = false,
}: GrowCalendarProps) {
  const [month, setMonth] = useState(() => toZonedTime(new Date(), CALENDAR_TZ));
  const [selected, setSelected] = useState<Date | undefined>();

  const filtered = useMemo(() => {
    if (viewMode === 'room' && location) {
      return events.filter((e) => e.location === location);
    }
    return events;
  }, [events, viewMode, location]);

  const dayMarkers = useMemo(() => buildCalendarDayMarkers(filtered), [filtered]);

  const dayEvents = selected ? filtered.filter((e) => eventOnDay(e, selected)) : [];
  const compact = variant === 'compact';
  const scaled = compact && fillWidth;

  const weekdayFormat = compact ? 'EEE' : 'cccc';

  const calendarFormatters = useMemo(
    () => ({
      formatWeekdayName: (weekday: Date) => format(weekday, weekdayFormat, { locale: dateFnsFr }),
    }),
    [weekdayFormat]
  );

  return (
    <div
      className={`grow-calendar flex flex-col gap-0 ${
        scaled ? 'grow-calendar--fill w-full min-h-0' : compact ? 'max-h-[620px]' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-0 px-2 pt-2 pb-1 shrink-0">
        <h2
          className={`font-bold tracking-tight text-text-default ${scaled ? 'text-base' : compact ? 'text-sm' : 'text-lg'}`}
        >
          Grow calendar
        </h2>
        <div className="flex gap-0">
          {showAddTask && onAddTask && (
            <button type="button" onClick={onAddTask} className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary">
              Add task
            </button>
          )}
          {onRefresh && (
            <button type="button" onClick={onRefresh} className="grow-cal-toolbar-btn">
              Refresh
            </button>
          )}
        </div>
      </div>

      <div className="grow-cal-legend" role="list" aria-label="Calendar legend">
        <span className="grow-cal-legend-item" role="listitem">
          <span className="grow-cal-marker-dot grow-cal-marker-dot--task" aria-hidden />
          Task
        </span>
        <span className="grow-cal-legend-item" role="listitem">
          <span className="grow-cal-marker-dot grow-cal-marker-dot--note" aria-hidden />
          Note
        </span>
        <span className="grow-cal-legend-item" role="listitem">
          <span className="grow-cal-phase bg-amber-500 grow-cal-legend-phase">
            <span className="grow-cal-phase-text">Phase</span>
          </span>
        </span>
        {viewMode === 'unified' &&
          Object.entries(ROOM_CALENDAR_COLORS).map(([room, cls]) => (
            <span key={room} className="grow-cal-legend-item" role="listitem">
              <span className={`grow-cal-phase ${cls} grow-cal-legend-phase`}>
                <span className="grow-cal-phase-text">{room.replace(' Room', '')}</span>
              </span>
            </span>
          ))}
      </div>

      {loading ? (
        <p className="text-sm text-text-secondary py-8 text-center grow-cal-panel">Loading calendar…</p>
      ) : (
        <CalendarMarkersContext.Provider value={dayMarkers}>
          <div className="grow-cal-panel min-h-0 flex-1 flex flex-col">
            <DayPicker
              mode="single"
              locale={fr}
              weekStartsOn={0}
              showOutsideDays
              formatters={calendarFormatters}
              selected={selected}
              onSelect={setSelected}
              month={month}
              onMonthChange={(m) => {
                setMonth(m);
                onMonthChange?.(m);
              }}
              components={{ DayButton: CalendarDayButton }}
              className={`${scaled ? 'text-sm' : compact ? 'text-xs' : 'text-sm'} w-full grow-calendar-picker`}
            />
          </div>
        </CalendarMarkersContext.Provider>
      )}

      {selected && (
        <CalendarDayDetail
          date={selected}
          events={dayEvents}
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}
