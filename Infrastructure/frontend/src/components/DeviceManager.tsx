import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { normalizeDeviceControlCluster, ZONES } from '../config/zones'
import { apiClient } from '../services/api'
import type { ControlHistoryEntry } from '../types/device'
import { DEVICE_TYPES } from '../types/relay'
import type {
  ChannelInfo,
  DeviceTypeOption,
  LightNameOption,
  RelayBoardStateResponse,
} from '../types/relay'
import { logger } from '../utils/logger'
import DfrBoardsPanel from './devices/DfrBoardsPanel'
import RelayChannelMatrix from './devices/RelayChannelMatrix'
import {
  buildLastStateChangeMap,
  buildRelayChannelViewModels,
  getChannelDisplayName,
  getReadableDeviceType,
  getRelayPinLabel,
  getUniqueLocationClusterPairs,
} from './devices/relayViewModel'

interface ChannelEditForm {
  device_name: string
  device_type: DeviceTypeOption | ''
  location: string
  cluster: string
  light_name: string
}

const DEFAULT_LOCATION = ZONES[0]?.location || ''
const DEFAULT_CLUSTER = ZONES[0]?.cluster || ''

const EMPTY_EDIT_FORM: ChannelEditForm = {
  device_name: '',
  device_type: '',
  location: DEFAULT_LOCATION,
  cluster: DEFAULT_CLUSTER,
  light_name: '',
}

function toUiDeviceType(deviceType: string | null): DeviceTypeOption | '' {
  if (!deviceType) {
    return ''
  }

  if (deviceType === 'co2' || deviceType === 'co2_tank') {
    return 'co2 tank'
  }

  if (deviceType === 'extraction_fan') {
    return 'extraction fan'
  }

  if ((DEVICE_TYPES as readonly string[]).includes(deviceType)) {
    return deviceType as DeviceTypeOption
  }

  return ''
}

function getDefaultLocationCluster(channelInfo: ChannelInfo | null): {
  location: string
  cluster: string
} {
  const location = channelInfo?.location || DEFAULT_LOCATION
  const clusterFromLocation = ZONES.find((zone) => zone.location === location)?.cluster || DEFAULT_CLUSTER

  return {
    location,
    cluster: normalizeDeviceControlCluster(location, channelInfo?.cluster ?? clusterFromLocation),
  }
}

