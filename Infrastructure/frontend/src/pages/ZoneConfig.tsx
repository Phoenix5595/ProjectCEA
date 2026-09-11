import { useParams } from 'react-router-dom'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { apiClient } from '../services/api'
import { extractErrorMessage } from '../utils/errors'
import { logger } from '../utils/logger'
import { getLocationDisplayName, getLocationBackendName, getClusterDisplayName } from '../config/zones'
import type { RoomModeWithParams } from '../types/modes'
import { useControlActions } from '../contexts/ControlActionsContext'
import LightIntensity from '../components/LightIntensity'
import VerticalPIDBlock from '../components/VerticalPIDBlock'
import VerticalNotesBlock from '../components/VerticalNotesBlock'
import ManualLightControl from '../components/ManualLightControl'
import RelayChannelMatrix from '../components/devices/RelayChannelMatrix'
import { buildRelayChannelViewModels } from '../components/devices/relayViewModel'
import type { RelayChannelViewModel } from '../components/devices/relayViewModel'
import { useControlSnapshot } from '../hooks/useControlSnapshot'
import type { ClimatePeriod } from '../types/climatePeriod'
import ClimatePeriodsTable from '../components/ClimatePeriodsTable'
import { TimelineEditor } from '../features/climate-timeline/components/TimelineEditor'
import type { TimelineSavedBaseline } from '../features/climate-timeline/state/timelineDraft'

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

export function shouldPersistLegacyTimelineValues(
  isConstant: boolean,
  timelineBaseline: TimelineSavedBaseline | null,
): boolean {
  return isConstant || timelineBaseline === null
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
  const [timelineBaseline, setTimelineBaseline] = useState<TimelineSavedBaseline | null>(null)
  const [loading, setLoading] = useState(section === 'control')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const { snapshot, mcpConnected } = useControlSnapshot()

  const relayChannels: RelayChannelViewModel[] = useMemo(
    () => buildRelayChannelViewModels(snapshot),
    [snapshot],
  )
  const [nowMs, setNowMs] = useState(Date.now())

  useEffect(() => {
    const interval = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  // Relay menu state — consumed by RelayChannelMatrix
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)

  const handleRelayMenuAction = useCallback(async (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => {
    const vm = relayChannels.find((c) => c.channel === channel)
    if (!vm) return
    if (!vm.isAssigned || !vm.assignedDeviceName || !vm.location || !vm.cluster) {
      return
    }

    const device = vm.assignedDeviceName
    const loc = vm.location
    const cluster = vm.cluster

    setMenuOpenChannel(null)

    try {
      if (action === 'auto') {
        await apiClient.commandDevice(loc, cluster, device, { action: 'AUTO', reason: 'Room menu AUTO' })
      } else if (action === 'off') {
        await apiClient.commandDevice(loc, cluster, device, { action: 'MANUAL_OFF', reason: 'Room menu OFF' })
      } else {
        const durationSeconds =
          action === 'timer-5m' ? 300
          : action === 'timer-10m' ? 600
          : action === 'timer-30m' ? 1800
          : 3600
        await apiClient.commandDevice(loc, cluster, device, {
          action: 'TIMED_ON',
          duration_seconds: durationSeconds,
          reason: `Room menu ON ${durationSeconds / 60}m`,
        })
      }
    } catch (err) {
      logger.error(`Relay action failed for channel ${channel}:`, err)
    }
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
      const start = new Date()
      const saved = await apiClient.getSaved({
        location: location!,
        cluster: cluster!,
        window: {
          start: start.toISOString(),
          end: new Date(start.getTime() + 24 * 60 * 60 * 1000).toISOString(),
          timezone: 'UTC',
        },
      })
      setTimelineBaseline(saved)
      setClimatePeriods(saved.periods.map((period) => ({ ...period })))
    } catch (err) {
      logger.error('Error loading room mode:', err)
      setError(extractErrorMessage(err, 'Failed to load'))
    } finally {
      setLoading(false)
    }
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

      if (shouldPersistLegacyTimelineValues(roomMode.is_constant, timelineBaseline)) {
        const p = updated.parameters
        const dayStart = toHHMM(p.day_start_time)
        const nightStart = toHHMM(p.night_start_time)
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
      }

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
  }, [roomMode, location, cluster, climatePeriods, timelineBaseline])

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
      <div className="max-w-[1920px] mx-auto h-[calc(100vh-1rem)] min-w-0 flex flex-col">
        {params && (
          <div className="flex-1 flex flex-col gap-1 min-h-0">
            {!isConstant && roomMode && (
              timelineBaseline ? (
                <div className="w-full min-w-0 shrink-0 overflow-auto">
                  <TimelineEditor
                    key={`${location}-${cluster}-${timelineBaseline.baseConfigRevision}-${roomMode.mode_id ?? 'unknown'}-${roomMode.submode_id ?? 'none'}`}
                    saved={timelineBaseline}
                    lockedPhotoperiodHours={lockedPhotoperiod}
                  />
                </div>
              ) : (
                <div className="w-full min-w-0 shrink-0 overflow-auto rounded-lg border border-border-subtle bg-surface-primary p-1">
                  <ClimatePeriodsTable periods={climatePeriods} onChange={setClimatePeriods} />
                </div>
              )
            )}
            {isConstant && (
              <div className="h-[300px] shrink-0 overflow-hidden rounded-lg border border-border-subtle bg-surface-primary p-0">
                <div className="flex h-full items-center justify-center text-sm text-text-subtle">Constant mode - no timeline</div>
              </div>
            )}

            {/* Climate Periods + Relay Matrix row - 450px */}
            <div className="flex min-h-0 shrink-0 flex-col gap-1 md:h-[580px] md:flex-row">
              <div className="flex min-h-[320px] min-w-0 flex-1 flex-col gap-1 overflow-hidden md:min-h-0">
                {!isConstant ? (
                  <>
                    <div className="h-full overflow-auto">
                      <LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact={true} />
                    </div>
                  </>
                ) : (
                  <ManualLightControl location={location} cluster={cluster} compact={true} />
                )}
              </div>
              <div className="w-full min-w-0 overflow-x-auto md:h-full md:w-auto md:shrink-0 md:overflow-hidden">
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
