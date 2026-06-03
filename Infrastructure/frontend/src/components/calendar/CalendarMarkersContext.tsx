import { createContext, useContext } from 'react';
import { format } from 'date-fns';
import { toZonedTime } from 'date-fns-tz';

import type { DayMarkers } from '../../utils/calendarDayMarkers';
import { CALENDAR_TZ } from '../../utils/flowerGrowPlan';

export const CalendarMarkersContext = createContext<Map<string, DayMarkers>>(new Map());

export function useCalendarMarkers(day: Date): DayMarkers {
  const map = useContext(CalendarMarkersContext);
  const key = format(toZonedTime(day, CALENDAR_TZ), 'yyyy-MM-dd');
  return (
    map.get(key) ?? {
      tasks: [],
      notes: [],
      scheduleBars: [],
    }
  );
}
