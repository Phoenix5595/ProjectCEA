import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import SetpointTimeline from './SetpointTimeline'

interface ClimateScheduleEditorProps {
  location: string
  cluster: string
}

interface ClimateSchedule {
  day_start_time: string
  day_end_time: string
  pre_day_duration: number
  pre_night_duration: number
  setpoints: {
    DAY?: any
    NIGHT?: any
    PRE_DAY?: any
    PRE_NIGHT?: any
  }
}

export default function ClimateScheduleEditor({ location, cluster }: ClimateScheduleEditorProps) {
  const [schedule, setSchedule] = useState<ClimateSchedule | null>(null)
  const [lightSchedule, setLightSchedule] = useState<any>(null)
  const [currentSetpoints, setCurrentSetpoints] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    loadData()
  }, [location, cluster])

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      // Load climate schedule
      const climateData = await apiClient.getClimateSchedule(location, cluster)
      
      // Load current setpoints for all modes
      let setpointsMap: Record<string, any> = {}
      try {
        const allSetpoints = await apiClient.getAllSetpointsForLocationCluster(location, cluster)
        allSetpoints.forEach((sp: any) => {
          if (sp.mode) {
            setpointsMap[sp.mode] = sp
          }
        })
        setCurrentSetpoints(setpointsMap)
      } catch (err: any) {
        console.warn('Error loading current setpoints:', err)
        // Don't fail the whole load if setpoints can't be loaded
      }
      
      // Merge current setpoints with schedule setpoints (current setpoints as defaults)
      const setpoints = {
        DAY: { ...setpointsMap.DAY, ...(climateData.setpoints?.DAY || {}) },
        NIGHT: { ...setpointsMap.NIGHT, ...(climateData.setpoints?.NIGHT || {}) },
        PRE_DAY: { ...setpointsMap.PRE_DAY, ...(climateData.setpoints?.PRE_DAY || {}) },
        PRE_NIGHT: { ...setpointsMap.PRE_NIGHT, ...(climateData.setpoints?.PRE_NIGHT || {}) }
      }
      
      setSchedule({
        ...climateData,
        setpoints
      })

      // Load light schedule for overlay
      const lightData = await apiClient.getRoomSchedule(location, cluster)
      setLightSchedule(lightData)
    } catch (err: any) {
      console.error('Error loading climate schedule:', err)
      setError(err.response?.data?.detail || err.message || 'Failed to load climate schedule')
    } finally {
      setLoading(false)
    }
  }

  async function handleSave() {
    if (!schedule) return

    setSaving(true)
    setError(null)
    setSuccess(null)

    try {
      const result = await apiClient.saveClimateSchedule(location, cluster, schedule)
      setSuccess('Climate schedule saved successfully')
      if (result.warnings && result.warnings.length > 0) {
        setError(result.warnings.join('; '))
      }
      // Reload to get updated data
      await loadData()
    } catch (err: any) {
      console.error('Error saving climate schedule:', err)
      setError(err.response?.data?.detail || err.message || 'Failed to save climate schedule')
    } finally {
      setSaving(false)
    }
  }

  function handleScheduleChange(updates: Partial<ClimateSchedule>) {
    if (!schedule) return
    setSchedule({ ...schedule, ...updates })
  }

  function handleSetpointChange(mode: string, setpointData: any) {
    if (!schedule) return
    setSchedule({
      ...schedule,
      setpoints: {
        ...schedule.setpoints,
        [mode]: setpointData
      }
    })
  }

  if (loading) {
    return <div className="text-gray-900">Loading climate schedule...</div>
  }

  if (!schedule) {
    return <div className="text-gray-900">Failed to load climate schedule</div>
  }

  return (
    <div className="space-y-8">
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
          {error}
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-800 px-4 py-3 rounded">
          {success}
        </div>
      )}

      <section>
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Climate Schedule Timeline</h2>
        <SetpointTimeline
          dayStartTime={schedule.day_start_time}
          dayEndTime={schedule.day_end_time}
          preDayDuration={schedule.pre_day_duration}
          preNightDuration={schedule.pre_night_duration}
          onDayStartChange={(time) => handleScheduleChange({ day_start_time: time })}
          onDayEndChange={(time) => handleScheduleChange({ day_end_time: time })}
          onPreDayDurationChange={(duration) => handleScheduleChange({ pre_day_duration: duration })}
          onPreNightDurationChange={(duration) => handleScheduleChange({ pre_night_duration: duration })}
          lightPhotoperiod={lightSchedule ? {
            startTime: lightSchedule.day_start_time,
            endTime: lightSchedule.day_end_time,
            rampUpDuration: lightSchedule.ramp_up_duration || 0,
            rampDownDuration: lightSchedule.ramp_down_duration || 0
          } : undefined}
          setpoints={schedule.setpoints}
        />
      </section>

      <section className="border-t border-gray-200 pt-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Setpoints</h2>
        <div className="grid grid-cols-2 gap-6">
          <div>
            <h3 className="text-lg font-semibold mb-3 text-gray-800">Day</h3>
            <SetpointModeEditor
              mode="DAY"
              setpoint={schedule.setpoints.DAY || {}}
              currentSetpoint={currentSetpoints.DAY}
              onChange={(data) => handleSetpointChange('DAY', data)}
            />
          </div>
          <div>
            <h3 className="text-lg font-semibold mb-3 text-gray-800">Night</h3>
            <SetpointModeEditor
              mode="NIGHT"
              setpoint={schedule.setpoints.NIGHT || {}}
              currentSetpoint={currentSetpoints.NIGHT}
              onChange={(data) => handleSetpointChange('NIGHT', data)}
            />
          </div>
          <div>
            <h3 className="text-lg font-semibold mb-3 text-gray-800">Pre-Day</h3>
            <SetpointModeEditor
              mode="PRE_DAY"
              setpoint={schedule.setpoints.PRE_DAY || {}}
              currentSetpoint={currentSetpoints.PRE_DAY}
              onChange={(data) => handleSetpointChange('PRE_DAY', data)}
              isAbsolute={true}
            />
          </div>
          <div>
            <h3 className="text-lg font-semibold mb-3 text-gray-800">Pre-Night</h3>
            <SetpointModeEditor
              mode="PRE_NIGHT"
              setpoint={schedule.setpoints.PRE_NIGHT || {}}
              currentSetpoint={currentSetpoints.PRE_NIGHT}
              onChange={(data) => handleSetpointChange('PRE_NIGHT', data)}
              isAbsolute={true}
            />
          </div>
        </div>
      </section>

      <div className="flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : 'Save Climate Schedule'}
        </button>
      </div>
    </div>
  )
}

