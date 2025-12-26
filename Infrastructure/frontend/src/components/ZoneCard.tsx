import { Zone, getLocationDisplayName } from '../config/zones'
import type { SensorDataResponse } from '../types/sensor'
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

interface ZoneCardProps {
  zone: Zone
  sensorData: SensorDataResponse
  devices: Device[]
  schedule?: RoomSchedule
  setpoint?: Setpoint
}

export default function ZoneCard({ zone, sensorData, devices, schedule, setpoint }: ZoneCardProps) {
  // Extract latest sensor values
  const getLatestValue = (sensorName: string): number | null => {
    const sensor = sensorData[sensorName]
    if (!sensor || !sensor.data || sensor.data.length === 0) return null
    return sensor.data[sensor.data.length - 1].value
  }

  // Sensor name suffix based on location: Flower Room = 'f', Veg Room = 'v', Lab = 'l'
  // Note: Uses backend location name (not display name) for sensor suffix
  const getSensorSuffix = () => {
    if (zone.location === 'Flower Room') return 'f'
    if (zone.location === 'Veg Room') return 'v'  // Backend still uses "Veg Room"
    if (zone.location === 'Lab') return 'l'
    return 'v' // default
  }
  const suffix = getSensorSuffix()
  
  const temperature = getLatestValue(`dry_bulb_${suffix}`)
  const humidity = getLatestValue(`rh_${suffix}`)
  const co2 = getLatestValue(`co2_${suffix}`)
  const vpd = getLatestValue(`vpd_${suffix}`)

  // Count active devices
  const activeDevices = devices.filter(d => d.state === 1).length
  
  // Filter lights
  const lights = devices.filter(d => d.device_name?.startsWith('light_'))

  // Format zone title: hide "main" cluster suffix and use display name
  const displayLocation = getLocationDisplayName(zone.location)
  const zoneTitle = zone.cluster === 'main' ? displayLocation : `${displayLocation} - ${zone.cluster}`

  return (
    <div className="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow border border-gray-200">
      <h2 className="text-xl font-bold mb-4 text-gray-900">
        {zoneTitle}
      </h2>
      
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <div className="text-sm font-medium text-gray-700 mb-1">Temperature</div>
          <div className="text-2xl font-bold text-gray-900">{formatTemperature(temperature)}</div>
          {setpoint?.temperature !== null && setpoint?.temperature !== undefined && (
            <div className="text-xs text-gray-500 mt-1">Setpoint: {formatTemperature(setpoint.temperature)}</div>
          )}
        </div>
        <div>
          <div className="text-sm font-medium text-gray-700 mb-1">Humidity</div>
          <div className="text-2xl font-bold text-gray-900">{formatHumidity(humidity)}</div>
          {setpoint?.humidity !== null && setpoint?.humidity !== undefined && (
            <div className="text-xs text-gray-500 mt-1">Setpoint: {formatHumidity(setpoint.humidity)}</div>
          )}
        </div>
        <div>
          <div className="text-sm font-medium text-gray-700 mb-1">CO₂</div>
          <div className="text-2xl font-bold text-gray-900">{formatCO2(co2)}</div>
          {setpoint?.co2 !== null && setpoint?.co2 !== undefined && (
            <div className="text-xs text-gray-500 mt-1">Setpoint: {formatCO2(setpoint.co2)}</div>
          )}
        </div>
        <div>
          <div className="text-sm font-medium text-gray-700 mb-1">VPD</div>
          <div className="text-2xl font-bold text-gray-900">{formatVPD(vpd)}</div>
          {setpoint?.vpd !== null && setpoint?.vpd !== undefined && (
            <div className="text-xs text-gray-500 mt-1">Setpoint: {formatVPD(setpoint.vpd)}</div>
          )}
        </div>
      </div>

      {lights.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-200">
          <div className="text-sm font-medium text-gray-700 mb-2">Lights</div>
          <div className="space-y-1">
            {lights.map(light => (
              <div key={light.device_name} className="flex justify-between items-center text-sm">
                <span className="text-gray-600">{light.device_name}</span>
                <span className={`font-semibold ${light.state === 1 ? 'text-green-600' : 'text-gray-400'}`}>
                  {light.state === 1 ? 'ON' : 'OFF'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {schedule && (
        <div className="mt-4 pt-4 border-t border-gray-200">
          <div className="text-sm font-medium text-gray-700 mb-2">Schedule</div>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Day:</span>
              <span className="font-semibold text-gray-900">
                {schedule.day_start_time} - {schedule.day_end_time}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Night:</span>
              <span className="font-semibold text-gray-900">
                {schedule.night_start_time} - {schedule.night_end_time}
              </span>
            </div>
            {(schedule.ramp_up_duration || schedule.ramp_down_duration) && (
              <div className="flex justify-between mt-2 pt-2 border-t border-gray-100">
                <span className="text-gray-600">Ramp:</span>
                <span className="text-gray-900">
                  Up: {schedule.ramp_up_duration || 0}min, Down: {schedule.ramp_down_duration || 0}min
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mt-4 pt-4 border-t border-gray-200">
        <div className="text-sm font-medium text-gray-700">
          Active Devices: <span className="font-bold text-gray-900">{activeDevices}</span> / <span className="text-gray-900">{devices.length}</span>
        </div>
      </div>
    </div>
  )
}

