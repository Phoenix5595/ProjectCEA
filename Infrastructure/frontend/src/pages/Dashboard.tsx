import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiClient } from '../services/api'
import { wsClient } from '../services/websocket'
import { useTheme } from '../contexts/ThemeContext'
import { logger } from '../utils/logger'
import type { Device } from '../types/device'

interface WeatherData {
  temperature: number
  humidity: number
  pressure: number
  wind_speed: number
  wind_direction: number | null
  description: string
  location: string
  timestamp: string
}

interface SystemStats {
  cpu_usage: number | null
  memory_usage: number | null
  disk_usage: number | null
  uptime: string | null
  load_avg?: string | null
  process_count?: number | null
  cpu_temp_c?: number | null
  throttle_status?: string | null
  services: Array<{
    name: string
    status: 'running' | 'stopped' | 'error' | 'unreachable'
    latency_ms?: number
  }>
}



export default function Dashboard() {
  const { theme, toggleTheme } = useTheme()
  const [devices, setDevices] = useState<Device[]>([])
  const [weatherData, setWeatherData] = useState<WeatherData | null>(null)
  const [systemStats, setSystemStats] = useState<SystemStats | null>(null)
  const [statusDevices, setStatusDevices] = useState<Record<string, Record<string, Record<string, { intensity?: number }>>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [sensorData, setSensorData] = useState<Record<string, number>>({})

  useEffect(() => {
    // Connect WebSocket
    wsClient.connect()

// Subscribe to device updates
    const unsubscribeDevice = wsClient.on('device_update', (message) => {
      setDevices(prev => prev.map(device => 
        device.location === message.location && 
          device.cluster === message.cluster && 
          device.device_name === message.device
            ? { ...device, state: message.state, mode: message.mode }
            : device
      ))
    })

    // Subscribe to sensor updates
    const unsubscribeSensor = wsClient.on('sensor_update', (message) => {
      setSensorData(prev => ({
        ...prev,
        [`${message.location}_${message.cluster}_${message.sensor}`]: message.value
      }))
    })

    // Load initial data
    loadInitialData()

    return () => {
      unsubscribeDevice()
      unsubscribeSensor()
      wsClient.disconnect()
    }
  }, [])

  async function loadInitialData() {
    try {
      // Load devices
      const devicesData = await apiClient.getAllDevices()
      setDevices(devicesData)

      // Load setpoints from Redis via backend API
      try {
        const setpointKeys = [
          'Flower Room_main_heating_setpoint',
          'Flower Room_main_cooling_setpoint',
          'Flower Room_main_co2_setpoint',
          'Flower Room_main_vpd_setpoint',
          'Veg Room_main_heating_setpoint',
          'Veg Room_main_cooling_setpoint',
          'Veg Room_main_co2_setpoint',
          'Veg Room_main_vpd_setpoint'
        ]
        
        // Add light intensity keys
        const lightIntensityKeys = [
          'Veg Room_main_light_1_intensity',
          'Veg Room_main_light_2_intensity', 
          'Veg Room_main_light_3_intensity',
          'Flower Room_main_light_1_intensity',
          'Flower Room_main_light_2_intensity',
          'Flower Room_main_light_3_intensity'
        ]
        
        // Combine all keys for single API call
        const allKeys = [...setpointKeys, ...lightIntensityKeys]
        
        // Get setpoints and light intensities from backend API (reads from Redis; uses same base URL as apiClient)
        const setpointData = await apiClient.getSensorDataBulk(allKeys)
        if (Object.keys(setpointData).length > 0) {
          setSensorData(prev => ({ ...prev, ...setpointData }))
        }
      } catch (error) {
        console.log('Setpoints and light intensities not available from API, using fallback')
      }

      // Load real weather data (API returns temp, rh; support legacy temperature/humidity)
      const weatherResponse = await apiClient.getLatestWeather()
      if (weatherResponse?.data) {
        const d = weatherResponse.data
        const temp = d.temp?.value ?? d.temperature?.value
        const rh = d.rh?.value ?? d.humidity?.value
        if (temp != null && rh != null) {
          setWeatherData({
            temperature: Number(temp),
            humidity: Number(rh),
            pressure: Number(d.pressure?.value ?? 0),
            wind_speed: Number(d.wind_speed?.value ?? 0),
            wind_direction: d.wind_direction?.value != null ? Number(d.wind_direction.value) : null,
            description: d.description?.value ?? 'N/A',
            location: 'Quebec City',
            timestamp: weatherResponse.timestamp ?? ''
          })
        }
      }

      // Load real system stats from /api/status (system + service_health + devices + effective setpoints)
      const statusResponse = await apiClient.getSystemStatus()
      if (statusResponse) {
        if (statusResponse.devices) setStatusDevices(statusResponse.devices)
        // Effective setpoints from automation Redis (Celsius; same source as control loop)
        const ep = statusResponse.effective_setpoints
        if (ep && typeof ep === 'object') {
          const effectiveFlat: Record<string, number> = {}
          for (const [location, clusters] of Object.entries(ep)) {
            if (!clusters || typeof clusters !== 'object') continue
            for (const [cluster, values] of Object.entries(clusters)) {
              if (!values || typeof values !== 'object') continue
              const prefix = `${location}_${cluster}_`
              if (values.heating_setpoint != null) effectiveFlat[`${prefix}heating_setpoint`] = Number(values.heating_setpoint)
              if (values.cooling_setpoint != null) effectiveFlat[`${prefix}cooling_setpoint`] = Number(values.cooling_setpoint)
              if (values.co2_setpoint != null) effectiveFlat[`${prefix}co2_setpoint`] = Number(values.co2_setpoint)
              if (values.vpd_setpoint != null) effectiveFlat[`${prefix}vpd_setpoint`] = Number(values.vpd_setpoint)
            }
          }
          if (Object.keys(effectiveFlat).length > 0) setSensorData(prev => ({ ...prev, ...effectiveFlat }))
        }
        const sys = statusResponse.system
        const formatUptime = (sec: number) => {
          const d = Math.floor(sec / 86400)
          const h = Math.floor((sec % 86400) / 3600)
          return `${d}d ${h}h`
        }
        setSystemStats({
          cpu_usage: sys?.cpu_percent ?? null,
          memory_usage: sys?.memory_percent ?? null,
          disk_usage: sys?.disk_percent ?? null,
          uptime: sys?.uptime_seconds != null ? formatUptime(sys.uptime_seconds) : null,
          load_avg: sys?.load_avg ?? null,
          process_count: sys?.process_count ?? null,
          cpu_temp_c: sys?.cpu_temp_c ?? null,
          throttle_status: sys?.throttle_status ?? null,
          services: Array.isArray(statusResponse.service_health)
            ? statusResponse.service_health.map((s: { name: string; status: string; latency_ms?: number }) => ({
                name: s.name,
                status: s.status as 'running' | 'stopped' | 'error' | 'unreachable',
                latency_ms: s.latency_ms
              }))
            : [
                { name: 'postgresql', status: 'running' as const },
                { name: 'redis-server', status: 'running' as const },
                { name: 'can-processor', status: 'running' as const },
                { name: 'soil-sensor-service', status: 'running' as const },
                { name: 'weather-service', status: 'running' as const },
                { name: 'cea-backend', status: 'running' as const },
                { name: 'automation-service', status: 'running' as const }
              ]
        })
      }
    } catch (error) {
      console.error('Error loading initial data:', error)
      logger.error('Error loading initial data:', error)
      // Do not set fake numbers; show "Unavailable" for system stats
      setSystemStats({
        cpu_usage: null,
        memory_usage: null,
        disk_usage: null,
        uptime: null,
        load_avg: null,
        process_count: null,
        cpu_temp_c: null,
        throttle_status: null,
        services: []
      })
    } finally {
      setLoading(false)
    }
  }

  // Refresh weather every 15 minutes
  useEffect(() => {
    const refreshWeather = async () => {
      try {
        const weatherResponse = await apiClient.getLatestWeather()
        if (weatherResponse?.data) {
          const d = weatherResponse.data
          const temp = d.temp?.value ?? d.temperature?.value
          const rh = d.rh?.value ?? d.humidity?.value
          if (temp != null && rh != null) {
            setWeatherData({
              temperature: Number(temp),
              humidity: Number(rh),
              pressure: Number(d.pressure?.value ?? 0),
              wind_speed: Number(d.wind_speed?.value ?? 0),
              wind_direction: d.wind_direction?.value != null ? Number(d.wind_direction.value) : null,
              description: d.description?.value ?? 'N/A',
              location: 'Quebec City',
              timestamp: weatherResponse.timestamp ?? ''
            })
          }
        }
      } catch {
        // Keep previous weather data on error
      }
    }
    const interval = setInterval(refreshWeather, 15 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  // Refresh system stats every 5 seconds
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const statusResponse = await apiClient.getSystemStatus()
        if (statusResponse) {
          const sys = statusResponse.system
          const formatUptime = (sec: number) => {
            const d = Math.floor(sec / 86400)
            const h = Math.floor((sec % 86400) / 3600)
            return `${d}d ${h}h`
          }
          setSystemStats({
            cpu_usage: sys?.cpu_percent ?? null,
            memory_usage: sys?.memory_percent ?? null,
            disk_usage: sys?.disk_percent ?? null,
            uptime: sys?.uptime_seconds != null ? formatUptime(sys.uptime_seconds) : null,
            load_avg: sys?.load_avg ?? null,
            process_count: sys?.process_count ?? null,
            cpu_temp_c: sys?.cpu_temp_c ?? null,
            throttle_status: sys?.throttle_status ?? null,
            services: Array.isArray(statusResponse.service_health)
              ? statusResponse.service_health.map((s: { name: string; status: string; latency_ms?: number }) => ({
                  name: s.name,
                  status: s.status as 'running' | 'stopped' | 'error' | 'unreachable',
                  latency_ms: s.latency_ms
                }))
              : [
                  { name: 'postgresql', status: 'running' as const },
                  { name: 'redis-server', status: 'running' as const },
                  { name: 'can-processor', status: 'running' as const },
                  { name: 'soil-sensor-service', status: 'running' as const },
                  { name: 'weather-service', status: 'running' as const },
                  { name: 'cea-backend', status: 'running' as const },
                  { name: 'automation-service', status: 'running' as const }
                ]
          })
        }
      } catch (error) {
        console.error('Error refreshing system stats:', error)
      }
    }, 5000)

    return () => clearInterval(interval)
  }, [])

  // Helper function to determine room light state
  const getRoomLightState = (location: string) => {
    // Handle nested device structure from API
    let roomLights: any[] = []
    
    // Check if devices is nested (API response structure)
    if (devices && typeof devices === 'object' && !Array.isArray(devices)) {
      // Extract devices from nested structure
      Object.keys(devices).forEach(roomName => {
        const roomData = (devices as any)[roomName]
        if (roomData && typeof roomData === 'object' && roomData.main) {
          Object.keys(roomData.main).forEach(deviceName => {
            const device = roomData.main[deviceName]
            if (device && deviceName.startsWith('light_')) {
              roomLights.push({
                ...device,
                device_name: deviceName,
                location: roomName,
                cluster: 'main'
              })
            }
          })
        }
      })
    } else if (Array.isArray(devices)) {
      // Handle flat array structure
      roomLights = devices.filter(d => 
        d.location === location && 
        d.cluster === 'main' && 
        d.device_name?.startsWith('light_')
      )
    }
    
    // Filter by specific location
    const locationLights = roomLights.filter(light => light.location === location)
    
    // Check if any lights are ON
    const hasLightsOn = locationLights.some(light => light.state === 1)
    
    return hasLightsOn ? '☀️' : '🌙'
  }

  // Helper function to get setpoint color based on climate mode
  const getSetpointColor = () => {
    // Default to amber color, can be enhanced with climate mode detection later
    return 'text-amber-400'
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 p-4">
        <div className="max-w-full mx-auto">
          <h1 className="text-3xl font-bold mb-8 text-gray-100">CEA Automation Dashboard</h1>
          <p className="text-gray-300">Loading...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 p-2">
      <div className="max-w-full mx-auto h-[calc(100vh-1rem)] flex flex-col">
        
        {/* Sticky Header: title left; weather (Quebec City) + theme right */}
        <div className="sticky top-0 z-10 bg-gray-950 p-1 mb-2">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-gray-100 flex items-center gap-2">
                <span>🌱</span> CEA Automation Dashboard
              </h1>
            </div>
            <div className="flex items-center gap-4">
              {weatherData && (
                <div className="flex items-center gap-3 text-sm text-gray-300" title={weatherData.timestamp ? `Quebec City weather · ${new Date(weatherData.timestamp).toLocaleString()}` : 'Quebec City weather'}>
                  <span className="text-gray-400 font-medium">Quebec City</span>
                  <span className="flex items-center gap-1">
                    <span>🌤</span> {Number(weatherData.temperature).toFixed(2)}°C
                  </span>
                  <span>{Number(weatherData.humidity).toFixed(2)}%</span>
                  <span>{Number(weatherData.pressure).toFixed(2)} hPa</span>
                  <span>{Number(weatherData.wind_speed).toFixed(2)} km/h</span>
                  {weatherData.wind_direction != null && (
                    <span title="Wind direction (degrees)">{Number(weatherData.wind_direction).toFixed(2)}°</span>
                  )}
                  {weatherData.description && weatherData.description !== 'N/A' && (
                    <span className="text-gray-400">{weatherData.description}</span>
                  )}
                </div>
              )}
              <button
              onClick={toggleTheme}
              className="p-2 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 transition-colors"
              aria-label="Toggle theme"
            >
              {theme === 'light' ? (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                </svg>
              )}
            </button>
            </div>
          </div>
        </div>

        {/* Main Content - Enhanced Layout */}
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-2 min-h-0">
          
          {/* Column 1: Veg Room */}
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 flex flex-col">
            <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
              <span className="flex items-center gap-2">
                <span>🌱</span> Vegetation Room
              </span>
              <div className="flex items-center gap-1">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/50 text-green-400 border border-green-800/50 cursor-help" title={`Status: ${getRoomLightState('Veg Room') === '☀️' ? 'Day' : 'Night'}`}>
                  {getRoomLightState('Veg Room')}
                </span>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <Link
                to={`/zone/${encodeURIComponent('Veg Room')}/${encodeURIComponent('main')}`}
                className="block h-full"
              >
                <div className="space-y-2">
                  {/* Current Conditions */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1">Current Conditions</div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="text-gray-500">Temperature</div>
                        <div className="text-white font-mono">
                          {sensorData['Veg Room_main_dry_bulb_f'] ? 
                            `${((sensorData['Veg Room_main_dry_bulb_f'] - 32) * 5/9).toFixed(2)}°C` : 
                            '--°C'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">Humidity</div>
                        <div className="text-white font-mono">
                          {sensorData['Veg Room_main_relative_humidity'] ? 
                            `${Number(sensorData['Veg Room_main_relative_humidity']).toFixed(2)}%` : 
                            '--%'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">CO2</div>
                        <div className="text-white font-mono">
                          {sensorData['Veg Room_main_co2'] ? 
                            `${Number(sensorData['Veg Room_main_co2']).toFixed(2)} ppm` : 
                            '-- ppm'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">VPD</div>
                        <div className="text-white font-mono">
                          {sensorData['Veg Room_main_vpd'] ? 
                            `${Number(sensorData['Veg Room_main_vpd']).toFixed(2)} kPa` : 
                            '-- kPa'
                          }
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Effective Setpoints (heating, cooling, RH, CO2, VPD) */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1" title="From Redis (effective_setpoint:*): heating, cooling, CO2, VPD">Effective Setpoints</div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="text-gray-500">Heating</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective heating setpoint (Veg Room, from Redis)">
                          {sensorData['Veg Room_main_heating_setpoint'] != null
                            ? `${Number(sensorData['Veg Room_main_heating_setpoint']).toFixed(2)}°C`
                            : '--°C'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">Cooling</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective cooling setpoint (Veg Room)">
                          {sensorData['Veg Room_main_cooling_setpoint'] != null
                            ? `${Number(sensorData['Veg Room_main_cooling_setpoint']).toFixed(2)}°C`
                            : '--°C'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">CO2</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective CO2 setpoint (Veg Room)">
                          {sensorData['Veg Room_main_co2_setpoint'] != null
                            ? `${Number(sensorData['Veg Room_main_co2_setpoint']).toFixed(2)} ppm`
                            : '-- ppm'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">VPD</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective VPD setpoint (Veg Room)">
                          {sensorData['Veg Room_main_vpd_setpoint'] != null
                            ? `${Number(sensorData['Veg Room_main_vpd_setpoint']).toFixed(2)} kPa`
                            : '-- kPa'}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Light Status */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1">Light Status</div>
                    <div className="space-y-1">
                      {devices.filter(d => d.location === 'Veg Room' && d.cluster === 'main' && d.device_name?.startsWith('light_')).map((device, index) => {
                        // Map device names to display names based on config
                        const displayNames: Record<string, string> = {
                          'light_1': 'Eyefinity Top',
                          'light_2': 'Ridgetop Bottom Right', 
                          'light_3': 'Ridgetop Bottom Left'
                        }
                        const displayName = displayNames[device.device_name] || device.device_name
                        
                        return (
                          <div key={index} className="flex items-center justify-between text-xs">
                            <span className="text-gray-300 truncate flex-1">
                              {displayName}
                            </span>
                            <div className="flex items-center gap-1">
                              <span className="text-cyan-400 font-mono">
                                {(sensorData[`Veg Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Veg Room']?.['main']?.[device.device_name]?.intensity) != null
                                  ? `${Number(sensorData[`Veg Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Veg Room']?.['main']?.[device.device_name]?.intensity).toFixed(2)}%`
                                  : '--%'
                                }
                              </span>
                              <span className={`text-[14px] px-1.5 py-0.5 rounded cursor-help ${
                                device.state === 1 
                                  ? 'bg-blue-900/50 text-blue-400 border border-blue-800/50' 
                                  : 'bg-gray-700 text-gray-500 border border-gray-600'
                              }`} title={`${displayName}: ${device.state === 1 ? 'Sun' : 'Moon'}`}>
                                {device.state === 1 ? '☀️' : '🌙'}
                              </span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Device Status */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-2">Device Status</div>
                    <div className="space-y-1">
                      {devices.filter(d => d.location === 'Veg Room' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => (
                        <div key={index} className="flex items-center justify-between text-xs">
                          <span className="text-gray-300 truncate flex-1">
                            {device.device_name}
                          </span>
                          <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${
                            device.state === 1 
                              ? 'bg-green-900 text-green-200' 
                              : 'bg-gray-700 text-gray-400'
                          }`}>
                            {device.state === 1 ? 'ON' : 'OFF'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Link>
            </div>
          </div>

          {/* Column 2: Flower Room */}
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 flex flex-col">
            <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
              <span className="flex items-center gap-2">
                <span>🌻</span> Flower Room
              </span>
              <div className="flex items-center gap-1">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/50 text-green-400 border border-green-800/50 cursor-help" title={`Status: ${getRoomLightState('Flower Room') === '☀️' ? 'Day' : 'Night'}`}>
                  {getRoomLightState('Flower Room')}
                </span>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <Link
                to={`/zone/${encodeURIComponent('Flower Room')}/${encodeURIComponent('main')}`}
                className="block h-full"
              >
                <div className="space-y-2">
                  {/* Current Conditions */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1">Current Conditions (Average)</div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="text-gray-500">Temperature</div>
                        <div className="text-white font-mono">
                          {sensorData['Flower Room_main_temperature_sensor'] ? 
                            `${Number(sensorData['Flower Room_main_temperature_sensor']).toFixed(2)}°C` : 
                            '--°C'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">Humidity</div>
                        <div className="text-white font-mono">
                          {sensorData['Flower Room_main_humidity_sensor'] ? 
                            `${Number(sensorData['Flower Room_main_humidity_sensor']).toFixed(2)}%` : 
                            '--%'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">CO2</div>
                        <div className="text-white font-mono">
                          {sensorData['Flower Room_main_co2_sensor'] ? 
                            `${Number(sensorData['Flower Room_main_co2_sensor']).toFixed(2)} ppm` : 
                            '-- ppm'
                          }
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">VPD</div>
                        <div className="text-white font-mono">
                          {sensorData['Flower Room_main_vpd_sensor'] ? 
                            `${Number(sensorData['Flower Room_main_vpd_sensor']).toFixed(2)} kPa` : 
                            '-- kPa'
                          }
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Effective Setpoints (heating, cooling, RH, CO2, VPD) */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1" title="From Redis (effective_setpoint:*): heating, cooling, CO2, VPD">Effective Setpoints</div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="text-gray-500">Heating</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective heating setpoint (Flower Room, from Redis)">
                          {sensorData['Flower Room_main_heating_setpoint'] != null
                            ? `${Number(sensorData['Flower Room_main_heating_setpoint']).toFixed(2)}°C`
                            : '--°C'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">Cooling</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective cooling setpoint (Flower Room)">
                          {sensorData['Flower Room_main_cooling_setpoint'] != null
                            ? `${Number(sensorData['Flower Room_main_cooling_setpoint']).toFixed(2)}°C`
                            : '--°C'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">CO2</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective CO2 setpoint (Flower Room)">
                          {sensorData['Flower Room_main_co2_setpoint'] != null
                            ? `${Number(sensorData['Flower Room_main_co2_setpoint']).toFixed(2)} ppm`
                            : '-- ppm'}
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">VPD</div>
                        <div className={`font-mono ${getSetpointColor()}`} title="Effective VPD setpoint (Flower Room)">
                          {sensorData['Flower Room_main_vpd_setpoint'] != null
                            ? `${Number(sensorData['Flower Room_main_vpd_setpoint']).toFixed(2)} kPa`
                            : '-- kPa'}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Light Status */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1" title="Light status for Flower Room">Light Status</div>
                    <div className="space-y-1">
                      {devices.filter(d => d.location === 'Flower Room' && d.cluster === 'main' && d.device_name?.startsWith('light_')).map((device, index) => {
                        // Map device names to display names based on config
                        const displayNames: Record<string, string> = {
                          'light_1': 'Chilled Front',
                          'light_2': 'Apache', 
                          'light_3': 'Chilled Back'
                        }
                        const displayName = displayNames[device.device_name] || device.device_name
                        
                        return (
                          <div key={index} className="flex items-center justify-between text-xs">
                            <span className="text-gray-300 truncate flex-1">
                              {displayName}
                            </span>
                            <div className="flex items-center gap-1">
                              <span className="text-cyan-400 font-mono">
                                {(sensorData[`Flower Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Flower Room']?.['main']?.[device.device_name]?.intensity) != null
                                  ? `${Number(sensorData[`Flower Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Flower Room']?.['main']?.[device.device_name]?.intensity).toFixed(2)}%`
                                  : '--%'
                                }
                              </span>
                              <span className={`text-[14px] px-1.5 py-0.5 rounded cursor-help ${
                                device.state === 1 
                                  ? 'bg-blue-900/50 text-blue-400 border border-blue-800/50' 
                                  : 'bg-gray-700 text-gray-500 border border-gray-600'
                              }`} title={`${displayName}: ${device.state === 1 ? 'Sun' : 'Moon'}`}>
                                {device.state === 1 ? '☀️' : '🌙'}
                              </span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Device Status */}
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-2">Device Status</div>
                    <div className="space-y-1">
                      {devices.filter(d => d.location === 'Flower Room' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => (
                        <div key={index} className="flex items-center justify-between text-xs">
                          <span className="text-gray-300 truncate flex-1">
                            {device.device_name}
                          </span>
                          <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${
                            device.state === 1 
                              ? 'bg-green-900 text-green-200' 
                              : 'bg-gray-700 text-gray-400'
                          }`}>
                            {device.state === 1 ? 'ON' : 'OFF'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Link>
            </div>
          </div>

          {/* Column 3: Split Layout - System + Lab */}
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 flex flex-col">
            
            {/* Top Half: System Status */}
            <div className="flex-1 border-b border-gray-800 pb-2 mb-2">
              <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <span>🖥</span> Mothernode Status
                </span>
                <div className="flex items-center gap-1">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/50 text-green-400 border border-green-800/50 cursor-help" title="All Systems Operational">
                    ✓
                  </span>
                </div>
              </div>
              <div className="space-y-2">
                {systemStats ? (
                  <>
                    {/* System Resources */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">System Resources</div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <div className="text-gray-500">CPU</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <span>{systemStats.cpu_usage != null ? `${Number(systemStats.cpu_usage).toFixed(2)}%` : '—'}</span>
                            {systemStats.cpu_usage != null && (
                              <div className="w-8 h-1 bg-gray-700 rounded overflow-hidden">
                                <div
                                  className="h-full bg-green-400 transition-all"
                                  style={{ width: `${Math.min(systemStats.cpu_usage, 100)}%` }}
                                />
                              </div>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Memory</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <span>{systemStats.memory_usage != null ? `${Number(systemStats.memory_usage).toFixed(2)}%` : '—'}</span>
                            {systemStats.memory_usage != null && (
                              <div className="w-8 h-1 bg-gray-700 rounded overflow-hidden">
                                <div
                                  className="h-full bg-blue-400 transition-all"
                                  style={{ width: `${Math.min(systemStats.memory_usage, 100)}%` }}
                                />
                              </div>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Disk</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <span>{systemStats.disk_usage != null ? `${Number(systemStats.disk_usage).toFixed(2)}%` : '—'}</span>
                            {systemStats.disk_usage != null && (
                              <div className="w-8 h-1 bg-gray-700 rounded overflow-hidden">
                                <div
                                  className="h-full bg-amber-400 transition-all"
                                  style={{ width: `${Math.min(systemStats.disk_usage, 100)}%` }}
                                />
                              </div>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Load Avg</div>
                          <div className="text-white font-mono text-[10px]">{systemStats.load_avg ?? '—'}</div>
                        </div>
                        <div>
                          <div className="text-gray-500">Processes</div>
                          <div className="text-white font-mono text-[10px]">{systemStats.process_count ?? '—'}</div>
                        </div>
                        <div>
                          <div className="text-gray-500">Uptime</div>
                          <div className="text-white font-mono text-[10px]">{systemStats.uptime ?? '—'}</div>
                        </div>
                      </div>
                    </div>

                    {/* Service Health */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">Service Health</div>
                      <div className="grid grid-cols-1 gap-1">
                        {systemStats.services.length === 0 ? (
                          <div className="text-xs text-gray-500">Status unknown</div>
                        ) : (
                          systemStats.services.map((service, index) => (
                            <div key={index} className="flex items-center justify-between text-xs">
                              <div className="flex items-center gap-2 flex-1">
                                <div className={`w-1.5 h-1.5 rounded-full ${
                                  service.status === 'running' ? 'bg-green-400' : service.status === 'unreachable' ? 'bg-red-400' : 'bg-yellow-400'
                                }`} />
                                <span className="text-gray-300">{service.name}</span>
                              </div>
                              <div className="flex items-center gap-1">
                                {service.latency_ms != null && (
                                  <span className="text-gray-500 text-[8px]">{service.latency_ms}ms</span>
                                )}
                                <span className={`px-1 py-0.5 rounded text-[8px] font-medium ${
                                  service.status === 'running'
                                    ? 'bg-green-900 text-green-200'
                                    : service.status === 'unreachable' || service.status === 'stopped'
                                    ? 'bg-red-900 text-red-200'
                                    : 'bg-yellow-900 text-yellow-200'
                                }`}>
                                  {service.status === 'running' ? '✓' : '✗'}
                                </span>
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Hardware Info */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">Hardware Info</div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <div className="text-gray-500">Model</div>
                          <div className="text-white font-mono text-[10px]">Raspberry Pi 5</div>
                        </div>
                        <div>
                          <div className="text-gray-500">CPU Cores</div>
                          <div className="text-white font-mono text-[10px]">4x 2.4GHz</div>
                        </div>
                        <div>
                          <div className="text-gray-500">RAM Total</div>
                          <div className="text-white font-mono text-[10px]">8GB</div>
                        </div>
                        <div>
                          <div className="text-gray-500">Storage</div>
                          <div className="text-white font-mono text-[10px]">256GB SSD</div>
                        </div>
                        <div>
                          <div className="text-gray-500">Temp</div>
                          <div className="text-white font-mono text-[10px]">{systemStats.cpu_temp_c != null ? `${Number(systemStats.cpu_temp_c).toFixed(2)}°C` : '—'}</div>
                        </div>
                        <div>
                          <div className="text-gray-500">Throttle</div>
                          <div className="text-white font-mono text-[10px]">{systemStats.throttle_status ?? '—'}</div>
                        </div>
                      </div>
                    </div>

                    {/* Network Status */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">Network</div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <div className="text-gray-500">API (8000)</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <div className="w-1.5 h-1.5 rounded-full bg-green-400" />
                            <span>Active</span>
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Auto (8001)</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <div className="w-1.5 h-1.5 rounded-full bg-green-400" />
                            <span>Active</span>
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">CAN Bus</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <div className="w-1.5 h-1.5 rounded-full bg-green-400" />
                            <span>250kbps</span>
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">WebSocket</div>
                          <div className="text-white font-mono flex items-center gap-1">
                            <div className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                            <span>Live</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="bg-gray-800 rounded p-2">
                    <div className="text-xs text-gray-400 mb-1">System Status</div>
                    <div className="text-xs text-gray-500">Loading system status...</div>
                  </div>
                )}
              </div>
            </div>

            {/* Bottom Half: Laboratory */}
            <div className="flex-1">
              <div className="text-[14px] text-gray-400 uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <span>🧪</span> Laboratory
                </span>
              </div>
              <div className="flex-1 overflow-y-auto">
                <Link
                  to={`/zone/${encodeURIComponent('Lab')}/${encodeURIComponent('main')}`}
                  className="block h-full"
                >
                  <div className="space-y-2">
                    {/* Current Conditions */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">Current Conditions</div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <div className="text-gray-500">Temperature</div>
                          <div className="text-white font-mono">
                            {sensorData['Lab_main_dry_bulb_f'] ? 
                              `${((sensorData['Lab_main_dry_bulb_f'] - 32) * 5/9).toFixed(2)}°C` : 
                              '--°C'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Humidity</div>
                          <div className="text-white font-mono">
                            {sensorData['Lab_main_relative_humidity'] ? 
                              `${Number(sensorData['Lab_main_relative_humidity']).toFixed(2)}%` : 
                              '--%'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">CO2</div>
                          <div className="text-white font-mono">
                            {sensorData['Lab_main_co2'] ? 
                              `${Number(sensorData['Lab_main_co2']).toFixed(2)} ppm` : 
                              '-- ppm'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">VPD</div>
                          <div className="text-white font-mono">
                            {sensorData['Lab_main_vpd'] ? 
                              `${Number(sensorData['Lab_main_vpd']).toFixed(2)} kPa` : 
                              '-- kPa'
                            }
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Water Parameters */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-1">Water Parameters</div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <div className="text-gray-500">Water Level</div>
                          <div className="text-cyan-400 font-mono">
                            {sensorData['Lab_main_water_level'] ? 
                              `${Number(sensorData['Lab_main_water_level']).toFixed(2)} cm` : 
                              '-- cm'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Water Temp</div>
                          <div className="text-cyan-400 font-mono">
                            {sensorData['Lab_main_water_temperature'] ? 
                              `${Number(sensorData['Lab_main_water_temperature']).toFixed(2)}°C` : 
                              '--°C'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">Water Pressure</div>
                          <div className="text-cyan-400 font-mono">
                            {sensorData['Lab_main_water_pressure'] ? 
                              `${Number(sensorData['Lab_main_water_pressure']).toFixed(2)} kPa` : 
                              '-- kPa'
                            }
                          </div>
                        </div>
                        <div>
                          <div className="text-gray-500">pH Level</div>
                          <div className="text-cyan-400 font-mono">
                            {sensorData['Lab_main_ph_level'] ? 
                              `${Number(sensorData['Lab_main_ph_level']).toFixed(2)}` : 
                              '--'
                            }
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Device Status */}
                    <div className="bg-gray-800 rounded p-2">
                      <div className="text-xs text-gray-400 mb-2">Device Status</div>
                      <div className="space-y-1">
                        {devices.filter(d => d.location === 'Lab' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => (
                          <div key={index} className="flex items-center justify-between text-xs">
                            <span className="text-gray-300 truncate flex-1">
                              {device.device_name}
                            </span>
                            <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${
                              device.state === 1 
                                ? 'bg-green-900 text-green-200' 
                                : 'bg-gray-700 text-gray-400'
                            }`}>
                              {device.state === 1 ? 'ON' : 'OFF'}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </Link>
              </div>
            </div>
          </div>
        </div>

        {/* Settings Section */}
        <div className="border-t border-gray-800 p-2">
          <div className="max-w-full mx-auto">
            <button 
              onClick={() => window.open('/device-config', '_blank')}
              className="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0 1.756 2.924 1.756 3.35 0zm1.75 6.9H17.025a1.75 1.75 0 0 0 0 3.5H12.025a1.75 1.75 0 0 0 0-3.5z" />
              </svg>
              <span className="text-sm font-medium">Device Config</span>
              <span className="text-xs text-gray-500">Hardware & Pin Assignment</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

