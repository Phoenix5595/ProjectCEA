import type { CalendarEventDto } from '../../types/calendar'

/** In-memory event payload for the guarded Calendar presentation preview. */
export function buildCalendarVisualFixture(referenceDate: Date): CalendarEventDto[] {
  const date = (day: number) => {
    const value = new Date(referenceDate)
    value.setDate(day)
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
  }
  const start = date(referenceDate.getDate())
  const phaseStart = date(referenceDate.getDate() - 2)
  const phaseEnd = date(referenceDate.getDate() + 3)
  const monthEnd = new Date(referenceDate.getFullYear(), referenceDate.getMonth() + 1, 0).getDate()
  return [
    { id: 'fixture-long-noted', source: 'manual', eventType: 'planned_task', title: 'Inspect flowering canopy, check irrigation emitters and record environmental observations', start, end: start, location: 'Flower Room', editable: true, colorKey: 'planned_task', notes: 'Long accessible fixture note: inspect both rows, confirm drip line flow, and record observations.', sync_status: 'pending_push' },
    { id: 'fixture-phase-a', source: 'crop_batch', eventType: 'flower_stretch', title: 'Stretch phase', start: phaseStart, end: phaseEnd, location: 'Flower Room', editable: false, colorKey: 'flower_stretch', growPlanId: 'fixture-grow', sync_status: 'synced' },
    { id: 'fixture-phase-b', source: 'crop_batch', eventType: 'flower_bulk', title: 'Bulk phase', start: date(referenceDate.getDate()), end: date(referenceDate.getDate() + 5), location: 'Flower Room', editable: false, colorKey: 'flower_bulk', growPlanId: 'fixture-grow', sync_status: 'conflict', sync_error_code: 'calendar_remote_conflict' },
    { id: 'fixture-fourth', source: 'manual', eventType: 'planned_task', title: 'Review room readings', start, location: 'Veg Room', editable: true, colorKey: 'planned_task' },
    { id: 'fixture-sixth', source: 'manual', eventType: 'planned_task', title: 'Irrigation verification', start, location: 'Flower Room', editable: true, colorKey: 'planned_task', sync_status: 'error', sync_error_code: 'calendar_auth_failed' },
    { id: 'fixture-fifth', source: 'manual', eventType: 'harvest', title: 'Harvest notes', start, location: 'Lab', editable: true, colorKey: 'harvest', notes: 'Fixture notes stay on the same event.' },
    { id: 'fixture-week-month-edge', source: 'crop_batch', eventType: 'bed_veg', title: 'Week and month boundary phase', start: date(monthEnd - 2), end: date(monthEnd + 2), location: 'Flower Room', editable: false, colorKey: 'bed_veg', growPlanId: 'fixture-grow' },
    { id: 'fixture-spring-dst', source: 'crop_batch', eventType: 'flower_stretch', title: 'Spring DST transition', start: '2026-03-07', end: '2026-03-09', location: 'Flower Room', editable: false, colorKey: 'flower_stretch', growPlanId: 'fixture-dst-spring' },
    { id: 'fixture-fall-dst', source: 'crop_batch', eventType: 'flower_bulk', title: 'Fall DST transition', start: '2026-10-31', end: '2026-11-02', location: 'Flower Room', editable: false, colorKey: 'flower_bulk', growPlanId: 'fixture-dst-fall' },
  ]
}
