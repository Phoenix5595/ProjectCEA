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
  loading: boolean
  error: string | null
}

export interface UseControlSnapshotReturn extends ControlSnapshotStoreState {
  refresh: () => Promise<void>
  refreshNow: () => Promise<void>
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
  loading: true,
  error: null,
}

let state: ControlSnapshotStoreState = INITIAL_STATE
const listeners = new Set<() => void>()
let timerId: ReturnType<typeof setInterval> | null = null
let inFlight: Promise<void> | null = null
let sequence = 0

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function deriveChannels(
  snapshot: ControlSnapshotResponse | null,
  registry: DeviceRegistryEntry[],
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

function deriveRelayState(snapshot: ControlSnapshotResponse | null): RelayBoardStateResponse | null {
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

async function doFetch(): Promise<void> {
  if (inFlight) return inFlight

  const seq = ++sequence
  const fetchPromise = (async () => {
    try {
      const [snapshot, registry] = await Promise.all([
        apiClient.getControlSnapshot(),
        apiClient.getDeviceRegistry(),
      ])

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
      }
    }
  })()

  inFlight = fetchPromise
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

function refreshNow(): Promise<void> {
  return doFetch()
}

export function useControlSnapshot(): UseControlSnapshotReturn {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return {
    ...snap,
    refresh: refreshNow,
    refreshNow,
  }
}
