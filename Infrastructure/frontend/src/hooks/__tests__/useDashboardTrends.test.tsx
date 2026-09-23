import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useDashboardTrends } from '../useDashboardTrends'
const { sensorRange } = vi.hoisted(() => ({ sensorRange: vi.fn() }))

vi.mock('../../features/monitoring/api/monitoringApi', () => ({
  MonitoringApi: class {
    sensorRange = sensorRange
  },
}))

function response() {
  const now = Date.parse('2026-09-23T16:00:00Z')
  return {
    metadata: {} as never,
    series: [
      {
        sensor: 'dry_bulb',
        node: 'main',
        unit_family: 'celsius',
        unit: '°C',
        points: [
          {
            timestamp: new Date(now - 20 * 60_000),
            average: 20,
            minimum: 20,
            maximum: 20,
            sample_count: 1,
          },
          {
            timestamp: new Date(now - 10 * 60_000),
            average: 21,
            minimum: 21,
            maximum: 21,
            sample_count: 1,
          },
          { timestamp: new Date(now), average: 22, minimum: 22, maximum: 22, sample_count: 1 },
        ],
      },
    ],
    statistics: [],
  }
}

describe('useDashboardTrends', () => {
  it('normalizes room series and computes a ten-minute delta', async () => {
    sensorRange.mockResolvedValue(response())
    const { result } = renderHook(() => useDashboardTrends())
    await waitFor(() => expect(result.current.byRoom['Veg Room']?.main?.temperature).toBeDefined())
    expect(result.current.byRoom['Veg Room'].main?.temperature?.delta10m).toBe(1)
    expect(sensorRange).toHaveBeenCalledWith('Veg Room', expect.any(String), expect.any(String), 60)
  })
})
