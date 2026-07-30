import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { normalizeDeviceControlCluster } from '../config/zones'
import { apiClient } from '../services/api'
import { useControlSnapshot } from '../hooks/useControlSnapshot'
import { logger } from '../utils/logger'
import DeviceTable from './devices/DeviceTable'
import DfrBoardsPanel from './devices/DfrBoardsPanel'
import RelayChannelMatrix from './devices/RelayChannelMatrix'
import SystemSettingsPanel from './devices/SystemSettingsPanel'
import { buildRelayChannelViewModels } from './devices/relayViewModel'

export default function DeviceManager() {
  const [activeTab, setActiveTab] = useState<'devices' | 'settings'>('devices')
  const [refreshKey, setRefreshKey] = useState(0)
  const handleSharedRefresh = useCallback(() => setRefreshKey((k) => k + 1), [])
  const { snapshot, mcpConnected, refreshNow } = useControlSnapshot()
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)
  const [nowMs, setNowMs] = useState(Date.now())

  const relayChannels = useMemo(
    () => buildRelayChannelViewModels(snapshot),
    [snapshot],
  )

  async function refreshRelayState() {
    try {
      await refreshNow()
    } catch (error) {
      logger.warn('Unable to refresh relay board state', error)
    }
  }

  useEffect(() => {
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1000)
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
    const vm = relayChannels.find((c) => c.channel === channel)
    if (!vm) return

    const isAssigned = !!vm.isAssigned && !!vm.assignedDeviceName && !!vm.location && !!vm.cluster

    try {
      if (isAssigned) {
        const location = vm.location!
        const deviceName = vm.assignedDeviceName!
        const rawCluster = vm.cluster!
        const cluster = normalizeDeviceControlCluster(location, rawCluster)

        if (action === 'auto') {
          await apiClient.commandDevice(location, cluster, deviceName, { action: 'AUTO', reason: 'Relay menu AUTO' })
          toast.success(`R${vm.physicalRelay} set to auto`)
        } else if (action === 'off') {
          await apiClient.commandDevice(location, cluster, deviceName, { action: 'MANUAL_OFF', reason: 'Relay menu OFF' })
          toast.success(`R${vm.physicalRelay} turned off`)
        } else {
          const durationSeconds =
            action === 'timer-5m'
              ? 300
              : action === 'timer-10m'
              ? 600
              : action === 'timer-30m'
              ? 1800
              : 3600
          await apiClient.commandDevice(location, cluster, deviceName, {
            action: 'TIMED_ON',
            duration_seconds: durationSeconds,
            reason: `Relay menu ON ${durationSeconds / 60}m`,
          })
          toast.success(`R${vm.physicalRelay} ON for ${durationSeconds / 60}m`)
        }
      } else {
        if (action === 'off') {
          await apiClient.controlChannel(channel, 0)
          toast.success(`Relay R${vm.physicalRelay} (${vm.pinLabel || '?'}) turned off`)
        } else if (action === 'auto') {
          toast.error('Auto requires an assigned device')
        } else {
          const durationSeconds =
            action === 'timer-5m'
              ? 300
              : action === 'timer-10m'
              ? 600
              : action === 'timer-30m'
              ? 1800
              : 3600
          await apiClient.controlChannel(channel, 1, durationSeconds)
          toast.success(`Relay R${vm.physicalRelay} (${vm.pinLabel || '?'}) ON for ${durationSeconds / 60}m`)
        }
      }

      await refreshRelayState()
      setMenuOpenChannel(null)
    } catch (error) {
      logger.error(`Failed relay menu action ${action} for channel ${channel}`, error)
      toast.error('Failed to apply relay action')
    }
  }

  const relayStatusLabel = mcpConnected
    ? 'Connected'
    : 'Unavailable'

  const relayStatusClasses = mcpConnected
    ? 'bg-status-success-bg/40 text-status-success-text border-status-success-border/70'
    : 'bg-status-danger-bg/40 text-status-danger-text border-status-danger-border/60'

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
          <DfrBoardsPanel />
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

          <div className="flex flex-row items-start gap-2 overflow-x-auto min-w-0">
            <div className="flex-1 min-w-0">
              <DeviceTable refreshKey={refreshKey} onRefresh={handleSharedRefresh} />
            </div>
            <div className="shrink-0 min-w-0">
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
          </div>
        </>
      )}
    </div>
  )
}