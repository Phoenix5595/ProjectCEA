import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { ZONES } from '../config/zones'

interface ChannelInfo {
  channel: number
  device_name: string | null
  display_name: string | null
  device_type: string | null
  location: string | null
  cluster: string | null
  light_name: string | null
}

interface LightName {
  name: string
  device_name: string
  location: string
  cluster: string
}

const DEVICE_TYPES = [
  'heater',
  'dehumidifier',
  'extraction fan',
  'fan',
  'humidifier',
  'co2 tank',
  'light'
]

export default function DeviceManager() {
  const [channels, setChannels] = useState<Record<string, ChannelInfo>>({})
  const [lightNames, setLightNames] = useState<LightName[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<{
    device_name: string
    device_type: string
    location: string
    cluster: string
    light_name: string
  }>({
    device_name: '',
    device_type: '',
    location: '',
    cluster: '',
    light_name: ''
  })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadChannels()
  }, [])

  async function loadChannels() {
    setLoading(true)
    try {
      const response = await apiClient.getChannels()
      setChannels(response.channels)
      setLightNames(response.light_names)
    } catch (error) {
      console.error('Error loading channels:', error)
    } finally {
      setLoading(false)
    }
  }

  function startEdit(channel: number) {
    const channelInfo = channels[channel.toString()]
    const defaultLocation = channelInfo?.location || ZONES[0].location
    const defaultCluster = channelInfo?.cluster || ZONES.find(z => z.location === defaultLocation)?.cluster || ZONES[0].cluster
    
    setEditing(channel)
    setEditForm({
      device_name: channelInfo?.device_name || '',
      device_type: channelInfo?.device_type || '',
      location: defaultLocation,
      cluster: defaultCluster,
      light_name: channelInfo?.light_name || ''
    })
  }

  function cancelEdit() {
    setEditing(null)
    setEditForm({
      device_name: '',
      device_type: '',
      location: '',
      cluster: '',
      light_name: ''
    })
  }

  async function saveEdit() {
    if (editing === null) return
    
    if (!editForm.device_name.trim()) {
      alert('Device name is required')
      return
    }
    
    if (!editForm.device_type) {
      alert('Device type is required')
      return
    }
    
    if (editForm.device_type === 'light' && !editForm.light_name) {
      alert('Light name is required for lights')
      return
    }
    
    setSaving(true)
    try {
      await apiClient.updateChannelDevice(
        editing,
        editForm.device_name,
        editForm.device_type,
        editForm.location,
        editForm.cluster,
        editForm.device_type === 'light' ? editForm.light_name : undefined
      )
      
      await loadChannels()
      cancelEdit()
    } catch (error) {
      console.error('Error updating channel device:', error)
      alert('Failed to update device configuration')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-gray-700 dark:text-gray-300">Loading channels...</p>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-2">Device Management</h2>
        <p className="text-gray-600 dark:text-gray-400">Manage MCP board pins (channels 0-15). Name devices and assign types.</p>
      </div>

      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-md border border-gray-200 dark:border-gray-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Channel
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Device Name
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Device Type
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Light Name
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Location/Cluster
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-800">
              {Array.from({ length: 16 }, (_, i) => i).map((channel) => {
                const channelInfo = channels[channel.toString()]
                const isEditing = editing === channel
                const isEmpty = !channelInfo?.device_name
                
                return (
                  <tr key={channel} className={isEmpty ? "bg-gray-50 dark:bg-gray-800/50" : "hover:bg-gray-50 dark:hover:bg-gray-800"}>
                    <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-gray-100">
                      {channel}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">
                      {isEditing ? (
                        <input
                          type="text"
                          value={editForm.device_name}
                          onChange={(e) => setEditForm({ ...editForm, device_name: e.target.value })}
                          className="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                          placeholder="Device name"
                        />
                      ) : (
                        channelInfo?.device_name || <span className="text-gray-400 dark:text-gray-500 italic">Empty</span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">
                      {isEditing ? (
                        <select
                          value={editForm.device_type}
                          onChange={(e) => setEditForm({ ...editForm, device_type: e.target.value, light_name: '' })}
                          className="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                        >
                          <option value="">Select type</option>
                          {DEVICE_TYPES.map((type) => (
                            <option key={type} value={type}>
                              {type.charAt(0).toUpperCase() + type.slice(1)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        channelInfo?.device_type ? (
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300">
                            {channelInfo.device_type}
                          </span>
                        ) : (
                          <span className="text-gray-400 dark:text-gray-500">-</span>
                        )
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">
                      {isEditing && editForm.device_type === 'light' ? (
                        <select
                          value={editForm.light_name}
                          onChange={(e) => setEditForm({ ...editForm, light_name: e.target.value })}
                          className="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                        >
                          <option value="">Select light</option>
                          {Array.from(new Map(lightNames.map(light => [light.name, light])).values()).map((light) => (
                            <option key={`${light.location}-${light.cluster}-${light.device_name}`} value={light.name}>
                              {light.name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        channelInfo?.light_name || <span className="text-gray-400 dark:text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">
                      {isEditing ? (
                        <div className="flex gap-2">
                          <select
                            value={editForm.location}
                            onChange={(e) => {
                              const newLocation = e.target.value
                              const firstCluster = ZONES.find(z => z.location === newLocation)?.cluster || ''
                              setEditForm({ ...editForm, location: newLocation, cluster: firstCluster })
                            }}
                            className="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-xs bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                          >
                            {ZONES.filter((z, i, self) => self.findIndex(z2 => z2.location === z.location) === i).map((zone) => (
                              <option key={zone.location} value={zone.location}>
                                {zone.location}
                              </option>
                            ))}
                          </select>
                          <select
                            value={editForm.cluster}
                            onChange={(e) => setEditForm({ ...editForm, cluster: e.target.value })}
                            className="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-xs bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                          >
                            {ZONES.filter(z => z.location === editForm.location).map((zone) => (
                              <option key={`${zone.location}-${zone.cluster}`} value={zone.cluster}>
                                {zone.cluster}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : (
                        channelInfo?.location && channelInfo?.cluster ? (
                          <span className="text-xs">{channelInfo.location}/{channelInfo.cluster}</span>
                        ) : (
                          <span className="text-gray-400 dark:text-gray-500">-</span>
                        )
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm">
                      {isEditing ? (
                        <div className="flex gap-2">
                          <button
                            onClick={saveEdit}
                            disabled={saving}
                            className="px-3 py-1 bg-blue-600 dark:bg-blue-500 text-white rounded hover:bg-blue-700 dark:hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            onClick={cancelEdit}
                            disabled={saving}
                            className="px-3 py-1 bg-gray-300 dark:bg-gray-600 text-gray-700 dark:text-gray-200 rounded hover:bg-gray-400 dark:hover:bg-gray-500 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEdit(channel)}
                          className="px-3 py-1 bg-blue-600 dark:bg-blue-500 text-white rounded hover:bg-blue-700 dark:hover:bg-blue-600"
                        >
                          {isEmpty ? 'Add' : 'Edit'}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
