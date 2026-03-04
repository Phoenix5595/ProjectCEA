import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { apiClient } from '../services/api'
import { wsClient } from '../services/websocket'
import { useTheme } from '../contexts/ThemeContext'
import { logger } from '../utils/logger'
import type { Device, ControlHistoryEntry } from '../types/device'

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

/** Parse live API response into flat keys ${location}_${cluster}_${sensorType} -> number. */
function parseLiveResponse(
  location: string,
  cluster: string,
  liveData: Record<string, { data?: Array<{ value?: number }> }>
): Record<string, number> {
  const flat: Record<string, number> = {}
  if (!liveData || typeof liveData !== 'object') return flat
  for (const [sensorType, resp] of Object.entries(liveData)) {
    const dp = Array.isArray(resp?.data) && resp.data.length > 0 ? resp.data[0] : null
    if (dp?.value != null) flat[`${location}_${cluster}_${sensorType}`] = Number(dp.value)
  }
  return flat
}


const MAX_REASON_LENGTH = 40

/** Format a single control history line for the dashboard log. */
function formatControlHistoryLine(entry: ControlHistoryEntry): string {
 const timeStr = (() => {
 try {
 const d = new Date(entry.timestamp)
 return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
 } catch {
 return '--:--'
 }
 })()
 const onOff = entry.new_state === 1 ? 'ON' : 'OFF'
 const reason = entry.reason?.trim() || ''
 const load = entry.load_percent != null ? Number(entry.load_percent) : null
 let suffix = ''
 if (entry.new_state === 1) {
 if (load != null && reason) suffix = ` (Load ${Math.round(load)}%, ${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`
 else if (load != null) suffix = ` (Load ${Math.round(load)}%)`
 else if (reason) suffix = ` (${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`
 } else {
 if (reason) suffix = ` (${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`
 }
 return `${timeStr} ${entry.device_name} ${onOff}${suffix}`
}

