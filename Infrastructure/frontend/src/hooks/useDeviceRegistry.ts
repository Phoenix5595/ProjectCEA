import { useState, useEffect, useCallback, useRef } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { DeviceRegistryEntry } from '../types/device'
import type { RelayBoardStateResponse, ChannelInfo } from '../types/relay'

export interface DeviceRegistryState {
  registry: DeviceRegistryEntry[]
  relayState: RelayBoardStateResponse | null
  channels: ChannelInfo[]
  mcpConnected: boolean
  loading: boolean
  error: string | null
}

export interface UseDeviceRegistryReturn extends DeviceRegistryState {
  refresh: () => Promise<void>
  registryByDeviceId: Map<number, DeviceRegistryEntry>
  channelByNumber: Map<number, ChannelInfo>
}

const DEFAULT_RELAY_STATE: RelayBoardStateResponse = {
  channels: Array(16).fill(false),
  timestamps: Array(16).fill(null),
  mcp_connected: false,
  simulation: false,
  modes: Array(16).fill(null),
  override_expires_at: Array(16).fill(null),
}

export function useDeviceRegistry(): UseDeviceRegistryReturn {
  const [registry, setRegistry] = useState<DeviceRegistryEntry[]>([])
  const [relayState, setRelayState] = useState<RelayBoardStateResponse | null>(null)
  const [channels, setChannels] = useState<ChannelInfo[]>([])
  const [mcpConnected, setMcpConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<number | null>(null)

  const fetchAll = useCallback(async () => {
    setError(null)
    try {
      const [registryData, relayData] = await Promise.all([
        apiClient.getDeviceRegistry(),
        apiClient.getRelayBoardState(),
      ])
      setRegistry(registryData)
      setRelayState(relayData)
      setMcpConnected(relayData.mcp_connected)
      setChannels(Array.from({ length: 16 }, (_, channel) => {
        const device = registryData.find((entry) => (entry.channel ?? entry.relay_channel) === channel)
        return {
          channel,
          device_name: device?.device_name ?? null,
          display_name: device?.display_name ?? null,
          device_type: device?.device_type ?? null,
          location: device?.location ?? null,
          cluster: device?.cluster ?? null,
          light_name: device?.device_type === 'light' ? device.display_name : null,
        }
      }))
    } catch (err) {
      logger.error('Failed to fetch device registry data:', err)
      setError('Failed to load device data')
      setRelayState(DEFAULT_RELAY_STATE)
      setMcpConnected(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchAll()
    intervalRef.current = window.setInterval(fetchAll, 5000)
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
      }
    }
  }, [fetchAll])

  const registryByDeviceId = new Map(registry.map((d) => [d.device_id, d]))
  const channelByNumber = new Map(channels.map((c) => [c.channel, c]))

  return {
    registry,
    relayState,
    channels,
    mcpConnected,
    loading,
    error,
    refresh: fetchAll,
    registryByDeviceId,
    channelByNumber,
  }
}
