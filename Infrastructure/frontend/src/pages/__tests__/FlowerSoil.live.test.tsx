import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import FlowerSoil from '../FlowerSoil'

const mocks = vi.hoisted(() => ({
  soilApi: {
    listRegistry: vi.fn(),
    soilLive: vi.fn(),
    soilHistory: vi.fn(),
  },
  chartFeed: { publish: vi.fn() },
}))

vi.mock('../../features/soil/api', () => ({ soilApi: mocks.soilApi }))
vi.mock('../../features/monitoring/charts', () => ({
  UPlotChart: () => <div data-testid="soil-chart" />,
  createMonitoringChartFeed: () => mocks.chartFeed,
}))

const HISTORY_RESPONSE = {
  start: new Date('2026-09-28T09:00:00Z'),
  end: new Date('2026-09-28T12:00:00Z'),
  max_points: 1000,
  tier: '1min',
  bucket_seconds: 60,
  series: [],
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-09-28T12:00:00Z'))
  mocks.soilApi.listRegistry.mockResolvedValue({ records: [], unassigned_count: 0 })
  mocks.soilApi.soilLive.mockResolvedValue({ generated_at: new Date(), probes: [] })
  mocks.soilApi.soilHistory.mockResolvedValue(HISTORY_RESPONSE)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('Flower soil live history controls', () => {
  it('stops the 5-second history refresh while paused and immediately resumes it', async () => {
    const view = render(
      <MemoryRouter initialEntries={['/flower/soil']}>
        <FlowerSoil />
      </MemoryRouter>
    )

    await waitFor(() => expect(mocks.soilApi.soilHistory).toHaveBeenCalledTimes(1))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(mocks.soilApi.soilHistory).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(mocks.soilApi.soilHistory).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(mocks.soilApi.soilHistory).toHaveBeenCalledTimes(3))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(mocks.soilApi.soilHistory).toHaveBeenCalledTimes(4)
    view.unmount()
  })
})
