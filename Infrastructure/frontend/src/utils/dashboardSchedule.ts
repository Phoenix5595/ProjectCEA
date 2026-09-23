import { addDays, format } from 'date-fns'
import { fromZonedTime, toZonedTime } from 'date-fns-tz'

import type { Schedule } from '../types/schedule'
import { CALENDAR_TZ } from './flowerGrowPlan'

export interface RoomTransition {
  at: Date
  label: string
  kind: 'start' | 'stop'
  deviceName: string
}

function timeToMinutes(value: string): number | null {
  const match = /^(\d{2}):(\d{2})/.exec(value)
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (hours > 23 || minutes > 59) return null
  return hours * 60 + minutes
}

function localInstant(day: Date, minutes: number): Date {
  const date = format(day, 'yyyy-MM-dd')
  const hours = String(Math.floor(minutes / 60)).padStart(2, '0')
  const mins = String(minutes % 60).padStart(2, '0')
  return fromZonedTime(new Date(`${date}T${hours}:${mins}:00`), CALENDAR_TZ)
}

function pythonWeekday(day: Date): number {
  return (day.getDay() + 6) % 7
}

function transitionLabel(schedule: Schedule, kind: 'start' | 'stop'): string {
  const mode = schedule.mode?.toUpperCase()
  if (mode === 'SUN' || mode === 'DAY') return kind === 'start' ? 'Lights on' : 'Lights off'
  if (mode === 'NIGHT') return kind === 'start' ? 'Night period' : 'Night period ends'
  const device = schedule.device_name.replace(/_/g, ' ')
  const display = device ? `${device.charAt(0).toUpperCase()}${device.slice(1)}` : 'Device'
  return `${display} ${kind === 'start' ? 'starts' : 'stops'}`
}

export function nextRoomTransition(
  schedules: Schedule[],
  location: string,
  cluster: string,
  now: Date
): RoomTransition | null {
  const zonedNow = toZonedTime(now, CALENDAR_TZ)
  const candidates: RoomTransition[] = []
  const roomSchedules = schedules.filter(
    schedule => schedule.enabled && schedule.location === location && schedule.cluster === cluster
  )

  for (let offset = 0; offset < 8; offset += 1) {
    const day = addDays(zonedNow, offset)
    const weekday = pythonWeekday(day)
    for (const schedule of roomSchedules) {
      if (schedule.day_of_week != null && schedule.day_of_week !== weekday) continue
      const startMinutes = timeToMinutes(schedule.start_time)
      const endMinutes = timeToMinutes(schedule.end_time)
      if (startMinutes == null || endMinutes == null) continue
      const start = localInstant(day, startMinutes)
      const end = localInstant(day, endMinutes)
      const endAdjusted = endMinutes < startMinutes ? addDays(end, 1) : end
      if (start > now) {
        candidates.push({
          at: start,
          label: transitionLabel(schedule, 'start'),
          kind: 'start',
          deviceName: schedule.device_name,
        })
      }
      if (endAdjusted > now) {
        candidates.push({
          at: endAdjusted,
          label: transitionLabel(schedule, 'stop'),
          kind: 'stop',
          deviceName: schedule.device_name,
        })
      }
    }
  }

  const unique = new Map<string, RoomTransition>()
  for (const candidate of candidates) {
    const key = `${candidate.at.getTime()}:${candidate.kind}:${candidate.label}`
    unique.set(key, candidate)
  }
  return [...unique.values()].sort((a, b) => a.at.getTime() - b.at.getTime())[0] ?? null
}

export function formatTransitionCountdown(at: Date | null, now: Date): string {
  if (!at) return 'No scheduled transition'
  const remainingMs = Math.max(0, at.getTime() - now.getTime())
  const totalMinutes = Math.round(remainingMs / 60_000)
  if (totalMinutes < 1) return 'now'
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return hours > 0 ? `in ${hours}h ${minutes}m` : `in ${minutes}m`
}
