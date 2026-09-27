import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../../services/api'
import { useActiveAlarms } from '../useActiveAlarms'

vi.mock('../../services/api', () => ({
  apiClient: {
    getActiveAlarms: vi.fn(),
    acknowledgeAlarm: vi.fn(),
  },
}))
beforeEach(() => {
  vi.clearAllMocks()
})

const alarm = {
  location: 'Lab',
  cluster: 'main',
  alarm_name: 'co2:low',
  severity: 'critical',
  message: 'CO2 low',
  active: true,
  acknowledged: false,
  opened_at: '2026-09-23T15:00:00Z',
  acknowledged_at: null,
  acknowledged_by: null,
}

describe('useActiveAlarms', () => {
  it('acknowledges an encoded alarm identity and refreshes durable state', async () => {
    vi.mocked(apiClient.getActiveAlarms).mockResolvedValue({
      generated_at: '',
      alarms: [alarm],
    } as never)
    vi.mocked(apiClient.acknowledgeAlarm).mockResolvedValue({
      success: true,
      acknowledged: true,
    } as never)
    const { result } = renderHook(() => useActiveAlarms())
    await waitFor(() => expect(result.current.alarms).toHaveLength(1))

    await act(async () => {
      await result.current.acknowledge(result.current.alarms[0])
    })

    expect(apiClient.acknowledgeAlarm).toHaveBeenCalledWith('Lab', 'main', 'co2:low')
    expect(apiClient.getActiveAlarms).toHaveBeenCalledTimes(2)
  })

  it('prevents duplicate acknowledgement requests while one is pending', async () => {
    vi.mocked(apiClient.getActiveAlarms).mockResolvedValue({
      generated_at: '',
      alarms: [alarm],
    } as never)
    vi.mocked(apiClient.acknowledgeAlarm).mockResolvedValue({
      success: true,
      acknowledged: true,
    } as never)
    const { result } = renderHook(() => useActiveAlarms())
    await waitFor(() => expect(result.current.alarms).toHaveLength(1))

    let first!: Promise<boolean>
    let second!: Promise<boolean>
    act(() => {
      first = result.current.acknowledge(result.current.alarms[0])
      second = result.current.acknowledge(result.current.alarms[0])
    })
    expect(apiClient.acknowledgeAlarm).toHaveBeenCalledTimes(1)
    await act(async () => {
      await Promise.all([first, second])
    })
  })
})
