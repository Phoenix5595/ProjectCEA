import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
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
 leaf_delta_day: number
 leaf_delta_night: number
 setpoints: {
 DAY?: any
 NIGHT?: any
 PRE_DAY?: any
 PRE_NIGHT?: any
 }
}


export function ScheduleCard({ 
 schedule, 
 onUpdate 
}: { 
 schedule: any; 
 onUpdate: (updates: any) => void 
}) {
 return (
 <div className="bg-surface-primary rounded-sm p-2 h-full border border-border-subtle">
 <div className="text-xs text-text-muted uppercase mb-2 font-bold tracking-wider">Schedule</div>
 <div className="space-y-2">
 <div className="grid grid-cols-2 gap-2">
 <div>
 <label className="text-[10px] text-text-subtle uppercase block mb-0.5">☀️ Day Start</label>
 <input 
 type="time" 
 value={schedule.day_start_time}
 onChange={(e) => onUpdate({ day_start_time: e.target.value })}
 className="w-full h-7 px-2 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid"
 />
 </div>
 <div>
 <label className="text-[10px] text-text-subtle uppercase block mb-0.5">🌙 Night Start</label>
 <input 
 type="time" 
 value={schedule.day_end_time}
 onChange={(e) => onUpdate({ day_end_time: e.target.value })}
 className="w-full h-7 px-2 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid"
 />
 </div>
 </div>
 <div className="grid grid-cols-2 gap-2">
 <div>
 <label className="text-[10px] text-text-subtle uppercase block mb-0.5">⏱️ Pre (min)</label>
 <div className="flex gap-1">
 <input
 type="number"
 min="0"
 max="120"
 value={schedule.pre_day_duration}
 onChange={(e) => onUpdate({ pre_day_duration: parseInt(e.target.value) || 0 })}
 className="w-full h-7 px-1 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input text-center"
 title="Pre-Day"
 />
 <input
 type="number"
 min="0"
 max="120"
 value={schedule.pre_night_duration}
 onChange={(e) => onUpdate({ pre_night_duration: parseInt(e.target.value) || 0 })}
 className="w-full h-7 px-1 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input text-center"
 title="Pre-Night"
 />
 </div>
 </div>
 <div>
 <label className="text-[10px] text-text-subtle uppercase block mb-0.5">� Δ (°C)</label>
 <div className="flex gap-1">
 <input
 type="number"
 step="0.1"
 value={schedule.leaf_delta_day ?? -2.0}
 onChange={(e) => onUpdate({ leaf_delta_day: parseFloat(e.target.value) || 0 })}
 className="w-full h-7 px-1 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input text-center"
 title="Day Delta"
 />
 <input
 type="number"
 step="0.1"
 value={schedule.leaf_delta_night ?? -1.0}
 onChange={(e) => onUpdate({ leaf_delta_night: parseFloat(e.target.value) || 0 })}
 className="w-full h-7 px-1 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input text-center"
 title="Night Delta"
 />
 </div>
 </div>
 </div>
 </div>
 </div>
 )
}

