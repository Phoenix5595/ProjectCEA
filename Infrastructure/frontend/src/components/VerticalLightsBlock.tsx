import { useState, useEffect, useCallback } from 'react'
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

interface VerticalLightsBlockProps {
  location: string | null
  cluster: string | null
}

export default function VerticalLightsBlock({ location, cluster }: VerticalLightsBlockProps) {
  const [lights, setLights] = useState<LightDevice[]>([])
  const [statuses, setStatuses] = useState<Record<string, LightStatus>>({})
  const [pendingTargets, setPendingTargets] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [hasPendingChanges, setHasPendingChanges] = useState(false)

  const fetchLightsAndStatus = useCallback(async () => {
    if (!location || !cluster) return
    
    try {
      const lightDevices = await apiClient.getLightsForZone(location, cluster)
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
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-2 h-full flex flex-col">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">Lights</div>
        <div className="text-gray-500 text-sm flex-1 flex items-center justify-center">Loading...</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-2 h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider">Lights</div>
        <div className="flex items-center gap-2">
          {lights.map((light) => {
            const status = statuses[light.device_name!]
            const isOn = status && status.intensity > 0
            return (
              <div 
                key={light.device_name}
                className={`text-[14px] px-1.5 py-0.5 rounded cursor-help transition-colors ${
                  isOn 
                    ? 'bg-green-900/50 text-green-400 border border-green-800/50' 
                    : 'bg-gray-800 text-gray-500 border border-gray-700'
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
        <div className="text-gray-500 text-sm flex-1 flex items-center justify-center">No lights found</div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          <div className="flex gap-0 h-full">
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
              <div key={light.device_name} className={`${!isOn ? 'opacity-50' : ''} flex flex-col items-center min-w-[100px] flex-1`}>
                <div className="text-[14px] text-gray-300 font-medium truncate text-center mb-1" title={light.display_name || light.device_name}>
                  {light.display_name || light.device_name}
                </div>
                <div className="flex items-center gap-1 mb-2 text-xs">
                  <div className="flex items-center gap-1">
                    <span className="text-gray-500 text-[12px]">CUR</span>
                    <span className="bg-gray-800 px-1 py-0.5 rounded text-cyan-400 font-mono text-[12px] min-w-[25px] text-center">
                      {currentIntensity}%
                    </span>
                  </div>
                  {dayTarget > 0 && (
                    <div className="flex items-center gap-1">
                      <span className="text-gray-500 text-[12px]">TGT</span>
                      <span className="bg-gray-800 px-1 py-0.5 rounded text-amber-400 font-mono text-[12px] min-w-[25px] text-center">
                        {dayTarget}%
                      </span>
                    </div>
                  )}
                </div>
                
                <div className="flex flex-col items-center flex-1">
                  <div className="relative w-16 h-full min-h-[120px]">
                    <div className="absolute inset-0 bg-gray-800 rounded overflow-hidden">
                      <div 
                        className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-blue-600 to-blue-400 transition-all"
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
                          handleTargetChange(light.device_name!, value)
                        }
                      }}
                      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                      title="Sun target: editable even when lights are off"
                    />
                    {dayTarget > 0 && (
                      <div 
                        className="absolute left-0 right-0 h-1 bg-amber-400 rounded"
                        style={{ bottom: `calc(${dayTarget}% - 2px)` }}
                        title={`Sun target: ${dayTarget}%`}
                      />
                    )}
                    {pendingTargets[light.device_name!] !== undefined && (
                      <div 
                        className="absolute left-0 right-0 h-1 bg-yellow-400 rounded"
                        style={{ bottom: `calc(${displayTarget}% - 2px)` }}
                        title={`Pending: ${displayTarget}%`}
                      />
                    )}
                  </div>
                  <div className="flex items-center gap-1">
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
                      className="w-12 h-5 px-1 text-[12px] text-center bg-gray-800 border border-gray-700 rounded text-gray-200 focus:outline-none focus:border-cyan-500 transition-colors"
                      title="Sun target %"
                    />
                    <span className="text-[12px] text-gray-500">%</span>
                  </div>
                </div>
              </div>
            )
          })}
          </div>
          
          {hasPendingChanges && (
            <div className="pt-4 border-t border-gray-800 mt-auto">
              <button
                onClick={savePendingChanges}
                className="w-full px-3 py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-white text-xs font-bold tracking-wide transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
              >
                Save Pending Changes
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
