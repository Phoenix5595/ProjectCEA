import { useState, useEffect, useRef } from 'react'
import { apiClient } from '../services/api'

interface ManualLightControlProps {
  location: string
  cluster: string
  compact?: boolean
}

interface LightDeviceDetails {
  device_name: string
  display_name?: string
  dimming_enabled: boolean
}

export default function ManualLightControl({ location, cluster, compact = false }: ManualLightControlProps) {
  const [lightDetails, setLightDetails] = useState<LightDeviceDetails[]>([])
  const [loadingDetails, setLoadingDetails] = useState(true)
  const [activeTimer, setActiveTimer] = useState<number | null>(null) // minutes remaining
  const [lastDefaultMode, setLastDefaultMode] = useState<'auto' | 'scheduled' | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const zoneKey = `${location}:${cluster}`
  const timerStorageKey = `light_timer_${zoneKey}`
  const modeStorageKey = `last_default_mode_${zoneKey}`

  // Load device details on mount
  useEffect(() => {
    async function loadDeviceDetails() {
      setLoadingDetails(true)
      try {
        console.log('ManualLightControl: Fetching devices for', location, cluster)
        const devices = await apiClient.getDevicesForLocationClusterWithDetails(location, cluster)
        console.log('ManualLightControl: Raw devices received', devices)
        const allDevices = Object.entries(devices)
        console.log('ManualLightControl: All device entries', allDevices.map(([name, dev]) => ({ name, type: dev?.device_type })))
        const lights = allDevices
          .filter(([_, device]: [string, any]) => {
            const isLight = device?.device_type === 'light'
            console.log('ManualLightControl: Device', _, 'type:', device?.device_type, 'isLight:', isLight)
            return isLight
          })
          .map(([deviceName, device]: [string, any]) => ({
            device_name: deviceName,
            display_name: device.display_name,
            dimming_enabled: device.dimming_enabled || false
          }))
        console.log('ManualLightControl: Filtered lights result', lights)
        setLightDetails(lights)
      } catch (err) {
        console.error('ManualLightControl: Error loading device details:', err)
        setLightDetails([])
      } finally {
        setLoadingDetails(false)
      }
    }
    loadDeviceDetails()
  }, [location, cluster])

  // Load timer state from localStorage on mount
  useEffect(() => {
    const savedTimerEnd = localStorage.getItem(timerStorageKey)
    const savedMode = localStorage.getItem(modeStorageKey) as 'auto' | 'scheduled' | null

    if (savedTimerEnd && savedMode) {
      const endTime = parseInt(savedTimerEnd, 10)
      const now = Date.now()
      const remaining = Math.max(0, Math.floor((endTime - now) / 60000)) // minutes

      if (remaining > 0) {
        setActiveTimer(remaining)
        setLastDefaultMode(savedMode)
        startTimerCountdown(remaining)
      } else {
        // Timer expired, restore mode
        restoreMode(savedMode)
        localStorage.removeItem(timerStorageKey)
        localStorage.removeItem(modeStorageKey)
      }
    }
  }, [])

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [])

  function startTimerCountdown(_minutes: number) {
    if (intervalRef.current) clearInterval(intervalRef.current)

    intervalRef.current = setInterval(() => {
      setActiveTimer(prev => {
        if (prev === null || prev <= 1) {
          if (intervalRef.current) clearInterval(intervalRef.current)
          return null
        }
        return prev - 1
      })
    }, 60000) // Update every minute
  }

  async function getScheduleIntensity(): Promise<number> {
    try {
      const schedules = await apiClient.getSchedules(location, cluster)
      const now = new Date()
      const currentTime = now.getHours() * 60 + now.getMinutes() // minutes since midnight
      const currentWeekday = now.getDay() === 0 ? 6 : now.getDay() - 1 // Convert to 0-6 (Monday-Sunday)

      // Find active schedule for any light device
      for (const schedule of schedules) {
        if (!schedule.enabled) continue
        if (schedule.device_name && !schedule.device_name.startsWith('light_')) continue

        const startMinutes = timeToMinutes(schedule.start_time)
        const endMinutes = timeToMinutes(schedule.end_time)
        const dayMatch = schedule.day_of_week === null || schedule.day_of_week === currentWeekday

        if (!dayMatch) continue

        let isActive = false
        if (startMinutes <= endMinutes) {
          // Same day schedule
          isActive = currentTime >= startMinutes && currentTime < endMinutes
        } else {
          // Overnight schedule
          isActive = currentTime >= startMinutes || currentTime < endMinutes
        }

        if (isActive && schedule.target_intensity !== null && schedule.target_intensity !== undefined) {
          return schedule.target_intensity
        }
      }

      // No active schedule with intensity, default to 100%
      return 100
    } catch (err) {
      console.error('Error getting schedule intensity:', err)
      return 100 // Default to 100% on error
    }
  }

  function timeToMinutes(timeStr: string): number {
    const [hours, minutes] = timeStr.split(':').map(Number)
    return hours * 60 + minutes
  }

  async function turnOnLights(durationMinutes?: number) {
    setLoading(true)
    setError(null)

    try {
      // Get schedule intensity
      const intensity = await getScheduleIntensity()

      // Save current mode as last default if not already in manual mode
      if (!lastDefaultMode && lightDetails.length > 0) {
        try {
          const devices = await apiClient.getDevicesForLocationCluster(location, cluster)
          const firstLight = lightDetails[0]
          const lightDevice = devices.devices[firstLight.device_name]
          if (lightDevice && lightDevice.mode) {
            const currentMode = lightDevice.mode
            if (currentMode === 'auto' || currentMode === 'scheduled') {
              setLastDefaultMode(currentMode as 'auto' | 'scheduled')
              localStorage.setItem(modeStorageKey, currentMode)
            }
          }
        } catch (err) {
          console.error('Error checking current mode:', err)
        }
        // Default to 'auto' if we can't determine
        if (!lastDefaultMode) {
          setLastDefaultMode('auto')
          localStorage.setItem(modeStorageKey, 'auto')
        }
      }

      // Turn on all lights
      for (const light of lightDetails) {
        try {
          // Set intensity if dimming enabled
          if (light.dimming_enabled) {
            await apiClient.setLightIntensity(location, cluster, light.device_name, intensity)
          }

          // Turn relay ON
          await apiClient.controlDevice(location, cluster, light.device_name, 1, 'Manual override')

          // Set to manual mode
          await apiClient.setDeviceMode(location, cluster, light.device_name, 'manual')
        } catch (err: any) {
          console.error(`Error controlling light ${light.device_name}:`, err)
          setError(err.response?.data?.detail || `Failed to control ${light.device_name}`)
        }
      }

      // Start timer if duration specified
      if (durationMinutes !== undefined) {
        const endTime = Date.now() + durationMinutes * 60000
        localStorage.setItem(timerStorageKey, endTime.toString())
        setActiveTimer(durationMinutes)
        startTimerCountdown(durationMinutes)

        // Set timeout to restore mode
        if (timerRef.current) clearTimeout(timerRef.current)
        const savedMode = lastDefaultMode || 'auto'
        timerRef.current = setTimeout(() => {
          const currentSavedMode = localStorage.getItem(modeStorageKey) as 'auto' | 'scheduled' | null
          restoreMode(currentSavedMode || savedMode)
        }, durationMinutes * 60000)
      }
    } catch (err: any) {
      console.error('Error turning on lights:', err)
      setError(err.response?.data?.detail || 'Failed to turn on lights')
    } finally {
      setLoading(false)
    }
  }

  async function turnOffLights() {
    setLoading(true)
    setError(null)

    try {
      // Clear any active timer
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      setActiveTimer(null)
      localStorage.removeItem(timerStorageKey)

      // Turn off all lights
      for (const light of lightDetails) {
        try {
          // Turn relay OFF
          await apiClient.controlDevice(location, cluster, light.device_name, 0, 'Manual override')

          // Set to manual mode
          await apiClient.setDeviceMode(location, cluster, light.device_name, 'manual')
        } catch (err: any) {
          console.error(`Error controlling light ${light.device_name}:`, err)
          setError(err.response?.data?.detail || `Failed to control ${light.device_name}`)
        }
      }
    } catch (err: any) {
      console.error('Error turning off lights:', err)
      setError(err.response?.data?.detail || 'Failed to turn off lights')
    } finally {
      setLoading(false)
    }
  }

  async function restoreMode(mode: 'auto' | 'scheduled') {
    setLoading(true)
    setError(null)

    try {
      // Clear timer
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      setActiveTimer(null)
      localStorage.removeItem(timerStorageKey)
      localStorage.removeItem(modeStorageKey)

      // Set all lights to auto/scheduled mode
      for (const light of lightDetails) {
        try {
          await apiClient.setDeviceMode(location, cluster, light.device_name, mode)
        } catch (err: any) {
          console.error(`Error restoring mode for ${light.device_name}:`, err)
          setError(err.response?.data?.detail || `Failed to restore mode for ${light.device_name}`)
        }
      }

      setLastDefaultMode(null)
    } catch (err: any) {
      console.error('Error restoring mode:', err)
      setError(err.response?.data?.detail || 'Failed to restore mode')
    } finally {
      setLoading(false)
    }
  }

  // Always render the component, even if no lights found
  console.log('ManualLightControl RENDER:', { 
    location, 
    cluster, 
    lightDetailsCount: lightDetails.length, 
    loadingDetails,
    lightDetails 
  })
  
  return (
    <div className={`${compact ? "" : "mt-4 pt-4 border-t border-gray-200 dark:border-gray-800"} bg-gray-50 dark:bg-gray-950/80`} style={{ minHeight: compact ? 'auto' : '100px', padding: compact ? '8px' : '12px', borderRadius: '4px', width: compact ? 'fit-content' : '100%', display: 'inline-block' }}>
      {!compact && <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Manual Control</div>}
      {compact && <div className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">Manual Control</div>}
      {loadingDetails && (
        <div className="text-xs text-gray-500 dark:text-gray-400 mb-2">Loading lights...</div>
      )}
      {!loadingDetails && lightDetails.length === 0 && (
        <div className="text-xs text-red-600 dark:text-red-400 mb-2 font-semibold">⚠️ No lights found in this zone (location: {location}, cluster: {cluster})</div>
      )}
      {!loadingDetails && lightDetails.length > 0 && (
        <div className="text-xs text-green-600 dark:text-green-400 mb-2 font-semibold">✓ Found {lightDetails.length} light(s): {lightDetails.map(l => l.device_name).join(', ')}</div>
      )}
      {error && (
        <div className="text-xs text-red-600 dark:text-red-400 mb-2">{error}</div>
      )}
      {activeTimer !== null && (
        <div className="text-xs text-blue-600 dark:text-blue-400 mb-2">
          Timer: {activeTimer} min remaining
        </div>
      )}
      {!loadingDetails && lightDetails.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => restoreMode(lastDefaultMode || 'auto')}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-blue-600 dark:bg-blue-500 text-white rounded hover:bg-blue-700 dark:hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Auto
          </button>
          <button
            onClick={() => turnOffLights()}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-gray-600 dark:bg-gray-500 text-white rounded hover:bg-gray-700 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Off
          </button>
          <button
            onClick={() => turnOnLights(5)}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-green-600 dark:bg-green-500 text-white rounded hover:bg-green-700 dark:hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            5m
          </button>
          <button
            onClick={() => turnOnLights(30)}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-green-600 dark:bg-green-500 text-white rounded hover:bg-green-700 dark:hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            30m
          </button>
          <button
            onClick={() => turnOnLights(60)}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-green-600 dark:bg-green-500 text-white rounded hover:bg-green-700 dark:hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            1h
          </button>
          <button
            onClick={() => turnOnLights(480)}
            disabled={loading}
            className="px-3 py-1.5 text-sm font-medium bg-green-600 dark:bg-green-500 text-white rounded hover:bg-green-700 dark:hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            8h
          </button>
        </div>
      ) : !loadingDetails ? (
        <div className="text-xs text-orange-600 dark:text-orange-400 mb-2 italic">Buttons will appear when lights are detected</div>
      ) : null}
    </div>
  )
}