export default function Dashboard() {
  const { theme, setTheme, themes } = useTheme()

 const [devices, setDevices] = useState<Device[]>([])
 const [weatherData, setWeatherData] = useState<WeatherData | null>(null)
 const [systemStats, setSystemStats] = useState<SystemStats | null>(null)
 const [statusDevices, setStatusDevices] = useState<Record<string, Record<string, Record<string, { intensity?: number; load_percent?: number }>>> | null>(null)
 const [controlHistoryByRoom, setControlHistoryByRoom] = useState<Record<string, ControlHistoryEntry[]>>({})
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
 const sensorKey = (message as { sensor?: string; sensor_type?: string }).sensor ?? (message as { sensor_type?: string }).sensor_type
 if (!sensorKey) return
 setSensorData(prev => ({
 ...prev,
 [`${message.location}_${message.cluster}_${sensorKey}`]: message.value
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
    const [
      devicesData,
      setpointData,
      vegLive,
      flowerALive,
      flowerBLive,
      labLive,
      weatherResponse,
      statusResponse,
      vegHistory,
      flowerHistory,
      labHistory
    ] = await Promise.all([
      apiClient.getAllDevices().catch(() => []),
      apiClient.getSensorDataBulk([
        'Flower Room_main_heating_setpoint', 'Flower Room_main_cooling_setpoint',
        'Flower Room_main_co2_setpoint', 'Flower Room_main_vpd_setpoint',
        'Veg Room_main_heating_setpoint', 'Veg Room_main_cooling_setpoint',
        'Veg Room_main_co2_setpoint', 'Veg Room_main_vpd_setpoint',
        'Veg Room_main_light_1_intensity', 'Veg Room_main_light_2_intensity',
        'Veg Room_main_light_3_intensity', 'Flower Room_main_light_1_intensity',
        'Flower Room_main_light_2_intensity', 'Flower Room_main_light_3_intensity',
        'Lab_main_lab_temp', 'Lab_main_water_temperature'
      ]).catch(() => ({})),
      apiClient.getLiveSensorData('Veg Room', 'main').catch(() => ({})),
      apiClient.getLiveSensorData('Flower Room', 'clusterA').catch(() => ({})),
      apiClient.getLiveSensorData('Flower Room', 'clusterB').catch(() => ({})),
      apiClient.getLiveSensorData('Lab', 'main').catch(() => ({})),
      apiClient.getLatestWeather().catch(() => null),
      apiClient.getSystemStatus().catch(() => null),
      apiClient.getControlHistory('Veg Room', 'main', 10).catch(() => []),
      apiClient.getControlHistory('Flower Room', 'main', 10).catch(() => []),
      apiClient.getControlHistory('Lab', 'main', 10).catch(() => [])
    ])

    if (devicesData) setDevices(devicesData)
    if (setpointData && Object.keys(setpointData).length > 0) {
      setSensorData(prev => ({ ...prev, ...setpointData }))
    }
    
    // Process all live data
    const allLiveFlat = {
      ...parseLiveResponse('Veg Room', 'main', vegLive as any),
      ...parseLiveResponse('Flower Room', 'clusterA', flowerALive as any),
      ...parseLiveResponse('Flower Room', 'clusterB', flowerBLive as any),
      ...parseLiveResponse('Lab', 'main', labLive as any)
    }
    if (Object.keys(allLiveFlat).length > 0) setSensorData(prev => ({ ...prev, ...allLiveFlat }))

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

    setControlHistoryByRoom({
      'Veg Room_main': vegHistory ?? [],
      'Flower Room_main': flowerHistory ?? [],
      'Lab_main': labHistory ?? []
    })

    if (statusResponse) {
      if (statusResponse.devices) setStatusDevices(statusResponse.devices)
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
              { name: 'onewire-worker', status: 'running' as const },
              { name: 'cea-backend', status: 'running' as const },
              { name: 'automation-service', status: 'running' as const }
            ]
      })
    }
  } catch (error) {
    console.error('Error loading initial data:', error)
    logger.error('Error loading initial data:', error)
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
 }
 }
 const interval = setInterval(refreshWeather, 15 * 60 * 1000)
 return () => clearInterval(interval)
 }, [])

  // Refresh system stats every 5 seconds (fast, no health check)
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
  setSystemStats(prev => ({
    ...prev,
    cpu_usage: sys?.cpu_percent ?? prev.cpu_usage,
    memory_usage: sys?.memory_percent ?? prev.memory_usage,
    disk_usage: sys?.disk_percent ?? prev.disk_usage,
    uptime: sys?.uptime_seconds != null ? formatUptime(sys.uptime_seconds) : prev.uptime,
    load_avg: sys?.load_avg ?? prev.load_avg,
    process_count: sys?.process_count ?? prev.process_count,
    cpu_temp_c: sys?.cpu_temp_c ?? prev.cpu_temp_c,
    throttle_status: sys?.throttle_status ?? prev.throttle_status,
  }))
  }
  } catch (error) {
  }
  }, 5000)

  return () => clearInterval(interval)
  }, [])

  // Refresh service health every 60 seconds (slower health checks, polled less frequently)
  useEffect(() => {
  const refreshHealth = async () => {
  try {
  const healthData = await apiClient.getSystemHealth()
  setSystemStats(prev => ({
    ...prev,
    services: Array.isArray(healthData)
      ? healthData.map((s: { name: string; status: string; latency_ms?: number }) => ({
        name: s.name,
        status: s.status as 'running' | 'stopped' | 'error' | 'unreachable',
        latency_ms: s.latency_ms
      }))
      : prev.services
  }))
  } catch (error) {
  }
  }
  refreshHealth()
  const interval = setInterval(refreshHealth, 60000)

  return () => clearInterval(interval)
  }, [])

  // Refresh all live sensors every 5 seconds
  useEffect(() => {
    const refreshLiveSensors = async () => {
      try {
        const [vegLive, flowerALive, flowerBLive, labLive] = await Promise.all([
          apiClient.getLiveSensorData('Veg Room', 'main').catch(() => ({})),
          apiClient.getLiveSensorData('Flower Room', 'clusterA').catch(() => ({})),
          apiClient.getLiveSensorData('Flower Room', 'clusterB').catch(() => ({})),
          apiClient.getLiveSensorData('Lab', 'main').catch(() => ({}))
        ])
        const allLiveFlat = {
          ...parseLiveResponse('Veg Room', 'main', vegLive as any),
          ...parseLiveResponse('Flower Room', 'clusterA', flowerALive as any),
          ...parseLiveResponse('Flower Room', 'clusterB', flowerBLive as any),
          ...parseLiveResponse('Lab', 'main', labLive as any)
        }
        if (Object.keys(allLiveFlat).length > 0) setSensorData(prev => ({ ...prev, ...allLiveFlat }))
      } catch (err) {
        logger.warn('Live sensor refresh failed', err)
      }
    }
    refreshLiveSensors()
    const interval = setInterval(refreshLiveSensors, 5000)
    return () => clearInterval(interval)
  }, [])


 // Refresh control history every 30s
 useEffect(() => {
 const rooms: [string, string][] = [['Veg Room', 'main'], ['Flower Room', 'main'], ['Lab', 'main']]
 const refresh = async () => {
 const historyByRoom: Record<string, ControlHistoryEntry[]> = {}
 await Promise.all(rooms.map(async ([loc, cluster]) => {
 try {
 const list = await apiClient.getControlHistory(loc, cluster, 10)
 historyByRoom[`${loc}_${cluster}`] = list ?? []
 } catch {
 historyByRoom[`${loc}_${cluster}`] = []
 }
 }))
 setControlHistoryByRoom(prev => ({ ...prev, ...historyByRoom }))
 }
 const interval = setInterval(refresh, 30000)
 return () => clearInterval(interval)
 }, [])

 const getRoomLightState = (location: string) => {
 let roomLights: any[] = []
 if (devices && typeof devices === 'object' && !Array.isArray(devices)) {
 Object.keys(devices).forEach(roomName => {
 const roomData = (devices as any)[roomName]
 if (roomData && typeof roomData === 'object' && roomData.main) {
 Object.keys(roomData.main).forEach(deviceName => {
 const device = roomData.main[deviceName]
 if (device && deviceName.startsWith('light_')) {
 roomLights.push({ ...device, device_name: deviceName, location: roomName, cluster: 'main' })
 }
 })
 }
 })
 } else if (Array.isArray(devices)) {
 roomLights = devices.filter(d => d.location === location && d.cluster === 'main' && d.device_name?.startsWith('light_'))
 }
 const locationLights = roomLights.filter(light => light.location === location)
 const hasLightsOn = locationLights.some(light => light.state === 1)
 return hasLightsOn ? '☀️' : '🌙'
 }

 const getSetpointColor = () => 'text-accent-setpoint'

 if (loading) {
 return (
 <div className="main-dashboard min-h-screen bg-surface-base p-4">
 <div className="max-w-full mx-auto">
 <h1 className="text-3xl font-bold mb-8 text-text-default">Siberian Jungle</h1>
 <p className="text-text-secondary">Loading...</p>
 </div>
 </div>
 )
 }

 return (
 <div className="main-dashboard min-h-screen bg-surface-base p-2">
 <div className="max-w-full mx-auto h-[calc(100vh-1rem)] flex flex-col">
 
 <div className="sticky top-0 z-10 bg-surface-base p-1 mb-2">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-xl font-bold text-text-default flex items-center gap-2">
 <img src="/favicon.ico" alt="" className="w-6 h-6 rounded-sm" aria-hidden />
 Siberian Jungle
 </h1>
 </div>
 <div className="flex items-center gap-4">
 {weatherData && (
 <div className="flex items-center gap-3 text-sm text-text-secondary" title={weatherData.timestamp ? `Quebec City weather · ${new Date(weatherData.timestamp).toLocaleString()}` : 'Quebec City weather'}>
 <span className="text-text-muted font-medium">Quebec City</span>
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
 <span className="text-text-muted">{weatherData.description}</span>
 )}
 </div>
 )}
 <button
 onClick={() => {
 const currentIndex = themes.indexOf(theme)
 const nextIndex = (currentIndex + 1) % themes.length
 setTheme(themes[nextIndex])
 }}
 className="p-2 rounded-lg bg-surface-secondary text-text-secondary hover:bg-surface-tertiary transition-colors"
 aria-label="Toggle theme"
 >
 <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
 </svg>
 </button>
 </div>
 </div>
 </div>

 <div className="flex-1 flex flex-col lg:flex-row gap-2 min-h-0">
 
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col lg:w-[37%]">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
 <span className="flex items-center gap-2">
 <span>🌱</span> Vegetation Room
 </span>
 <div className="flex items-center gap-1">
 <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 cursor-help" title={`Status: ${getRoomLightState('Veg Room') === '☀️' ? 'Day' : 'Night'}`}>
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
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Current Conditions</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div>
 <div className="text-text-subtle">Temperature</div>
 <div className="text-text-default font-mono tabular-nums">
 {sensorData['Veg Room_main_dry_bulb_f'] ? `${((sensorData['Veg Room_main_dry_bulb_f'] - 32) * 5/9).toFixed(2)}°C` : '--°C'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">Humidity</div>
 <div className="text-text-default font-mono tabular-nums">
 {sensorData['Veg Room_main_relative_humidity'] ? `${Number(sensorData['Veg Room_main_relative_humidity']).toFixed(2)}%` : '--%'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">CO2</div>
 <div className="text-text-default font-mono tabular-nums">
 {sensorData['Veg Room_main_co2'] ? `${Number(sensorData['Veg Room_main_co2']).toFixed(2)} ppm` : '-- ppm'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">VPD</div>
 <div className="text-text-default font-mono tabular-nums">
 {sensorData['Veg Room_main_vpd'] ? `${Number(sensorData['Veg Room_main_vpd']).toFixed(2)} kPa` : '-- kPa'}
 </div>
 </div>
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1" title="From Redis (effective_setpoint:*): heating, cooling, CO2, VPD">Effective Setpoints</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div>
 <div className="text-text-subtle">Heating</div>
 <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
 {sensorData['Veg Room_main_heating_setpoint'] != null ? `${Number(sensorData['Veg Room_main_heating_setpoint']).toFixed(2)}°C` : '--°C'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">Cooling</div>
 <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
 {sensorData['Veg Room_main_cooling_setpoint'] != null ? `${Number(sensorData['Veg Room_main_cooling_setpoint']).toFixed(2)}°C` : '--°C'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">CO2</div>
 <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
 {sensorData['Veg Room_main_co2_setpoint'] != null ? `${Number(sensorData['Veg Room_main_co2_setpoint']).toFixed(2)} ppm` : '-- ppm'}
 </div>
 </div>
 <div>
 <div className="text-text-subtle">VPD</div>
 <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
 {sensorData['Veg Room_main_vpd_setpoint'] != null ? `${Number(sensorData['Veg Room_main_vpd_setpoint']).toFixed(2)} kPa` : '-- kPa'}
 </div>
 </div>
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Light Status</div>
 <div className="space-y-1">
 {devices.filter(d => d.location === 'Veg Room' && d.cluster === 'main' && d.device_name?.startsWith('light_')).map((device, index) => {
 const displayNames: Record<string, string> = { 'light_1': 'Eyefinity Top', 'light_2': 'Ridgetop Bottom Right', 'light_3': 'Ridgetop Bottom Left' }
 const displayName = displayNames[device.device_name] || device.device_name
 const nameParts = displayName.split(' ')
 const firstName = nameParts[0]
 const restName = nameParts.slice(1).join(' ')
 return (
 <div key={index} className="flex items-center justify-between text-xs">
 <span className="text-text-secondary flex-1 min-w-0 leading-tight">
 <span className="block">{firstName}</span>
 {restName && <span className="block text-[10px] text-text-muted">{restName}</span>}
 </span>
 <div className="flex items-center gap-1 shrink-0">
 <span className="text-accent-data font-mono tabular-nums">
 {(sensorData[`Veg Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Veg Room']?.['main']?.[device.device_name]?.intensity) != null ? `${Number(sensorData[`Veg Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Veg Room']?.['main']?.[device.device_name]?.intensity).toFixed(2)}%` : '--%'}
 </span>
 <span className={`text-[14px] px-1.5 py-0.5 rounded cursor-help ${device.state === 1 ? 'bg-btn-primary-dim/50 text-btn-primary-data border border-btn-primary-active/50' : 'bg-surface-tertiary text-text-subtle border border-border-emphasis'}`} title={`${displayName}: ${device.state === 1 ? 'Sun' : 'Moon'}`}>{device.state === 1 ? '☀️' : '🌙'}</span>
 </div>
 </div>
 )
 })}
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-2">Device Status</div>
 <div className="space-y-1">
 {devices.filter(d => d.location === 'Veg Room' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => {
 const loadPct = statusDevices?.['Veg Room']?.['main']?.[device.device_name]?.load_percent
 return (
 <div key={index} className="flex items-center justify-between text-xs">
 <span className="text-text-secondary truncate flex-1">{device.device_name}</span>
 <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${device.state === 1 ? 'bg-status-success-bg text-status-success-text' : 'bg-surface-tertiary text-text-muted'}`}>{device.state === 1 ? 'ON' : 'OFF'}{loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}</span>
 </div>
 )
 })}
 </div>
 </div>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Recent on/off</div>
 {controlHistoryByRoom['Veg Room_main']?.length ? (
 <div className="space-y-0.5 text-[10px] text-text-secondary font-mono tabular-nums">
 {controlHistoryByRoom['Veg Room_main'].slice(0, 10).map((entry, i) => <div key={i} title={entry.reason ?? undefined}>{formatControlHistoryLine(entry)}</div>)}
 </div>
 ) : <div className="text-[10px] text-text-subtle">No recent changes</div>}
 </div>
 </div>
 </Link>
 </div>
 </div>

 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col lg:w-[37%]">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
 <span className="flex items-center gap-2"><span>🌻</span> Flower Room</span>
 <div className="flex items-center gap-1">
 <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 cursor-help" title={`Status: ${getRoomLightState('Flower Room') === '☀️' ? 'Day' : 'Night'}`}>{getRoomLightState('Flower Room')}</span>
 </div>
 </div>
 <div className="flex-1 overflow-y-auto">
 <Link to={`/zone/${encodeURIComponent('Flower Room')}/${encodeURIComponent('main')}`} className="block h-full">
 <div className="space-y-2">
 <div className="flex gap-2 items-stretch">
 <div className="flex-1 bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Front</div>
 <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
 <div><div className="text-text-subtle">Temperature</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterA_temperature_sensor'] ? Number(sensorData['Flower Room_clusterA_temperature_sensor']).toFixed(2) + '°C' : '--°C'}</div></div>
 <div><div className="text-text-subtle">Humidity</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterA_humidity_sensor'] ? Number(sensorData['Flower Room_clusterA_humidity_sensor']).toFixed(2) + '%' : '--%'}</div></div>
 <div><div className="text-text-subtle">CO2</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterA_co2_sensor'] ? Number(sensorData['Flower Room_clusterA_co2_sensor']).toFixed(2) + ' ppm' : '-- ppm'}</div></div>
 <div><div className="text-text-subtle">VPD</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterA_vpd_sensor'] ? Number(sensorData['Flower Room_clusterA_vpd_sensor']).toFixed(2) + ' kPa' : '-- kPa'}</div></div>
 </div>
 </div>
 <div className="flex-1 bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Back</div>
 <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
 <div><div className="text-text-subtle">Temperature</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterB_temperature_sensor'] ? Number(sensorData['Flower Room_clusterB_temperature_sensor']).toFixed(2) + '°C' : '--°C'}</div></div>
 <div><div className="text-text-subtle">Humidity</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterB_humidity_sensor'] ? Number(sensorData['Flower Room_clusterB_humidity_sensor']).toFixed(2) + '%' : '--%'}</div></div>
 <div><div className="text-text-subtle">CO2</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterB_co2_sensor'] ? Number(sensorData['Flower Room_clusterB_co2_sensor']).toFixed(2) + ' ppm' : '-- ppm'}</div></div>
 <div><div className="text-text-subtle">VPD</div><div className="text-text-default font-mono tabular-nums">{sensorData['Flower Room_clusterB_vpd_sensor'] ? Number(sensorData['Flower Room_clusterB_vpd_sensor']).toFixed(2) + ' kPa' : '-- kPa'}</div></div>
 </div>
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1" title="From Redis (effective_setpoint:*): heating, cooling, CO2, VPD">Effective Setpoints</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div><div className="text-text-subtle">Heating</div><div className={`font-mono tabular-nums ${getSetpointColor()}`}>{sensorData['Flower Room_main_heating_setpoint'] != null ? Number(sensorData['Flower Room_main_heating_setpoint']).toFixed(2) + '°C' : '--°C'}</div></div>
 <div><div className="text-text-subtle">Cooling</div><div className={`font-mono tabular-nums ${getSetpointColor()}`}>{sensorData['Flower Room_main_cooling_setpoint'] != null ? Number(sensorData['Flower Room_main_cooling_setpoint']).toFixed(2) + '°C' : '--°C'}</div></div>
 <div><div className="text-text-subtle">CO2</div><div className={`font-mono tabular-nums ${getSetpointColor()}`}>{sensorData['Flower Room_main_co2_setpoint'] != null ? Number(sensorData['Flower Room_main_co2_setpoint']).toFixed(2) + ' ppm' : '-- ppm'}</div></div>
 <div><div className="text-text-subtle">VPD</div><div className={`font-mono tabular-nums ${getSetpointColor()}`}>{sensorData['Flower Room_main_vpd_setpoint'] != null ? Number(sensorData['Flower Room_main_vpd_setpoint']).toFixed(2) + ' kPa' : '-- kPa'}</div></div>
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Light Status</div>
 <div className="space-y-1">
  {devices.filter(d => d.location === 'Flower Room' && d.cluster === 'main' && d.device_name?.startsWith('light_')).map((device, index) => {
  const displayNames: Record<string, string> = { 'light_1': 'Chilled Front', 'light_2': 'Apache', 'light_3': 'Chilled Back' }
  const displayName = displayNames[device.device_name] || device.device_name
  const nameParts = displayName.split(' ')
  const firstName = nameParts[0]
  const restName = nameParts.slice(1).join(' ')
  return (
  <div key={index} className="flex items-center justify-between text-xs">
  <span className="text-text-secondary flex-1 min-w-0 leading-tight">
  <span className="block">{firstName}</span>
  {restName && <span className="block text-[10px] text-text-muted">{restName}</span>}
  </span>
  <div className="flex items-center gap-1 shrink-0">
  <span className="text-accent-data font-mono tabular-nums">{(sensorData[`Flower Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Flower Room']?.['main']?.[device.device_name]?.intensity) != null ? Number(sensorData[`Flower Room_main_${device.device_name}_intensity`] ?? statusDevices?.['Flower Room']?.['main']?.[device.device_name]?.intensity).toFixed(2) + '%' : '--%'}</span>
  <span className={`text-[14px] px-1.5 py-0.5 rounded cursor-help ${device.state === 1 ? 'bg-btn-primary-dim/50 text-btn-primary-data border border-btn-primary-active/50' : 'bg-surface-tertiary text-text-subtle border border-border-emphasis'}`} title={`${displayName}: ${device.state === 1 ? 'Sun' : 'Moon'}`}>{device.state === 1 ? '☀️' : '🌙'}</span>
  </div>
  </div>
  )
  })}
 </div>
 </div>

 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-2">Device Status</div>
 <div className="space-y-1">
 {devices.filter(d => d.location === 'Flower Room' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => {
 const loadPct = statusDevices?.['Flower Room']?.['main']?.[device.device_name]?.load_percent
 return (
 <div key={index} className="flex items-center justify-between text-xs">
 <span className="text-text-secondary truncate flex-1">{device.device_name}</span>
 <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${device.state === 1 ? 'bg-status-success-bg text-status-success-text' : 'bg-surface-tertiary text-text-muted'}`}>{device.state === 1 ? 'ON' : 'OFF'}{loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}</span>
 </div>
 )
 })}
 </div>
 </div>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Recent on/off</div>
 {controlHistoryByRoom['Flower Room_main']?.length ? (
 <div className="space-y-0.5 text-[10px] text-text-secondary font-mono tabular-nums">
 {controlHistoryByRoom['Flower Room_main'].slice(0, 10).map((entry, i) => <div key={i} title={entry.reason ?? undefined}>{formatControlHistoryLine(entry)}</div>)}
 </div>
 ) : <div className="text-[10px] text-text-subtle">No recent changes</div>}
 </div>
 </div>
 </Link>
 </div>
 </div>

 <div className="bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col lg:w-[26%]">
 <div className="flex-1 border-b border-border-subtle pb-2 mb-2 overflow-y-auto">
 <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
 <span className="flex items-center gap-2"><span>🖥</span> Mothernode Status</span>
 <div className="flex items-center gap-1"><span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 cursor-help" title="All Systems Operational">✓</span></div>
 </div>
 <div className="space-y-2">
 {systemStats ? (
 <>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">System Resources</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div><div className="text-text-subtle">CPU</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><span>{systemStats.cpu_usage != null ? `${Number(systemStats.cpu_usage).toFixed(2)}%` : '—'}</span>{systemStats.cpu_usage != null && <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden"><div className="h-full bg-status-success transition-all" style={{ width: `${Math.min(systemStats.cpu_usage, 100)}%` }} /></div>}</div></div>
 <div><div className="text-text-subtle">Memory</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><span>{systemStats.memory_usage != null ? `${Number(systemStats.memory_usage).toFixed(2)}%` : '—'}</span>{systemStats.memory_usage != null && <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden"><div className="h-full bg-btn-primary-data transition-all" style={{ width: `${Math.min(systemStats.memory_usage, 100)}%` }} /></div>}</div></div>
 <div><div className="text-text-subtle">Disk</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><span>{systemStats.disk_usage != null ? `${Number(systemStats.disk_usage).toFixed(2)}%` : '—'}</span>{systemStats.disk_usage != null && <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden"><div className="h-full bg-accent-setpoint transition-all" style={{ width: `${Math.min(systemStats.disk_usage, 100)}%` }} /></div>}</div></div>
 <div><div className="text-text-subtle">Load Avg</div><div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.load_avg ?? '—'}</div></div>
 <div><div className="text-text-subtle">Processes</div><div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.process_count ?? '—'}</div></div>
 <div><div className="text-text-subtle">Uptime</div><div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.uptime ?? '—'}</div></div>
 </div>
 </div>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Service Health</div>
 <div className="grid grid-cols-1 gap-1">
 {systemStats.services.length === 0 ? <div className="text-xs text-text-subtle">Status unknown</div> : systemStats.services.map((service, index) => (
 <div key={index} className="flex items-center justify-between text-xs">
 <div className="flex items-center gap-2 flex-1"><div className={`w-1.5 h-1.5 rounded-full ${service.status === 'running' ? 'bg-status-success' : service.status === 'unreachable' ? 'bg-status-danger' : 'bg-status-warning'}`} /><span className="text-text-secondary">{service.name}</span></div>
 <div className="flex items-center gap-1">{service.latency_ms != null && <span className="text-text-subtle text-[8px]">{service.latency_ms}ms</span>}<span className={`px-1 py-0.5 rounded text-[8px] font-medium ${service.status === 'running' ? 'bg-status-success-bg text-status-success-text' : service.status === 'unreachable' || service.status === 'stopped' ? 'bg-status-danger-bg text-status-danger-text' : 'bg-status-warning-bg text-status-warning-text'}`}>{service.status === 'running' ? '✓' : '✗'}</span></div>
 </div>
 ))}
 </div>
 </div>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Hardware Info</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div><div className="text-text-subtle">Model</div><div className="text-text-default font-mono tabular-nums text-[10px]">Raspberry Pi 5</div></div>
 <div><div className="text-text-subtle">CPU Cores</div><div className="text-text-default font-mono tabular-nums text-[10px]">4x 2.4GHz</div></div>
 <div><div className="text-text-subtle">RAM Total</div><div className="text-text-default font-mono tabular-nums text-[10px]">8GB</div></div>
 <div><div className="text-text-subtle">Storage</div><div className="text-text-default font-mono tabular-nums text-[10px]">256GB SSD</div></div>
 <div><div className="text-text-subtle">Temp</div><div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.cpu_temp_c != null ? `${Number(systemStats.cpu_temp_c).toFixed(2)}°C` : '—'}</div></div>
 <div><div className="text-text-subtle">Throttle</div><div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.throttle_status ?? '—'}</div></div>
 </div>
 </div>
 <div className="bg-surface-secondary rounded-sm p-2">
 <div className="text-xs text-text-muted mb-1">Network</div>
 <div className="grid grid-cols-2 gap-2 text-xs">
 <div><div className="text-text-subtle">API (8000)</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><div className="w-1.5 h-1.5 rounded-full bg-status-success" /><span>Active</span></div></div>
 <div><div className="text-text-subtle">Auto (8001)</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><div className="w-1.5 h-1.5 rounded-full bg-status-success" /><span>Active</span></div></div>
 <div><div className="text-text-subtle">CAN Bus</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><div className="w-1.5 h-1.5 rounded-full bg-status-success" /><span>250kbps</span></div></div>
 <div><div className="text-text-subtle">WebSocket</div><div className="text-text-default font-mono tabular-nums flex items-center gap-1"><div className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse" /><span>Live</span></div></div>
 </div>
 </div>
 </>
  ) : <div className="bg-surface-secondary rounded-sm p-2"><div className="text-xs text-text-muted mb-1">System Status</div><div className="text-xs text-text-subtle">Loading system status...</div></div>}
  </div>
  </div>
  
  <div className="flex-1">

  <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
  <span className="flex items-center gap-2"><span>🧪</span> Laboratory</span>
  </div>
  <div className="flex-1 overflow-y-auto">
  <Link to={`/zone/${encodeURIComponent('Lab')}/${encodeURIComponent('main')}`} className="block h-full">
  <div className="space-y-2">
  <div className="bg-surface-secondary rounded-sm p-2">
  <div className="text-xs text-text-muted mb-1">Current Conditions</div>
  <div className="grid grid-cols-2 gap-2 text-xs">
  <div><div className="text-text-subtle">Lab temp</div><div className="text-text-default font-mono tabular-nums">{sensorData['Lab_main_lab_temp'] != null ? `${Number(sensorData['Lab_main_lab_temp']).toFixed(2)}°C` : '--°C'}</div></div>
  <div><div className="text-text-subtle">Humidity</div><div className="text-text-default font-mono tabular-nums">{sensorData['Lab_main_relative_humidity'] ? `${Number(sensorData['Lab_main_relative_humidity']).toFixed(2)}%` : '--%'}</div></div>
  <div><div className="text-text-subtle">CO2</div><div className="text-text-default font-mono tabular-nums">{sensorData['Lab_main_co2'] ? `${Number(sensorData['Lab_main_co2']).toFixed(2)} ppm` : '-- ppm'}</div></div>
  <div><div className="text-text-subtle">VPD</div><div className="text-text-default font-mono tabular-nums">{sensorData['Lab_main_vpd'] ? `${Number(sensorData['Lab_main_vpd']).toFixed(2)} kPa` : '-- kPa'}</div></div>
  </div>
  </div>
  <div className="bg-surface-secondary rounded-sm p-2">
  <div className="text-xs text-text-muted mb-1">Water Parameters</div>
  <div className="grid grid-cols-2 gap-2 text-xs">
  <div><div className="text-text-subtle">Water Level</div><div className="text-accent-data font-mono tabular-nums">{sensorData['Lab_main_water_level'] ? `${Number(sensorData['Lab_main_water_level']).toFixed(2)} cm` : '-- cm'}</div></div>
  <div><div className="text-text-subtle">Water Temp</div><div className="text-accent-data font-mono tabular-nums">{sensorData['Lab_main_water_temperature'] ? `${Number(sensorData['Lab_main_water_temperature']).toFixed(2)}°C` : '--°C'}</div></div>
  <div><div className="text-text-subtle">Water Pressure</div><div className="text-accent-data font-mono tabular-nums">{sensorData['Lab_main_water_pressure'] ? `${Number(sensorData['Lab_main_water_pressure']).toFixed(2)} kPa` : '-- kPa'}</div></div>
  <div><div className="text-text-subtle">pH Level</div><div className="text-accent-data font-mono tabular-nums">{sensorData['Lab_main_ph_level'] ? `${Number(sensorData['Lab_main_ph_level']).toFixed(2)}` : '--'}</div></div>
  </div>
  </div>
  <div className="bg-surface-secondary rounded-sm p-2">
  <div className="text-xs text-text-muted mb-2">Device Status</div>
  <div className="space-y-1">
  {devices.filter(d => d.location === 'Lab' && d.cluster === 'main' && !d.device_name?.startsWith('light_')).map((device, index) => {
  const loadPct = statusDevices?.['Lab']?.['main']?.[device.device_name]?.load_percent
  return (
  <div key={index} className="flex items-center justify-between text-xs">
  <span className="text-text-secondary truncate flex-1">{device.device_name}</span>
  <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${device.state === 1 ? 'bg-status-success-bg text-status-success-text' : 'bg-surface-tertiary text-text-muted'}`}>{device.state === 1 ? 'ON' : 'OFF'}{loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}</span>
  </div>
  )
  })}
  </div>
  </div>
  <div className="bg-surface-secondary rounded-sm p-2">
  <div className="text-xs text-text-muted mb-1">Recent on/off</div>
  {controlHistoryByRoom['Lab_main']?.length ? (
  <div className="space-y-0.5 text-[10px] text-text-secondary font-mono tabular-nums">
  {controlHistoryByRoom['Lab_main'].slice(0, 10).map((entry, i) => <div key={i} title={entry.reason ?? undefined}>{formatControlHistoryLine(entry)}</div>)}
  </div>
  ) : <div className="text-[10px] text-text-subtle">No recent changes</div>}
  </div>
       </div>
     </Link>
     </div>
     </div>
     </div>
     </div>
     </div>
     </div>
   )
 }


