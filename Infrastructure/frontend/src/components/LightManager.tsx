import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { LightStatus } from '../types/light'

interface LightDevice {
 device_name: string
 display_name?: string
 dimming_enabled?: boolean
 dimming_board_id?: number
 dimming_channel?: number
}

interface LightManagerProps {
 location: string
 cluster: string
 lights: LightDevice[]
}


export default function LightManager({ location, cluster, lights }: LightManagerProps) {
 const [lightStatuses, setLightStatuses] = useState<Record<string, LightStatus>>({})
 const [inputValues, setInputValues] = useState<Record<string, string>>({}) // Store input values as strings to prevent focus loss
 const [schedules, setSchedules] = useState<Record<string, any>>({}) // Store schedules by device_name
 const [saving, setSaving] = useState<Record<string, boolean>>({}) // Track save operations
 const [savedValues, setSavedValues] = useState<Record<string, string>>({}) // Track saved values to detect changes
 const [savingAll, setSavingAll] = useState(false) // Track "Save All" operation
 const [deviceStates, setDeviceStates] = useState<Record<string, number>>({}) // Store relay states (0=OFF, 1=ON)
 
 const [roomSchedule, setRoomSchedule] = useState<any>(null)
 const [roomScheduleSaving, setRoomScheduleSaving] = useState(false)
 
 // Collapse state for schedule details
 const [showSchedule, setShowSchedule] = useState(false)

 useEffect(() => {
 // Load initial light statuses
 loadLightStatuses()
 // Load schedules for each light
 loadSchedules()
 // Load device states (relay states)
 loadDeviceStates()
 // Load room schedule
 loadRoomSchedule()
 
 // Refresh every 1 second for faster updates
 const interval = setInterval(() => {
 loadLightStatuses()
 loadDeviceStates()
 }, 1000)
 return () => clearInterval(interval)
 }, [location, cluster, lights])

 async function loadRoomSchedule() {
 try {
 const schedule = await apiClient.getRoomSchedule(location, cluster)
 setRoomSchedule(schedule)
 } catch (error) {
 logger.error('Error loading room schedule', error)
 }
 }

 async function handleSaveRoomSchedule() {
 if (!roomSchedule) return
 setRoomScheduleSaving(true)
 try {
 await apiClient.saveRoomSchedule(location, cluster, roomSchedule)
 await loadRoomSchedule()
 alert('Room schedule saved')
 } catch (error) {
 logger.error('Error saving room schedule', error)
 alert('Failed to save room schedule')
 } finally {
 setRoomScheduleSaving(false)
 }
 }

 async function loadDeviceStates() {
 try {
 const devices = await apiClient.getDevicesForLocationCluster(location, cluster)
 const states: Record<string, number> = {}
 
 // Extract relay state for each light
 for (const light of lights) {
 const device = devices.devices[light.device_name]
 if (device && device.state !== undefined) {
 states[light.device_name] = device.state
 }
 }
 
 setDeviceStates(states)
 } catch (error: any) {
 logger.error('Error loading device states:', error)
 }
 }

 async function loadSchedules() {
 try {
 const allSchedules = await apiClient.getSchedules(location, cluster)
 const scheduleMap: Record<string, any> = {}
 const newInputValues: Record<string, string> = {}
 
 // Find day/sun schedules for each light (backend uses SUN for dimmable lights, legacy may use DAY)
 for (const light of lights.filter(l => l.dimming_enabled)) {
 const daySchedule = allSchedules.find(
 s => s.device_name === light.device_name &&
 s.enabled &&
 ((s.mode === 'SUN' || s.mode === 'DAY') || (s.mode === null && s.target_intensity !== null && s.target_intensity !== undefined))
 )
 
 if (daySchedule) {
 scheduleMap[light.device_name] = daySchedule
 // Initialize input value with target_intensity from schedule
 if (daySchedule.target_intensity !== null && daySchedule.target_intensity !== undefined) {
 // Normalize to integer string (intensities are always whole numbers)
 const targetIntensity = Math.round(Number(daySchedule.target_intensity))
 newInputValues[light.device_name] = targetIntensity.toString()
 }
 }
 }
 
 setSchedules(scheduleMap)
 
 // Update input values and saved values atomically to prevent false "modified" states
 // Update both in the same synchronous batch
 setInputValues(prev => {
 return { ...prev, ...newInputValues }
 })
 // Update saved values to track what's been saved - use same values as inputValues
 // This must happen synchronously after inputValues to prevent race conditions
 setSavedValues(prev => {
 return { ...prev, ...newInputValues }
 })
 } catch (error: any) {
 logger.error('Error loading schedules:', error)
 }
 }

 async function loadLightStatuses() {
 const dimmableLights = lights.filter(l => l.dimming_enabled)
 
 for (const light of dimmableLights) {
 try {
 const status = await apiClient.getLightStatus(location, cluster, light.device_name)
 setLightStatuses(prev => ({
 ...prev,
 [light.device_name]: status
 }))
 // Only set input values if they don't exist yet (initial load)
 // Don't overwrite user's slider position during refresh
 // But update if schedule has changed (after save)
 setInputValues(prev => {
 const schedule = schedules[light.device_name]
 const currentInputValue = prev[light.device_name]
 
 // If input value doesn't exist, initialize it
 if (currentInputValue === undefined) {
 const targetValue = schedule?.target_intensity ?? 
 (status.target_intensity !== null && status.target_intensity !== undefined
 ? status.target_intensity
 : status.intensity)
 const newValue = targetValue.toString()
 // Also initialize saved value
 setSavedValues(prev => ({
 ...prev,
 [light.device_name]: newValue
 }))
 return {
 ...prev,
 [light.device_name]: newValue
 }
 }
 
 // If schedule exists and has a target_intensity, sync input value to it
 // This ensures slider position matches saved value after save
 // BUT only sync if the user hasn't made unsaved changes (currentInputValue === savedValue)
 // This prevents overwriting user's edits while they're adjusting the slider
 if (schedule && schedule.target_intensity !== null && schedule.target_intensity !== undefined) {
 const savedValue = schedule.target_intensity.toString()
 const currentSavedValue = savedValues[light.device_name]
 // Only sync if current input matches what was previously saved (no unsaved changes)
 // This allows the schedule to update the UI if changed externally, but preserves user edits
 if (currentInputValue === currentSavedValue && currentInputValue !== savedValue) {
 return {
 ...prev,
 [light.device_name]: savedValue
 }
 }
 }
 
 // Keep existing value
 return prev
 })
 // setErrors(prev => {
 // const newErrors = { ...prev }
 // delete newErrors[light.device_name]
 // return newErrors
 // })
 } catch (error: any) {
 // Ignore errors in refresh
 }
 }
 }

 async function handleSaveTargetIntensity(deviceName: string) {
 setSaving(prev => ({ ...prev, [deviceName]: true }))

 try {
 const targetIntensity = parseFloat(inputValues[deviceName] ?? '0')

 if (isNaN(targetIntensity) || targetIntensity < 0 || targetIntensity > 100) {
 throw new Error('Intensity must be 0-100')
 }

 // Use lights target endpoint (POST .../target) - same path as LightSlidersPanel/VerticalLightsBlock;
 // updates SUN/DAY schedule by device and refreshes scheduler (fix from 6a9a6a5).
 const result = await apiClient.setLightIntensity(location, cluster, deviceName, targetIntensity)

 const savedIntensity =
 (result as { target_intensity?: number })?.target_intensity ??
 Math.round(targetIntensity)
 const normalizedValue = Math.min(100, Math.max(0, savedIntensity)).toString()

 setSchedules(prev => {
 const updated = { ...prev }
 if (updated[deviceName]) {
 updated[deviceName] = { ...updated[deviceName], target_intensity: savedIntensity }
 }
 return updated
 })
 setInputValues(prev => ({ ...prev, [deviceName]: normalizedValue }))
 setSavedValues(prev => ({ ...prev, [deviceName]: normalizedValue }))

 const refreshStatus = async () => {
 try {
 const status = await apiClient.getLightStatus(location, cluster, deviceName)
 setLightStatuses(prev => ({ ...prev, [deviceName]: status }))
 } catch {
 // Ignore
 }
 }
 setTimeout(refreshStatus, 500)
 
 /*
 setErrors(prev => {
 const newErrors = { ...prev }
 delete newErrors[deviceName]
 return newErrors
 })
 */
 } catch (error: any) {
 logger.error(`Error saving intensity for ${deviceName}:`, error)
 /*
 setErrors(prev => ({
 ...prev,
 [deviceName]: 'Save failed'
 }))
 */
 } finally {
 setSaving(prev => ({ ...prev, [deviceName]: false }))
 }
 }

 async function handleSaveAll() {
 const dimmableLights = lights.filter(l => l.dimming_enabled)
 const lightsToSave = dimmableLights.filter(light => {
 const currentValue = inputValues[light.device_name]
 const savedValue = savedValues[light.device_name]
 return currentValue !== undefined && currentValue !== savedValue
 })

 if (lightsToSave.length === 0) {
 return
 }

 setSavingAll(true)
 const savePromises = lightsToSave.map(light => handleSaveTargetIntensity(light.device_name))
 
 try {
 await Promise.all(savePromises)
 } catch (error) {
 logger.error('Error saving all lights:', error)
 } finally {
 setSavingAll(false)
 }
 }

 function hasUnsavedChanges(deviceName: string): boolean {
 const currentValue = inputValues[deviceName]
 const savedValue = savedValues[deviceName]
 const schedule = schedules[deviceName]
 
 if (currentValue === undefined || currentValue === '') {
 return false
 }
 
 const currentInt = parseInt(currentValue, 10)
 if (isNaN(currentInt)) {
 return false
 }
 
 if (schedule && schedule.target_intensity !== null && schedule.target_intensity !== undefined) {
 const scheduleInt = Math.round(Number(schedule.target_intensity))
 return currentInt !== scheduleInt
 }
 
 if (savedValue === undefined) {
 return false
 }
 
 const savedInt = parseInt(savedValue, 10)
 if (isNaN(savedInt)) {
 return false
 }
 
 return currentInt !== savedInt
 }

 function getUnsavedCount(): number {
 const dimmableLights = lights.filter(l => l.dimming_enabled)
 return dimmableLights.filter(light => hasUnsavedChanges(light.device_name)).length
 }

 const dimmableLights = lights.filter(l => l.dimming_enabled)

 if (dimmableLights.length === 0) {
 return (
 <div className="text-text-muted">
 No dimmable lights.
 </div>
 )
 }

 return (
 <div className="bg-surface-primary rounded-sm p-2 border border-border-subtle h-full">
 <div className="flex justify-between items-center mb-2">
 <div className="text-xs font-bold text-text-muted uppercase tracking-wider">
 LIGHTS
 </div>
 <button
 onClick={handleSaveAll}
 disabled={savingAll || getUnsavedCount() === 0}
 className="px-2 py-0.5 bg-accent-active hover:bg-accent-hover disabled:bg-surface-tertiary disabled:text-text-subtle disabled:cursor-not-allowed text-text-default text-[10px] font-bold rounded-sm transition-colors"
 >
 {savingAll ? '...' : `SAVE (${getUnsavedCount()})`}
 </button>
 </div>

 <div className="space-y-2">
 {dimmableLights.map((light) => {
 const status = lightStatuses[light.device_name]
 const displayName = light.display_name || light.device_name.replace('grow_light_', 'Light ')
 
 const schedule = schedules[light.device_name]
 const targetIntensity = schedule?.target_intensity ?? status?.target_intensity ?? 0
 const sliderValue = inputValues[light.device_name] ?? targetIntensity.toString()

 const relayState = deviceStates[light.device_name]
 const isRelayOn = relayState === 1
 
 return (
 <div key={light.device_name} className="flex items-center gap-2">
 {/* Name & Status */}
 <div className="w-16 shrink-0">
 <div className="text-xs text-text-secondary truncate font-medium">{displayName}</div>
 <div className="flex items-center gap-1">
 <div className={`w-1.5 h-1.5 rounded-full ${isRelayOn ? 'bg-status-success-vivid' : 'bg-surface-quaternary'}`}></div>
 <span className="text-[10px] text-text-subtle">{status ? `${status.intensity.toFixed(0)}%` : '-'}</span>
 </div>
 </div>

 {/* Slider Progress Bar Style */}
 <div className="flex-1 relative h-4 bg-surface-secondary rounded-sm overflow-hidden">
 <div 
 className="absolute top-0 left-0 bottom-0 bg-accent-dim"
 style={{ width: `${sliderValue}%` }}
 ></div>
 {/* Hatch pattern for intensity */}
 <div 
 className="absolute top-0 left-0 bottom-0 opacity-30"
 style={{ 
 width: `${sliderValue}%`,
 backgroundImage: 'linear-gradient(45deg,rgba(255,255,255,.15) 25%,transparent 25%,transparent 50%,rgba(255,255,255,.15) 50%,rgba(255,255,255,.15) 75%,transparent 75%,transparent)',
 backgroundSize: '1rem 1rem'
 }}
 ></div>
 
 <input
 type="range"
 min="0"
 max="100"
 step="1"
 value={sliderValue}
 onChange={(e) => setInputValues(prev => ({ ...prev, [light.device_name]: e.target.value }))}
 disabled={!schedule || saving[light.device_name]}
 className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
 />
 
 <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
 <span className="text-[10px] font-bold text-text-secondary shadow-black drop-shadow-md">{sliderValue}%</span>
 </div>
 </div>
 
 {/* State Indicator */}
 <div className="w-4 shrink-0 text-right">
 {hasUnsavedChanges(light.device_name) && (
 <span className="text-orange-400 text-[10px]">●</span>
 )}
 </div>
 </div>
 )
 })}
 </div>
 
 <div className="mt-2 pt-2 border-t border-border-subtle">
 <button 
 onClick={() => setShowSchedule(!showSchedule)}
 className="text-[10px] text-text-subtle hover:text-text-secondary flex items-center gap-1 w-full"
 >
 <span>{showSchedule ? '▼' : '▶'}</span> Schedule
 </button>
 
 {showSchedule && roomSchedule && (
 <div className="mt-2 p-2 bg-surface-secondary rounded-sm space-y-2">
 <div className="grid grid-cols-2 gap-2">
 <div>
 <label className="text-[10px] text-text-subtle block">Day Start</label>
 <input 
 type="time" 
 value={roomSchedule.day_start_time || ''} 
 onChange={e => setRoomSchedule({...roomSchedule, day_start_time: e.target.value})}
 className="w-full bg-surface-primary border border-border-default rounded-sm text-text-input text-xs px-1"
 />
 </div>
 <div>
 <label className="text-[10px] text-text-subtle block">Day End</label>
 <input 
 type="time" 
 value={roomSchedule.day_end_time || ''} 
 onChange={e => setRoomSchedule({...roomSchedule, day_end_time: e.target.value})}
 className="w-full bg-surface-primary border border-border-default rounded-sm text-text-input text-xs px-1"
 />
 </div>
 </div>
 <div className="grid grid-cols-2 gap-2">
 <div>
 <label className="text-[10px] text-text-subtle block">Ramp Up (m)</label>
 <input 
 type="number" 
 value={roomSchedule.ramp_up_duration || 0} 
 onChange={e => setRoomSchedule({...roomSchedule, ramp_up_duration: parseInt(e.target.value) || 0})}
 className="w-full bg-surface-primary border border-border-default rounded-sm text-text-input text-xs px-1"
 />
 </div>
 <div>
 <label className="text-[10px] text-text-subtle block">Ramp Down (m)</label>
 <input 
 type="number" 
 value={roomSchedule.ramp_down_duration || 0} 
 onChange={e => setRoomSchedule({...roomSchedule, ramp_down_duration: parseInt(e.target.value) || 0})}
 className="w-full bg-surface-primary border border-border-default rounded-sm text-text-input text-xs px-1"
 />
 </div>
 </div>
 <button 
 onClick={handleSaveRoomSchedule}
 disabled={roomScheduleSaving}
 className="w-full bg-btn-primary hover:bg-btn-primary-hover text-text-default text-xs font-bold py-1 rounded-sm disabled:opacity-50"
 >
 {roomScheduleSaving ? '...' : 'Update Schedule'}
 </button>
 </div>
 )}
 </div>
 </div>
 )
}

