import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { normalizeDeviceControlCluster } from '../config/zones'
import { apiClient } from '../services/api'
import type { ChannelInfo, RelayBoardStateResponse } from '../types/relay'
import { logger } from '../utils/logger'
import DeviceTable from './devices/DeviceTable'
import DfrBoardsPanel from './devices/DfrBoardsPanel'
import RelayChannelMatrix from './devices/RelayChannelMatrix'
import SystemSettingsPanel from './devices/SystemSettingsPanel'
import {
  buildRelayChannelViewModels,
  getRelayNumber,
  getRelayPinLabel,
} from './devices/relayViewModel'

const DEFAULT_RELAY_STATE: RelayBoardStateResponse = {
  channels: Array(16).fill(false),
  timestamps: Array(16).fill(null),
  mcp_connected: false,
  simulation: false,
  modes: Array(16).fill(null),
  override_expires_at: Array(16).fill(null),
}

export default function DeviceManager() {
  const [activeTab, setActiveTab] = useState<'devices' | 'settings'>('devices')
  const [refreshKey, setRefreshKey] = useState(0)
  const handleSharedRefresh = useCallback(() => setRefreshKey((k) => k + 1), [])
  const [channels, setChannels] = useState<ChannelInfo[]>([])
  const [relayState, setRelayState] = useState<RelayBoardStateResponse>(DEFAULT_RELAY_STATE)
  const [loading, setLoading] = useState(true)
  const [loadingError, setLoadingError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(Date.now())
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)

  const persistedChannelMap = useMemo(
    () => new Map(channels.map((channelInfo) => [channelInfo.channel, channelInfo])),
    [channels]
  )

  const relayChannels = useMemo(() => {
    const vms = buildRelayChannelViewModels(
      channels,
      relayState.channels,
      relayState.timestamps,
      relayState.modes,
      relayState.override_expires_at
    )
    return vms.sort((a, b) => getRelayNumber(a.channel) - getRelayNumber(b.channel))
  }, [channels, relayState])

  async function loadChannels(showLoader = true) {
    if (showLoader) {
      setLoading(true)
    }

    setLoadingError(null)

    try {
      const response = await apiClient.getChannels()
      const sortedChannels = Object.values(response.channels).sort((a, b) => a.channel - b.channel)
      setChannels(sortedChannels)
    } catch (error) {
      logger.error('Error loading channels:', error)
      setLoadingError('Unable to load relay channel assignments.')
    } finally {
      if (showLoader) {
        setLoading(false)
      }
    }
  }

  async function refreshRelayState() {
    try {
      const response = await apiClient.getRelayBoardState()
      setRelayState(response)
    } catch (error) {
      logger.warn('Unable to refresh relay board state', error)
      setRelayState(DEFAULT_RELAY_STATE)
    }
  }

  useEffect(() => {
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(intervalId)
  }, [])

  useEffect(() => {
    void loadChannels()
  }, [])

  useEffect(() => {
    void refreshRelayState()
    const intervalId = window.setInterval(() => {
      void refreshRelayState()
    }, 5000)

    return () => window.clearInterval(intervalId)
  }, [])

  useEffect(() => {
    const onDocumentClick = () => setMenuOpenChannel(null)
    document.addEventListener('click', onDocumentClick)
    return () => document.removeEventListener('click', onDocumentClick)
  }, [])

  async function handleRelayMenuAction(
    channel: number,
    action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off'
  ) {
    const channelInfo = persistedChannelMap.get(channel)
    const isAssigned = !!(channelInfo?.device_name && channelInfo.location && channelInfo.cluster)

    try {
      if (isAssigned) {
        const location = channelInfo.location!
        const deviceName = channelInfo.device_name!
        const rawCluster = channelInfo.cluster!
        const cluster = normalizeDeviceControlCluster(location, rawCluster)

        if (action === 'auto') {
          await apiClient.setDeviceMode(location, cluster, deviceName, 'auto')
          toast.success(`Channel ${channel} set to auto`)
        } else if (action === 'off') {
          await apiClient.setDeviceMode(location, cluster, deviceName, 'manual')
          await apiClient.controlDevice(location, cluster, deviceName, 0, 'Manual off from relay menu')
          toast.success(`Channel ${channel} turned off`)
        } else {
          const durationSeconds =
            action === 'timer-5m'
              ? 300
              : action === 'timer-10m'
              ? 600
              : action === 'timer-30m'
              ? 1800
              : 3600

          await apiClient.setDeviceMode(location, cluster, deviceName, 'manual')
          await apiClient.controlDevice(
            location,
            cluster,
            deviceName,
            1,
            'Manual timed activation',
            durationSeconds
          )
          toast.success(`Channel ${channel} manual activation started`)
        }
      } else {
        if (action === 'off') {
          await apiClient.controlChannel(channel, 0)
          toast.success(`Relay R${getRelayNumber(channel)} (${getRelayPinLabel(channel)}) turned off`)
        } else if (action !== 'auto') {
          const durationSeconds =
            action === 'timer-5m'
              ? 300
              : action === 'timer-10m'
              ? 600
              : action === 'timer-30m'
              ? 1800
              : 3600
          await apiClient.controlChannel(channel, 1, durationSeconds)
          toast.success(`Relay R${getRelayNumber(channel)} (${getRelayPinLabel(channel)}) ON for ${durationSeconds / 60}m`)
        }
      }

      await refreshRelayState()
      setMenuOpenChannel(null)
    } catch (error) {
      logger.error(`Failed relay menu action ${action} for channel ${channel}`, error)
      toast.error('Failed to apply relay action')
    }
  }

  const relayStatusLabel = relayState.mcp_connected
    ? relayState.simulation
      ? 'Simulation'
      : 'Connected'
    : 'Unavailable'

  const relayStatusClasses = relayState.mcp_connected
    ? relayState.simulation
      ? 'bg-status-warning-bg/40 text-status-warning-text border-status-warning-border/60'
      : 'bg-status-success-bg/40 text-status-success-text border-status-success-border/70'
    : 'bg-status-danger-bg/40 text-status-danger-text border-status-danger-border/60'

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-text-secondary">Loading channels...</p>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-2">
      <div className="flex border-b border-border-subtle">
        <button
          type="button"
          onClick={() => setActiveTab('devices')}
          className={`px-4 py-2 text-sm font-medium ${
            activeTab === 'devices'
              ? 'border-b-2 border-btn-primary-light text-btn-primary-text'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Devices & Relays
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('settings')}
          className={`px-4 py-2 text-sm font-medium ${
            activeTab === 'settings'
              ? 'border-b-2 border-btn-primary-light text-btn-primary-text'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Settings
        </button>
      </div>

      {activeTab === 'settings' && <SystemSettingsPanel />}

      {activeTab === 'devices' && (
        <>
          <DfrBoardsPanel refreshKey={refreshKey} onRefresh={handleSharedRefresh} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-2xl font-bold text-text-input">Devices and Relay Mapping</h2>
              <p className="mt-1 text-sm text-text-muted">
                Master assignment view for MCP23017 relay channels, pins, and device mapping.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className={`rounded border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide ${relayStatusClasses}`}>
                Relay board: {relayStatusLabel}
              </div>
            </div>
          </div>

          {loadingError && (
            <div className="rounded-md border border-status-danger-border/60 bg-status-danger-bg/20 px-4 py-2 text-sm text-status-danger-text">
              {loadingError}
            </div>
          )}

          <DeviceTable refreshKey={refreshKey} onRefresh={handleSharedRefresh} />

          <div>
            <RelayChannelMatrix
              channels={relayChannels}
              nowMs={nowMs}
              variant="panel"
              menuOpenChannel={menuOpenChannel}
              onToggleMenu={(channel) =>
                setMenuOpenChannel((previous) => (previous === channel ? null : channel))
              }
              onMenuAction={handleRelayMenuAction}
            />
          </div>
        </>
      )}
    </div>
  )
}