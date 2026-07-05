import { useParams } from 'react-router-dom'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { apiClient } from '../services/api'
import { extractErrorMessage } from '../utils/errors'
import { logger } from '../utils/logger'
import { getLocationDisplayName, getLocationBackendName, getClusterDisplayName } from '../config/zones'
import type { RoomModeWithParams, ModeParameters } from '../types/modes'
import { useControlActions } from '../contexts/ControlActionsContext'
import ClimatePeriodTimeline from '../components/ClimatePeriodTimeline'
import ClimatePeriodsTable from '../components/ClimatePeriodsTable'
import LightIntensity from '../components/LightIntensity'
import VerticalPIDBlock from '../components/VerticalPIDBlock'
import VerticalNotesBlock from '../components/VerticalNotesBlock'
import ManualLightControl from '../components/ManualLightControl'
import RelayChannelMatrix from '../components/devices/RelayChannelMatrix'
import { buildRelayChannelViewModels, makeDeviceKey } from '../components/devices/relayViewModel'
import type { RelayChannelViewModel } from '../components/devices/relayViewModel'
import type { ChannelInfo } from '../types/relay'
import type { ClimatePeriod } from '../types/climatePeriod'

export type ZoneConfigSection = 'control' | 'automation';

export interface ZoneConfigProps {
  location?: string;
  cluster?: string;
  section?: ZoneConfigSection;
}

interface RawClimatePeriod {
  id?: number
  period_name: string
  start_time?: string | null
  end_time?: string | null
  ramp_minutes: number
  heating_setpoint?: number | null
  cooling_setpoint?: number | null
  vpd_setpoint?: number | null
  co2_setpoint?: number | null
  details?: string | null
}

function mapPeriodsFromApi(periods: RawClimatePeriod[]): ClimatePeriod[] {
  return periods.map((p) => ({
    id: p.id,
    period_name: p.period_name,
    start_time: p.start_time ? p.start_time.substring(0, 5) : '00:00',
    end_time: p.end_time ? p.end_time.substring(0, 5) : '00:00',
    ramp_minutes: p.ramp_minutes,
    heating_setpoint: p.heating_setpoint ?? null,
    cooling_setpoint: p.cooling_setpoint ?? null,
    vpd_setpoint: p.vpd_setpoint != null ? Math.round(p.vpd_setpoint * 100) / 100 : null,
    co2_setpoint: p.co2_setpoint ?? null,
    details: p.details || ''
  }))
}

function createConstantPeriod(modeName: string): ClimatePeriod {
  return {
    period_name: `${modeName.charAt(0).toUpperCase()}${modeName.slice(1)} Constant`,
    start_time: '00:00',
    end_time: '00:00',
    ramp_minutes: 0,
    heating_setpoint: null,
    cooling_setpoint: null,
    vpd_setpoint: null,
    co2_setpoint: null,
    details: '24h constant setpoints'
  }
}