interface SetpointModeEditorProps {
  mode: string
  setpoint: any
  currentSetpoint?: any
  onChange: (data: any) => void
  isAbsolute?: boolean
}

function SetpointModeEditor({ mode: _mode, setpoint, currentSetpoint, onChange, isAbsolute = false }: SetpointModeEditorProps) {
  function handleChange(field: string, value: any) {
    onChange({ ...setpoint, [field]: value })
  }

  return (
    <div className="space-y-4">
      {isAbsolute && (
        <div className="text-sm text-gray-600 mb-2">
          Absolute setpoint (not relative to night/day)
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Heating Setpoint (°C)
        </label>
        <input
          type="number"
          step="0.1"
          value={setpoint.heating_setpoint ?? ''}
          onChange={(e) => handleChange('heating_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md"
        />
        {currentSetpoint?.heating_setpoint !== undefined && (
          <div className="mt-1 text-xs text-gray-500">
            Current: {currentSetpoint.heating_setpoint}°C
          </div>
        )}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Cooling Setpoint (°C)
        </label>
        <input
          type="number"
          step="0.1"
          value={setpoint.cooling_setpoint ?? ''}
          onChange={(e) => handleChange('cooling_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md"
        />
        {currentSetpoint?.cooling_setpoint !== undefined && (
          <div className="mt-1 text-xs text-gray-500">
            Current: {currentSetpoint.cooling_setpoint}°C
          </div>
        )}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          VPD Setpoint (kPa)
        </label>
        <input
          type="number"
          step="0.01"
          value={setpoint.vpd ?? ''}
          onChange={(e) => handleChange('vpd', e.target.value ? parseFloat(e.target.value) : null)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md"
        />
        {currentSetpoint?.vpd !== undefined && (
          <div className="mt-1 text-xs text-gray-500">
            Current: {currentSetpoint.vpd} kPa
          </div>
        )}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          CO₂ Setpoint (ppm)
        </label>
        <input
          type="number"
          step="1"
          value={setpoint.co2 ?? ''}
          onChange={(e) => handleChange('co2', e.target.value ? parseFloat(e.target.value) : null)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md"
        />
        {currentSetpoint?.co2 !== undefined && (
          <div className="mt-1 text-xs text-gray-500">
            Current: {currentSetpoint.co2} ppm
          </div>
        )}
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Ramp In Duration (minutes)
        </label>
        <input
          type="number"
          min="0"
          max="240"
          value={setpoint.ramp_in_duration || 0}
          onChange={(e) => handleChange('ramp_in_duration', parseInt(e.target.value) || 0)}
          className="w-full px-3 py-2 border border-gray-300 rounded-md"
        />
        {currentSetpoint?.ramp_in_duration !== undefined && (
          <div className="mt-1 text-xs text-gray-500">
            Current: {currentSetpoint.ramp_in_duration} minutes
          </div>
        )}
        {setpoint.vpd && setpoint.ramp_in_duration > 15 && (
          <div className="mt-1 text-sm text-yellow-600">
            Warning: VPD ramp duration &gt; 15 minutes may cause stomatal shock
          </div>
        )}
      </div>
    </div>
  )
}

