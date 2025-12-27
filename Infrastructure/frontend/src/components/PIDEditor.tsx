import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { validatePIDParameter } from '../utils/validation'
import type { PIDParameters, PIDParameterUpdate } from '../types/pid'

const DEVICE_TYPES = ['heater', 'fan', 'co2']

export default function PIDEditor() {
  const [selectedDeviceType, setSelectedDeviceType] = useState<string>('heater')
  const [formData, setFormData] = useState<PIDParameterUpdate>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [currentParams, setCurrentParams] = useState<PIDParameters | null>(null)

  useEffect(() => {
    loadPIDParameters()
  }, [selectedDeviceType])

  async function loadPIDParameters() {
    try {
      const params = await apiClient.getPIDParameters(selectedDeviceType)
      setCurrentParams(params)
      setFormData({
        kp: params.kp,
        ki: params.ki,
        kd: params.kd,
      })
      setErrors({})
    } catch (error) {
      console.error('Error loading PID parameters:', error)
      setCurrentParams(null)
    }
  }

  function handleChange(param: 'kp' | 'ki' | 'kd', value: number) {
    setFormData(prev => ({ ...prev, [param]: value }))
    // Validate immediately
    const result = validatePIDParameter(selectedDeviceType, param, value)
    if (!result.isValid) {
      setErrors(prev => ({ ...prev, [param]: result.error || 'Invalid value' }))
    } else {
      setErrors(prev => {
        const newErrors = { ...prev }
        delete newErrors[param]
        return newErrors
      })
    }
  }

  async function handleSubmit() {
    if (Object.keys(errors).length > 0) {
      alert('Please fix validation errors before submitting')
      return
    }

    setLoading(true)
    try {
      await apiClient.updatePIDParameters(selectedDeviceType, formData)
      alert('PID parameters updated successfully')
      loadPIDParameters()
    } catch (error: any) {
      alert(`Error updating PID parameters: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
          Device Type
        </label>
        <select
          value={selectedDeviceType}
          onChange={(e) => setSelectedDeviceType(e.target.value)}
          className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          {DEVICE_TYPES.map(type => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Note: Fans can use PID control for temperature cooling. Dehumidifiers are ON/OFF only.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Kp (Proportional Gain)
          </label>
          <input
            type="number"
            step="0.01"
            value={formData.kp ?? ''}
            onChange={(e) => handleChange('kp', parseFloat(e.target.value))}
            className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${errors.kp ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'}`}
          />
          {currentParams?.kp !== undefined && (
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Current: {currentParams.kp}
            </div>
          )}
          {errors.kp && (
            <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.kp}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Ki (Integral Gain)
          </label>
          <input
            type="number"
            step="0.001"
            value={formData.ki ?? ''}
            onChange={(e) => handleChange('ki', parseFloat(e.target.value))}
            className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${errors.ki ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'}`}
          />
          {currentParams?.ki !== undefined && (
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Current: {currentParams.ki}
            </div>
          )}
          {errors.ki && (
            <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.ki}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Kd (Derivative Gain)
          </label>
          <input
            type="number"
            step="0.01"
            value={formData.kd ?? ''}
            onChange={(e) => handleChange('kd', parseFloat(e.target.value))}
            className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${errors.kd ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'}`}
          />
          {currentParams?.kd !== undefined && (
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Current: {currentParams.kd}
            </div>
          )}
          {errors.kd && (
            <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.kd}</p>
          )}
        </div>
      </div>

      <div className="mt-6">
        <button
          onClick={handleSubmit}
          disabled={loading || Object.keys(errors).length > 0}
          className="bg-blue-600 dark:bg-blue-500 text-white px-6 py-2 rounded-md hover:bg-blue-700 dark:hover:bg-blue-600 disabled:opacity-50"
        >
          {loading ? 'Saving...' : 'Save PID Parameters'}
        </button>
      </div>
    </div>
  )
}

