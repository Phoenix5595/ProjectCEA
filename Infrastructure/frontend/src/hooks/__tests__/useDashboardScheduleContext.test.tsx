import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../services/api'
import { useDashboardScheduleContext } from '../useDashboardScheduleContext'

vi.mock('../../services/api', () => ({
  apiClient: {
    getAllModes: vi.fn(),
    getSchedules: vi.fn(),
  },
}))

const schedule = {
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

describe('useDashboardScheduleContext', () => {
  beforeEach(() => vi.clearAllMocks())

  it('retains the last successful datasets during a partial refresh failure', async () => {
    vi.mocked(apiClient.getAllModes).mockResolvedValue({
      'Veg Room:main': { location: 'Veg Room', cluster: 'main', mode: 'day' },
    })
    vi.mocked(apiClient.getSchedules).mockResolvedValue([schedule])
    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() => expect(result.current.schedules).toHaveLength(1))

    vi.mocked(apiClient.getAllModes).mockRejectedValue(new Error('mode service down'))
    vi.mocked(apiClient.getSchedules).mockResolvedValue([schedule])
    await result.current.refresh()

    expect(result.current.schedules).toHaveLength(1)
    expect(result.current.modes['Veg Room:main']?.mode).toBe('day')
    await waitFor(() => expect(result.current.error).toContain('grow mode unavailable'))
  })
})
