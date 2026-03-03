import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'

interface LightDevice {
  device_name: string
  display_name?: string
  dimming_enabled?: boolean
  dimming_board_id?: string | null
  dimming_channel?: number | null
}

interface LightStatus {
 intensity: number
 target_intensity?: number | null
 day_target_intensity?: number | null
}

interface VerticalLightsBlockProps {
  location: string | null
  cluster: string | null
  compact?: boolean
}

export default function VerticalLightsBlock({ location, cluster, compact }: VerticalLightsBlockProps) {
 const [lights, setLights] = useState<LightDevice[]>([])
 const [statuses, setStatuses] = useState<Record<string, LightStatus>>({})
 const [pendingTargets, setPendingTargets] = useState<Record<string, number>>({})
 const [loading, setLoading] = useState(true)
 const [hasPendingChanges, setHasPendingChanges] = useState(false)

 const fetchLightsAndStatus = useCallback(async () => {
 if (!location || !cluster) return
 
 try {
 const allLights = await apiClient.getLightsForZone(location, cluster)
 const lightDevices = allLights.filter(l => l.dimming_enabled && l.dimming_board_id !== null && l.dimming_channel !== null)
 setLights(lightDevices)
 
 const schedules = await apiClient.getSchedules(location, cluster)
 
 const statusPromises = lightDevices.map(async (light: LightDevice) => {
 try {
 const status = await apiClient.getLightStatus(location!, cluster!, light.device_name)
 
 const sunSchedule = schedules.find((s: any) => 
 s.device_name === light.device_name &&
 (s.mode === 'SUN' || s.mode === 'DAY') &&
 s.enabled && 
 s.target_intensity !== null && 
 s.target_intensity !== undefined
 )
 const dayTargetIntensity = sunSchedule?.target_intensity ?? null
 
 return { deviceName: light.device_name, status, dayTargetIntensity }
 } catch (err) {
 logger.error(`Error getting light status for ${light.device_name}:`, err)
 return { deviceName: light.device_name, status: null, dayTargetIntensity: null }
 }
 })
 
 const results = await Promise.all(statusPromises)
 const statusMap: Record<string, LightStatus> = {}
 results.forEach(({ deviceName, status, dayTargetIntensity }) => {
 if (status) {
 statusMap[deviceName] = {
 intensity: status.intensity,
 target_intensity: status.target_intensity ?? null,
 day_target_intensity: dayTargetIntensity ?? status.target_intensity ?? null
 }
 }
 })
 setStatuses(statusMap)
 } catch (err) {
 logger.error('Failed to load lights:', err)
 } finally {
 setLoading(false)
 }
 }, [location, cluster])

 useEffect(() => {
 fetchLightsAndStatus()
 const interval = setInterval(fetchLightsAndStatus, 5000)
 return () => clearInterval(interval)
 }, [fetchLightsAndStatus])

 useEffect(() => {
 setHasPendingChanges(Object.keys(pendingTargets).length > 0)
 }, [pendingTargets])

 function handleTargetChange(deviceName: string, value: number) {
 // Validate and clamp the value
 const clampedValue = Math.max(0, Math.min(100, value))
 setPendingTargets(prev => ({
 ...prev,
 [deviceName]: clampedValue
 }))
 setHasPendingChanges(true)
 }

 async function savePendingChanges() {
 const entries = Object.entries(pendingTargets)
 for (const [deviceName, target] of entries) {
 try {
 await apiClient.setLightIntensity(location!, cluster!, deviceName, target)
 } catch (err) {
 logger.error(`Failed to set light intensity for ${deviceName}:`, err)
 }
 }
 setPendingTargets({})
 await fetchLightsAndStatus()
 }

 if (loading) {
 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-2 h-full flex flex-col">
 <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-4">Lights</div>
 <div className="text-text-subtle text-sm flex-1 flex items-center justify-center">Loading...</div>
 </div>
 )
 }

 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-2 h-full flex flex-col">
 <div className="flex items-center justify-between mb-2">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider">Lights</div>
 <div className="flex items-center gap-2">
 {lights.map((light) => {
 const status = statuses[light.device_name!]
 const isOn = status && status.intensity > 0
 return (
              <div 
                key={light.device_name}
                className={`text-[14px] px-1 py-0 rounded cursor-help transition-colors ${
                  isOn 
                    ? 'bg-status-success-bg/50 text-status-success border border-status-success-border/50' 
                    : 'bg-surface-secondary text-text-subtle border border-border-default'
                }`}
                title={`${light.display_name || light.device_name}: ${isOn ? 'Sun' : 'Moon'}`}
              >
 {isOn ? '☀️' : '🌙'}
 </div>
 )
 })}
 </div>
 </div>
 
  {lights.length === 0 ? (
  <div className="text-text-subtle text-sm flex-1 flex items-center justify-center">No lights found</div>
  ) : (
  <div className="flex-1 overflow-hidden flex flex-col gap-2 min-h-0">
  {lights.map(light => {
  const status = statuses[light.device_name!]
  if (!status) return null
  
  const currentIntensity = status.intensity
  const savedTarget = status.target_intensity || 0
  const dayTarget = status.day_target_intensity || 0
  const pendingTarget = pendingTargets[light.device_name!]
  const displayTarget = pendingTarget ?? savedTarget
  const sliderPosition = currentIntensity
  const isOn = status && status.intensity > 0
  
  return (
<div key={light.device_name} className={`${!isOn ? 'opacity-50' : ''} flex items-center gap-3 flex-1 min-h-0 bg-surface-secondary/30 rounded px-2`}>
   <div className={`${compact ? 'text-[14px] w-[80px]' : 'text-[16px] w-[100px]'} text-text-secondary font-bold whitespace-normal leading-tight tracking-wider shrink-0`} title={light.display_name || light.device_name}>
    {light.display_name || light.device_name}
  </div>
  
  <div className="flex flex-col justify-center gap-1 shrink-0 w-16">
    <div className="flex items-center justify-between bg-surface-secondary px-1 py-0.5 rounded-sm">
      <span className="text-accent-setpoint font-mono tabular-nums text-[12px] leading-none">
        {dayTarget}%
      </span>
      <span className="text-text-subtle text-[9px] leading-none">TGT</span>
    </div>
    <div className="flex items-center justify-between bg-surface-secondary px-1 py-0.5 rounded-sm">
      <span className="text-accent-data font-mono tabular-nums text-[12px] leading-none">
        {currentIntensity}%
      </span>
      <span className="text-text-subtle text-[9px] leading-none">CUR</span>
    </div>
  </div>
  
  <div className="flex-1 flex items-center h-full py-2">
  <div className={`relative w-full h-full min-h-[40px]`}>
  <div className="absolute inset-0 bg-surface-secondary rounded overflow-hidden shadow-inner">
  <div 
  className="absolute top-0 bottom-0 right-0 bg-linear-to-l from-btn-primary-hover to-btn-primary-data transition-all"
  style={{ width: `${sliderPosition}%` }}
  />
  </div>
  <input
  type="range"
  min={0}
  max={100}
  value={displayTarget}
  onChange={(e) => {
  const value = parseInt(e.target.value)
  if (!isNaN(value)) {
  handleTargetChange(light.device_name!, value)
  }
  }}
  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
  title="Sun target: editable even when lights are off"
  />
  {dayTarget > 0 && (
  <div 
  className="absolute top-0 bottom-0 w-1 bg-accent-setpoint rounded-sm"
  style={{ right: `calc(${dayTarget}% - 2px)` }}
  title={`Sun target: ${dayTarget}%`}
  />
  )}
  {pendingTargets[light.device_name!] !== undefined && (
  <div 
  className="absolute top-0 bottom-0 w-1 bg-status-warning rounded-sm"
  style={{ right: `calc(${displayTarget}% - 2px)` }}
  title={`Pending: ${displayTarget}%`}
  />
  )}
  </div>
  </div>
  
                <div className="flex flex-col items-center gap-1 shrink-0 ml-3">
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={displayTarget}
                    onChange={(e) => {
                      const value = parseInt(e.target.value)
                      if (!isNaN(value)) {
                        handleTargetChange(light.device_name!, value)
                      }
                    }}
                    className="w-14 h-6 px-1 text-center bg-surface-secondary border border-border-default rounded-sm text-[14px] text-text-input font-mono"
                  />
                  <span className="text-[10px] text-text-subtle font-bold tracking-wide">% SET</span>
                </div>
  
  </div>
  )
  })}
  </div>
  )}
  
  {hasPendingChanges && (
  <div className="mt-2 flex justify-end shrink-0">
  <button
  onClick={savePendingChanges}
  className="px-4 py-1.5 bg-accent-vivid hover:bg-accent-hover text-white text-xs font-bold rounded transition-colors"
  >
  APPLY LIGHT CHANGES
  </button>
  </div>
  )}
  </div>
  )
}
