import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ZONES } from '../config/zones'
import { apiClient } from '../services/api'
import { wsClient } from '../services/websocket'
import { useTheme } from '../contexts/ThemeContext'
import type { Device } from '../types/device'
import ZoneCard from '../components/ZoneCard'
import DeviceManager from '../components/DeviceManager'

interface RoomSchedule {
  day_start_time: string
  day_end_time: string
  night_start_time: string
  night_end_time: string
  ramp_up_duration: number | null
  ramp_down_duration: number | null
}

import type { Setpoint } from '../types/setpoint'

interface ZoneSetpoints {
  day?: Setpoint
  night?: Setpoint
}

export default function Dashboard() {
  const { theme, toggleTheme } = useTheme()
  const [activeTab, setActiveTab] = useState<'zones' | 'devices'>('zones')
  const [devices, setDevices] = useState<Device[]>([])
  const [schedules, setSchedules] = useState<Record<string, RoomSchedule>>({})
  const [setpoints, setSetpoints] = useState<Record<string, ZoneSetpoints>>({})
  const [loading, setLoading] = useState(true)

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

    // Load initial data
    loadInitialData()

    return () => {
      unsubscribeDevice()
      wsClient.disconnect()
    }
  }, [])

  async function loadInitialData() {
    try {
      // Load devices
      const devicesData = await apiClient.getAllDevices()
      setDevices(devicesData)

      // Load schedules for each zone
      const schedulePromises = ZONES.map(async (zone) => {
        try {
          const schedule = await apiClient.getRoomSchedule(zone.location, zone.cluster)
          return { zone, schedule }
        } catch (error) {
          console.error(`Error loading schedule for ${zone.location}/${zone.cluster}:`, error)
          return { zone, schedule: null }
        }
      })

      const scheduleResults = await Promise.all(schedulePromises)
      const scheduleMap: Record<string, RoomSchedule> = {}
      scheduleResults.forEach(({ zone, schedule }) => {
        if (schedule) {
          const key = `${zone.location}:${zone.cluster}`
          scheduleMap[key] = schedule
        }
      })
      setSchedules(scheduleMap)

      // Load setpoints for each zone (get DAY and NIGHT setpoints)
      const setpointPromises = ZONES.map(async (zone) => {
        try {
          // Get all setpoints for this zone (all modes)
          const allSetpoints = await apiClient.getAllSetpointsForLocationCluster(zone.location, zone.cluster)
          // Extract DAY and NIGHT setpoints
          const daySetpoint = allSetpoints.find(sp => sp.mode === 'DAY')
          const nightSetpoint = allSetpoints.find(sp => sp.mode === 'NIGHT')
          return { zone, daySetpoint, nightSetpoint }
        } catch (error) {
          console.error(`Error loading setpoints for ${zone.location}/${zone.cluster}:`, error)
          return { zone, daySetpoint: undefined, nightSetpoint: undefined }
        }
      })

      const setpointResults = await Promise.all(setpointPromises)
      const setpointMap: Record<string, ZoneSetpoints> = {}
      setpointResults.forEach(({ zone, daySetpoint, nightSetpoint }) => {
        const key = `${zone.location}:${zone.cluster}`
        setpointMap[key] = {
          day: daySetpoint,
          night: nightSetpoint
        }
      })
      setSetpoints(setpointMap)
    } catch (error) {
      console.error('Error loading initial data:', error)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-100 dark:bg-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-3xl font-bold mb-8 text-gray-900 dark:text-gray-100">CEA Automation Dashboard</h1>
          <p className="text-gray-700 dark:text-gray-300">Loading...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-100 dark:bg-gray-950 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">CEA Automation Dashboard</h1>
          <button
            onClick={toggleTheme}
            className="p-2 rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
            aria-label="Toggle theme"
          >
            {theme === 'light' ? (
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            ) : (
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
              </svg>
            )}
          </button>
        </div>
        
        <div className="bg-white dark:bg-gray-900 rounded-lg shadow-md border border-gray-200 dark:border-gray-800">
          <div className="border-b border-gray-200 dark:border-gray-800">
            <nav className="flex">
              <button
                onClick={() => setActiveTab('zones')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'zones'
                    ? 'border-b-2 border-blue-600 dark:border-blue-500 text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20'
                    : 'text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                Zones
              </button>
              <button
                onClick={() => setActiveTab('devices')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'devices'
                    ? 'border-b-2 border-blue-600 dark:border-blue-500 text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20'
                    : 'text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                Devices
              </button>
            </nav>
          </div>

          <div className="dark:bg-black">
            {activeTab === 'zones' && (
              <div className="p-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {ZONES.map((zone) => {
                    const zoneKey = `${zone.location}:${zone.cluster}`
                    const zoneDevices = devices.filter(
                      d => d.location === zone.location && d.cluster === zone.cluster
                    )
                    return (
                      <Link
                        key={zoneKey}
                        to={`/zone/${encodeURIComponent(zone.location)}/${encodeURIComponent(zone.cluster)}`}
                        className="block"
                      >
                        <ZoneCard
                          zone={zone}
                          sensorData={{}}
                          devices={zoneDevices}
                          schedule={schedules[zoneKey]}
                          setpoints={setpoints[zoneKey]}
                        />
                      </Link>
                    )
                  })}
                </div>
              </div>
            )}
            {activeTab === 'devices' && (
              <DeviceManager />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

