import type { CalendarEventDto } from '../types/calendar';

/** Normalize API row (snake_case or camelCase) to CalendarEventDto. */
export function normalizeCalendarEvent(raw: Record<string, unknown>): CalendarEventDto {
  const start = String(raw.start ?? raw.start_date ?? '');
  const endRaw = raw.end ?? raw.end_date;
  return {
    id: String(raw.id ?? ''),
    source: (raw.source as CalendarEventDto['source']) ?? 'manual',
    eventType: String(raw.eventType ?? raw.event_type ?? 'planned_task'),
    title: String(raw.title ?? ''),
    start,
    end: endRaw != null ? String(endRaw) : undefined,
    location: String(raw.location ?? ''),
    cluster: raw.cluster != null ? String(raw.cluster) : undefined,
    editable: Boolean(raw.editable ?? raw.deleted_at == null),
    colorKey: String(raw.colorKey ?? raw.event_type ?? 'planned_task'),
    numericId: typeof raw.numericId === 'number' ? raw.numericId : (raw.id as number | undefined),
    notes: raw.notes != null && String(raw.notes).trim() ? String(raw.notes) : undefined,
    metadata:
      raw.metadata && typeof raw.metadata === 'object'
        ? (raw.metadata as Record<string, unknown>)
        : undefined,
  };
}
