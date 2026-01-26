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
            s.mode === 'DAY' && 
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
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
        <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-3">Lights</div>
        <div className="text-gray-500 text-[12px]">Loading...</div>
      </div>
    )
  }

  if (lights.length === 0) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
        <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-3">Lights</div>
        <div className="text-gray-500 text-[12px]">No dimmable lights</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
      <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-3">
        Lights ({lights.length})
      </div>
      <div className="space-y-3">
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

  return (
    <div className={`${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[12px] text-gray-300 font-medium truncate max-w-[120px]" title={label}>
          {label}
        </span>
        <div className="flex items-center gap-2 text-[12px]">
          <div className="flex items-center gap-1">
            <span className="text-gray-500">CUR</span>
            <span className="bg-gray-800 px-1.5 py-0.5 rounded text-cyan-400 font-mono min-w-[32px] text-center">
              {currentIntensity}%
            </span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">TGT</span>
            <span className="bg-gray-800 px-1.5 py-0.5 rounded text-amber-400 font-mono min-w-[32px] text-center">
              {dayTarget}%
            </span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1 h-5">
          <div className="absolute inset-0 bg-gray-800 rounded overflow-hidden">
            <div 
              className="absolute h-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all"
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
                onTargetChange(value)
              }
            }}
            disabled={disabled}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
          />
          {dayTarget > 0 && (
            <div 
              className="absolute top-0 w-1 h-full bg-amber-400 rounded"
              style={{ left: `calc(${dayTarget}% - 2px)` }}
              title={`Day Target: ${dayTarget}%`}
            />
          )}
          {hasPending && (
            <div 
              className="absolute top-0 w-1 h-full bg-yellow-400 rounded"
              style={{ left: `calc(${displayTarget}% - 2px)` }}
              title={`Pending: ${displayTarget}%`}
            />
          )}
        </div>
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
          className="w-12 h-6 px-1 text-[14px] text-center bg-gray-800 border border-gray-700 rounded text-gray-200 disabled:opacity-50"
        />
        <span className="text-[12px] text-gray-500">%</span>
      </div>
    </div>
  )
}
