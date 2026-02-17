import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { validateSetpoint } from '../utils/validation'
import { logger } from '../utils/logger'
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
 const [rampInDuration, setRampInDuration] = useState<number>(0)

 useEffect(() => {
 // Load setpoint for the specified mode
 loadSetpoint(mode)
 }, [mode, location, cluster])

 async function loadSetpoint(mode: 'DAY' | 'NIGHT' | null) {
 try {
 logger.debug('Loading setpoint:', { location, cluster, mode })
 const setpoint = await apiClient.getSetpoints(location, cluster, mode || undefined)
 logger.debug('Loaded setpoint:', setpoint)
 const newFormData: SetpointUpdate = {
 heating_setpoint: setpoint.heating_setpoint ?? undefined,
 cooling_setpoint: setpoint.cooling_setpoint ?? undefined,
 co2: setpoint.co2 ?? undefined,
 vpd: setpoint.vpd ?? undefined,
 ramp_in_duration: setpoint.ramp_in_duration ?? 0,
 mode: mode ?? undefined,
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
 setRampInDuration(setpoint.ramp_in_duration ?? 0)
 setErrors({})
 } catch (error) {
 logger.error('Error loading setpoint:', error)
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

 if (rampInDuration < 0 || rampInDuration > 240) {
 newErrors.ramp_in_duration = 'Ramp duration must be between 0 and 240 minutes'
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
 ramp_in_duration: rampInDuration,
 mode: mode || undefined
 }
 logger.debug('Saving setpoints:', { location, cluster, mode, updateData })
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
 logger.error('Setpoint update error:', error);
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
 <label className="block text-sm font-medium text-text-secondary mb-1">
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
 className={`border-2 rounded-md px-3 py-2 w-full bg-surface-primary text-text-input font-medium focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light ${errors.heating_setpoint ? 'border-status-danger-vivid' : 'border-border-emphasis'}`}
 />
 <p className="text-xs text-text-muted mt-1">
 Current: {formatValue(savedValues.heating_setpoint, '°C')}
 </p>
 {errors.heating_setpoint && (
 <p className="text-sm font-medium text-status-danger mt-1">{errors.heating_setpoint}</p>
 )}
 </div>

 <div>
 <label className="block text-sm font-medium text-text-secondary mb-1">
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
 className={`border-2 rounded-md px-3 py-2 w-full bg-surface-primary text-text-input font-medium focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light ${errors.cooling_setpoint ? 'border-status-danger-vivid' : 'border-border-emphasis'}`}
 />
 <p className="text-xs text-text-muted mt-1">
 Current: {formatValue(savedValues.cooling_setpoint, '°C')}
 </p>
 {errors.cooling_setpoint && (
 <p className="text-sm font-medium text-status-danger mt-1">{errors.cooling_setpoint}</p>
 )}
 </div>

 <div>
 <label className="block text-sm font-medium text-text-secondary mb-1">
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
 className={`border-2 rounded-md px-3 py-2 w-full bg-surface-primary text-text-input font-medium focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light ${errors.co2 ? 'border-status-danger-vivid' : 'border-border-emphasis'}`}
 />
 <p className="text-xs text-text-muted mt-1">
 Current: {formatValue(savedValues.co2, 'ppm')}
 </p>
 {errors.co2 && (
 <p className="text-sm font-medium text-status-danger mt-1">{errors.co2}</p>
 )}
 </div>

 <div>
 <label className="block text-sm font-medium text-text-secondary mb-1">
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
 className={`border-2 rounded-md px-3 py-2 w-full bg-surface-primary text-text-input font-medium focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light ${errors.vpd ? 'border-status-danger-vivid' : 'border-border-emphasis'}`}
 />
 <p className="text-xs text-text-muted mt-1">
 Current: {formatValue(savedValues.vpd, 'kPa')}
 </p>
 {errors.vpd && (
 <p className="text-sm font-medium text-status-danger mt-1">{errors.vpd}</p>
 )}
 <p className="text-sm text-text-muted mt-1">
 VPD setpoint controls fans and dehumidifiers. When VPD is below setpoint, devices turn ON.
 </p>
 </div>

 <div>
 <label className="block text-sm font-medium text-text-secondary mb-1">
 Ramp-In Duration (minutes)
 </label>
 <input
 type="number"
 step="1"
 min="0"
 max="240"
 value={rampInDuration}
 onChange={(e) => setRampInDuration(parseInt(e.target.value) || 0)}
 className={`border-2 rounded-md px-3 py-2 w-full bg-surface-primary text-text-input font-medium focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light ${errors.ramp_in_duration ? 'border-status-danger-vivid' : 'border-border-emphasis'}`}
 />
 <p className="text-xs text-text-muted mt-1">
 Current: {savedValues.ramp_in_duration ?? 0} minutes
 </p>
 {errors.ramp_in_duration && (
 <p className="text-sm font-medium text-status-danger mt-1">{errors.ramp_in_duration}</p>
 )}
 {rampInDuration > 15 && formData.vpd !== undefined && (
 <p className="text-sm font-medium text-status-warning mt-1">
 Warning: Long ramp durations (&gt;15 min) may cause VPD fluctuations during transition
 </p>
 )}
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
 <span className="text-sm font-medium text-text-input">Dry run (validate only)</span>
 </label>
 <button
 onClick={handleSubmit}
 disabled={loading}
 className="bg-btn-primary-hover text-text-default font-semibold px-6 py-2 rounded-md hover:bg-btn-primary disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
 >
 {loading ? 'Saving...' : 'Save Setpoints'}
 </button>
 </div>
 </div>
 )
}