export default function DeviceManager() {
  const [channels, setChannels] = useState<ChannelInfo[]>([])
  const [lightNames, setLightNames] = useState<LightNameOption[]>([])
  const [relayState, setRelayState] = useState<RelayBoardStateResponse | null>(null)
  const [lastStateChangeByDevice, setLastStateChangeByDevice] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [loadingError, setLoadingError] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<ChannelEditForm>(EMPTY_EDIT_FORM)
  const [saving, setSaving] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const [manualTimersByChannel, setManualTimersByChannel] = useState<Record<number, number>>({})
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)
  const [isClearingEdit, setIsClearingEdit] = useState(false)
  const timerActionInFlight = useRef<Set<number>>(new Set())
  const tablePanelRef = useRef<HTMLDivElement | null>(null)
  const matrixPanelRef = useRef<HTMLDivElement | null>(null)

  const persistedChannelMap = useMemo(
    () => new Map(channels.map((channelInfo) => [channelInfo.channel, channelInfo])),
    [channels]
  )

  const displayChannels = useMemo(() => {
    if (editing === null) {
      return channels
    }

    return channels.map((channelInfo) => {
      if (channelInfo.channel !== editing) {
        return channelInfo
      }

      return {
        ...channelInfo,
        device_name: editForm.device_name.trim() || null,
        device_type: editForm.device_type || null,
        location: editForm.location || null,
        cluster: editForm.cluster || null,
        light_name: editForm.device_type === 'light' ? editForm.light_name || null : null,
      }
    })
  }, [channels, editing, editForm])

  const displayChannelMap = useMemo(
    () => new Map(displayChannels.map((channelInfo) => [channelInfo.channel, channelInfo])),
    [displayChannels]
  )

  const relayChannels = useMemo(
    () =>
      buildRelayChannelViewModels(
        displayChannels,
        relayState?.channels || null,
        lastStateChangeByDevice
      ),
    [displayChannels, relayState, lastStateChangeByDevice]
  )

  const statusByChannel = useMemo(() => {
    const statuses: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }> = {}

    relayChannels.forEach((relayChannel) => {
      const timerEndAt = manualTimersByChannel[relayChannel.channel]
      if (timerEndAt && timerEndAt > nowMs) {
        const remainingMs = timerEndAt - nowMs
        const totalSeconds = Math.max(0, Math.floor(remainingMs / 1000))
        const minutes = Math.floor(totalSeconds / 60)
        const seconds = totalSeconds % 60
        statuses[relayChannel.channel] = {
          text: `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`,
          // Manual mode uses active styling by request.
          tone: 'active',
        }
        return
      }

      if (!relayChannel.isStateKnown) {
        statuses[relayChannel.channel] = { text: 'Unknown', tone: 'unknown' }
      } else if (relayChannel.isActive) {
        statuses[relayChannel.channel] = { text: 'ON', tone: 'active' }
      } else {
        statuses[relayChannel.channel] = { text: 'IDLE', tone: 'idle' }
      }
    })

    return statuses
  }, [manualTimersByChannel, nowMs, relayChannels])

  const hasPendingChanges = useMemo(() => {
    if (editing === null) {
      return false
    }

    const original = persistedChannelMap.get(editing) || null
    const defaults = getDefaultLocationCluster(original)

    const originalForm: ChannelEditForm = {
      device_name: original?.device_name || '',
      device_type: toUiDeviceType(original?.device_type || null),
      location: original?.location || defaults.location,
      cluster: original?.cluster || defaults.cluster,
      light_name: original?.device_type === 'light' ? original?.light_name || '' : '',
    }

    const normalizedCurrent: ChannelEditForm = {
      device_name: editForm.device_name.trim(),
      device_type: editForm.device_type,
      location: editForm.location,
      cluster: editForm.cluster,
      light_name: editForm.device_type === 'light' ? editForm.light_name : '',
    }

    return (
      normalizedCurrent.device_name !== originalForm.device_name ||
      normalizedCurrent.device_type !== originalForm.device_type ||
      normalizedCurrent.location !== originalForm.location ||
      normalizedCurrent.cluster !== originalForm.cluster ||
      normalizedCurrent.light_name !== originalForm.light_name
    )
  }, [editing, editForm, persistedChannelMap])

  const uniqueLightNames = useMemo(
    () => Array.from(new Map(lightNames.map((light) => [light.name, light])).values()),
    [lightNames]
  )

  const locationOptions = useMemo(
    () =>
      ZONES.filter(
        (zone, index, self) =>
          self.findIndex((candidate) => candidate.location === zone.location) === index
      ),
    []
  )

  async function loadChannels(showLoader = true) {
    if (showLoader) {
      setLoading(true)
    }

    setLoadingError(null)

    try {
      const response = await apiClient.getChannels()
      const sortedChannels = Object.values(response.channels).sort((a, b) => a.channel - b.channel)
      setChannels(sortedChannels)
      setLightNames(response.light_names || [])
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
      setRelayState(null)
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

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (editing === null || hasPendingChanges || isClearingEdit) {
        return
      }

      const target = event.target as Node | null
      const isInsideTable = tablePanelRef.current?.contains(target || null) ?? false
      const isInsideMatrix = matrixPanelRef.current?.contains(target || null) ?? false
      if (isInsideTable || isInsideMatrix) {
        return
      }

      cancelEdit()
    }

    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [editing, hasPendingChanges, isClearingEdit])

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      const currentTime = Date.now()
      Object.entries(manualTimersByChannel).forEach(([channelString, endAt]) => {
        const channel = Number(channelString)
        if (currentTime < endAt || timerActionInFlight.current.has(channel)) {
          return
        }

        const channelInfo = persistedChannelMap.get(channel)
        if (!channelInfo?.device_name || !channelInfo.location || !channelInfo.cluster) {
          setManualTimersByChannel((previous) => {
            const next = { ...previous }
            delete next[channel]
            return next
          })
          return
        }

        timerActionInFlight.current.add(channel)
        const location = channelInfo.location
        const cluster = normalizeDeviceControlCluster(location, channelInfo.cluster)
        const deviceName = channelInfo.device_name
        void (async () => {
          try {
            await apiClient.controlDevice(
              location,
              cluster,
              deviceName,
              0,
              'Manual countdown elapsed'
            )
            await apiClient.setDeviceMode(
              location,
              cluster,
              deviceName,
              'auto'
            )
            await refreshRelayState()
          } catch (error) {
            logger.error(`Failed to auto-complete manual timer for channel ${channel}`, error)
          } finally {
            timerActionInFlight.current.delete(channel)
            setManualTimersByChannel((previous) => {
              const next = { ...previous }
              delete next[channel]
              return next
            })
          }
        })()
      })
    }, 1000)

    return () => window.clearInterval(intervalId)
  }, [manualTimersByChannel, persistedChannelMap])

  useEffect(() => {
    const locationClusterPairs = getUniqueLocationClusterPairs(channels)
    if (!locationClusterPairs.length) {
      setLastStateChangeByDevice({})
      return
    }

    let active = true

    const refreshControlHistory = async () => {
      const historyByZone = await Promise.all(
        locationClusterPairs.map(async ({ location, cluster }) => {
          try {
            return await apiClient.getControlHistory(location, cluster, 100)
          } catch (error) {
            logger.warn(`Unable to load control history for ${location}/${cluster}`, error)
            return [] as ControlHistoryEntry[]
          }
        })
      )

      if (!active) {
        return
      }

      const mergedHistory = historyByZone.flat()
      setLastStateChangeByDevice(buildLastStateChangeMap(mergedHistory))
    }

    void refreshControlHistory()
    const intervalId = window.setInterval(() => {
      void refreshControlHistory()
    }, 15000)

    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [channels])

  function startEdit(channel: number) {
    if (editing === channel) {
      return
    }

    if (editing !== null && hasPendingChanges) {
      toast.error('Save current changes before editing another channel')
      return
    }

    const channelInfo = persistedChannelMap.get(channel) || null
    const defaults = getDefaultLocationCluster(channelInfo)

    setEditing(channel)
    setIsClearingEdit(false)
    setEditForm({
      device_name: channelInfo?.device_name || '',
      device_type: toUiDeviceType(channelInfo?.device_type || null),
      location: defaults.location,
      cluster: defaults.cluster,
      light_name: channelInfo?.light_name || '',
    })
  }

  function openEditFromRelayBox(channel: number) {
    startEdit(channel)
    window.requestAnimationFrame(() => {
      const tableRow = document.getElementById(`channel-row-${channel}`)
      tableRow?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }

  async function handleRelayMenuAction(
    channel: number,
    action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off'
  ) {
    const channelInfo = persistedChannelMap.get(channel)
    if (!channelInfo?.device_name || !channelInfo.location || !channelInfo.cluster) {
      toast.error('Assign this channel before controlling it')
      return
    }

    const { location, device_name: deviceName } = channelInfo
    const cluster = normalizeDeviceControlCluster(location, channelInfo.cluster)

    try {
      if (action === 'auto') {
        await apiClient.setDeviceMode(location, cluster, deviceName, 'auto')
        setManualTimersByChannel((previous) => {
          const next = { ...previous }
          delete next[channel]
          return next
        })
        toast.success(`Channel ${channel} set to auto`)
      } else if (action === 'off') {
        await apiClient.setDeviceMode(location, cluster, deviceName, 'manual')
        await apiClient.controlDevice(location, cluster, deviceName, 0, 'Manual off from relay menu')
        setManualTimersByChannel((previous) => {
          const next = { ...previous }
          delete next[channel]
          return next
        })
        toast.success(`Channel ${channel} turned off`)
      } else {
        const durationMs =
          action === 'timer-5m'
            ? 5 * 60 * 1000
            : action === 'timer-10m'
            ? 10 * 60 * 1000
            : action === 'timer-30m'
            ? 30 * 60 * 1000
            : 60 * 60 * 1000

        await apiClient.setDeviceMode(location, cluster, deviceName, 'manual')
        await apiClient.controlDevice(location, cluster, deviceName, 1, 'Manual timed activation')
        setManualTimersByChannel((previous) => ({
          ...previous,
          [channel]: Date.now() + durationMs,
        }))
        toast.success(`Channel ${channel} manual activation started`)
      }

      await refreshRelayState()
      setMenuOpenChannel(null)
    } catch (error) {
      logger.error(`Failed relay menu action ${action} for channel ${channel}`, error)
      toast.error('Failed to apply relay action')
    }
  }

  function clearChannelRow(channel: number) {
    const existing = persistedChannelMap.get(channel) || null
    if (!existing) {
      return
    }

    if (editing !== null && editing !== channel && hasPendingChanges) {
      toast.error('Save current changes before clearing another channel')
      return
    }

    setEditing(channel)
    setIsClearingEdit(true)
    setEditForm({
      device_name: '',
      device_type: '',
      location: '',
      cluster: '',
      light_name: '',
    })

    toast.success(`Channel ${channel} marked for clearing`)
  }

  function cancelEdit() {
    setEditing(null)
    setIsClearingEdit(false)
    setEditForm(EMPTY_EDIT_FORM)
  }

  async function saveEdit() {
    if (editing === null) {
      return
    }

    if (isClearingEdit) {
      setSaving(true)
      try {
        await apiClient.clearChannelDevice(editing)
        await loadChannels(false)
        await refreshRelayState()
        cancelEdit()
        toast.success(`Channel ${editing} cleared`)
      } catch (error) {
        logger.error('Error clearing channel device:', error)
        toast.error('Failed to clear channel')
      } finally {
        setSaving(false)
      }
      return
    }

    if (!editForm.device_name.trim()) {
      toast.error('Device name is required')
      return
    }

    if (!editForm.device_type) {
      toast.error('Device type is required')
      return
    }

    if (!editForm.location) {
      toast.error('Location is required for assigned devices')
      return
    }

    if (editForm.device_type === 'light' && !editForm.light_name) {
      toast.error('Light name is required for lights')
      return
    }

    setSaving(true)

    try {
      await apiClient.updateChannelDevice(
        editing,
        editForm.device_name.trim(),
        editForm.device_type,
        editForm.location,
        normalizeDeviceControlCluster(editForm.location, editForm.cluster),
        editForm.device_type === 'light' ? editForm.light_name : undefined
      )

      await loadChannels(false)
      await refreshRelayState()
      cancelEdit()
      toast.success(`Channel ${editing} updated`)
    } catch (error) {
      logger.error('Error updating channel device:', error)
      toast.error('Failed to update device configuration')
    } finally {
      setSaving(false)
    }
  }

  const relayStatusLabel = relayState
    ? relayState.mcp_connected
      ? relayState.simulation
        ? 'Simulation'
        : 'Connected'
      : 'Unavailable'
    : 'Unavailable'

  const relayStatusClasses = relayState
    ? relayState.mcp_connected
      ? relayState.simulation
        ? 'bg-status-warning-bg/40 text-status-warning-text border-status-warning-border/60'
        : 'bg-status-success-bg/40 text-status-success-text border-status-success-border/70'
      : 'bg-status-danger-bg/40 text-status-danger-text border-status-danger-border/60'
    : 'bg-surface-tertiary text-text-muted border-border-emphasis'

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-text-secondary">Loading channels...</p>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-2">
      <DfrBoardsPanel />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold text-text-input">Devices and Relay Mapping</h2>
          <p className="mt-1 text-sm text-text-muted">
            Master assignment view for MCP23017 relay channels, pins, and device mapping.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {hasPendingChanges && (
            <button
              type="button"
              onClick={saveEdit}
              disabled={saving}
              className="rounded-sm bg-btn-primary-light px-3 py-1 text-xs font-semibold text-text-default hover:bg-btn-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          )}
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

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div ref={tablePanelRef} className="rounded-lg border border-border-subtle bg-surface-primary shadow-md md:col-span-1">
          <div className="border-b border-border-subtle px-2 py-1">
            <h3 className="text-lg font-semibold text-text-default">Channel Assignment Table</h3>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-border-default">
              <thead className="bg-surface-secondary">
                <tr>
                  <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                    Channel
                  </th>
                  <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                    Device Type
                  </th>
                  <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                    Device Name
                  </th>
                  <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                    Location
                  </th>
                </tr>
              </thead>

              <tbody className="divide-y divide-border-subtle bg-surface-primary">
                {relayChannels.map((relayChannel) => {
                  const channelInfo = displayChannelMap.get(relayChannel.channel)
                  const isEditing = editing === relayChannel.channel
                  const isEmpty = !channelInfo?.device_name
                  return (
                    <tr
                      key={relayChannel.channel}
                      id={`channel-row-${relayChannel.channel}`}
                      onClick={() => startEdit(relayChannel.channel)}
                      className={[
                        'cursor-pointer',
                        isEditing ? 'bg-btn-primary-dim/20' : '',
                        isEmpty ? 'bg-surface-secondary/50' : 'hover:bg-surface-secondary',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                    >
                      <td
                        className="whitespace-nowrap px-2 py-1 text-sm font-medium text-text-input"
                        onDoubleClick={(event) => {
                          event.stopPropagation()
                          clearChannelRow(relayChannel.channel)
                        }}
                        title="Double-click to clear this row"
                      >
                        <div>{relayChannel.channel}</div>
                        <div className="text-xs text-text-muted">{getRelayPinLabel(relayChannel.channel)}</div>
                      </td>

                      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                        {isEditing ? (
                          <select
                            value={editForm.device_type}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) =>
                              setEditForm({
                                ...editForm,
                                device_type: event.target.value as DeviceTypeOption | '',
                                light_name: '',
                              })
                            }
                            className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                          >
                            <option value="">Select type</option>
                            {DEVICE_TYPES.map((deviceType) => (
                              <option key={deviceType} value={deviceType}>
                                {getReadableDeviceType(deviceType)}
                              </option>
                            ))}
                          </select>
                        ) : channelInfo?.device_type ? (
                          <span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2.5 py-0.5 text-xs font-medium text-btn-primary-text">
                            {getReadableDeviceType(channelInfo.device_type)}
                          </span>
                        ) : (
                          <span className="text-text-subtle">-</span>
                        )}
                      </td>

                      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                        {isEditing ? (
                          editForm.device_type === 'light' ? (
                            <select
                              value={editForm.light_name}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) => {
                                const selectedLightName = event.target.value
                                const selectedLight =
                                  uniqueLightNames.find(
                                    (light) =>
                                      light.name === selectedLightName &&
                                      light.location === editForm.location
                                  ) ||
                                  uniqueLightNames.find((light) => light.name === selectedLightName)

                                setEditForm({
                                  ...editForm,
                                  light_name: selectedLightName,
                                  device_name: selectedLight?.device_name || '',
                                })
                              }}
                              className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                            >
                              <option value="">Select light</option>
                              {uniqueLightNames.map((light) => (
                                <option
                                  key={`${light.location}-${light.cluster}-${light.device_name}`}
                                  value={light.name}
                                >
                                  {light.name}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              value={editForm.device_name}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) =>
                                setEditForm({ ...editForm, device_name: event.target.value })
                              }
                              className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                              placeholder="Device name"
                            />
                          )
                        ) : (
                          (channelInfo ? getChannelDisplayName(channelInfo) : null) || (
                            <span className="italic text-text-subtle">Empty</span>
                          )
                        )}
                      </td>

                      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                        {isEditing ? (
                          <select
                            value={editForm.location}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => {
                              const nextLocation = event.target.value
                              const firstCluster =
                                nextLocation
                                  ? ZONES.find((zone) => zone.location === nextLocation)?.cluster ||
                                    DEFAULT_CLUSTER
                                  : ''
                              setEditForm({
                                ...editForm,
                                location: nextLocation,
                                // Cluster remains internal for API compatibility.
                                cluster: firstCluster,
                              })
                            }}
                            className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                          >
                            <option value="">No location</option>
                            {locationOptions.map((zone) => (
                              <option key={zone.location} value={zone.location}>
                                {zone.location}
                              </option>
                            ))}
                          </select>
                        ) : channelInfo?.location ? (
                          <span className="text-xs">{channelInfo.location}</span>
                        ) : (
                          <span className="text-text-subtle">No location</span>
                        )}
                      </td>

                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div ref={matrixPanelRef} className="rounded-lg border border-border-subtle bg-surface-primary p-2 shadow-md md:col-span-1">
          <div className="mb-2 flex items-start justify-between gap-1">
            <div>
              <h3 className="text-lg font-semibold text-text-default">Relay Matrix</h3>
            </div>
          </div>

          <RelayChannelMatrix
            channels={relayChannels}
            nowMs={nowMs}
            editingChannel={editing}
            onSelectChannel={openEditFromRelayBox}
            statusByChannel={statusByChannel}
            menuOpenChannel={menuOpenChannel}
            onToggleMenu={(channel) =>
              setMenuOpenChannel((previous) => (previous === channel ? null : channel))
            }
            onMenuAction={handleRelayMenuAction}
          />
        </div>
      </div>
    </div>
  )
}

