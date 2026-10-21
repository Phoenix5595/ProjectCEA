import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ControlSnapshotResponse } from '../../services/api/devices'
import { useControlSnapshot } from '../useControlSnapshot'


const mocks = vi.hoisted(() => ({
  getControlSnapshot: vi.fn(),
  getDeviceRegistry: vi.fn(),
}))

vi.mock('../../services/api', () => ({
  apiClient: {
    getControlSnapshot: mocks.getControlSnapshot,
    getDeviceRegistry: mocks.getDeviceRegistry,
  },
}))

const BASE_MS = new Date('2026-10-01T12:00:00Z').getTime()

function controlSnapshot(registryVersion: number): ControlSnapshotResponse {
  return {
    dfr_boards: [],
    failsafes: [],
    freshness: 'FRESH',
    generated_at: new Date(BASE_MS).toISOString(),
    hardware_alarms: [],
    registry_version: registryVersion,
    relays: [],
    sampled_at: null,
    stale_since: null,
  }
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('useControlSnapshot registry refresh', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(BASE_MS)
    mocks.getControlSnapshot.mockReset()
    mocks.getDeviceRegistry.mockReset()
    mocks.getDeviceRegistry.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('loads the registry once per version and force refreshes it on demand', async () => {
    let registryVersion = 1
    mocks.getControlSnapshot.mockImplementation(async () => controlSnapshot(registryVersion))
    const { result } = renderHook(() => useControlSnapshot())
    await settle()
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(1)

    registryVersion = 2
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(2)

    await act(async () => {
      await result.current.refreshNow({ forceRegistry: true })
    })
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(3)
  })

  it('runs a forced registry refresh after an in-flight ordinary refresh', async () => {
    mocks.getControlSnapshot.mockImplementation(async () => controlSnapshot(1))
    const { result } = renderHook(() => useControlSnapshot())
    await settle()
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(1)

    const ordinarySnapshot = Promise.withResolvers<ControlSnapshotResponse>()
    mocks.getControlSnapshot.mockImplementationOnce(() => ordinarySnapshot.promise)
    let ordinaryRefresh!: Promise<void>
    act(() => {
      ordinaryRefresh = result.current.refreshNow()
    })
    const forcedRefresh = result.current.refreshNow({ forceRegistry: true })
    expect(mocks.getControlSnapshot).toHaveBeenCalledTimes(2)

    ordinarySnapshot.resolve(controlSnapshot(1))
    await act(async () => {
      await Promise.all([ordinaryRefresh, forcedRefresh])
    })

    expect(mocks.getControlSnapshot).toHaveBeenCalledTimes(3)
    expect(mocks.getDeviceRegistry).toHaveBeenCalledTimes(2)
  })
})
