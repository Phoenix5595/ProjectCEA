import { useParams, Link } from 'react-router-dom'
import { useState, useEffect } from 'react'
import PIDEditor from '../components/PIDEditor'
import RoomScheduleEditor from '../components/RoomScheduleEditor'
import LightManager from '../components/LightManager'
import ClimateScheduleEditor from '../components/ClimateScheduleEditor'
import { apiClient } from '../services/api'
import { getLocationDisplayName, getLocationBackendName } from '../config/zones'

export default function ZoneConfig() {
  const { location: locationParam, cluster } = useParams<{ location: string; cluster: string }>()
  // URL should have backend location name (from zones config), but convert display name if needed
  // React Router automatically decodes URL params, so we just need to map if it's a display name
  const location = locationParam ? getLocationBackendName(locationParam) : null
  const [activeTab, setActiveTab] = useState<'climate' | 'lights' | 'pid'>('climate')
  const [lights, setLights] = useState<any[]>([])

  useEffect(() => {
    if (location && cluster) {
      loadLights()
    }
  }, [location, cluster])

  async function loadLights() {
    if (!location || !cluster) {
      console.log('loadLights: Missing location or cluster', { location, cluster })
      return
    }
    try {
      // Ensure we use backend location name (not display name)
      const backendLocation = getLocationBackendName(location)
      console.log('loadLights: Using backend location', { location, backendLocation, cluster })
      const devices = await apiClient.getDevicesForLocationClusterWithDetails(backendLocation, cluster)
      console.log('loadLights: Devices received', { deviceCount: Object.keys(devices).length, devices })
      // Filter for lights with dimming enabled
      const allDevices = Object.entries(devices)
      console.log('loadLights: All devices', allDevices.map(([name, dev]) => ({ name, type: dev.device_type, dimming: dev.dimming_enabled })))
      const lightDevices = allDevices
        .filter(([_, device]: [string, any]) => {
          const isLight = device.device_type === 'light'
          const hasDimming = device.dimming_enabled === true
          console.log('loadLights: Filtering device', { device_type: device.device_type, dimming_enabled: device.dimming_enabled, isLight, hasDimming })
          return isLight && hasDimming
        })
        .map(([deviceName, device]: [string, any]) => ({
          device_name: deviceName,
          display_name: device.display_name,
          dimming_enabled: device.dimming_enabled,
          dimming_board_id: device.dimming_board_id,
          dimming_channel: device.dimming_channel
        }))
      console.log('loadLights: Filtered lights', lightDevices)
      setLights(lightDevices)
    } catch (error) {
      console.error('Error loading lights:', error)
    }
  }


  if (!location || !cluster) {
    return <div className="text-gray-900">Invalid zone</div>
  }

  return (
    <div className="min-h-screen bg-gray-100 p-8">
      <div className="max-w-7xl mx-auto">
        <Link to="/" className="text-blue-700 hover:text-blue-800 hover:underline mb-4 inline-block font-medium">
          ← Back to Dashboard
        </Link>
        
        <h1 className="text-3xl font-bold mb-8 text-gray-900">
          Configuration: {cluster === 'main' ? getLocationDisplayName(location) : `${getLocationDisplayName(location)} - ${cluster}`}
        </h1>

        <div className="bg-white rounded-lg shadow-md border border-gray-200">
          <div className="border-b border-gray-200">
            <nav className="flex">
              <button
                onClick={() => setActiveTab('climate')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'climate'
                    ? 'border-b-2 border-blue-600 text-blue-700 bg-blue-50'
                    : 'text-gray-700 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                Climate
              </button>
              <button
                onClick={() => setActiveTab('lights')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'lights'
                    ? 'border-b-2 border-blue-600 text-blue-700 bg-blue-50'
                    : 'text-gray-700 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                Lights
              </button>
              <button
                onClick={() => setActiveTab('pid')}
                className={`px-6 py-3 font-semibold ${
                  activeTab === 'pid'
                    ? 'border-b-2 border-blue-600 text-blue-700 bg-blue-50'
                    : 'text-gray-700 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                PID Parameters
              </button>
            </nav>
          </div>

          <div className="p-6">
            {activeTab === 'climate' && location && (
              <ClimateScheduleEditor location={location} cluster={cluster!} />
            )}
            {activeTab === 'lights' && location && (
              <div className="space-y-8">
                <section>
                  <h2 className="text-xl font-semibold mb-4 text-gray-900">Light Schedule</h2>
                  <RoomScheduleEditor location={location} cluster={cluster!} period="day" />
                </section>
                <section className="border-t border-gray-200 pt-6">
                  <h2 className="text-xl font-semibold mb-4 text-gray-900">Light Management</h2>
                  <LightManager location={location} cluster={cluster!} lights={lights} />
                </section>
              </div>
            )}
            {activeTab === 'pid' && (
              <PIDEditor />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

