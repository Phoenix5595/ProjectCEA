export interface RoomMode {
  id: number
  name: string
  description?: string
  photoperiod_hours?: number
  is_constant: boolean
}

export interface FlowerSubmode {
  id: number
  name: string
  description?: string
  week_start?: number
  week_end?: number
}

export interface ModeParameters {
  day_start_time: string
  night_start_time: string
  ramp_up_minutes: number
  ramp_down_minutes: number
  pre_day_ramp_minutes: number
  pre_night_ramp_minutes: number
  pre_day_minutes: number
  pre_night_minutes: number
  light_ramp_up_minutes: number
  light_ramp_down_minutes: number
  pre_day_heat_temp: number
  pre_day_cool_temp: number
  pre_day_vpd: number
  pre_day_co2: number
  day_heat_temp: number
  day_cool_temp: number
  day_vpd: number
  day_co2: number
  day_leaf_delta: number
  pre_night_heat_temp: number
  pre_night_cool_temp: number
  pre_night_vpd: number
  pre_night_co2: number
  night_heat_temp: number
  night_cool_temp: number
  night_vpd: number
  night_co2: number
  night_leaf_delta: number
  main_light_intensity: number
  supplemental_light_intensity: number
}

export interface RoomModeWithParams {
  location: string
  cluster: string
  mode_name: string
  submode_name?: string
  is_constant: boolean
  parameters: ModeParameters
}

export interface SetModeRequest {
  mode_name: string
  submode_name?: string
}

export interface UpdateParametersRequest {
  day_start_time?: string
  night_start_time?: string
  ramp_up_minutes?: number
  ramp_down_minutes?: number
  pre_day_ramp_minutes?: number
  pre_night_ramp_minutes?: number
  pre_day_minutes?: number
  pre_night_minutes?: number
  light_ramp_up_minutes?: number
  light_ramp_down_minutes?: number
  pre_day_heat_temp?: number
  pre_day_cool_temp?: number
  pre_day_vpd?: number
  pre_day_co2?: number
  day_heat_temp?: number
  day_cool_temp?: number
  day_vpd?: number
  day_co2?: number
  day_leaf_delta?: number
  pre_night_heat_temp?: number
  pre_night_cool_temp?: number
  pre_night_vpd?: number
  pre_night_co2?: number
  night_heat_temp?: number
  night_cool_temp?: number
  night_vpd?: number
  night_co2?: number
  night_leaf_delta?: number
  main_light_intensity?: number
  supplemental_light_intensity?: number
}

export const MODE_DISPLAY_NAMES: Record<string, string> = {
  veg: 'Veg',
  flower: 'Flower',
  drying: 'Drying',
  sleep: 'Sleep'
}

export const SUBMODE_DISPLAY_NAMES: Record<string, string> = {
  stretch: 'Stretch',
  bulk: 'Bulk',
  ripen: 'Ripen'
}

export const MODE_COLORS: Record<string, string> = {
  veg: 'bg-emerald-600',
  flower: 'bg-pink-600',
  drying: 'bg-amber-600',
  sleep: 'bg-gray-600'
}

export const SUBMODE_COLORS: Record<string, string> = {
  stretch: 'bg-pink-500',
  bulk: 'bg-pink-600',
  ripen: 'bg-pink-700'
}
