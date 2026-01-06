import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import type { LightStatus } from '../types/light'

interface LightDevice {
  device_name: string
  display_name?: string
  dimming_enabled?: boolean
  dimming_board_id?: number
  dimming_channel?: number
}

interface LightManagerProps {
  location: string
  cluster: string
  lights: LightDevice[]
}

export default function LightManager({ location, cluster, lights }: LightManagerProps) {
  const [lightStatuses, setLightStatuses] = useState<Record<string, LightStatus>>({})
  const [inputValues, setInputValues] = useState<Record<string, string>>({}) // Store input values as strings to prevent focus loss
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [schedules, setSchedules] = useState<Record<string, any>>({}) // Store schedules by device_name
  const [saving, setSaving] = useState<Record<string, boolean>>({}) // Track save operations
  const [savedValues, setSavedValues] = useState<Record<string, string>>({}) // Track saved values to detect changes
  const [savingAll, setSavingAll] = useState(false) // Track "Save All" operation
  const [deviceStates, setDeviceStates] = useState<Record<string, number>>({}) // Store relay states (0=OFF, 1=ON)

  useEffect(() => {
    // Load initial light statuses
    loadLightStatuses()
    // Load schedules for each light
    loadSchedules()
    // Load device states (relay states)
    loadDeviceStates()
    
    // Refresh every 1 second for faster updates
    const interval = setInterval(() => {
      loadLightStatuses()
      loadDeviceStates()
    }, 1000)
    return () => clearInterval(interval)
  }, [location, cluster, lights])

  async function loadDeviceStates() {
    try {
      const devices = await apiClient.getDevicesForLocationCluster(location, cluster)
      const states: Record<string, number> = {}
      
      // Extract relay state for each light
      for (const light of lights) {
        const device = devices.devices[light.device_name]
        if (device && device.state !== undefined) {
          states[light.device_name] = device.state
        }
      }
      
      setDeviceStates(states)
    } catch (error: any) {
      console.error('Error loading device states:', error)
    }
  }

  async function loadSchedules() {
    try {
      const allSchedules = await apiClient.getSchedules(location, cluster)
      const scheduleMap: Record<string, any> = {}
      const newInputValues: Record<string, string> = {}
      
      // Find day schedules for each light
      for (const light of lights.filter(l => l.dimming_enabled)) {
        // Find the day schedule for this light (mode='DAY')
        // Also check for schedules without mode if they have target_intensity (backward compatibility)
        const daySchedule = allSchedules.find(
          s => s.device_name === light.device_name && 
               s.enabled &&
               ((s.mode === 'DAY') || (s.mode === null && s.target_intensity !== null && s.target_intensity !== undefined))
        )
        
        if (daySchedule) {
          scheduleMap[light.device_name] = daySchedule
          // Initialize input value with target_intensity from schedule
          if (daySchedule.target_intensity !== null && daySchedule.target_intensity !== undefined) {
            // Normalize to integer string (intensities are always whole numbers)
            const targetIntensity = Math.round(Number(daySchedule.target_intensity))
            newInputValues[light.device_name] = targetIntensity.toString()
          }
        } else {
          // Log warning if no schedule found
          console.warn(`No DAY schedule found for ${light.device_name} in ${location}/${cluster}. Available schedules:`, 
            allSchedules.filter(s => s.device_name === light.device_name))
        }
      }
      
      setSchedules(scheduleMap)
      
      // Update input values and saved values atomically to prevent false "modified" states
      // Update both in the same synchronous batch
      setInputValues(prev => {
        return { ...prev, ...newInputValues }
      })
      // Update saved values to track what's been saved - use same values as inputValues
      // This must happen synchronously after inputValues to prevent race conditions
      setSavedValues(prev => {
        return { ...prev, ...newInputValues }
      })
    } catch (error: any) {
      console.error('Error loading schedules:', error)
    }
  }

  async function loadLightStatuses() {
    const dimmableLights = lights.filter(l => l.dimming_enabled)
    
    for (const light of dimmableLights) {
      try {
        const status = await apiClient.getLightStatus(location, cluster, light.device_name)
        setLightStatuses(prev => ({
          ...prev,
          [light.device_name]: status
        }))
        // Only set input values if they don't exist yet (initial load)
        // Don't overwrite user's slider position during refresh
        // But update if schedule has changed (after save)
        setInputValues(prev => {
          const schedule = schedules[light.device_name]
          const currentInputValue = prev[light.device_name]
          
          // If input value doesn't exist, initialize it
          if (currentInputValue === undefined) {
            const targetValue = schedule?.target_intensity ?? 
                               (status.target_intensity !== null && status.target_intensity !== undefined
                                 ? status.target_intensity
                                 : status.intensity)
            const newValue = targetValue.toString()
            // Also initialize saved value
            setSavedValues(prev => ({
              ...prev,
              [light.device_name]: newValue
            }))
            return {
              ...prev,
              [light.device_name]: newValue
            }
          }
          
          // If schedule exists and has a target_intensity, sync input value to it
          // This ensures slider position matches saved value after save
          // BUT only sync if the user hasn't made unsaved changes (currentInputValue === savedValue)
          // This prevents overwriting user's edits while they're adjusting the slider
          if (schedule && schedule.target_intensity !== null && schedule.target_intensity !== undefined) {
            const savedValue = schedule.target_intensity.toString()
            const currentSavedValue = savedValues[light.device_name]
            // Only sync if current input matches what was previously saved (no unsaved changes)
            // This allows the schedule to update the UI if changed externally, but preserves user edits
            if (currentInputValue === currentSavedValue && currentInputValue !== savedValue) {
              return {
                ...prev,
                [light.device_name]: savedValue
              }
            }
          }
          
          // Keep existing value
          return prev
        })
        setErrors(prev => {
          const newErrors = { ...prev }
          delete newErrors[light.device_name]
          return newErrors
        })
      } catch (error: any) {
        console.error(`Error loading light status for ${light.device_name}:`, error)
        setErrors(prev => ({
          ...prev,
          [light.device_name]: error.response?.data?.detail || 'Failed to load status'
        }))
      }
    }
  }

  async function handleSaveTargetIntensity(deviceName: string) {
    let schedule = schedules[deviceName]
    
    // If schedule not found, try reloading schedules first (might be stale state)
    if (!schedule) {
      console.warn(`Schedule not found for ${deviceName}, attempting to reload schedules...`)
      await loadSchedules()
      schedule = schedules[deviceName]
    }
    
    if (!schedule) {
      setErrors(prev => ({
        ...prev,
        [deviceName]: 'No day schedule found for this light. Please create a schedule first.'
      }))
      return
    }

    setSaving(prev => ({ ...prev, [deviceName]: true }))
    setErrors(prev => {
      const newErrors = { ...prev }
      delete newErrors[deviceName]
      return newErrors
    })

    try {
      const targetIntensity = parseFloat(inputValues[deviceName] ?? '0')
      
      if (isNaN(targetIntensity) || targetIntensity < 0 || targetIntensity > 100) {
        throw new Error('Target intensity must be between 0 and 100')
      }

      // Update the schedule with new target_intensity
      let updateResult
      try {
        updateResult = await apiClient.updateSchedule(schedule.id, {
          target_intensity: targetIntensity
        })
      } catch (apiError: any) {
        throw apiError
      }

      // Update schedules state with the API response directly (avoid race condition with reload)
      setSchedules(prev => {
        const updated = { ...prev }
        if (updateResult) {
          updated[deviceName] = updateResult
        }
        return updated
      })

      // Update input value to match saved value (use API response, not state)
      const savedIntensity = updateResult?.target_intensity ?? targetIntensity
      // Normalize to integer string (intensities are always whole numbers)
      const normalizedValue = Math.round(Number(savedIntensity)).toString()
      setInputValues(prev => {
        return {
          ...prev,
          [deviceName]: normalizedValue
        }
      })
      
      // Update saved values to reflect what was just saved
      setSavedValues(prev => {
        return {
          ...prev,
          [deviceName]: normalizedValue
        }
      })

      // Reload schedules in background to sync with other potential changes (but don't wait for it)
      loadSchedules().catch(err => {
        console.warn('Background schedule reload failed:', err)
      })
      
      // Refresh immediately, then again after control loop has time to apply
      // Control loop runs every 1s, so we check at 0.5s and 1.5s to catch it quickly
      const refreshStatus = async () => {
        try {
          const status = await apiClient.getLightStatus(location, cluster, deviceName)
          setLightStatuses(prev => ({
            ...prev,
            [deviceName]: status
          }))
        } catch (error) {
          // Ignore errors in refresh
        }
      }
      
      // First refresh after 500ms (catches if control loop runs soon)
      await new Promise(resolve => setTimeout(resolve, 500))
      await refreshStatus()
      
      // Second refresh after another 1s (catches next control loop iteration)
      setTimeout(refreshStatus, 1000)

      setErrors(prev => {
        const newErrors = { ...prev }
        delete newErrors[deviceName]
        return newErrors
      })
    } catch (error: any) {
      console.error(`Error saving target intensity for ${deviceName}:`, error)
      setErrors(prev => ({
        ...prev,
        [deviceName]: error.response?.data?.detail || error.message || 'Failed to save target intensity'
      }))
    } finally {
      setSaving(prev => ({ ...prev, [deviceName]: false }))
    }
  }

  async function handleSaveAll() {
    const dimmableLights = lights.filter(l => l.dimming_enabled)
    const lightsToSave = dimmableLights.filter(light => {
      const schedule = schedules[light.device_name]
      if (!schedule) return false
      
      const currentValue = inputValues[light.device_name]
      const savedValue = savedValues[light.device_name]
      
      // Check if value has changed from saved value
      return currentValue !== undefined && currentValue !== savedValue
    })

    if (lightsToSave.length === 0) {
      alert('No changes to save')
      return
    }

    setSavingAll(true)
    const savePromises = lightsToSave.map(light => handleSaveTargetIntensity(light.device_name))
    
    try {
      await Promise.all(savePromises)
      // Wait a moment for control loop to apply changes, then refresh immediately
      await new Promise(resolve => setTimeout(resolve, 500))
      await loadLightStatuses()
      // Refresh again after another moment to catch any delayed updates
      setTimeout(() => {
        loadLightStatuses()
      }, 1000)
      alert(`Successfully saved ${lightsToSave.length} light${lightsToSave.length > 1 ? 's' : ''}`)
    } catch (error) {
      console.error('Error saving all lights:', error)
      alert('Some lights failed to save. Please check individual errors.')
    } finally {
      setSavingAll(false)
    }
  }

  function hasUnsavedChanges(deviceName: string): boolean {
    const currentValue = inputValues[deviceName]
    const savedValue = savedValues[deviceName]
    const schedule = schedules[deviceName]
    
    // If currentValue is undefined, no unsaved changes (nothing entered)
    if (currentValue === undefined || currentValue === '') {
      return false
    }
    
    // Normalize to integers for comparison (intensities are always whole numbers)
    const currentInt = parseInt(currentValue, 10)
    if (isNaN(currentInt)) {
      return false
    }
    
    // Always use schedule as source of truth if available (handles state timing issues)
    // This prevents false positives when savedValues hasn't updated yet
    if (schedule && schedule.target_intensity !== null && schedule.target_intensity !== undefined) {
      const scheduleInt = Math.round(Number(schedule.target_intensity))
      return currentInt !== scheduleInt
    }
    
    // Fallback to savedValue comparison if no schedule
    if (savedValue === undefined) {
      return false
    }
    
    const savedInt = parseInt(savedValue, 10)
    if (isNaN(savedInt)) {
      return false
    }
    
    return currentInt !== savedInt
  }

  function getUnsavedCount(): number {
    const dimmableLights = lights.filter(l => l.dimming_enabled)
    return dimmableLights.filter(light => hasUnsavedChanges(light.device_name)).length
  }

  const dimmableLights = lights.filter(l => l.dimming_enabled)

  if (dimmableLights.length === 0) {
    return (
      <div className="text-gray-600 dark:text-gray-400">
        No dimmable lights configured for this zone.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Save All Button */}
      <div className="flex justify-end">
        <button
          onClick={handleSaveAll}
          disabled={savingAll || getUnsavedCount() === 0}
          className="px-6 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
        >
          {savingAll ? 'Saving All...' : `Save All${getUnsavedCount() > 0 ? ` (${getUnsavedCount()})` : ''}`}
        </button>
      </div>

      <div className="border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden bg-white dark:bg-gray-900">
        {/* Header Row */}
        <div className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_3fr_1fr_1fr] border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800">
          <div className="px-2 py-2 text-left text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Device
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Current Intensity
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Day Target
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Voltage
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Relay State
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            Target Slider
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
            <div>Number</div>
            <div>field</div>
          </div>
          <div className="px-2 py-2 text-center text-sm font-semibold text-gray-900 dark:text-gray-100">
            Status
          </div>
        </div>

        {/* Data Rows */}
        {dimmableLights.map((light) => {
          const status = lightStatuses[light.device_name]
          const error = errors[light.device_name]
          const displayName = light.display_name || light.device_name
          const deviceInfo = status 
            ? `Device:Board ${light.dimming_board_id ?? '?'}, Channel ${light.dimming_channel ?? '?'} ${status.intensity.toFixed(1)}%`
            : `Device:Board ${light.dimming_board_id ?? '?'}, Channel ${light.dimming_channel ?? '?'}`

          // Get schedule for this specific light
          const schedule = schedules[light.device_name]
          // Get target intensity for this light (from schedule or status)
          const targetIntensity = schedule?.target_intensity ?? status?.target_intensity ?? 0
          const sliderValue = inputValues[light.device_name] ?? targetIntensity.toString()

          const relayState = deviceStates[light.device_name]
          
          return (
            <div key={light.device_name} className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_3fr_1fr_1fr] border-b border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800">
              <div className="px-2 py-2 text-sm text-gray-900 dark:text-gray-100 border-r border-gray-200 dark:border-gray-800">
                <div className="font-medium">{displayName}</div>
                <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">{deviceInfo}</div>
                {error && (
                  <div className="text-xs text-red-600 dark:text-red-400 mt-1">{error}</div>
                )}
                {saving[light.device_name] && (
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">Saving...</div>
                )}
              </div>
              <div className="px-2 py-2 text-center border-r border-gray-200 dark:border-gray-800 flex items-center justify-center">
                {status ? (
                  <div className={`text-sm font-semibold ${
                    // Highlight if current intensity exceeds day target
                    (schedule?.target_intensity !== null && schedule?.target_intensity !== undefined && status.intensity > schedule.target_intensity) ||
                    (status.target_intensity !== null && status.target_intensity !== undefined && status.intensity > status.target_intensity)
                      ? 'text-red-600 dark:text-red-400'
                      : 'text-gray-900 dark:text-gray-100'
                  }`}>
                    {status.intensity.toFixed(1)}%
                    {/* Show warning if above target */}
                    {((schedule?.target_intensity !== null && schedule?.target_intensity !== undefined && status.intensity > schedule.target_intensity) ||
                      (status.target_intensity !== null && status.target_intensity !== undefined && status.intensity > status.target_intensity)) && (
                      <span className="ml-1 text-xs" title="Current intensity exceeds day target">⚠</span>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-gray-400 dark:text-gray-500">-</div>
                )}
              </div>
              <div className="px-2 py-2 text-center border-r border-gray-200 dark:border-gray-800 flex items-center justify-center">
                {schedule && schedule.target_intensity !== null && schedule.target_intensity !== undefined ? (
                  <div className="text-sm font-semibold text-blue-600 dark:text-blue-400">
                    {Math.round(schedule.target_intensity)}%
                  </div>
                ) : status && status.target_intensity !== null && status.target_intensity !== undefined ? (
                  <div className="text-sm font-semibold text-blue-600 dark:text-blue-400">
                    {Math.round(status.target_intensity)}%
                  </div>
                ) : (
                  <div className="text-sm text-gray-400 dark:text-gray-500">-</div>
                )}
              </div>
              <div className="px-2 py-2 text-center border-r border-gray-200 dark:border-gray-800 flex items-center justify-center">
                {status ? (
                  <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {status.voltage.toFixed(2)}V
                  </div>
                ) : (
                  <div className="text-sm text-gray-400 dark:text-gray-500">-</div>
                )}
              </div>
              <div className="px-2 py-2 text-center border-r border-gray-200 dark:border-gray-800 flex items-center justify-center">
                {relayState !== undefined ? (
                  <div className={`text-sm font-semibold ${
                    relayState === 1 
                      ? 'text-green-600 dark:text-green-400' 
                      : 'text-gray-500 dark:text-gray-400'
                  }`}>
                    {relayState === 1 ? 'ON' : 'OFF'}
                  </div>
                ) : (
                  <div className="text-sm text-gray-400 dark:text-gray-500">-</div>
                )}
              </div>
            <div className="px-2 py-2 border-r border-gray-200 dark:border-gray-800 flex items-center">
              {status ? (
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="1"
                  value={sliderValue}
                  onChange={(e) => setInputValues(prev => ({ ...prev, [light.device_name]: e.target.value }))}
                  disabled={!schedule || saving[light.device_name]}
                  className="w-full h-2 bg-gray-200 dark:bg-gray-900 rounded-lg appearance-none cursor-pointer accent-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
                />
              ) : (
                <div className="text-sm text-gray-400 dark:text-gray-500 w-full text-center">Loading...</div>
              )}
            </div>
            <div className="px-2 py-2 text-center border-r border-gray-200 dark:border-gray-800 flex items-center justify-center">
              {status ? (
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  value={sliderValue}
                  onChange={(e) => setInputValues(prev => ({ ...prev, [light.device_name]: e.target.value }))}
                  disabled={!schedule || saving[light.device_name]}
                  className="w-20 border-2 border-gray-400 dark:border-gray-600 rounded-md px-2 py-1 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-medium text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                />
              ) : (
                <div className="text-sm text-gray-400 dark:text-gray-500">-</div>
              )}
            </div>
            <div className="px-2 py-2 text-center flex items-center justify-center">
              {schedule ? (
                hasUnsavedChanges(light.device_name) ? (
                  <span className="text-xs text-orange-600 dark:text-orange-400 font-medium" title="Unsaved changes">
                    ● Modified
                  </span>
                ) : saving[light.device_name] ? (
                  <span className="text-xs text-blue-600 dark:text-blue-400">Saving...</span>
                ) : (
                  <span className="text-xs text-gray-400 dark:text-gray-500">Saved</span>
                )
              ) : (
                <div className="text-xs text-gray-400 dark:text-gray-500">No schedule</div>
              )}
            </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