export function SetpointCard({ 
 mode, 
 setpoint, 
 currentSetpoint, 
 onChange, 
 title 
}: { 
 mode: string; 
 setpoint: any; 
 currentSetpoint?: any; 
 onChange: (data: any) => void;
 title?: string;
}) {
 function handleChange(field: string, value: any) {
 onChange({ ...setpoint, [field]: value })
 }

 const inputClass = "w-full h-7 px-2 text-sm bg-surface-secondary border border-border-default rounded-sm text-text-input focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid"
 const labelClass = "text-[10px] text-text-subtle uppercase flex items-center gap-1"

 return (
 <div className="bg-surface-primary rounded-sm p-2 h-full border border-border-subtle">
 <div className="text-xs text-text-muted uppercase mb-2 font-bold tracking-wider flex justify-between items-center">
 <span>{title || mode}</span>
 {setpoint.ramp_in_duration > 0 && (
 <span className="text-[10px] bg-surface-secondary px-1 rounded-sm text-accent-data">Ramp: {setpoint.ramp_in_duration}m</span>
 )}
 </div>
 <div className="grid grid-cols-2 gap-2">
 <div className="space-y-1">
 <div className="flex justify-between items-center">
 <label className={labelClass}>🌡️ Heat</label>
 {currentSetpoint?.heating_setpoint !== undefined && <span className="text-[10px] text-text-faint">{currentSetpoint.heating_setpoint}</span>}
 </div>
 <input
 type="number"
 step="0.1"
 value={setpoint.heating_setpoint ?? ''}
 onChange={(e) => handleChange('heating_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
 className={`${inputClass} text-orange-200`}
 />
 </div>
 <div className="space-y-1">
 <div className="flex justify-between items-center">
 <label className={labelClass}>❄️ Cool</label>
 {currentSetpoint?.cooling_setpoint !== undefined && <span className="text-[10px] text-text-faint">{currentSetpoint.cooling_setpoint}</span>}
 </div>
 <input
 type="number"
 step="0.1"
 value={setpoint.cooling_setpoint ?? ''}
 onChange={(e) => handleChange('cooling_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
 className={`${inputClass} text-btn-primary-info`}
 />
 </div>
 <div className="space-y-1">
 <div className="flex justify-between items-center">
 <label className={labelClass}>💧 VPD</label>
 {currentSetpoint?.vpd !== undefined && <span className="text-[10px] text-text-faint">{currentSetpoint.vpd}</span>}
 </div>
 <input
 type="number"
 step="0.01"
 value={setpoint.vpd ?? ''}
 onChange={(e) => handleChange('vpd', e.target.value ? parseFloat(e.target.value) : null)}
 className={`${inputClass} text-emerald-200`}
 />
 </div>
 <div className="space-y-1">
 <div className="flex justify-between items-center">
 <label className={labelClass}>🌬️ CO2</label>
 {currentSetpoint?.co2 !== undefined && <span className="text-[10px] text-text-faint">{currentSetpoint.co2}</span>}
 </div>
 <input
 type="number"
 step="1"
 value={setpoint.co2 ?? ''}
 onChange={(e) => handleChange('co2', e.target.value ? parseFloat(e.target.value) : null)}
 className={`${inputClass} text-mode-auto-text`}
 />
 </div>
 </div>
 </div>
 )
}


export default function ClimateScheduleEditor({ location, cluster }: ClimateScheduleEditorProps) {
 const [schedule, setSchedule] = useState<ClimateSchedule | null>(null)
 const [lightSchedule, setLightSchedule] = useState<any>(null)
 const [currentSetpoints, setCurrentSetpoints] = useState<Record<string, any>>({})
 const [currentSchedule, setCurrentSchedule] = useState<ClimateSchedule | null>(null)
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
 const climateData = await apiClient.getClimateSchedule(location, cluster)
 
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
 logger.warn('Error loading current setpoints:', err)
 }
 
 const currentPreDay = climateData.pre_day_duration !== undefined && climateData.pre_day_duration !== null ? climateData.pre_day_duration : 0
 const currentPreNight = climateData.pre_night_duration !== undefined && climateData.pre_night_duration !== null ? climateData.pre_night_duration : 0
 
 setCurrentSchedule({
 day_start_time: climateData.day_start_time,
 day_end_time: climateData.day_end_time,
 pre_day_duration: currentPreDay,
 pre_night_duration: currentPreNight,
 leaf_delta_day: climateData.leaf_delta_day ?? -2.0,
 leaf_delta_night: climateData.leaf_delta_night ?? -1.0,
 setpoints: {
 DAY: { ...(climateData.setpoints?.DAY || {}) },
 NIGHT: { ...(climateData.setpoints?.NIGHT || {}) },
 PRE_DAY: { ...(climateData.setpoints?.PRE_DAY || {}) },
 PRE_NIGHT: { ...(climateData.setpoints?.PRE_NIGHT || {}) }
 }
 })
 
 const setpoints = {
 DAY: { ...setpointsMap.DAY, ...(climateData.setpoints?.DAY || {}) },
 NIGHT: { ...setpointsMap.NIGHT, ...(climateData.setpoints?.NIGHT || {}) },
 PRE_DAY: { ...setpointsMap.PRE_DAY, ...(climateData.setpoints?.PRE_DAY || {}) },
 PRE_NIGHT: { ...setpointsMap.PRE_NIGHT, ...(climateData.setpoints?.PRE_NIGHT || {}) }
 }
 
 setSchedule({
 ...climateData,
 leaf_delta_day: climateData.leaf_delta_day ?? -2.0,
 leaf_delta_night: climateData.leaf_delta_night ?? -1.0,
 setpoints
 })

 const lightData = await apiClient.getRoomSchedule(location, cluster)
 setLightSchedule(lightData)
 } catch (err: any) {
 logger.error('Error loading climate schedule:', err)
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
 setSuccess('Saved')
 if (result.warnings && result.warnings.length > 0) {
 setError(result.warnings.join('; '))
 }
 setTimeout(() => setSuccess(null), 2000)
 await loadData()
 } catch (err: any) {
 logger.error('Error saving climate schedule:', err)
 setError(err.response?.data?.detail || err.message || 'Failed to save')
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
 return <div className="text-text-input p-4">Loading climate schedule...</div>
 }

 if (!schedule) {
 return <div className="text-text-input p-4">Failed to load climate schedule</div>
 }

 return (
 <div className="flex flex-col h-full p-2 gap-2">
 {/* Timeline Section */}
 <div className="relative h-20 w-full bg-surface-primary border border-border-subtle rounded-lg overflow-hidden shrink-0">
 <SetpointTimeline
 dayStartTime={schedule.day_start_time}
 dayEndTime={schedule.day_end_time}
 preDayDuration={schedule.pre_day_duration}
 preNightDuration={schedule.pre_night_duration}
 currentPreDayDuration={currentSchedule?.pre_day_duration !== undefined ? currentSchedule.pre_day_duration : schedule.pre_day_duration}
 currentPreNightDuration={currentSchedule?.pre_night_duration !== undefined ? currentSchedule.pre_night_duration : schedule.pre_night_duration}
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
 compact={true}
 className="h-full"
 />
 </div>

 {/* Editor Grid */}
 <div className="flex-1 min-h-0 flex flex-col gap-2">
 <div className="flex justify-between items-center px-1">
 <div className="flex-1"></div>
 {(error || success) && (
 <div className={`text-xs px-2 py-0.5 rounded-sm mr-2 ${error ? 'bg-status-danger-bg text-status-danger-text' : 'bg-status-success-bg text-status-success-text'}`}>
 {error || success}
 </div>
 )}
 <button
 onClick={handleSave}
 disabled={saving}
 className="px-3 py-1 bg-accent-active hover:bg-accent-hover disabled:opacity-50 text-text-default text-xs font-bold rounded-sm shadow-xs transition-colors"
 >
 {saving ? '...' : 'SAVE'}
 </button>
 </div>

 <div className="grid grid-cols-3 gap-2 flex-1 min-h-0">
 <ScheduleCard schedule={schedule} onUpdate={handleScheduleChange} />
 
 <SetpointCard 
 mode="DAY" 
 title="☀️ DAY SETPOINTS"
 setpoint={schedule.setpoints.DAY || {}} 
 currentSetpoint={{ ...currentSetpoints.DAY, ...(currentSchedule?.setpoints?.DAY || {}) }}
 onChange={(data) => handleSetpointChange('DAY', data)}
 />
 
 <SetpointCard 
 mode="NIGHT" 
 title="🌙 NIGHT SETPOINTS"
 setpoint={schedule.setpoints.NIGHT || {}} 
 currentSetpoint={{ ...currentSetpoints.NIGHT, ...(currentSchedule?.setpoints?.NIGHT || {}) }}
 onChange={(data) => handleSetpointChange('NIGHT', data)}
 />
 </div>
 </div>
 </div>
 )
}
// Remove old components no longer used in this file
// SetpointModeEditor is replaced by SetpointCard


