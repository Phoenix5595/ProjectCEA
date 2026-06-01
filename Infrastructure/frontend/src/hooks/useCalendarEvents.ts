import { useCallback, useEffect, useMemo, useState } from 'react';
import { addMonths, endOfMonth, format, startOfMonth, subMonths } from 'date-fns';
import { toZonedTime } from 'date-fns-tz';

import { apiClient } from '../services/api';
import type { CalendarEventDto } from '../types/calendar';
import { CALENDAR_TZ } from '../utils/flowerGrowPlan';
import { normalizeCalendarEvent } from '../utils/normalizeCalendarEvent';

export function useCalendarEvents(location?: string, month?: Date) {
  const [events, setEvents] = useState<CalendarEventDto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Stable month reference — use getTime() for dependency arrays (value equality, not reference)
  const safeMonth = useMemo(() => month ?? new Date(), [month?.getTime()]);

  const zonedMonth = toZonedTime(safeMonth, CALENDAR_TZ);

  const rangeStart = useMemo(
    () => format(startOfMonth(subMonths(zonedMonth, 1)), 'yyyy-MM-dd'),
    [zonedMonth.getTime()]
  );

  const rangeEnd = useMemo(
    () => format(endOfMonth(addMonths(zonedMonth, 1)), 'yyyy-MM-dd'),
    [zonedMonth.getTime()]
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let cursor: string | undefined;
      const all: CalendarEventDto[] = [];
      do {
        const res = await apiClient.getCalendarEvents(rangeStart, rangeEnd, location, cursor);
        all.push(
          ...res.items.map((item) =>
            normalizeCalendarEvent(item as unknown as Record<string, unknown>)
          )
        );
        cursor = res.next_cursor ?? undefined;
      } while (cursor);
      setEvents(all);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load calendar');
    } finally {
      setLoading(false);
    }
  }, [rangeStart, rangeEnd, location]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { events, loading, error, refresh };
}
