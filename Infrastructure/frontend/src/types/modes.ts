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
  light_ramp_up_minutes: number
  light_ramp_down_minutes: number
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

/** Resolved mode/submode identity pair used for selection and running state. */
export type ModeProfileIdentity = {
  readonly modeId: number
  readonly submodeId: number | null
  readonly modeName: string
  readonly submodeName: string | null
}

export interface SetModeRequest {
  mode_name: string
  submode_name?: string
  /** Optional 409 guard: the activation applies only from this config revision. */
  expected_config_revision?: string
}

export interface UpdateParametersRequest {
  day_start_time?: string
  night_start_time?: string
  light_ramp_up_minutes?: number
  light_ramp_down_minutes?: number
  main_light_intensity?: number
  supplemental_light_intensity?: number
}

/** Committed activation response: ModeActivationResponse + runtime metadata. */
export interface ModeActivationResponse extends RoomModeWithParams {
  /** Config revision the activation was committed with. */
  config_revision: string
  /** True when the runtime registry accepted the new snapshot install. */
  runtime_ready: boolean
  /** Explicit post-commit refresh warning; never implies a rollback. */
  warning?: string | null
}

/** Committed direct parameter update: ModeParametersUpdateResponse. */
export interface ModeParametersUpdateResponse extends RoomModeWithParams {
  config_revision: string
  notification_warning?: string | null
}

export const MODE_DISPLAY_NAMES: Record<string, string> = {
  veg: 'Veg',
  flower: 'Flower',
  drying: 'Drying',
  sleep: 'Sleep',
}

export const SUBMODE_DISPLAY_NAMES: Record<string, string> = {
  stretch: 'Stretch',
  bulk: 'Bulk',
  ripen: 'Ripen',
}

export const MODE_COLORS: Record<string, string> = {
  veg: 'bg-emerald-600',
  flower: 'bg-pink-600',
  drying: 'bg-amber-600',
  sleep: 'bg-muted',
}

export const SUBMODE_COLORS: Record<string, string> = {
  stretch: 'bg-pink-500',
  bulk: 'bg-pink-600',
  ripen: 'bg-pink-700',
}
