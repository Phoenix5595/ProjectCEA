import { useSyncExternalStore } from 'react'

import { apiClient } from '../services/api'
import type { ControlSnapshotResponse } from '../services/api/devices'
import type { DeviceRegistryEntry } from '../types/device'
import type { ChannelInfo, RelayBoardStateResponse } from '../types/relay'
import { logger } from '../utils/logger'

export interface ControlSnapshotStoreState {
  snapshot: ControlSnapshotResponse | null
  registry: DeviceRegistryEntry[]
  channels: ChannelInfo[]
  relayState: RelayBoardStateResponse | null
  mcpConnected: boolean
  /** Latest installed registry counter; null until a snapshot has arrived. */
  registryVersion: number | null
  loading: boolean
  error: string | null
}
type RefreshOptions = { forceRegistry?: boolean }


export interface UseControlSnapshotReturn extends ControlSnapshotStoreState {
  /** Latest installed registry counter; null until a snapshot has arrived. */
  registryVersion: number | null
  refresh: (options?: RefreshOptions) => Promise<void>
  refreshNow: (options?: RefreshOptions) => Promise<void>
}

const POLL_INTERVAL_MS = 1000

const INITIAL_STATE: ControlSnapshotStoreState = {
  snapshot: null,
  registry: [],
  channels: Array.from({ length: 16 }, (_, channel) => ({
    channel,
    device_name: null,
    display_name: null,
    device_type: null,
    location: null,
    cluster: null,
    light_name: null,
  })),
  relayState: null,
  mcpConnected: false,
  registryVersion: null,
  loading: true,
  error: null,
}

let state: ControlSnapshotStoreState = INITIAL_STATE
const listeners = new Set<() => void>()
let timerId: ReturnType<typeof setInterval> | null = null
let inFlight: Promise<void> | null = null
let inFlightForceRegistry = false
let sequence = 0

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function deriveChannels(
  snapshot: ControlSnapshotResponse | null,
  registry: DeviceRegistryEntry[]
): ChannelInfo[] {
  const byChannel = new Map<number, DeviceRegistryEntry>()
  for (const entry of registry) {
    const ch = entry.channel ?? entry.relay_channel ?? null
    if (ch != null) byChannel.set(ch, entry)
  }

  const snapshotRelaysByChannel = new Map<number, ControlSnapshotResponse['relays'][number]>()
  if (snapshot) {
    for (const r of snapshot.relays) {
      snapshotRelaysByChannel.set(r.channel, r)
    }
  }

  return Array.from({ length: 16 }, (_, channel) => {
    const device = byChannel.get(channel)
    if (device) {
      return {
        channel,
        device_name: device.device_name,
        display_name: device.display_name ?? null,
        device_type: device.device_type,
        location: device.location,
        cluster: device.cluster,
        light_name: device.device_type === 'light' ? (device.display_name ?? null) : null,
      }
    }
    const relay = snapshotRelaysByChannel.get(channel)
    const assignment = relay?.assignment ?? null
    if (assignment) {
      return {
        channel,
        device_name: assignment.device_name,
        display_name: assignment.display_name,
        device_type: assignment.device_type,
        location: assignment.location,
        cluster: assignment.cluster,
        light_name: assignment.device_type === 'light' ? assignment.display_name : null,
      }
    }
    return {
      channel,
      device_name: null,
      display_name: null,
      device_type: null,
      location: null,
      cluster: null,
      light_name: null,
    }
  })
}

function deriveRelayState(
  snapshot: ControlSnapshotResponse | null
): RelayBoardStateResponse | null {
  if (!snapshot) return null

  const relaysByChannel = new Map<number, (typeof snapshot.relays)[number]>()
  for (const r of snapshot.relays) {
    relaysByChannel.set(r.channel, r)
  }

  const channels: boolean[] = []
  const timestamps: (string | null)[] = []
  const modes: (string | null)[] = []
  const overrideExpiresAt: (string | null)[] = []

  for (let i = 0; i < 16; i++) {
    const r = relaysByChannel.get(i)
    channels.push(r?.observed_state === true)
    timestamps.push(r?.changed_at ?? null)
    modes.push(r?.command_mode ?? null)
    overrideExpiresAt.push(r?.command_expires_at ?? null)
  }

  return {
    channels,
    timestamps,
    mcp_connected: snapshot.freshness === 'FRESH',
    simulation: false,
    modes,
    override_expires_at: overrideExpiresAt,
  }
}

async function doFetch({ forceRegistry = false }: RefreshOptions = {}): Promise<void> {
  if (inFlight) {
    if (!forceRegistry || inFlightForceRegistry) return inFlight
    await inFlight
    return doFetch({ forceRegistry: true })
  }

  const seq = ++sequence
  const fetchPromise = (async () => {
    try {
      const snapshot = await apiClient.getControlSnapshot()
      if (seq !== sequence) return

      const registryVersion = Number.isSafeInteger(snapshot.registry_version)
        ? snapshot.registry_version
        : null
      const shouldFetchRegistry =
        forceRegistry || state.snapshot === null || registryVersion !== state.registryVersion
      const registry = shouldFetchRegistry
        ? await apiClient.getDeviceRegistry()
        : state.registry
      if (seq !== sequence) return

      const channels = deriveChannels(snapshot, registry)
      const relayState = deriveRelayState(snapshot)
      const mcpConnected = snapshot.freshness === 'FRESH'

      state = {
        snapshot,
        registry,
        channels,
        relayState,
        mcpConnected,
        registryVersion,
        loading: false,
        error: null,
      }
      emit()
    } catch (err) {
      if (seq !== sequence) return

      logger.error('Failed to fetch control snapshot:', err)
      state = {
        ...state,
        loading: false,
        error: 'Failed to load device data',
      }
      emit()
    } finally {
      if (seq === sequence) {
        inFlight = null
        inFlightForceRegistry = false
      }
    }
  })()

  inFlight = fetchPromise
  inFlightForceRegistry = forceRegistry
  return fetchPromise
}

function startTimer(): void {
  if (timerId !== null) return
  timerId = setInterval(() => {
    void doFetch()
  }, POLL_INTERVAL_MS)
}

function stopTimer(): void {
  if (timerId === null) return
  clearInterval(timerId)
  timerId = null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    startTimer()
    void doFetch()
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      stopTimer()
    }
  }
}

function getSnapshot(): ControlSnapshotStoreState {
  return state
}

function refreshNow(options?: RefreshOptions): Promise<void> {
  return doFetch(options)
}

export function useControlSnapshot(): UseControlSnapshotReturn {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return {
    ...snap,
    refresh: refreshNow,
    refreshNow,
  }
}
