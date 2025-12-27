import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { validateSetpoint } from '../utils/validation'
import type { SetpointUpdate } from '../types/setpoint'

interface SetpointEditorProps {
  location: string
  cluster: string
  onUpdate: () => void
  mode?: 'DAY' | 'NIGHT' | null
}

export default function SetpointEditor({ location, cluster, onUpdate, mode = null }: SetpointEditorProps) {
  const [formData, setFormData] = useState<SetpointUpdate>({})
  // Store input values as strings to prevent focus loss while typing
  const [inputValues, setInputValues] = useState<Record<string, string>>({})
  // Track saved values to show what's currently saved
  const [savedValues, setSavedValues] = useState<Record<string, number | null>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [dryRun, setDryRun] = useState(false)

  useEffect(() => {
    // Load setpoint for the specified mode
    loadSetpoint(mode)
  }, [mode, location, cluster])

  async function loadSetpoint(mode: 'DAY' | 'NIGHT' | null) {
    try {
      console.log('Loading setpoint:', { location, cluster, mode })
      const setpoint = await apiClient.getSetpoints(location, cluster, mode || undefined)
      console.log('Loaded setpoint:', setpoint)
      const newFormData = {
        heating_setpoint: setpoint.heating_setpoint ?? undefined,
        cooling_setpoint: setpoint.cooling_setpoint ?? undefined,
        co2: setpoint.co2 ?? undefined,
        vpd: setpoint.vpd ?? undefined,
        mode: mode as any,
      }
      setFormData(newFormData)
      // Set input values as strings for display
      setInputValues({
        heating_setpoint: setpoint.heating_setpoint?.toString() ?? '',
        cooling_setpoint: setpoint.cooling_setpoint?.toString() ?? '',
        co2: setpoint.co2?.toString() ?? '',
        vpd: setpoint.vpd?.toString() ?? '',
      })
      // Track saved values
      setSavedValues({
        heating_setpoint: setpoint.heating_setpoint ?? null,
        cooling_setpoint: setpoint.cooling_setpoint ?? null,
        co2: setpoint.co2 ?? null,
        vpd: setpoint.vpd ?? null,
      })
      setErrors({})
    } catch (error) {
      console.error('Error loading setpoint:', error)
      // On error, clear the form to show empty state
      setFormData({})
      setInputValues({
        heating_setpoint: '',
        cooling_setpoint: '',
        co2: '',
        vpd: '',
      })
      setSavedValues({
        heating_setpoint: null,
        cooling_setpoint: null,
        co2: null,
        vpd: null,
      })
    }
  }

  function handleInputChange(field: keyof SetpointUpdate, value: string) {
    // Update input value as string (allows typing without losing focus)
    setInputValues(prev => ({ ...prev, [field]: value }))
    
    // Parse and update formData only if value is valid
    const numValue = value === '' ? null : (parseFloat(value) || null)
    if (numValue !== null && !isNaN(numValue)) {
      setFormData(prev => ({ ...prev, [field]: numValue }))
    } else if (value === '') {
      setFormData(prev => ({ ...prev, [field]: undefined }))
    }
    
    // Clear error for this field
    if (errors[field]) {
      setErrors(prev => {
        const newErrors = { ...prev }
        delete newErrors[field]
        return newErrors
      })
    }
  }

  function handleBlur(field: keyof SetpointUpdate) {
    // On blur, ensure formData is updated with final parsed value
    const inputValue = inputValues[field] || ''
    const numValue = inputValue === '' ? null : (parseFloat(inputValue) || null)
    if (numValue !== null && !isNaN(numValue)) {
      setFormData(prev => ({ ...prev, [field]: numValue }))
    } else {
      setFormData(prev => ({ ...prev, [field]: undefined }))
    }
  }

  function validate(): boolean {
    const newErrors: Record<string, string> = {}

    if (formData.heating_setpoint !== undefined) {
      const result = validateSetpoint('temperature', formData.heating_setpoint)
      if (!result.isValid) {
        newErrors.heating_setpoint = result.error || 'Invalid heating setpoint'
      }
    }

    if (formData.cooling_setpoint !== undefined) {
      const result = validateSetpoint('temperature', formData.cooling_setpoint)
      if (!result.isValid) {
        newErrors.cooling_setpoint = result.error || 'Invalid cooling setpoint'
      }
    }

    if (formData.co2 !== undefined) {
      const result = validateSetpoint('co2', formData.co2)
      if (!result.isValid) {
        newErrors.co2 = result.error || 'Invalid CO2'
      }
    }

    if (formData.vpd !== undefined) {
      const result = validateSetpoint('vpd', formData.vpd)
      if (!result.isValid) {
        newErrors.vpd = result.error || 'Invalid VPD'
      }
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  async function handleSubmit() {
    if (!validate()) {
      return
    }

    if (dryRun) {
      alert('Dry run: Changes validated but not applied')
      return
    }

    setLoading(true)
    try {
      // Ensure mode is included in the update
      const updateData = {
        ...formData,
        mode: mode || undefined
      }
      console.log('Saving setpoints:', { location, cluster, mode, updateData })
      await apiClient.updateSetpoints(location, cluster, updateData)
      // Update saved values after successful save
      setSavedValues({
        heating_setpoint: formData.heating_setpoint ?? null,
        cooling_setpoint: formData.cooling_setpoint ?? null,
        co2: formData.co2 ?? null,
        vpd: formData.vpd ?? null,
      })
      alert('Setpoints updated successfully')
      // Reload the setpoint to show the saved values
      await loadSetpoint(mode)
      onUpdate()
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error';
      console.error('Setpoint update error:', error);
      alert(`Error updating setpoints: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }

  function formatValue(value: number | null | undefined, unit: string): string {
    if (value === null || value === undefined) return 'Not set'
    return `${value} ${unit}`
  }

  return (
    <div>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Heating Setpoint (°C)
          </label>
          <input
            type="number"
            step="0.1"
            min="10"
            max="35"
            value={inputValues.heating_setpoint ?? formData.heating_setpoint ?? ''}
            onChange={(e) => handleInputChange('heating_setpoint', e.target.value)}
            onBlur={() => handleBlur('heating_setpoint')}
            className={`border-2 rounded-md px-3 py-2 w-full bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${errors.heating_setpoint ? 'border-red-600' : 'border-gray-400'}`}
          />
          <p className="text-xs text-gray-500 mt-1">
            Current: {formatValue(savedValues.heating_setpoint, '°C')}
          </p>
          {errors.heating_setpoint && (
            <p className="text-sm font-medium text-red-700 mt-1">{errors.heating_setpoint}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Cooling Setpoint (°C)
          </label>
          <input
            type="number"
            step="0.1"
            min="10"
            max="35"
            value={inputValues.cooling_setpoint ?? formData.cooling_setpoint ?? ''}
            onChange={(e) => handleInputChange('cooling_setpoint', e.target.value)}
            onBlur={() => handleBlur('cooling_setpoint')}
            className={`border-2 rounded-md px-3 py-2 w-full bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${errors.cooling_setpoint ? 'border-red-600' : 'border-gray-400'}`}
          />
          <p className="text-xs text-gray-500 mt-1">
            Current: {formatValue(savedValues.cooling_setpoint, '°C')}
          </p>
          {errors.cooling_setpoint && (
            <p className="text-sm font-medium text-red-700 mt-1">{errors.cooling_setpoint}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            CO₂ (ppm)
          </label>
          <input
            type="number"
            step="1"
            min="400"
            max="2000"
            value={inputValues.co2 ?? formData.co2 ?? ''}
            onChange={(e) => handleInputChange('co2', e.target.value)}
            onBlur={() => handleBlur('co2')}
            className={`border-2 rounded-md px-3 py-2 w-full bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${errors.co2 ? 'border-red-600' : 'border-gray-400'}`}
          />
          <p className="text-xs text-gray-500 mt-1">
            Current: {formatValue(savedValues.co2, 'ppm')}
          </p>
          {errors.co2 && (
            <p className="text-sm font-medium text-red-700 mt-1">{errors.co2}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            VPD (kPa) - Controls dehumidifying devices
          </label>
          <input
            type="number"
            step="0.01"
            min="0"
            max="5"
            value={inputValues.vpd ?? formData.vpd ?? ''}
            onChange={(e) => handleInputChange('vpd', e.target.value)}
            onBlur={() => handleBlur('vpd')}
            className={`border-2 rounded-md px-3 py-2 w-full bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 ${errors.vpd ? 'border-red-600' : 'border-gray-400'}`}
          />
          <p className="text-xs text-gray-500 mt-1">
            Current: {formatValue(savedValues.vpd, 'kPa')}
          </p>
          {errors.vpd && (
            <p className="text-sm font-medium text-red-700 mt-1">{errors.vpd}</p>
          )}
          <p className="text-sm text-gray-600 mt-1">
            VPD setpoint controls fans and dehumidifiers. When VPD is below setpoint, devices turn ON.
          </p>
        </div>
      </div>

      <div className="mt-6 flex items-center gap-4">
        <label className="flex items-center">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            className="mr-2"
          />
          <span className="text-sm font-medium text-gray-800">Dry run (validate only)</span>
        </label>
        <button
          onClick={handleSubmit}
          disabled={loading}
          className="bg-blue-700 text-white font-semibold px-6 py-2 rounded-md hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
        >
          {loading ? 'Saving...' : 'Save Setpoints'}
        </button>
      </div>
    </div>
  )
}