export default function ZoneConfig({
  location: propsLocation,
  cluster: propsCluster,
  section = 'control',
}: ZoneConfigProps) {
  const { location: locationParam, cluster: urlCluster } = useParams<{ location: string; cluster: string }>()
  const { setActions } = useControlActions()
  const lightIntensityRef = useRef<{ savePendingChanges: () => Promise<void> }>(null)

  const location = propsLocation 
    ? getLocationBackendName(propsLocation)
    : (locationParam ? getLocationBackendName(locationParam) : null)
  const cluster = propsCluster ?? urlCluster ?? 'main'
  
  const [roomMode, setRoomMode] = useState<RoomModeWithParams | null>(null)
  const [climatePeriods, setClimatePeriods] = useState<ClimatePeriod[]>([])
  const [loading, setLoading] = useState(section === 'control')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Relay matrix state
  const [relayChannels, setRelayChannels] = useState<RelayChannelViewModel[]>([]) // eslint-disable-line @typescript-eslint/no-unused-vars
  const [relayState, setRelayState] = useState<boolean[] | null>(null)
  const [relayTimestamps, setRelayTimestamps] = useState<(string | null)[]>(Array(16).fill(null))
  const [nowMs, setNowMs] = useState(Date.now())
  // @ts-ignore
  const [mcpConnected, setMcpConnected] = useState<boolean>(true)
  const [channelInfoList, setChannelInfoList] = useState<ChannelInfo[]>([])

  const fetchRelayData = useCallback(async () => {
    try {
      const stateRes = await apiClient.getRelayBoardState()
      setRelayState(stateRes.channels)
      setRelayTimestamps(stateRes.timestamps ?? Array(16).fill(null))
      setMcpConnected(stateRes.mcp_connected)
    } catch (err) {
      logger.error('Failed to fetch relay board state:', err)
      setRelayState(null)
      setMcpConnected(false)
    }
  }, [])

  const loadChannels = useCallback(async () => {
    try {
      const res = await apiClient.getChannels()
      setChannelInfoList(Object.values(res.channels))
    } catch (err) {
      logger.error('Failed to fetch channel assignments:', err)
    }
  }, [])

  // Load channel assignments once on mount
  useEffect(() => {
    loadChannels()
  }, [loadChannels])

  // Poll relay state every 5 seconds
  useEffect(() => {
    fetchRelayData()
    const interval = setInterval(fetchRelayData, 5000)
    return () => clearInterval(interval)
  }, [fetchRelayData])

  // Tick nowMs every second for elapsed time display
  useEffect(() => {
    const interval = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  // Build view models when channel data or relay state changes
  useEffect(() => {
    const lastStateMap: Record<string, string> = {}
    channelInfoList.forEach((info) => {
      const ts = relayTimestamps[info.channel]
      if (ts && info.location && info.cluster && info.device_name) {
        const key = makeDeviceKey(info.location, info.cluster, info.device_name)
        lastStateMap[key] = ts
      }
    })
    setRelayChannels(buildRelayChannelViewModels(channelInfoList, relayState, lastStateMap))
  }, [channelInfoList, relayState, relayTimestamps])

  // Relay menu state — consumed by RelayChannelMatrix
  // @ts-ignore
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)

  const handleRelayMenuAction = useCallback(async (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => {
    const ch = relayChannels.find((c: { channel: number }) => c.channel === channel)
    if (!ch?.assignedDeviceName || !ch.location) return

    const device = ch.assignedDeviceName
    const location = ch.location
    const cluster = ch.cluster || 'main' // Device cluster, not sensor sub-cluster

    // Close dropdown
    setMenuOpenChannel(null)

    try {
      if (action === 'auto') {
        await apiClient.setDeviceMode(location, cluster, device, 'auto')
      } else if (action === 'off') {
        await apiClient.setDeviceMode(location, cluster, device, 'manual')
        await apiClient.controlDevice(location, cluster, device, 0, 'Manual override: OFF')
      } else {
        const minutes = action === 'timer-5m' ? 5 : action === 'timer-10m' ? 10 : action === 'timer-30m' ? 30 : 60
        await apiClient.setDeviceMode(location, cluster, device, 'manual')
        await apiClient.controlDevice(location, cluster, device, 1, `Manual override: ON ${minutes}m`, minutes * 60)
      }
    } catch (err) {
      logger.error(`Relay action failed for channel ${channel}:`, err)
    }
  }, [relayChannels])

  // @ts-ignore
  const statusByChannel = useMemo(() => {
    const map: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }> = {}
    const now = Date.now()
    for (const ch of relayChannels) {
      const ts = ch.lastStateChangeAt
      if (ts && ch.isActive) {
        const elapsedMs = now - new Date(ts).getTime()
        if (elapsedMs >= 0 && elapsedMs < 60 * 60 * 1000) {
          const remaining = Math.ceil((60 * 60 * 1000 - elapsedMs) / 1000)
          const minutes = Math.floor(remaining / 60)
          const seconds = remaining % 60
          map[ch.channel] = {
            text: `${minutes}:${String(seconds).padStart(2, '0')}`,
            tone: 'active',
          }
        }
      }
    }
    return map
  }, [relayChannels])

  const loadClimatePeriodsForMode = useCallback(
    async (mode: RoomModeWithParams) => {
      if (!location || !cluster) return
      const periods = await apiClient.getClimatePeriods(
        location,
        cluster,
        mode.mode_id ?? undefined,
        mode.submode_id ?? undefined
      )
      if (periods && periods.length > 0) {
        setClimatePeriods(mapPeriodsFromApi(periods as unknown as RawClimatePeriod[]))
      } else if (mode.is_constant) {
        setClimatePeriods([createConstantPeriod(mode.mode_name)])
      } else {
        setClimatePeriods([])
      }
    },
    [location, cluster]
  )

  async function loadRoomMode() {
    setLoading(true)
    setError(null)
    try {
      const mode = await apiClient.getRoomModeWithParams(location!, cluster!)
      setRoomMode(mode)

      await loadClimatePeriodsForMode(mode)
    } catch (err) {
      logger.error('Error loading room mode:', err)
      setError(extractErrorMessage(err, 'Failed to load'))
    } finally {
      setLoading(false)
    }
  }

  function handleParamChange(updates: Partial<ModeParameters>) {
    if (!roomMode) return
    setRoomMode({
      ...roomMode,
      parameters: { ...roomMode.parameters, ...updates }
    })
  }

  /** Format time to HH:MM (mode_parameters may return HH:MM:SS from DB). */
  function toHHMM(t: string | undefined): string {
    if (!t) return '06:00'
    const parts = t.trim().split(/[:\s]/)
    const h = parts[0]?.padStart(2, '0') ?? '06'
    const m = parts[1]?.padStart(2, '0') ?? '00'
    return `${h}:${m}`
  }

  const handleModeChange = useCallback(async (modeName: string, submodeName?: string) => {
    if (!location || !cluster) return
    
    try {
      const newMode = await apiClient.setRoomMode(location, cluster, { mode_name: modeName, submode_name: submodeName })
      setRoomMode(newMode)
      await loadClimatePeriodsForMode(newMode)
      setSuccess('Mode changed')
      setTimeout(() => setSuccess(null), 2000)
    } catch (err) {
      logger.error('Error changing mode:', err)
      setError(extractErrorMessage(err, 'Failed to change mode'))
    }
  }, [location, cluster, loadClimatePeriodsForMode])

  const handleSave = useCallback(async () => {
    if (!roomMode || !location || !cluster) return

    setSaving(true)
    setError(null)
    try {
      const updated = await apiClient.updateRoomParameters(location, cluster, roomMode.parameters)
      setRoomMode(updated)

      const p = updated.parameters
      const dayStart = toHHMM(p.day_start_time)
      const nightStart = toHHMM(p.night_start_time)
      // Light ramps: must match mode_parameters.light_ramp_* — not ramp_up_minutes
      // (legacy climate transition fields), or room_schedule / Redis aggregate state show wrong values.
      await apiClient.saveRoomSchedule(location, cluster, {
        day_start_time: dayStart,
        day_end_time: nightStart,
        night_start_time: nightStart,
        night_end_time: dayStart,
        ramp_up_duration: p.light_ramp_up_minutes ?? null,
        ramp_down_duration: p.light_ramp_down_minutes ?? null,
      })

      const periodsToSave =
        roomMode.is_constant && climatePeriods.length === 0
          ? [createConstantPeriod(roomMode.mode_name)]
          : climatePeriods

      await apiClient.saveClimatePeriods(
        location,
        cluster,
        periodsToSave as unknown as Record<string, unknown>[],
        updated.mode_id ?? undefined,
        updated.submode_id ?? undefined
      )

      await lightIntensityRef.current?.savePendingChanges()

      setSuccess('Saved')
      setTimeout(() => setSuccess(null), 2000)
    } catch (err) {
      logger.error('Error saving parameters:', err)
      const response = (err as { response?: { data?: { detail?: unknown } } })?.response
      const detail = response?.data?.detail
      if (Array.isArray(detail)) {
        setError(detail.join(', '))
      } else if (detail && typeof detail === 'object' && 'errors' in detail) {
        const errs = (detail as { errors?: unknown }).errors
        setError(Array.isArray(errs) ? errs.join(', ') : String(errs))
      } else if (typeof detail === 'string') {
        setError(detail)
      } else {
        setError(extractErrorMessage(err, 'Failed to save'))
      }
    } finally {
      setSaving(false)
    }
  }, [roomMode, location, cluster, climatePeriods])

  useEffect(() => {
    if (location && cluster && section === 'control') {
      loadRoomMode();
    }
  }, [location, cluster, section]);

  useEffect(() => {
    if (section !== 'control') {
      setActions({
        roomName:
          cluster === 'main'
            ? getLocationDisplayName(location || '')
            : `${getLocationDisplayName(location || '')} - ${getClusterDisplayName(location || '', cluster)}`,
        showActions: false,
      });
      return () => setActions({});
    }

    setActions({
      roomName:
        cluster === 'main'
          ? getLocationDisplayName(location || '')
          : `${getLocationDisplayName(location || '')} - ${getClusterDisplayName(location || '', cluster)}`,
      showActions: true,
      saving,
      saveSuccess: success,
      saveError: error,
      currentMode: roomMode,
      onSave: handleSave,
      onModeChange: handleModeChange,
    });

    return () => setActions({});
  }, [
    section,
    location,
    cluster,
    saving,
    success,
    error,
    roomMode,
    handleSave,
    handleModeChange,
    setActions,
  ]);

  if (!location || !cluster) {
    return <div className="text-text-default">Invalid zone</div>;
  }

  if (section === 'automation') {
    return (
      <div className="min-h-screen bg-surface-base p-1">
        <div className="max-w-[1920px] mx-auto h-[calc(100vh-1rem)] flex flex-col min-h-0">
          <div className="flex-1 min-h-0">
            <VerticalPIDBlock location={location} cluster={cluster} />
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-surface-base flex items-center justify-center text-text-muted">
        Loading...
      </div>
    );
  }

  const params = roomMode?.parameters
  const isConstant = roomMode?.is_constant || false
  const currentModeName = roomMode?.mode_name || 'veg'
  const lockedPhotoperiod = currentModeName === 'flower' ? 12 : currentModeName === 'veg' ? 18 : null

  return (
    <div className="min-h-screen bg-surface-base p-1">
      <div className="max-w-[1920px] mx-auto h-[calc(100vh-1rem)] flex flex-col">
        {params && (
          <div className="flex-1 flex flex-col gap-1 min-h-0">
            {/* Climate Timeline - 270px fixed, full width */}
            <div className="h-[300px] shrink-0 bg-surface-primary rounded-lg border border-border-subtle overflow-hidden p-2">
              {!isConstant ? (
                <ClimatePeriodTimeline
                  periods={climatePeriods}
                  lightDayStart={params?.day_start_time || '06:00'}
                  lightDayEnd={params?.night_start_time || '18:00'}
                  className="h-full"
                  onDayStartChange={(time) => handleParamChange({ day_start_time: time })}
                  onDayEndChange={(time) => handleParamChange({ night_start_time: time })}
                  lockedPhotoperiodHours={lockedPhotoperiod}
                  rampUpDuration={params.light_ramp_up_minutes}
                  rampDownDuration={params.light_ramp_down_minutes}
                  onRampUpChange={(d) => handleParamChange({ light_ramp_up_minutes: d ?? 0 })}
                  onRampDownChange={(d) => handleParamChange({ light_ramp_down_minutes: d ?? 0 })}
                />
              ) : (
                <div className="h-full flex items-center justify-center text-text-subtle text-sm">
                  Constant mode - no timeline
                </div>
              )}
            </div>

            {/* Climate Periods + Relay Matrix row - 450px */}
            <div className="flex gap-1 h-[580px] shrink-0">
              <div className="flex-1 flex flex-col gap-1 h-full overflow-hidden">
                {!isConstant ? (
                  <>
                    <div className="bg-surface-primary rounded-lg border border-border-subtle p-1 flex-[56] overflow-auto">
                      <ClimatePeriodsTable
                        periods={climatePeriods}
                        onChange={setClimatePeriods}
                      />
                    </div>
                    <div className="flex-[44] overflow-auto">
                      <LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact={true} />
                    </div>
                  </>
                ) : (
                  <ManualLightControl location={location} cluster={cluster} compact={true} />
                )}
              </div>
              <div className="w-[35%] h-full">
                {!mcpConnected && (
                  <div className="mb-1 rounded-sm border border-status-error-border/80 bg-status-error-bg/30 px-2 py-1 text-[10px] font-semibold text-status-error-text">
                    MCP23017 disconnected
                  </div>
                )}
                <RelayChannelMatrix
                  channels={relayChannels}
                  nowMs={nowMs}
                  variant="compact"
                  location={location}
                  statusByChannel={statusByChannel}
                  menuOpenChannel={menuOpenChannel}
                  onToggleMenu={(ch: number) => setMenuOpenChannel(prev => prev === ch ? null : ch)}
                  onMenuAction={handleRelayMenuAction}
                />
              </div>
            </div>

            <div className="flex-1 min-h-[160px]">
              <VerticalNotesBlock location={location} cluster={cluster} currentMode={roomMode?.mode_name} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
