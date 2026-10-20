/** Types for hardware configuration and the settings API contracts. */

/** DFR0971 board entry in hardware config. */
export interface DfrBoardConfig {
  board_id: number
  i2c_address: number
  name: string
}

/** Hardware configuration group (matches backend HardwareGroup schema). */
export interface HardwareConfig {
  i2c_bus?: number | null
  mcp_i2c_bus?: number | null
  dfr0971_i2c_bus?: number | null
  i2c_address?: number | null
  simulation?: boolean | null
  active_low?: boolean | null
  require_mcp?: boolean | null
  dfr0971_boards?: DfrBoardConfig[] | null
}

/** PID limit pair for a single device type. */
export interface PidLimitsPair {
  kp_min: number
  kp_max: number
  ki_min: number
  ki_max: number
  kd_min: number
  kd_max: number
}

/** PID limits for all device types. */
export interface PidLimitsGroup {
  heater?: PidLimitsPair | null
  fan?: PidLimitsPair | null
  co2?: PidLimitsPair | null
}

/** Safety limits settings value. */
export interface SafetyLimitsConfig {
  min_temperature?: number | null
  max_temperature?: number | null
  min_humidity?: number | null
  max_humidity?: number | null
  min_co2?: number | null
  max_co2?: number | null
}

export interface PidDefault {
  kp: number
  ki: number
  kd: number
}

export interface AutomationSettingsValues {
  update_interval: number
  last_good_hold_period: number
  binary_hysteresis: number
  pid_limits: PidLimitsGroup
  pid_defaults: Record<string, PidDefault>
  safety_limits: SafetyLimitsConfig
}

export interface SoilSettingsValues {
  poll_interval_seconds: number
  discovery_interval_seconds: number
}

export interface OnewireSettingsValues {
  poll_interval_seconds: number
}

export interface WeatherSettingsValues {
  station_icao: string
  poll_interval_seconds: number
  api_url: string
  room_name: string
  device_name: string
}

export interface SettingsServiceValues {
  automation: AutomationSettingsValues
  soil: SoilSettingsValues
  onewire: OnewireSettingsValues
  weather: WeatherSettingsValues
}

export type SettingsServiceName = keyof SettingsServiceValues

export interface SettingSchema {
  type: string
  minimum: number | null
  maximum: number | null
  pattern: string | null
  read_only: boolean
}

export interface SettingRecord<T> {
  value: T
  updated_at: string | null
  updated_by: string | null
}

export interface ServiceSettings<T extends object> {
  settings: { [K in keyof T]: SettingRecord<T[K]> }
  schema: { [K in keyof T]: SettingSchema }
}

export interface SettingsResponse {
  services: {
    [K in SettingsServiceName]: ServiceSettings<SettingsServiceValues[K]>
  }
}

export type SettingsUpdateRequest<T extends SettingsServiceName> =
  Partial<SettingsServiceValues[T]>

/** GET /api/config response (hardware-only after settings API cutover). */
export interface SystemConfigResponse {
  hardware: HardwareConfig
  pending_restart_required_changes: string[]
  restart_hashes: {
    current: string | null
    sidecar: string | null
  }
}

/** PUT /api/config request body (hardware only). */
export interface ConfigUpdateRequest {
  hardware?: HardwareConfig | null
}

/** PUT /api/config response shape. */
export interface ConfigUpdateResponse {
  pending_restart_required_changes: string[]
  restart_hashes: {
    current: string
    sidecar: string | null
  }
}

/** POST /api/config/restart response shape. */
export interface RestartServiceResponse {
  status: string
  delay_seconds: number
  command: string
}
