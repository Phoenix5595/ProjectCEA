import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import CircularTimePicker from './CircularTimePicker'

interface RoomScheduleEditorProps {
  location: string
  cluster: string
  period: 'day' | 'night'
}

interface RoomSchedule {
  day_start_time: string
  day_end_time: string
  night_start_time: string
  night_end_time: string
  ramp_up_duration: number | null
  ramp_down_duration: number | null
}

export default function RoomScheduleEditor({ location, cluster, period }: RoomScheduleEditorProps) {
  const [dayStartTime, setDayStartTime] = useState('06:00')
  const [dayEndTime, setDayEndTime] = useState('20:00')
  const [nightStartTime, setNightStartTime] = useState('20:00')
  const [nightEndTime, setNightEndTime] = useState('06:00')
  const [rampUpDuration, setRampUpDuration] = useState<number | null>(30)
  const [rampDownDuration, setRampDownDuration] = useState<number | null>(15)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadRoomSchedule()
  }, [location, cluster])

  async function loadRoomSchedule() {
    setLoading(true)
    try {
      const schedule = await apiClient.getRoomSchedule(location, cluster)
      if (import.meta.env.DEV) {
        console.log('Loaded schedule from API:', schedule)
      }
      if (schedule) {
        // Ensure times are in HH:MM format
        const formatTime = (time: string | undefined, defaultTime: string): string => {
          if (!time) return defaultTime
          // If time is already in HH:MM format, return it
          if (time.match(/^\d{2}:\d{2}$/)) return time
          // If it's a time object string, extract HH:MM
          if (typeof time === 'string' && time.includes(':')) {
            const parts = time.split(':')
            return `${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}`
          }
          return defaultTime
        }
        
        const dayStart = formatTime(schedule.day_start_time, '06:00')
        const dayEnd = formatTime(schedule.day_end_time, '20:00')
        const nightStart = formatTime(schedule.night_start_time, '20:00')
        const nightEnd = formatTime(schedule.night_end_time, '06:00')
        const rampUp = schedule.ramp_up_duration ?? 30
        const rampDown = schedule.ramp_down_duration ?? 15
        
        if (import.meta.env.DEV) {
          console.log('Setting state:', { dayStart, dayEnd, nightStart, nightEnd, rampUp, rampDown })
        }
        
        // Set all state values - ensure we're setting the exact values from the API
        setDayStartTime(dayStart)
        setDayEndTime(dayEnd)
        setNightStartTime(nightStart)
        setNightEndTime(nightEnd)
        // Handle null/undefined ramp values - use null if explicitly null, otherwise use the value or default
        setRampUpDuration(rampUp !== null && rampUp !== undefined ? rampUp : null)
        setRampDownDuration(rampDown !== null && rampDown !== undefined ? rampDown : null)
      }
    } catch (error: any) {
      console.error('Error loading room schedule:', error)
      // Keep default values on error
      // In production, could show a user-friendly error message here
      if (import.meta.env.DEV) {
        console.error('Full error details:', error)
      }
    } finally {
      setLoading(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      const schedule: RoomSchedule = {
        day_start_time: dayStartTime,
        day_end_time: dayEndTime,
        night_start_time: nightStartTime,
        night_end_time: nightEndTime,
        ramp_up_duration: rampUpDuration,
        ramp_down_duration: rampDownDuration,
      }

      await apiClient.saveRoomSchedule(location, cluster, schedule)
      // Reload the schedule to ensure we have the correct values from the database
      await loadRoomSchedule()
      alert('Room schedule saved successfully! All devices in this room will follow this schedule.')
    } catch (error: any) {
      console.error('Error saving room schedule:', error)
      // Extract detailed error message from API response
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error occurred'
      console.error('Error details:', {
        status: error.response?.status,
        statusText: error.response?.statusText,
        data: error.response?.data,
        message: error.message
      })
      alert(`Error saving room schedule: ${errorMessage}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="text-gray-600">Loading schedule...</div>
  }

  return (
    <div>
      {period === 'day' ? (
        <div className="space-y-6">
          <div className="flex justify-center">
            <CircularTimePicker
              dayStartTime={dayStartTime}
              dayEndTime={dayEndTime}
              onDayStartChange={(time) => {
                setDayStartTime(time)
                setNightEndTime(time) // Sync night end time
              }}
              onDayEndChange={(time) => {
                setDayEndTime(time)
                setNightStartTime(time) // Sync night start time
              }}
              label="Day & Night Schedule"
              period="day"
              rampUpDuration={rampUpDuration}
              rampDownDuration={rampDownDuration}
              onRampUpChange={setRampUpDuration}
              onRampDownChange={setRampDownDuration}
              showPresetButtons={location !== 'Veg Room'}
              lockedPhotoperiodHours={location === 'Veg Room' ? 18 : null}
              location={location}
              cluster={cluster}
            />
          </div>

          <div className="mt-6">
            <button
              onClick={handleSave}
              disabled={saving}
              className="bg-blue-700 text-white font-semibold px-6 py-2 rounded-md hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
            >
              {saving ? 'Saving...' : 'Save Schedule'}
            </button>
            <p className="text-xs text-gray-500 mt-2">
              This will create schedules for all devices in this room. Existing schedules will be replaced.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="flex justify-center">
            <CircularTimePicker
              dayStartTime={nightStartTime}
              dayEndTime={nightEndTime}
              onDayStartChange={(time) => {
                setNightStartTime(time)
                setDayEndTime(time) // Sync day end time
              }}
              onDayEndChange={(time) => {
                setNightEndTime(time)
                setDayStartTime(time) // Sync day start time
              }}
              label="Night Period"
              period="night"
              location={location}
              cluster={cluster}
            />
          </div>

          <div className="mt-6">
            <button
              onClick={handleSave}
              disabled={saving}
              className="bg-blue-700 text-white font-semibold px-6 py-2 rounded-md hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
            >
              {saving ? 'Saving...' : 'Save Night Schedule'}
            </button>
            <p className="text-xs text-gray-500 mt-2">
              This will create schedules for all devices in this room. Existing schedules will be replaced.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
