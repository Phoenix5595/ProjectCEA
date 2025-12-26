import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import type { LightStatus } from '../types/light'
import type { Schedule } from '../types/schedule'

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
  const [savedValues, setSavedValues] = useState<Record<string, { intensity: number; voltage: number }>>({})
  const [inputValues, setInputValues] = useState<Record<string, string>>({}) // Store input values as strings to prevent focus loss
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [schedules, setSchedules] = useState<Schedule[]>([])

  useEffect(() => {
    // Load initial light statuses
    loadLightStatuses()
    loadSchedules()
    
    // Refresh every 5 seconds
    const interval = setInterval(loadLightStatuses, 5000)
    return () => clearInterval(interval)
  }, [location, cluster, lights])

  async function loadSchedules() {
    try {
      const data = await apiClient.getSchedules(location, cluster)
      setSchedules(data)
    } catch (error) {
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
        // Update saved values when loading
        setSavedValues(prev => ({
          ...prev,
          [light.device_name]: {
            intensity: status.intensity,
            voltage: status.voltage
          }
        }))
        // Set input values as strings for display
        setInputValues(prev => ({
          ...prev,
          [light.device_name]: status.intensity.toString()
        }))
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

  async function handleIntensityChange(deviceName: string, intensity: number) {
    setLoading(prev => ({ ...prev, [deviceName]: true }))
    setErrors(prev => {
      const newErrors = { ...prev }
      delete newErrors[deviceName]
      return newErrors
    })

    try {
      const status = await apiClient.setLightIntensity(location, cluster, deviceName, intensity)
      setLightStatuses(prev => ({
        ...prev,
        [deviceName]: status
      }))
      // Update saved values after successful save
      setSavedValues(prev => ({
        ...prev,
        [deviceName]: {
          intensity: status.intensity,
          voltage: status.voltage
        }
      }))
      // Update input values to match saved value
      setInputValues(prev => ({
        ...prev,
        [deviceName]: status.intensity.toString()
      }))
    } catch (error: any) {
      console.error(`Error setting intensity for ${deviceName}:`, error)
      setErrors(prev => ({
        ...prev,
        [deviceName]: error.response?.data?.detail || 'Failed to set intensity'
      }))
    } finally {
      setLoading(prev => ({ ...prev, [deviceName]: false }))
    }
  }

  const dimmableLights = lights.filter(l => l.dimming_enabled)

  if (dimmableLights.length === 0) {
    return (
      <div className="text-gray-600">
        No dimmable lights configured for this zone.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Individual Light Controls */}
      <div className="border-t border-gray-300 pt-6">
        <h2 className="text-xl font-bold text-gray-900 mb-4">Individual Light Controls</h2>
      </div>
      
      {dimmableLights.map((light) => {
        const status = lightStatuses[light.device_name]
        const isLoading = loading[light.device_name]
        const error = errors[light.device_name]
        const displayName = light.display_name || light.device_name

        return (
          <div key={light.device_name} className="border border-gray-200 rounded-lg p-4 bg-gray-50">
            <div className="mb-4">
              <h3 className="text-lg font-semibold text-gray-900 mb-1">{displayName}</h3>
              <div className="text-sm text-gray-600">
                Device: {light.device_name}
                {light.dimming_board_id !== undefined && (
                  <> • Board {light.dimming_board_id}, Channel {light.dimming_channel}</>
                )}
              </div>
            </div>

            {status && (
              <div className="mb-4">
                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <div className="text-sm font-medium text-gray-700 mb-1">Intensity</div>
                    <div className="text-2xl font-bold text-gray-900">
                      {status.intensity.toFixed(1)}%
                    </div>
                    {savedValues[light.device_name] && (
                      <p className="text-xs text-gray-500 mt-1">
                        Current: {savedValues[light.device_name].intensity.toFixed(1)}%
                      </p>
                    )}
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-700 mb-1">Voltage</div>
                    <div className="text-2xl font-bold text-gray-900">
                      {status.voltage.toFixed(2)}V
                    </div>
                    {savedValues[light.device_name] && (
                      <p className="text-xs text-gray-500 mt-1">
                        Current: {savedValues[light.device_name].voltage.toFixed(2)}V
                      </p>
                    )}
                  </div>
                </div>

                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Set Intensity (0-100%)
                  </label>
                  <div className="flex items-center gap-4">
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={inputValues[light.device_name] ?? status.intensity.toString()}
                      onChange={(e) => setInputValues(prev => ({ ...prev, [light.device_name]: e.target.value }))}
                      onMouseUp={() => {
                        const val = parseFloat(inputValues[light.device_name] ?? status.intensity.toString())
                        if (!isNaN(val) && val >= 0 && val <= 100) {
                          handleIntensityChange(light.device_name, val)
                        }
                      }}
                      onTouchEnd={() => {
                        const val = parseFloat(inputValues[light.device_name] ?? status.intensity.toString())
                        if (!isNaN(val) && val >= 0 && val <= 100) {
                          handleIntensityChange(light.device_name, val)
                        }
                      }}
                      disabled={isLoading}
                      className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                    />
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="1"
                      value={inputValues[light.device_name] ?? status.intensity.toString()}
                      onChange={(e) => setInputValues(prev => ({ ...prev, [light.device_name]: e.target.value }))}
                      onBlur={() => {
                        const val = parseFloat(inputValues[light.device_name] ?? status.intensity.toString())
                        if (!isNaN(val) && val >= 0 && val <= 100) {
                          handleIntensityChange(light.device_name, val)
                        } else {
                          // Revert to last valid intensity if input is invalid
                          setInputValues(prev => ({ ...prev, [light.device_name]: status.intensity.toString() }))
                        }
                      }}
                      disabled={isLoading}
                      className="w-20 border-2 border-gray-400 rounded-md px-3 py-2 bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <span className="text-sm text-gray-600 w-8">%</span>
                  </div>
                </div>

                {status.board_info && (
                  <div className="text-xs text-gray-500 mt-2">
                    Board: {status.board_info.name || `Board ${status.board_info.board_id}`} 
                    (I2C: 0x{status.board_info.i2c_address.toString(16).toUpperCase()})
                  </div>
                )}
              </div>
            )}

            {error && (
              <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                {error}
              </div>
            )}

            {isLoading && (
              <div className="mt-2 text-sm text-gray-500">Updating...</div>
            )}

            {!status && !error && (
              <div className="text-sm text-gray-500">Loading status...</div>
            )}

            {/* Schedules for this light */}
            <div className="mt-4 pt-4 border-t border-gray-300">
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Schedules</h4>
              {(() => {
                const lightSchedules = schedules.filter(s => s.device_name === light.device_name)
                if (lightSchedules.length === 0) {
                  return (
                    <p className="text-xs text-gray-500">
                      No schedules configured. Go to the Schedules tab to create one.
                    </p>
                  )
                }
                return (
                  <div className="space-y-2">
                    {lightSchedules.map((schedule) => (
                      <div
                        key={schedule.id}
                        className="text-xs bg-white border border-gray-200 rounded p-2"
                      >
                        <div className="font-medium text-gray-700">{schedule.name}</div>
                        <div className="text-gray-600 mt-1">
                          {schedule.start_time} - {schedule.end_time}
                          {schedule.day_of_week !== null && ` (Day ${schedule.day_of_week})`}
                          {schedule.mode && ` • ${schedule.mode}`}
                          {schedule.target_intensity !== null && schedule.target_intensity !== undefined && (
                            <>
                              {` • ${schedule.target_intensity}%`}
                              {(schedule.ramp_up_duration || schedule.ramp_down_duration) && (
                                <span className="text-gray-500">
                                  {' '}(↑{schedule.ramp_up_duration || 0}m ↓{schedule.ramp_down_duration || 0}m)
                                </span>
                              )}
                            </>
                          )}
                          {!schedule.enabled && <span className="text-red-600"> • DISABLED</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )
              })()}
            </div>
          </div>
        )
      })}
    </div>
  )
}

