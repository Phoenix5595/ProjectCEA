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
          
          const daySchedule = schedules.find((s: any) => 
            s.device_name === light.device_name &&
            s.mode === 'DAY' && 
            s.enabled && 
            s.target_intensity !== null && 
            s.target_intensity !== undefined
          )
          const dayTargetIntensity = daySchedule?.target_intensity ?? null
          
          return { deviceName: light.device_name, status, dayTargetIntensity }
        } catch {
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
    setPendingTargets(prev => ({ ...prev, [deviceName]: value }))
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
      <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">Lights</div>
      
      {lights.length === 0 ? (
        <div className="text-gray-500 text-sm flex-1 flex items-center justify-center">No lights found</div>
      ) : (
        <div className="flex-1 overflow-y-auto">
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
              <div key={light.device_name} className={`${!isOn ? 'opacity-50' : ''} mb-4`}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-300 font-medium truncate max-w-[120px]" title={light.display_name || light.device_name}>
                    {light.display_name || light.device_name}
                  </span>
                  <div className="flex items-center gap-2 text-xs">
                    <div className="flex items-center gap-1">
                      <span className="text-gray-500">CUR</span>
                      <span className="bg-gray-800 px-1 py-0.5 rounded text-cyan-400 font-mono min-w-[30px] text-center">
                        {currentIntensity}%
                      </span>
                    </div>
                    {dayTarget > 0 && (
                      <div className="flex items-center gap-1">
                        <span className="text-gray-500">TGT</span>
                        <span className="bg-gray-800 px-1 py-0.5 rounded text-amber-400 font-mono min-w-[30px] text-center">
                          {dayTarget}%
                        </span>
                      </div>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      isOn 
                        ? 'bg-green-900/50 text-green-400 border border-green-800/50' 
                        : 'bg-gray-800 text-gray-500 border border-gray-700'
                    }`}>
                      {isOn ? 'ON' : 'OFF'}
                    </span>
                  </div>
                </div>
                
                <div className="flex items-center gap-2">
                  <div className="relative flex-1 h-6">
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
                      onChange={(e) => handleTargetChange(light.device_name!, parseInt(e.target.value))}
                      disabled={!isOn}
                      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
                    />
                    {dayTarget > 0 && (
                      <div 
                        className="absolute top-0 w-0.5 h-full bg-amber-400 rounded"
                        style={{ left: `calc(${dayTarget}% - 1px)` }}
                        title={`Day Target: ${dayTarget}%`}
                      />
                    )}
                    {pendingTargets[light.device_name!] !== undefined && (
                      <div 
                        className="absolute top-0 w-0.5 h-full bg-yellow-400 rounded"
                        style={{ left: `calc(${displayTarget}% - 1px)` }}
                        title={`Pending: ${displayTarget}%`}
                      />
                    )}
                  </div>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={displayTarget}
                    onChange={(e) => handleTargetChange(light.device_name!, Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                    disabled={!isOn}
                    className="w-12 h-6 px-1 text-xs text-center bg-gray-800 border border-gray-700 rounded text-gray-200 disabled:opacity-50 focus:outline-none focus:border-cyan-500 transition-colors"
                  />
                  <span className="text-xs text-gray-500">%</span>
                </div>
              </div>
            )
          })}
          
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
