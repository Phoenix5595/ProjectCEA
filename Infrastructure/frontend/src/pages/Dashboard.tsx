import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ZONES } from '../config/zones'
import { apiClient } from '../services/api'
import { wsClient } from '../services/websocket'
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

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<'zones' | 'devices'>('zones')
  const [devices, setDevices] = useState<Device[]>([])
  const [schedules, setSchedules] = useState<Record<string, RoomSchedule>>({})
  const [setpoints, setSetpoints] = useState<Record<string, Setpoint>>({})
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

      // Load setpoints for each zone (get all modes, then determine current mode)
      const setpointPromises = ZONES.map(async (zone) => {
        try {
          // Get all setpoints for this zone (all modes)
          const allSetpoints = await apiClient.getAllSetpointsForLocationCluster(zone.location, zone.cluster)
          // For now, use the first available setpoint or default to null mode
          // TODO: Determine current mode based on schedule/time
          const currentSetpoint = allSetpoints.find(sp => sp.mode === null) || allSetpoints[0] || null
          return { zone, setpoint: currentSetpoint }
        } catch (error) {
          console.error(`Error loading setpoints for ${zone.location}/${zone.cluster}:`, error)
          return { zone, setpoint: null }
        }
      })

      const setpointResults = await Promise.all(setpointPromises)
      const setpointMap: Record<string, Setpoint> = {}
      setpointResults.forEach(({ zone, setpoint }) => {
        if (setpoint) {
          const key = `${zone.location}:${zone.cluster}`
          setpointMap[key] = setpoint
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
      <div className="min-h-screen bg-gray-100 p-8">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-3xl font-bold mb-8 text-gray-900">CEA Automation Dashboard</h1>
          <p className="text-gray-700">Loading...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold mb-8 text-gray-900">CEA Automation Dashboard</h1>
        
        <div className="bg-white rounded-lg shadow-md border border-gray-200">
          <div className="border-b border-gray-200">
            <nav className="flex">
              <button
                onClick={() => setActiveTab('zones')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'zones'
                    ? 'border-b-2 border-blue-600 text-blue-700 bg-blue-50'
                    : 'text-gray-700 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                Zones
              </button>
              <button
                onClick={() => setActiveTab('devices')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'devices'
                    ? 'border-b-2 border-blue-600 text-blue-700 bg-blue-50'
                    : 'text-gray-700 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                Devices
              </button>
            </nav>
          </div>

          <div>
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
                          setpoint={setpoints[zoneKey]}
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

