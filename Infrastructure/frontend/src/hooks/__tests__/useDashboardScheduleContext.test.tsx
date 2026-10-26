import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../services/api'
import { getDashboardMode, useDashboardScheduleContext } from '../useDashboardScheduleContext'

vi.mock('../../services/api', () => ({
  apiClient: {
    getActiveRoomMode: vi.fn(),
    getSchedules: vi.fn(),
    getPendingModeChange: vi.fn(),
    getExpectedHarvest: vi.fn(),
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
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getPendingModeChange).mockResolvedValue(null)
    vi.mocked(apiClient.getExpectedHarvest).mockResolvedValue(null)
  })

  it('loads Flower and Veg modes independently from schedules', async () => {
    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => {
      if (location === 'Flower Room') throw new Error('Flower mode service down')
      return {
        location,
        cluster: 'main',
        mode_name: 'veg',
        submode_name: null,
      }
    })
    vi.mocked(apiClient.getSchedules).mockResolvedValue([schedule])

    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() => expect(result.current.schedules).toHaveLength(1))
    await waitFor(() =>
      expect(result.current.modeErrors['Flower Room:main']).toBe('Flower mode service down')
    )

    expect(apiClient.getActiveRoomMode).toHaveBeenCalledWith('Flower Room', 'main')
    expect(apiClient.getActiveRoomMode).toHaveBeenCalledWith('Veg Room', 'main')
    expect(apiClient.getActiveRoomMode).not.toHaveBeenCalledWith('Lab', 'main')
    expect(result.current.modes['Flower Room:main']).toBeUndefined()
    expect(result.current.modes['Veg Room:main']?.mode_name).toBe('veg')
    expect(getDashboardMode(result.current.modes, 'Veg Room', 'main')?.mode_name).toBe('veg')
    expect(getDashboardMode(result.current.modes, 'Flower Room', 'main')).toBeNull()
    expect(result.current.modeErrors['Veg Room:main']).toBeNull()
    expect(result.current.error).toContain('grow mode unavailable')
  })

  it('preserves each last known mode and schedules across independent failures', async () => {
    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => ({
      location,
      cluster: 'main',
      mode_name: location === 'Flower Room' ? 'sleep' : 'veg',
      submode_name: null,
    }))
    vi.mocked(apiClient.getSchedules).mockResolvedValue([schedule])

    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() => expect(Object.keys(result.current.modes)).toHaveLength(2))

    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => {
      if (location === 'Flower Room') throw new Error('Flower refresh failed')
      return { location, cluster: 'main', mode_name: 'drying', submode_name: null }
    })
    vi.mocked(apiClient.getSchedules).mockRejectedValue(new Error('schedule service down'))
    await act(async () => result.current.refresh())

    expect(result.current.modes['Flower Room:main']?.mode_name).toBe('sleep')
    expect(result.current.modes['Veg Room:main']?.mode_name).toBe('drying')
    expect(result.current.modeErrors['Flower Room:main']).toBe('Flower refresh failed')
    expect(result.current.modeErrors['Veg Room:main']).toBeNull()
    expect(result.current.schedules).toHaveLength(1)
    expect(result.current.error).toContain('grow mode unavailable')
    expect(result.current.error).toContain('schedules unavailable')

    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => {
      if (location === 'Veg Room') throw new Error('Veg refresh failed')
      return { location, cluster: 'main', mode_name: 'flower', submode_name: 'bulk' }
    })
    vi.mocked(apiClient.getSchedules).mockResolvedValue([])
    await act(async () => result.current.refresh())

    expect(result.current.modes['Flower Room:main']?.mode_name).toBe('flower')
    expect(result.current.modes['Flower Room:main']?.submode_name).toBe('bulk')
    expect(result.current.modes['Veg Room:main']?.mode_name).toBe('drying')
    expect(result.current.modeErrors['Flower Room:main']).toBeNull()
    expect(result.current.modeErrors['Veg Room:main']).toBe('Veg refresh failed')
    expect(result.current.schedules).toEqual([])
    expect(result.current.error).toContain('grow mode unavailable')
  })

  it('rejects active modes with a mismatched room or an empty mode name', async () => {
    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => ({
      location,
      cluster: 'main',
      mode_name: location === 'Flower Room' ? 'sleep' : 'veg',
      submode_name: null,
    }))
    vi.mocked(apiClient.getSchedules).mockResolvedValue([])

    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() => expect(Object.keys(result.current.modes)).toHaveLength(2))

    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location =>
      location === 'Flower Room'
        ? { location: 'Veg Room', cluster: 'main', mode_name: 'veg', submode_name: null }
        : { location, cluster: 'main', mode_name: '   ', submode_name: null }
    )
    await act(async () => result.current.refresh())

    expect(result.current.modes['Flower Room:main']?.mode_name).toBe('sleep')
    expect(result.current.modes['Veg Room:main']?.mode_name).toBe('veg')
    expect(result.current.modeErrors['Flower Room:main']).toContain('did not match')
    expect(result.current.modeErrors['Veg Room:main']).toContain('no mode name')
    expect(result.current.error).toContain('grow mode unavailable')
  })

  it('keeps the last pending mode change when the calendar status read fails', async () => {
    const pending = {
      from_mode: 'veg',
      to_mode: 'flower',
      to_submode: 'stretch',
      event_id: 500,
      since: '2026-10-25',
    }
    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => ({
      location,
      cluster: 'main',
      mode_name: 'veg',
      submode_name: null,
    }))
    vi.mocked(apiClient.getSchedules).mockResolvedValue([])
    vi.mocked(apiClient.getPendingModeChange).mockResolvedValue(pending)

    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() => expect(result.current.pendingModeChange).toEqual(pending))
    expect(apiClient.getPendingModeChange).toHaveBeenCalledWith('Flower Room', 'main')

    vi.mocked(apiClient.getPendingModeChange).mockRejectedValue(new Error('calendar down'))
    await act(async () => result.current.refresh())

    expect(result.current.pendingModeChange).toEqual(pending)
    expect(result.current.error).toContain('calendar status unavailable')
  })

  it('keeps the last expected harvests when a harvest read fails', async () => {
    const harvest = { harvest_date: '2027-01-03', grow_plan_id: 'gp-1', event_id: 502 }
    vi.mocked(apiClient.getActiveRoomMode).mockImplementation(async location => ({
      location,
      cluster: 'main',
      mode_name: 'flower',
      submode_name: 'bulk',
    }))
    vi.mocked(apiClient.getSchedules).mockResolvedValue([])
    vi.mocked(apiClient.getExpectedHarvest).mockImplementation(async location =>
      location === 'Flower Room' ? harvest : null
    )

    const { result } = renderHook(() => useDashboardScheduleContext())
    await waitFor(() =>
      expect(result.current.expectedHarvests['Flower Room:main']).toEqual(harvest)
    )
    expect(apiClient.getExpectedHarvest).toHaveBeenCalledWith('Flower Room', 'main')
    expect(apiClient.getExpectedHarvest).toHaveBeenCalledWith('Veg Room', 'main')
    expect(result.current.expectedHarvests['Veg Room:main']).toBeNull()

    vi.mocked(apiClient.getExpectedHarvest).mockRejectedValue(new Error('harvest down'))
    await act(async () => result.current.refresh())

    expect(result.current.expectedHarvests['Flower Room:main']).toEqual(harvest)
    expect(result.current.expectedHarvests['Veg Room:main']).toBeNull()
    expect(result.current.error).toContain('calendar status unavailable')
  })
})
