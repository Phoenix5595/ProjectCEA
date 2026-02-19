import { useState, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'

interface LightDevice {
 device_name: string
 display_name?: string
 dimming_enabled?: boolean
}

interface LightStatus {
 intensity: number
 target_intensity?: number | null
 day_target_intensity?: number | null
}

interface LightSlidersPanelProps {
 location: string
 cluster: string
}

export interface LightSlidersPanelRef {
 savePendingChanges: () => Promise<void>
 hasPendingChanges: () => boolean
}

const LightSlidersPanel = forwardRef<LightSlidersPanelRef, LightSlidersPanelProps>(({ location, cluster }, ref) => {
 const [lights, setLights] = useState<LightDevice[]>([])
 const [statuses, setStatuses] = useState<Record<string, LightStatus>>({})
 const [pendingTargets, setPendingTargets] = useState<Record<string, number>>({})
 const [loading, setLoading] = useState(true)

 const fetchLightsAndStatus = useCallback(async () => {
 try {
 const lightDevices = await apiClient.getLightsForZone(location, cluster)
 setLights(lightDevices)
 
 const schedules = await apiClient.getSchedules(location, cluster)
 
 const statusPromises = lightDevices.map(async (light: LightDevice) => {
 try {
 const status = await apiClient.getLightStatus(location, cluster, light.device_name)
 
 const daySchedule = schedules.find((s: any) =>
 s.device_name === light.device_name &&
 (s.mode === 'SUN' || s.mode === 'DAY') &&
 s.enabled &&
 s.target_intensity !== null &&
 s.target_intensity !== undefined
 )
 const dayTargetIntensity = daySchedule?.target_intensity ?? null
 
 return { deviceName: light.device_name, status, dayTargetIntensity }
 } catch (err) {
 logger.error(`Error getting light status for ${light.device_name}:`, err)
 return { deviceName: light.device_name, status: null, dayTargetIntensity: null }
 }
 })
 
 const results = await Promise.all(statusPromises)
 const statusMap: Record<string, LightStatus> = {}
 results.forEach(({ deviceName, status, dayTargetIntensity }: { deviceName: string; status: LightStatus | null; dayTargetIntensity: number | null }) => {
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

 function handleTargetChange(deviceName: string, value: number) {
 setPendingTargets(prev => ({ ...prev, [deviceName]: value }))
 }

 async function savePendingChanges() {
 const entries = Object.entries(pendingTargets)
 for (const [deviceName, target] of entries) {
 try {
 await apiClient.setLightIntensity(location, cluster, deviceName, target)
 } catch (err) {
 logger.error(`Failed to set light intensity for ${deviceName}:`, err)
 }
 }
 setPendingTargets({})
 await fetchLightsAndStatus()
 }

 useImperativeHandle(ref, () => ({
 savePendingChanges,
 hasPendingChanges: () => Object.keys(pendingTargets).length > 0
 }))

 if (loading) {
 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-3">Lights</div>
 <div className="text-text-subtle text-[12px]">Loading...</div>
 </div>
 )
 }

 if (lights.length === 0) {
 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-3">Lights</div>
 <div className="text-text-subtle text-[12px]">No dimmable lights</div>
 </div>
 )
 }

 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-3">
 Lights ({lights.length})
 </div>
  <div className="flex flex-wrap gap-4">
  {lights.map((light) => {
 const status = statuses[light.device_name]
 const currentIntensity = status?.intensity ?? 0
 const savedTarget = status?.target_intensity ?? currentIntensity
 const dayTarget = status?.day_target_intensity ?? savedTarget
 const pendingTarget = pendingTargets[light.device_name]
 const hasPending = pendingTarget !== undefined && pendingTarget !== savedTarget

 return (
 <LightRow
 key={light.device_name}
 label={light.display_name || light.device_name}
 currentIntensity={currentIntensity}
 savedTarget={savedTarget}
 dayTarget={dayTarget}
 pendingTarget={pendingTarget}
 hasPending={hasPending}
 onTargetChange={(v) => handleTargetChange(light.device_name, v)}
 disabled={!light.dimming_enabled}
 />
 )
 })}
 </div>
 </div>
 )
})

export default LightSlidersPanel

interface LightRowProps {
 label: string
 currentIntensity: number
 savedTarget: number
 dayTarget: number
 pendingTarget: number | undefined
 hasPending: boolean
 onTargetChange: (value: number) => void
 disabled?: boolean
}

function LightRow({ 
 label, 
 currentIntensity, 
 savedTarget,
 dayTarget,
 pendingTarget, 
 hasPending, 
 onTargetChange, 
 disabled 
}: LightRowProps) {
 const displayTarget = pendingTarget ?? savedTarget
 const sliderPosition = currentIntensity

  const firstWord = label.split(' ')[0]
  const restWords = label.split(' ').slice(1).join(' ')
  
  return (
  <div className={`flex items-center gap-2 min-w-[100px] ${disabled ? 'opacity-50' : ''}`}>
    <div className="text-[11px] text-text-secondary font-medium text-right leading-tight w-12 shrink-0" title={label}>
      <div>{firstWord}</div>
      <div>{restWords}</div>
    </div>
    <div className="flex flex-col items-center">
  <div className="flex items-center gap-2 mb-1">
 <div className="flex items-center gap-1">
 <span className="text-text-subtle text-[10px]">CUR</span>
 <span className="bg-surface-secondary px-0.5 rounded-sm text-accent-data font-mono tabular-nums text-[10px] min-w-[28px] text-center">
 {currentIntensity}%
 </span>
 </div>
 <div className="flex items-center gap-1">
 <span className="text-text-subtle text-[10px]">TGT</span>
 <span className="bg-surface-secondary px-0.5 rounded-sm text-accent-setpoint font-mono tabular-nums text-[10px] min-w-[28px] text-center">
 {dayTarget}%
 </span>
 </div>
 </div>
 <div className="relative w-8 h-32">
 <div className="absolute inset-0 bg-surface-secondary rounded-sm overflow-hidden">
 <div 
 className="absolute bottom-0 left-0 right-0 bg-linear-to-t from-btn-primary-hover to-btn-primary-data transition-all"
 style={{ height: `${sliderPosition}%` }}
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
 onTargetChange(value)
 }
 }}
 disabled={disabled}
 className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
 />
 {dayTarget > 0 && (
 <div 
 className="absolute left-0 right-0 h-1 bg-accent-setpoint rounded-sm"
 style={{ bottom: `calc(${dayTarget}% - 2px)` }}
 title={`Day Target: ${dayTarget}%`}
 />
 )}
 {hasPending && (
 <div 
 className="absolute left-0 right-0 h-1 bg-status-warning rounded-sm"
 style={{ bottom: `calc(${displayTarget}% - 2px)` }}
 title={`Pending: ${displayTarget}%`}
 />
 )}
 </div>
 <div className="flex flex-col items-center gap-1 mt-2">
 <input
 type="number"
 min={0}
 max={100}
 value={displayTarget}
 onChange={(e) => {
 const value = parseInt(e.target.value)
 if (!isNaN(value)) {
 onTargetChange(value)
 }
 }}
 disabled={disabled}
 className="w-14 h-6 px-1 text-[12px] text-center bg-surface-secondary border border-border-default rounded-sm text-text-input disabled:opacity-50"
 />
  <span className="text-[10px] text-text-subtle">%</span>
  </div>
  </div>
  </div>
  )
}
