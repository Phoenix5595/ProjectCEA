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
  light_ramp_up_minutes: number
  light_ramp_down_minutes: number
  day_heat_temp: number
  day_cool_temp: number
  day_vpd: number
  day_co2: number
  day_leaf_delta: number
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
  mode_id: number | null
  submode_id: number | null
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
  light_ramp_up_minutes?: number
  light_ramp_down_minutes?: number
  day_heat_temp?: number
  day_cool_temp?: number
  day_vpd?: number
  day_co2?: number
  day_leaf_delta?: number
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
  sleep: 'bg-muted'
}

export const SUBMODE_COLORS: Record<string, string> = {
  stretch: 'bg-pink-500',
  bulk: 'bg-pink-600',
  ripen: 'bg-pink-700'
}
