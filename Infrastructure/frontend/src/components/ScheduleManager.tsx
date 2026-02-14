import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { findConflicts } from '../utils/conflictDetection'
import { wsClient } from '../services/websocket'
import { useToast } from '../contexts/ToastContext'
import { logger } from '../utils/logger'
import type { Schedule, ScheduleCreate, ScheduleUpdate } from '../types/schedule'

interface ScheduleManagerProps {
  location: string
  cluster: string
}

export default function ScheduleManager({ location, cluster }: ScheduleManagerProps) {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [devices, setDevices] = useState<Array<{ name: string; display_name?: string; device_type?: string }>>([])
  const [loading, setLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null)
  const [formData, setFormData] = useState<ScheduleCreate>({
    name: '',
    location,
    cluster,
    device_name: '',
    start_time: '06:00',
    end_time: '18:00',
    enabled: true,
  })
  const [conflicts, setConflicts] = useState<string[]>([])
  const [versionConflict, setVersionConflict] = useState<string | null>(null)
  const { showToast } = useToast()

  useEffect(() => {
    loadSchedules()
    loadDevices()
    
    // Listen for schedule updates from WebSocket
    const unsubscribe = wsClient.on('schedule_update', (message: any) => {
      if (message.schedule && message.schedule.location === location && message.schedule.cluster === cluster) {
        // Update the schedule in our list
        setSchedules(prev => {
          const index = prev.findIndex(s => s.id === message.schedule_id)
          if (index >= 0) {
            const updated = [...prev]
            updated[index] = { ...updated[index], ...message.schedule }
            return updated
          }
          // If it's a new schedule or we don't have it, reload
          loadSchedules()
          return prev
        })
        
        // Show toast notification
        showToast(`Schedule "${message.schedule.name}" was updated by another user`, 'info')
        
        // If we're editing this schedule, refresh it
        if (editingSchedule && editingSchedule.id === message.schedule_id) {
          setEditingSchedule({ ...editingSchedule, ...message.schedule })
          showToast('Schedule was updated. Please review changes before saving.', 'warning', 8000)
        }
      }
    })
    
    return () => {
      unsubscribe()
    }
  }, [location, cluster, editingSchedule, showToast])

  async function loadDevices() {
    try {
      const devicesData = await apiClient.getDevicesForLocationClusterWithDetails(location, cluster)
      const deviceList = Object.entries(devicesData).map(([name, device]: [string, any]) => ({
        name,
        display_name: device.display_name,
        device_type: device.device_type
      }))
      setDevices(deviceList.sort((a, b) => a.name.localeCompare(b.name)))
    } catch (error) {
      logger.error('Error loading devices:', error)
    }
  }

  async function loadSchedules() {
    try {
      const data = await apiClient.getSchedules(location, cluster)
      setSchedules(data)
    } catch (error) {
      logger.error('Error loading schedules:', error)
    }
  }

  function handleFormChange(field: keyof ScheduleCreate, value: any) {
    setFormData(prev => ({ ...prev, [field]: value }))
  }

  function checkConflicts() {
    if (!formData.start_time || !formData.end_time) return

    const newSchedule: Schedule = {
      id: editingSchedule?.id || -1,
      name: formData.name,
      location: formData.location,
      cluster: formData.cluster,
      device_name: formData.device_name,
      day_of_week: formData.day_of_week ?? null,
      start_time: formData.start_time,
      end_time: formData.end_time,
      enabled: formData.enabled ?? true,
      mode: formData.mode,
      created_at: editingSchedule?.created_at || new Date().toISOString(),
    }

    const detectedConflicts = findConflicts(newSchedule, schedules)
    setConflicts(detectedConflicts.map(c => c.reason))
  }

  useEffect(() => {
    if (showForm && formData.start_time && formData.end_time) {
      checkConflicts()
    }
  }, [formData.start_time, formData.end_time, formData.day_of_week, formData.mode])

  async function handleSubmit() {
    if (conflicts.length > 0) {
      alert('Cannot create schedule with conflicts:\n' + conflicts.join('\n'))
      return
    }

    setLoading(true)
    setVersionConflict(null)
    try {
      if (editingSchedule) {
        const update: ScheduleUpdate = {
          name: formData.name,
          start_time: formData.start_time,
          end_time: formData.end_time,
          day_of_week: formData.day_of_week ?? null,
          enabled: formData.enabled,
          mode: formData.mode,
          target_intensity: formData.target_intensity ?? null,
          ramp_up_duration: formData.ramp_up_duration ?? null,
          ramp_down_duration: formData.ramp_down_duration ?? null,
          expected_version: editingSchedule.updated_at || null,
        }
        await apiClient.updateSchedule(editingSchedule.id, update)
        showToast('Schedule updated successfully', 'success')
      } else {
        await apiClient.createSchedule(formData)
        showToast('Schedule created successfully', 'success')
      }
      setShowForm(false)
      setEditingSchedule(null)
      setFormData({
        name: '',
        location,
        cluster,
        device_name: '',
        start_time: '06:00',
        end_time: '18:00',
        enabled: true,
      })
      loadSchedules()
    } catch (error: any) {
      // Handle version conflict (409)
      if (error.response?.status === 409) {
        const conflictDetail = error.response?.data?.detail
        const message = typeof conflictDetail === 'string' 
          ? conflictDetail 
          : conflictDetail?.detail || 'This schedule was modified by another user. Please refresh and try again.'
        setVersionConflict(message)
        showToast(message, 'error', 10000)
        // Reload schedules to get latest version
        loadSchedules()
      } else {
        const errorMsg = error.response?.data?.detail || error.message || 'Error saving schedule'
        showToast(errorMsg, 'error')
        alert(errorMsg)
      }
    } finally {
      setLoading(false)
    }
  }
  
  function handleRefresh() {
    loadSchedules()
    if (editingSchedule) {
      const updated = schedules.find(s => s.id === editingSchedule.id)
      if (updated) {
        setEditingSchedule(updated)
        setFormData({
          name: updated.name,
          location: updated.location,
          cluster: updated.cluster,
          device_name: updated.device_name,
          start_time: updated.start_time,
          end_time: updated.end_time,
          day_of_week: updated.day_of_week,
          enabled: updated.enabled,
          mode: updated.mode,
          target_intensity: updated.target_intensity ?? null,
          ramp_up_duration: updated.ramp_up_duration ?? null,
          ramp_down_duration: updated.ramp_down_duration ?? null,
        })
        setVersionConflict(null)
      }
    }
  }

  async function handleDelete(scheduleId: number) {
    if (!confirm('Are you sure you want to delete this schedule?')) return

    try {
      await apiClient.deleteSchedule(scheduleId)
      loadSchedules()
    } catch (error: any) {
      alert(`Error deleting schedule: ${error.response?.data?.detail || error.message}`)
    }
  }

  function startEdit(schedule: Schedule) {
    setEditingSchedule(schedule)
    setFormData({
      name: schedule.name,
      location: schedule.location,
      cluster: schedule.cluster,
      device_name: schedule.device_name,
      start_time: schedule.start_time,
      end_time: schedule.end_time,
      day_of_week: schedule.day_of_week,
      enabled: schedule.enabled,
      mode: schedule.mode,
      target_intensity: schedule.target_intensity ?? null,
      ramp_up_duration: schedule.ramp_up_duration ?? null,
      ramp_down_duration: schedule.ramp_down_duration ?? null,
    })
    setShowForm(true)
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold text-text-default">Schedules</h2>
        <button
          onClick={() => {
            setShowForm(!showForm)
            setEditingSchedule(null)
            setFormData({
              name: '',
              location,
              cluster,
              device_name: '',
              start_time: '06:00',
              end_time: '18:00',
              enabled: true,
            })
          }}
          className="bg-blue-600 dark:bg-blue-500 text-white px-4 py-2 rounded-md hover:bg-blue-700 dark:hover:bg-blue-600"
        >
          {showForm ? 'Cancel' : '+ New Schedule'}
        </button>
      </div>

      {showForm && (
        <div className="mb-6 p-4 border border-border-subtle rounded-md bg-surface-primary">
          <h3 className="font-bold mb-4 text-text-default">
            {editingSchedule ? 'Edit Schedule' : 'Create Schedule'}
          </h3>

          {conflicts.length > 0 && (
            <div className="mb-4 p-3 bg-status-warning/10 border border-status-warning rounded-md">
              <p className="font-medium text-status-warning">Conflicts detected:</p>
              <ul className="list-disc list-inside text-sm text-status-warning">
                {conflicts.map((conflict, i) => (
                  <li key={i}>{conflict}</li>
                ))}
              </ul>
            </div>
          )}

          {versionConflict && (
            <div className="mb-4 p-3 bg-status-danger/10 border border-status-danger rounded-md">
              <p className="font-medium text-status-danger mb-2">{versionConflict}</p>
              <button
                onClick={handleRefresh}
                className="text-sm bg-status-danger text-text-default px-3 py-1 rounded-sm hover:bg-status-danger/80"
              >
                Refresh Data
              </button>
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Name
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => handleFormChange('name', e.target.value)}
                className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Device
              </label>
              <select
                value={formData.device_name}
                onChange={(e) => handleFormChange('device_name', e.target.value)}
                className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                required
              >
                <option value="">Select a device...</option>
                {devices.map((device) => (
                  <option key={device.name} value={device.name}>
                    {device.display_name || device.name}
                    {device.device_type && ` (${device.device_type})`}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Start Time (HH:MM)
                </label>
                <input
                  type="time"
                  value={formData.start_time}
                  onChange={(e) => handleFormChange('start_time', e.target.value)}
                  className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  End Time (HH:MM)
                </label>
                <input
                  type="time"
                  value={formData.end_time}
                  onChange={(e) => handleFormChange('end_time', e.target.value)}
                  className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Day of Week (optional, leave empty for daily)
              </label>
              <select
                value={formData.day_of_week ?? ''}
                onChange={(e) => handleFormChange('day_of_week', e.target.value ? parseInt(e.target.value) : null)}
                className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
              >
                <option value="">Daily</option>
                <option value="0">Monday</option>
                <option value="1">Tuesday</option>
                <option value="2">Wednesday</option>
                <option value="3">Thursday</option>
                <option value="4">Friday</option>
                <option value="5">Saturday</option>
                <option value="6">Sunday</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Mode (optional)
              </label>
              <select
                value={formData.mode ?? ''}
                onChange={(e) => handleFormChange('mode', e.target.value || undefined)}
                className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
              >
                <option value="">None</option>
                <option value="DAY">DAY</option>
                <option value="NIGHT">NIGHT</option>
                <option value="TRANSITION">TRANSITION</option>
              </select>
            </div>

            <div className="border-t border-border-subtle pt-4">
              <h4 className="text-sm font-semibold text-text-secondary mb-2">Light Ramp Settings (optional)</h4>
              <p className="text-xs text-text-muted mb-3">
                For dimmable lights: set target intensity and ramp durations for gradual transitions
              </p>
              
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-1">
                    Target Intensity (0-100%)
                  </label>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="1"
                    value={formData.target_intensity ?? ''}
                    onChange={(e) => handleFormChange('target_intensity', e.target.value ? parseFloat(e.target.value) : null)}
                    className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                    placeholder="Leave empty for ON/OFF only"
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1">
                      Ramp Up (minutes)
                    </label>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={formData.ramp_up_duration ?? ''}
                      onChange={(e) => handleFormChange('ramp_up_duration', e.target.value ? parseInt(e.target.value) : null)}
                      className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                      placeholder="0 = instant"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1">
                      Ramp Down (minutes)
                    </label>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={formData.ramp_down_duration ?? ''}
                      onChange={(e) => handleFormChange('ramp_down_duration', e.target.value ? parseInt(e.target.value) : null)}
                      className="border border-border-emphasis rounded-md px-3 py-2 w-full bg-surface-primary text-text-default"
                      placeholder="0 = instant"
                    />
                  </div>
                </div>
              </div>
            </div>

            <div>
              <label className="flex items-center">
                <input
                  type="checkbox"
                  checked={formData.enabled ?? true}
                  onChange={(e) => handleFormChange('enabled', e.target.checked)}
                  className="mr-2"
                />
                <span className="text-sm text-text-secondary">Enabled</span>
              </label>
            </div>

            <button
              onClick={handleSubmit}
              disabled={loading || conflicts.length > 0}
              className="bg-accent-primary text-text-default px-6 py-2 rounded-md hover:bg-accent-primary/80 disabled:opacity-50"
            >
              {loading ? 'Saving...' : editingSchedule ? 'Update' : 'Create'}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {schedules.length === 0 ? (
          <p className="text-text-muted">No schedules configured</p>
        ) : (
          schedules.map((schedule) => (
            <div
              key={schedule.id}
              className="p-4 border border-border-subtle rounded-md flex justify-between items-center bg-surface-primary"
            >
              <div>
                <div className="font-medium text-text-default">{schedule.name}</div>
                <div className="text-sm text-text-muted">
                  {schedule.device_name} • {schedule.start_time} - {schedule.end_time}
                  {schedule.day_of_week !== null && ` • Day ${schedule.day_of_week}`}
                  {schedule.mode && ` • Mode: ${schedule.mode}`}
                  {schedule.target_intensity !== null && schedule.target_intensity !== undefined && (
                    <>
                      {` • ${schedule.target_intensity}%`}
                      {(schedule.ramp_up_duration || schedule.ramp_down_duration) && (
                        <span className="text-xs">
                          {' '}(↑{schedule.ramp_up_duration || 0}m ↓{schedule.ramp_down_duration || 0}m)
                        </span>
                      )}
                    </>
                  )}
                  {!schedule.enabled && ' • DISABLED'}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => startEdit(schedule)}
                  className="text-accent-primary hover:underline"
                >
                  Edit
                </button>
                <button
                  onClick={() => handleDelete(schedule.id)}
                  className="text-status-danger hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

