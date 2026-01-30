import { useParams, Link } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import { getLocationDisplayName, getLocationBackendName } from '../config/zones'
import type { RoomModeWithParams, ModeParameters } from '../types/modes'
import RoomModeSelector from '../components/RoomModeSelector'
import SetpointTimeline from '../components/SetpointTimeline'
import SetpointsTable from '../components/SetpointsTable'
import CircularTimePicker from '../components/CircularTimePicker'
import VerticalLightsBlock from '../components/VerticalLightsBlock'
import VerticalPIDBlock from '../components/VerticalPIDBlock'
import VerticalNotesBlock from '../components/VerticalNotesBlock'
import ManualLightControl from '../components/ManualLightControl'

export default function ZoneConfig() {
  const { location: locationParam, cluster } = useParams<{ location: string; cluster: string }>()
  const location = locationParam ? getLocationBackendName(locationParam) : null
  
  const [roomMode, setRoomMode] = useState<RoomModeWithParams | null>(null)
  const [savedParams, setSavedParams] = useState<ModeParameters | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    if (location && cluster) {
      loadRoomMode()
    }
  }, [location, cluster])

  async function loadRoomMode() {
    setLoading(true)
    setError(null)
    try {
      const mode = await apiClient.getRoomModeWithParams(location!, cluster!)
      setRoomMode(mode)
      setSavedParams({ ...mode.parameters })
    } catch (err: any) {
      logger.error('Error loading room mode:', err)
      setError(err.response?.data?.detail || err.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  async function handleModeChange(modeName: string, submodeName?: string) {
    if (!location || !cluster) return
    
    try {
      const newMode = await apiClient.setRoomMode(location, cluster, { mode_name: modeName, submode_name: submodeName })
      setRoomMode(newMode)
      setSavedParams({ ...newMode.parameters })
      setSuccess('Mode changed')
      setTimeout(() => setSuccess(null), 2000)
    } catch (err: any) {
      logger.error('Error changing mode:', err)
      setError(err.response?.data?.detail || 'Failed to change mode')
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

  async function handleSave() {
    if (!roomMode || !location || !cluster) return

    setSaving(true)
    setError(null)
    try {
      const updated = await apiClient.updateRoomParameters(location, cluster, roomMode.parameters)
      setRoomMode(updated)
      setSavedParams({ ...updated.parameters })

      const p = updated.parameters
      const dayStart = toHHMM(p.day_start_time)
      const nightStart = toHHMM(p.night_start_time)
      await apiClient.saveRoomSchedule(location, cluster, {
        day_start_time: dayStart,
        day_end_time: nightStart,
        night_start_time: nightStart,
        night_end_time: dayStart,
        ramp_up_duration: p.ramp_up_minutes ?? null,
        ramp_down_duration: p.ramp_down_minutes ?? null,
      })

      setSuccess('Saved')
      setTimeout(() => setSuccess(null), 2000)
    } catch (err: any) {
      logger.error('Error saving parameters:', err)
      setError(err.response?.data?.detail || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  if (!location || !cluster) {
    return <div className="text-gray-100">Invalid zone</div>
  }

  if (loading) {
    return <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-400">Loading...</div>
  }

  const params = roomMode?.parameters
  const isConstant = roomMode?.is_constant || false
  const currentModeName = roomMode?.mode_name || 'veg'
  const lockedPhotoperiod = currentModeName === 'flower' ? 12 : currentModeName === 'veg' ? 18 : null

  return (
    <div className="min-h-screen bg-gray-950 p-2">
      <div className="max-w-[1920px] mx-auto h-[calc(100vh-1rem)] flex flex-col">
        <div className="sticky top-0 z-10 bg-gray-950 p-1 mb-2">
          <div className="flex items-center justify-between px-1">
            <h1 className="text-lg font-bold text-gray-100 flex items-center gap-2">
              <span>{location === 'Veg Room' ? '🌱' : location === 'Flower Room' ? '🌻' : '🌿'}</span> 
              {cluster === 'main' ? getLocationDisplayName(location) : `${getLocationDisplayName(location)} - ${cluster}`}
            </h1>
            <div className="flex items-center gap-3">
              {(error || success) && (
                <div className={`text-xs px-2 py-0.5 rounded ${error ? 'bg-red-900 text-red-200' : 'bg-green-900 text-green-200'}`}>
                  {error || success}
                </div>
              )}
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-3 py-1 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 text-white text-xs font-bold rounded transition-colors"
              >
                {saving ? '...' : 'SAVE'}
              </button>
              <RoomModeSelector
                currentMode={roomMode}
                onModeChange={handleModeChange}
                location={location}
              />
              <Link to="/" className="text-xs text-gray-400 hover:text-white font-medium flex items-center gap-1 bg-gray-800 px-2 py-1 rounded border border-gray-700 hover:border-gray-500 transition-colors">
                <span>←</span> Back
              </Link>
            </div>
          </div>
        </div>

        {params && (
          <div className="flex-1 flex flex-col gap-2 min-h-0">
            <div className="h-[270px] flex-shrink-0 bg-gray-900 rounded-lg border border-gray-800 overflow-hidden p-3">
              <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2">Climate Timeline</div>
              {!isConstant && (
                <SetpointTimeline
                  dayStartTime={params.day_start_time}
                  dayEndTime={params.night_start_time}
                  preDayDuration={params.pre_day_minutes}
                  preNightDuration={params.pre_night_minutes}
                  onDayStartChange={(time) => handleParamChange({ day_start_time: time })}
                  onDayEndChange={(time) => handleParamChange({ night_start_time: time })}
                  onPreDayDurationChange={(d) => handleParamChange({ pre_day_minutes: d })}
                  onPreNightDurationChange={(d) => handleParamChange({ pre_night_minutes: d })}
                  lightPhotoperiod={{
                    startTime: params.day_start_time,
                    endTime: params.night_start_time,
                    rampUpDuration: params.light_ramp_up_minutes,
                    rampDownDuration: params.light_ramp_down_minutes
                  }}
                  setpoints={{
                    DAY: { heating_setpoint: params.day_heat_temp, cooling_setpoint: params.day_cool_temp, vpd: params.day_vpd, co2: params.day_co2, ramp_in_duration: params.ramp_up_minutes },
                    NIGHT: { heating_setpoint: params.night_heat_temp, cooling_setpoint: params.night_cool_temp, vpd: params.night_vpd, co2: params.night_co2, ramp_in_duration: params.ramp_down_minutes },
                    PRE_DAY: { heating_setpoint: params.pre_day_heat_temp, cooling_setpoint: params.pre_day_cool_temp, vpd: params.pre_day_vpd, co2: params.pre_day_co2, ramp_in_duration: params.pre_day_ramp_minutes },
                    PRE_NIGHT: { heating_setpoint: params.pre_night_heat_temp, cooling_setpoint: params.pre_night_cool_temp, vpd: params.pre_night_vpd, co2: params.pre_night_co2, ramp_in_duration: params.pre_night_ramp_minutes }
                  }}
                  className="h-[calc(100%-28px)]"
                />
              )}
              {isConstant && (
                <div className="h-full flex items-center justify-center text-gray-500 text-sm">
                  Constant mode - no timeline
                </div>
              )}
            </div>

            <div className="flex gap-2">
              <div className="w-[35%] flex flex-col gap-2">
                {!isConstant && (
                  <div className="bg-gray-900 rounded-lg border border-gray-800 p-[11px] flex-shrink-0">
                    <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2">Light Schedule</div>
                    <div className="flex justify-center">
                      <CircularTimePicker
                        dayStartTime={params.day_start_time}
                        dayEndTime={params.night_start_time}
                      onDayStartChange={(time) => handleParamChange({ day_start_time: time })}
                      onDayEndChange={(time) => handleParamChange({ night_start_time: time })}
                      showPresetButtons={false}
                      lockedPhotoperiodHours={lockedPhotoperiod}
                      rampUpDuration={params.light_ramp_up_minutes}
                      rampDownDuration={params.light_ramp_down_minutes}
                      onRampUpChange={(d) => handleParamChange({ light_ramp_up_minutes: d ?? 0 })}
                      onRampDownChange={(d) => handleParamChange({ light_ramp_down_minutes: d ?? 0 })}
                      size={420}
                    />
                  </div>
                </div>
                )}
              </div>
              
              <div className="w-[65%] flex flex-col gap-2">
                <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
                  <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2">Setpoints</div>
                  <SetpointsTable
                    params={params}
                    currentParams={savedParams || undefined}
                    isConstant={isConstant}
                    onChange={handleParamChange}
                  />
                </div>
              </div>
            </div>
            
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
              <VerticalLightsBlock location={location} cluster={cluster} />
              {(currentModeName === 'drying' || currentModeName === 'sleep') && (
                <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
                  <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2">Manual Light Control</div>
                  <ManualLightControl location={location} cluster={cluster} compact={true} />
                </div>
              )}
              <VerticalPIDBlock />
              <VerticalNotesBlock location={location} cluster={cluster} currentMode={roomMode?.mode_name} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
