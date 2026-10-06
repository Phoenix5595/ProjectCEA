import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CurrentPublicationResponse, MonitoringApi, MonitoringRequestOptions, ProjectionPublicationResponse } from '../../monitoring/api'
import { authorityCurrent, authorityFuture, authorityNow } from './timelineAuthorityFixtures'
import { currentActiveProfileKey, currentMatchesRegistryVersion, useActiveClimateProjection } from '../state/useActiveClimateProjection'

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(authorityNow)) })
afterEach(() => vi.useRealTimers())
function client() {
  const controlCurrent = vi.fn(async (_location: string, _options?: MonitoringRequestOptions): Promise<CurrentPublicationResponse> => ({ quality: 'exact', value: authorityCurrent(Date.now()) }))
  const controlProjection = vi.fn(async (_location: string, _options?: MonitoringRequestOptions): Promise<ProjectionPublicationResponse> => ({ quality: 'estimated', value: [authorityFuture(Date.now())] }))
  const api = { controlCurrent, controlProjection } as unknown as MonitoringApi
  return { api, controlCurrent, controlProjection }
}
const room = { location: 'Flower Room', cluster: 'main', registryVersion: 9 }
async function settle() { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }

describe('current/future running authority', () => {
  it('publishes independently fresh current while future is pending, without overlapping either read', async () => {
    const mock = client()
    const { promise, reject } = Promise.withResolvers<ProjectionPublicationResponse>()
    mock.controlProjection.mockReturnValueOnce(promise)
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    await settle()
    expect(hook.result.current.current?.series[0]?.value).toBe(22)
    expect(hook.result.current.future).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(mock.controlCurrent).toHaveBeenCalledTimes(1)
    expect(mock.controlProjection).toHaveBeenCalledTimes(1)
    await act(async () => { reject(new Error('projection timeout')); await Promise.resolve() })
    expect(hook.result.current.current?.series[0]?.value).toBe(22)
    expect(hook.result.current.future).toEqual([])
    hook.unmount()
  })

  it('uses exact registry hex and room+cluster profile metadata, not global config cursor or truncated IDs', () => {
    expect(currentMatchesRegistryVersion(9, '0000009')).toBe(true)
    expect(currentMatchesRegistryVersion(37, '0000009')).toBe(false)
    const current = authorityCurrent()
    expect(currentActiveProfileKey(9, current, 'Flower Room', 'main')).toEqual({ modeId: 3, submodeId: 2 })
    expect(currentActiveProfileKey(9, current, 'Veg Room', 'main')).toBeNull()
    expect(currentActiveProfileKey(9, current, 'Flower Room', 'other')).toBeNull()
    current.series[1]!.value = 3.5
    expect(currentActiveProfileKey(9, current, 'Flower Room', 'main')).toBeNull()
    current.series[1]!.value = 3
    current.series.pop()
    expect(currentActiveProfileKey(9, current, 'Flower Room', 'main')).toEqual({ modeId: 3, submodeId: null })
  })

  it('issues initial reads in parallel, polls current at one second and caches future until refresh', async () => {
    const mock = client()
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    expect(mock.controlCurrent).toHaveBeenCalledTimes(1)
    expect(mock.controlProjection).toHaveBeenCalledTimes(1)
    await settle()
    expect(hook.result.current.current?.series[0]?.value).toBe(22)
    expect(hook.result.current.future[0]?.series[0]?.value).toBe(24)
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(mock.controlCurrent).toHaveBeenCalledTimes(2)
    expect(mock.controlProjection).toHaveBeenCalledTimes(1)
    await act(async () => { await hook.result.current.refresh() })
    expect(mock.controlProjection).toHaveBeenCalledTimes(2)
    hook.unmount()
  })

  it('retains factual current on future failure and removes paired future on current failure', async () => {
    const mock = client()
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    await settle()
    mock.controlProjection.mockRejectedValueOnce(new Error('future down'))
    await act(async () => { await hook.result.current.refresh() })
    expect(hook.result.current.current?.series[0]?.value).toBe(22)
    expect(hook.result.current.future).toEqual([])
    expect(hook.result.current.projectionError).toContain('future down')
    await act(async () => { await hook.result.current.refresh() })
    mock.controlCurrent.mockRejectedValueOnce(new Error('current down'))
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(hook.result.current.current).toBeNull()
    expect(hook.result.current.future).toEqual([])
    hook.unmount()
  })

  it('expires current immediately even while a single flight is unresolved', async () => {
    const mock = client()
    const fixed = authorityCurrent()
    mock.controlCurrent.mockResolvedValueOnce({ quality: 'exact', value: fixed })
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    await settle()
    const { promise, resolve } = Promise.withResolvers<CurrentPublicationResponse>()
    mock.controlCurrent.mockReturnValueOnce(promise)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(mock.controlCurrent).toHaveBeenCalledTimes(2)
    expect(hook.result.current.current).toBeNull()
    expect(hook.result.current.future).toEqual([])
    await act(async () => { resolve({ quality: 'unavailable', value: null }) })
    hook.unmount()
  })

  it('fences cluster/registry generations, aborts prior signals and never exposes cross-room profile facts', async () => {
    const mock = client()
    const { promise, resolve } = Promise.withResolvers<CurrentPublicationResponse>()
    mock.controlCurrent.mockReturnValueOnce(promise)
    const hook = renderHook(({ cluster, registryVersion }) => useActiveClimateProjection({ ...room, cluster, registryVersion, api: mock.api }),
      { initialProps: { cluster: 'main', registryVersion: 9 } })
    const oldSignal = mock.controlCurrent.mock.calls[0]?.[1]?.signal
    hook.rerender({ cluster: 'aux', registryVersion: 9 })
    await settle()
    await act(async () => { resolve({ quality: 'exact', value: authorityCurrent() }) })
    expect(oldSignal?.aborted).toBe(true)
    expect(hook.result.current.current).toBeNull()
    expect(hook.result.current.future).toEqual([])
    hook.rerender({ cluster: 'main', registryVersion: 10 })
    await settle()
    expect(hook.result.current.current).toBeNull()
    hook.unmount()
  })

  it('accepts fresh all-NULL targets and failed persistence as metadata, but rejects unusable wrappers and mismatched futures', async () => {
    const mock = client()
    const current = authorityCurrent()
    current.series[0]!.value = null
    current.series[0]!.quality = 'unavailable'
    current.persistence = { state: 'failed', error: 'flush down' }
    mock.controlCurrent.mockResolvedValue({ quality: 'exact', value: current })
    mock.controlProjection.mockResolvedValue({ quality: 'estimated', value: [{ ...authorityFuture(), version: { ...authorityFuture().version, config_version: 38 } }] })
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    await settle()
    expect(hook.result.current.current).not.toBeNull()
    expect(currentActiveProfileKey(9, hook.result.current.current, room.location, room.cluster)).toEqual({ modeId: 3, submodeId: 2 })
    expect(hook.result.current.future).toEqual([])
    mock.controlCurrent.mockResolvedValue({ quality: 'unavailable', value: current })
    await act(async () => { await hook.result.current.refresh() })
    expect(hook.result.current.current).toBeNull()
    expect(hook.result.current.future).toEqual([])
    hook.unmount()
  })

  it('fetches future when full current version changes, on interval expiry and at thirty seconds', async () => {
    const mock = client()
    const hook = renderHook(() => useActiveClimateProjection({ ...room, api: mock.api }))
    await settle()
    const version = { ...authorityCurrent().version, config_version: 38 }
    mock.controlCurrent.mockImplementation(async () => ({ quality: 'exact', value: { ...authorityCurrent(Date.now()), version } }))
    mock.controlProjection.mockImplementation(async () => ({ quality: 'estimated', value: [{ ...authorityFuture(Date.now()), version }] }))
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(mock.controlProjection).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(mock.controlProjection).toHaveBeenCalledTimes(3)
    const short = authorityFuture(Date.now())
    short.version = version
    short.valid_until = new Date(Date.now() + 100)
    short.series[0]!.valid_until = short.valid_until
    mock.controlProjection.mockResolvedValueOnce({ quality: 'estimated', value: [short] })
    await act(async () => { await hook.result.current.refresh() })
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    expect(mock.controlProjection).toHaveBeenCalledTimes(5)
    hook.unmount()
  })
})
