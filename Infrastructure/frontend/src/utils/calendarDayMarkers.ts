import { eachDayOfInterval, format, parseISO } from 'date-fns'
import { toZonedTime } from 'date-fns-tz'

import type { CalendarEventDto } from '../types/calendar'
import { ROOM_CALENDAR_COLORS } from '../types/calendar'

import { CALENDAR_TZ } from './flowerGrowPlan'

export interface ScheduleBarMarker {
  eventId: string
  location: string
  colorClass: string
  title: string
}

export interface DayMarkers {
  events: CalendarEventDto[]
  tasks: CalendarEventDto[]
  notes: CalendarEventDto[]
  scheduleBars: ScheduleBarMarker[]
}

const GROW_PHASE_TYPES = new Set([
  'clone_window', 'pot_veg', 'bed_veg', 'flip_to_flower',
  'flower_stretch', 'flower_bulk', 'flower_ripen', 'drying',
])

/** Stable 'yyyy-MM-dd' key in CALENDAR_TZ; matches buildCalendarDayMarkers keys. */
export function calendarDayKey(d: Date): string {
  return format(toZonedTime(d, CALENDAR_TZ), 'yyyy-MM-dd')
}

/** Day key of an event's start ISO string (date-only values get noon anchoring). */
export function calendarEventDayKey(iso: string): string {
  return calendarDayKey(parseEventDate(iso))
}

function parseEventDate(iso: string): Date {
  return toZonedTime(parseISO(iso.length === 10 ? `${iso}T12:00:00` : iso), CALENDAR_TZ)
}

export function isGrowPlanPhase(ev: CalendarEventDto): boolean {
  if (ev.growPlanId != null || ev.metadata?.grow_plan_id != null) return true
  return GROW_PHASE_TYPES.has(ev.eventType)
}

/** True when the event's inclusive [start, end] calendar interval covers the given day. */
export function calendarEventOccursOnDay(ev: CalendarEventDto, day: Date): boolean {
  const key = calendarDayKey(day)
  const startKey = calendarEventDayKey(ev.start)
  const endKey = calendarEventDayKey(ev.end ?? ev.start)
  return startKey <= key && key <= endKey
}

function isMultiDaySchedule(ev: CalendarEventDto): boolean {
  return (ev.end ?? ev.start) > ev.start || isGrowPlanPhase(ev)
}

function isTaskMarker(ev: CalendarEventDto): boolean {
  if (ev.eventType === 'planned_task' || ev.source === 'mode_transition') return true
  if (isMultiDaySchedule(ev)) return false
  if (ev.eventType === 'harvest') return true
  return !ev.end || ev.end === ev.start
}

function hasNotes(ev: CalendarEventDto): boolean {
  return Boolean(ev.notes?.trim())
}

function scheduleColor(location: string): string {
  return ROOM_CALENDAR_COLORS[location] ?? 'bg-slate-500'
}

/** Index visible month events into unique inclusive-day events and legacy groups. */
export function buildCalendarDayMarkers(events: CalendarEventDto[]): Map<string, DayMarkers> {
  const map = new Map<string, DayMarkers>()
  const seenEvents = new Map<string, Set<string>>()
  const ensure = (key: string): DayMarkers => {
    let markers = map.get(key)
    if (!markers) {
      markers = { events: [], tasks: [], notes: [], scheduleBars: [] }
      map.set(key, markers)
    }
    return markers
  }

  for (const ev of events) {
    const start = parseEventDate(ev.start)
    const days = eachDayOfInterval({ start, end: parseEventDate(ev.end ?? ev.start) })
    for (const day of days) {
      const key = calendarDayKey(day)
      const markers = ensure(key)
      let seen = seenEvents.get(key)
      if (!seen) {
        seen = new Set<string>()
        seenEvents.set(key, seen)
      }
      if (!seen.has(ev.id)) {
        seen.add(ev.id)
        markers.events.push(ev)
      }
    }

    if (isMultiDaySchedule(ev)) {
      for (const day of days) {
        const markers = ensure(calendarDayKey(day))
        if (!markers.scheduleBars.some(bar => bar.eventId === ev.id)) {
          markers.scheduleBars.push({ eventId: ev.id, location: ev.location, colorClass: scheduleColor(ev.location), title: ev.title })
        }
      }
    }
    if (isTaskMarker(ev)) {
      const markers = ensure(calendarDayKey(start))
      if (!markers.tasks.some(task => task.id === ev.id)) markers.tasks.push(ev)
    }
    if (hasNotes(ev)) {
      const markers = ensure(calendarDayKey(start))
      if (!markers.notes.some(note => note.id === ev.id)) markers.notes.push(ev)
    }
  }

  for (const markers of map.values()) {
    markers.events.sort((a, b) =>
      calendarEventDayKey(a.start).localeCompare(calendarEventDayKey(b.start)) ||
      a.location.localeCompare(b.location) || a.id.localeCompare(b.id)
    )
  }
  return map
}
