import { eachDayOfInterval, format, parseISO } from 'date-fns';
import { toZonedTime } from 'date-fns-tz';

import type { CalendarEventDto } from '../types/calendar';
import { ROOM_CALENDAR_COLORS } from '../types/calendar';
import { CALENDAR_TZ } from './flowerGrowPlan';

export interface ScheduleBarMarker {
  eventId: string;
  location: string;
  colorClass: string;
  title: string;
}

export interface DayMarkers {
  tasks: CalendarEventDto[];
  notes: CalendarEventDto[];
  scheduleBars: ScheduleBarMarker[];
}

const GROW_PHASE_TYPES = new Set([
  'clone_window',
  'pot_veg',
  'bed_veg',
  'flip_to_flower',
  'flower_stretch',
  'flower_bulk',
  'flower_ripen',
  'drying',
]);

function dayKey(d: Date): string {
  return format(toZonedTime(d, CALENDAR_TZ), 'yyyy-MM-dd');
}

function parseEventDate(iso: string): Date {
  return toZonedTime(parseISO(iso.length === 10 ? `${iso}T12:00:00` : iso), CALENDAR_TZ);
}

function isGrowPlanPhase(ev: CalendarEventDto): boolean {
  if (ev.metadata?.grow_plan_id) return true;
  return GROW_PHASE_TYPES.has(ev.eventType);
}

function isMultiDaySchedule(ev: CalendarEventDto): boolean {
  const end = ev.end ?? ev.start;
  if (end > ev.start) return true;
  return isGrowPlanPhase(ev);
}

function isTaskMarker(ev: CalendarEventDto): boolean {
  if (ev.eventType === 'planned_task') return true;
  if (ev.source === 'mode_transition') return true;
  if (isMultiDaySchedule(ev)) return false;
  if (ev.eventType === 'harvest') return true;
  return !ev.end || ev.end === ev.start;
}

function hasNotes(ev: CalendarEventDto): boolean {
  return Boolean(ev.notes?.trim());
}

function scheduleColor(location: string): string {
  return ROOM_CALENDAR_COLORS[location] ?? 'bg-slate-500';
}

/** Index visible month events into per-day task circles, note squares, and schedule lines. */
export function buildCalendarDayMarkers(events: CalendarEventDto[]): Map<string, DayMarkers> {
  const map = new Map<string, DayMarkers>();

  const ensure = (key: string): DayMarkers => {
    let m = map.get(key);
    if (!m) {
      m = { tasks: [], notes: [], scheduleBars: [] };
      map.set(key, m);
    }
    return m;
  };

  for (const ev of events) {
    const start = parseEventDate(ev.start);
    const end = parseEventDate(ev.end ?? ev.start);
    const days = eachDayOfInterval({ start, end });

    if (isMultiDaySchedule(ev)) {
      for (const d of days) {
        const key = dayKey(d);
        const m = ensure(key);
        if (!m.scheduleBars.some((b) => b.eventId === ev.id)) {
          m.scheduleBars.push({
            eventId: ev.id,
            location: ev.location,
            colorClass: scheduleColor(ev.location),
            title: ev.title,
          });
        }
      }
    }

    if (isTaskMarker(ev)) {
      const key = dayKey(start);
      const m = ensure(key);
      if (!m.tasks.some((t) => t.id === ev.id)) {
        m.tasks.push(ev);
      }
    }

    if (hasNotes(ev)) {
      const noteDay = dayKey(start);
      const m = ensure(noteDay);
      if (!m.notes.some((n) => n.id === ev.id)) {
        m.notes.push(ev);
      }
    }
  }

  return map;
}

export const MARKER_LIMITS = {
  tasks: 2,
  notes: 2,
  scheduleBars: 3,
} as const;

const DEFAULT_LABEL_MAX = 16;

/** Short label for task / note chips inside day cells. */
export function truncateMarkerLabel(text: string, maxLen: number = DEFAULT_LABEL_MAX): string {
  const trimmed = text.trim().replace(/\s+/g, ' ');
  if (!trimmed) return '—';
  if (trimmed.length <= maxLen) return trimmed;
  return `${trimmed.slice(0, Math.max(1, maxLen - 1))}…`;
}

/** Prefer first line of notes; fall back to event title. */
export function noteMarkerLabel(ev: CalendarEventDto, maxLen?: number): string {
  const raw = ev.notes?.trim();
  const line = raw ? raw.split(/\r?\n/)[0] : ev.title;
  return truncateMarkerLabel(line, maxLen);
}
