import { Zone, getLocationDisplayName } from '../config/zones'
import type { Device } from '../types/device'
import type { Setpoint } from '../types/setpoint'
import { formatTemperature, formatHumidity, formatCO2, formatVPD } from '../utils/formatters'

interface RoomSchedule {
 day_start_time: string
 day_end_time: string
 night_start_time: string
 night_end_time: string
 ramp_up_duration: number | null
 ramp_down_duration: number | null
}

interface ZoneSetpoints {
 day?: Setpoint
 night?: Setpoint
}

interface ZoneCardProps {
 zone: Zone
 sensorData?: Record<string, number>
 devices: Device[]
 schedule?: RoomSchedule
 setpoints?: ZoneSetpoints
}

export default function ZoneCard({ zone, devices, schedule, setpoints }: ZoneCardProps) {
 // Count active devices
 const activeDevices = devices.filter(d => d.state === 1).length
 
 // Filter lights
 const lights = devices.filter(d => d.device_name?.startsWith('light_'))

 // Format zone title: hide "main" cluster suffix and use display name
 const displayLocation = getLocationDisplayName(zone.location)
 const zoneTitle = zone.cluster === 'main' ? displayLocation : `${displayLocation} - ${zone.cluster}`

 return (
 <div className="bg-surface-primary rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow border border-border-subtle">
 <h2 className="text-xl font-bold mb-4 text-text-default">
 {zoneTitle}
 </h2>
 
 {setpoints && (setpoints.day || setpoints.night) && (
 <div className="mb-4">
 <div className="text-sm font-semibold text-text-secondary mb-3">Setpoints</div>
 
 {setpoints.day && (
 <div className="mb-4 pb-3 border-b border-border-subtle">
 <div className="text-xs font-medium text-btn-primary-data mb-2">DAY</div>
 <div className="grid grid-cols-2 gap-3">
 {setpoints.day.heating_setpoint !== null && setpoints.day.heating_setpoint !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Heating Setpoint</div>
 <div className="text-lg font-bold text-text-default">{formatTemperature(setpoints.day.heating_setpoint)}</div>
 </div>
 )}
 {setpoints.day.cooling_setpoint !== null && setpoints.day.cooling_setpoint !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Cooling Setpoint</div>
 <div className="text-lg font-bold text-text-default">{formatTemperature(setpoints.day.cooling_setpoint)}</div>
 </div>
 )}
 {setpoints.day.humidity !== null && setpoints.day.humidity !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Humidity</div>
 <div className="text-lg font-bold text-text-default">{formatHumidity(setpoints.day.humidity)}</div>
 </div>
 )}
 {setpoints.day.co2 !== null && setpoints.day.co2 !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">CO₂</div>
 <div className="text-lg font-bold text-text-default">{formatCO2(setpoints.day.co2)}</div>
 </div>
 )}
 {setpoints.day.vpd !== null && setpoints.day.vpd !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">VPD</div>
 <div className="text-lg font-bold text-text-default">{formatVPD(setpoints.day.vpd)}</div>
 </div>
 )}
 </div>
 </div>
 )}
 
 {setpoints.night && (
 <div>
 <div className="text-xs font-medium text-indigo-400 mb-2">NIGHT</div>
 <div className="grid grid-cols-2 gap-3">
 {setpoints.night.heating_setpoint !== null && setpoints.night.heating_setpoint !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Heating Setpoint</div>
 <div className="text-lg font-bold text-text-default">{formatTemperature(setpoints.night.heating_setpoint)}</div>
 </div>
 )}
 {setpoints.night.cooling_setpoint !== null && setpoints.night.cooling_setpoint !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Cooling Setpoint</div>
 <div className="text-lg font-bold text-text-default">{formatTemperature(setpoints.night.cooling_setpoint)}</div>
 </div>
 )}
 {setpoints.night.humidity !== null && setpoints.night.humidity !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">Humidity</div>
 <div className="text-lg font-bold text-text-default">{formatHumidity(setpoints.night.humidity)}</div>
 </div>
 )}
 {setpoints.night.co2 !== null && setpoints.night.co2 !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">CO₂</div>
 <div className="text-lg font-bold text-text-default">{formatCO2(setpoints.night.co2)}</div>
 </div>
 )}
 {setpoints.night.vpd !== null && setpoints.night.vpd !== undefined && (
 <div>
 <div className="text-xs text-text-muted mb-1">VPD</div>
 <div className="text-lg font-bold text-text-default">{formatVPD(setpoints.night.vpd)}</div>
 </div>
 )}
 </div>
 </div>
 )}
 </div>
 )}

 {lights.length > 0 && (
 <div className="mt-4 pt-4 border-t border-border-subtle">
 <div className="text-sm font-medium text-text-secondary mb-2">Lights</div>
 <div className="space-y-1">
 {lights.map(light => (
 <div key={light.device_name} className="flex justify-between items-center text-sm">
 <span className="text-text-muted">{light.device_name}</span>
 <span className={`font-semibold ${light.state === 1 ? 'text-status-success' : 'text-text-subtle'}`}>
 {light.state === 1 ? 'Sun' : 'Moon'}
 </span>
 </div>
 ))}
 </div>
 </div>
 )}

 {schedule && (
 <div className="mt-4 pt-4 border-t border-border-subtle">
 <div className="text-sm font-medium text-text-secondary mb-2">Schedule</div>
 <div className="space-y-1 text-sm">
 <div className="flex justify-between">
 <span className="text-text-muted">Day:</span>
 <span className="font-semibold text-text-default">
 {schedule.day_start_time} - {schedule.day_end_time}
 </span>
 </div>
 <div className="flex justify-between">
 <span className="text-text-muted">Night:</span>
 <span className="font-semibold text-text-default">
 {schedule.night_start_time} - {schedule.night_end_time}
 </span>
 </div>
 {(schedule.ramp_up_duration || schedule.ramp_down_duration) && (
 <div className="flex justify-between mt-2 pt-2 border-t border-border-subtle">
 <span className="text-text-muted">Ramp:</span>
 <span className="text-text-default">
 Up: {schedule.ramp_up_duration || 0}min, Down: {schedule.ramp_down_duration || 0}min
 </span>
 </div>
 )}
 </div>
 </div>
 )}

 <div className="mt-4 pt-4 border-t border-border-subtle">
 <div className="text-sm font-medium text-text-secondary">
 Active Devices: <span className="font-bold text-text-default">{activeDevices}</span> / <span className="text-text-default">{devices.length}</span>
 </div>
 </div>
 </div>
 )
}

