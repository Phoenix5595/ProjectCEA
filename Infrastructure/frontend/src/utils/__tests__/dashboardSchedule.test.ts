import { describe, expect, it } from 'vitest'

import type { Schedule } from '../../types/schedule'
import { formatTransitionCountdown, nextRoomTransition } from '../dashboardSchedule'

const baseSchedule: Schedule = {
  id: 1,
  name: 'Lights',
  location: 'Veg Room',
  cluster: 'main',
  device_name: 'light_1',
  day_of_week: null,
  start_time: '06:00',
  end_time: '18:00',
  enabled: true,
  mode: 'SUN',
  created_at: '2026-01-01T00:00:00Z',
}

describe('dashboard schedule transitions', () => {
  it('selects the next daily light transition and ignores disabled rooms', () => {
    const now = new Date('2026-09-23T16:00:00.000Z')
    const transition = nextRoomTransition(
      [baseSchedule, { ...baseSchedule, id: 2, enabled: false, location: 'Lab' }],
      'Veg Room',
      'main',
      now
    )
    expect(transition?.label).toBe('Lights off')
    expect(formatTransitionCountdown(transition?.at ?? null, now)).toBe('in 6h 0m')
  })

  it('handles an overnight schedule end before the next start', () => {
    const now = new Date('2026-09-23T03:00:00.000Z')
    const transition = nextRoomTransition(
      [{ ...baseSchedule, start_time: '22:00', end_time: '06:00', mode: 'NIGHT' }],
      'Veg Room',
      'main',
      now
    )
    expect(transition?.label).toBe('Night period ends')
  })
})
