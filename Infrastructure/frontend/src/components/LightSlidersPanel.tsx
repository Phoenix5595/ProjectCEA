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
  target_intensity: number | null
}

interface LightSlidersPanelProps {
  location: string
  cluster: string
  onIntensityChange?: (deviceName: string, intensity: number) => void
}

export default function LightSlidersPanel({
  location,
  cluster,
  onIntensityChange
}: LightSlidersPanelProps) {
  const [lights, setLights] = useState<LightDevice[]>([])
  const [statuses, setStatuses] = useState<Record<string, LightStatus>>({})
  const [pendingValues, setPendingValues] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)

  const fetchLightsAndStatus = useCallback(async () => {
    try {
      const lightDevices = await apiClient.getLightsForZone(location, cluster)
      setLights(lightDevices)
      
      const statusPromises = lightDevices.map(async (light) => {
        try {
          const status = await apiClient.getLightStatus(location, cluster, light.device_name)
          return { deviceName: light.device_name, status }
        } catch {
          return { deviceName: light.device_name, status: null }
        }
      })
      
      const results = await Promise.all(statusPromises)
      const statusMap: Record<string, LightStatus> = {}
      results.forEach(({ deviceName, status }) => {
        if (status) {
          statusMap[deviceName] = {
            intensity: status.intensity,
            target_intensity: status.target_intensity ?? null
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

  async function handleIntensityChange(deviceName: string, intensity: number) {
    setPendingValues(prev => ({ ...prev, [deviceName]: intensity }))
    try {
      await apiClient.setLightIntensity(location, cluster, deviceName, intensity)
      onIntensityChange?.(deviceName, intensity)
      setStatuses(prev => ({
        ...prev,
        [deviceName]: { ...prev[deviceName], intensity }
      }))
    } catch (err) {
      logger.error('Failed to set light intensity:', err)
    } finally {
      setPendingValues(prev => {
        const next = { ...prev }
        delete next[deviceName]
        return next
      })
    }
  }

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
          const targetIntensity = status?.target_intensity
          const pendingValue = pendingValues[light.device_name]
          const displayValue = pendingValue ?? currentIntensity

          return (
            <LightRow
              key={light.device_name}
              label={light.display_name || light.device_name}
              currentIntensity={currentIntensity}
              targetIntensity={targetIntensity}
              value={displayValue}
              onChange={(v) => handleIntensityChange(light.device_name, v)}
              disabled={!light.dimming_enabled}
            />
          )
        })}
      </div>
    </div>
  )
}

interface LightRowProps {
  label: string
  currentIntensity: number
  targetIntensity: number | null
  value: number
  onChange: (value: number) => void
  disabled?: boolean
}

function LightRow({ label, currentIntensity, targetIntensity, value, onChange, disabled }: LightRowProps) {
  const [localValue, setLocalValue] = useState(value)

  useEffect(() => {
    setLocalValue(value)
  }, [value])

  function handleSliderChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = parseInt(e.target.value)
    setLocalValue(v)
  }

  function handleSliderCommit() {
    if (localValue !== value) {
      onChange(localValue)
    }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = Math.max(0, Math.min(100, parseInt(e.target.value) || 0))
    setLocalValue(v)
    onChange(v)
  }

  const percentage = (localValue / 100) * 100

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
              {targetIntensity !== null ? `${targetIntensity}%` : '—'}
            </span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1 h-5">
          <div className="absolute inset-0 bg-gray-800 rounded overflow-hidden">
            <div 
              className="h-full bg-gradient-to-r from-amber-600 to-amber-400 transition-all"
              style={{ width: `${percentage}%` }}
            />
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={localValue}
            onChange={handleSliderChange}
            onMouseUp={handleSliderCommit}
            onTouchEnd={handleSliderCommit}
            disabled={disabled}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
          />
          {targetIntensity !== null && targetIntensity !== currentIntensity && (
            <div 
              className="absolute top-0 w-0.5 h-full bg-amber-400/60"
              style={{ left: `${targetIntensity}%` }}
              title={`Target: ${targetIntensity}%`}
            />
          )}
        </div>
        <input
          type="number"
          min={0}
          max={100}
          value={localValue}
          onChange={handleInputChange}
          disabled={disabled}
          className="w-14 h-6 px-1 text-[16px] text-center bg-gray-800 border border-gray-700 rounded text-gray-200 disabled:opacity-50"
        />
        <span className="text-[12px] text-gray-500">%</span>
      </div>
    </div>
  )
}
