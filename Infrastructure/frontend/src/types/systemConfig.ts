/** TypeScript types for the system configuration API (GET/PUT /api/config, POST /api/config/restart). */

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

/** PID limit pair for a single device type (matches backend PidLimitsPair schema). */
export interface PidLimitsPair {
  kp_min: number
  kp_max: number
  ki_min: number
  ki_max: number
  kd_min: number
  kd_max: number
}

/** PID limits for all device types (matches backend PidLimitsGroup schema). */
export interface PidLimitsGroup {
  heater?: PidLimitsPair | null
  fan?: PidLimitsPair | null
  co2?: PidLimitsPair | null
}

/** Safety limits group (matches backend SafetyLimitsGroup schema). */
export interface SafetyLimitsConfig {
  min_temperature?: number | null
  max_temperature?: number | null
  min_humidity?: number | null
  max_humidity?: number | null
  min_co2?: number | null
  max_co2?: number | null
}

/** Tuning parameters (matches backend TuningGroup schema, includes pid_limits). */
export interface TuningConfig {
  update_interval?: number | null
  last_good_hold_period?: number | null
  binary_hysteresis?: number | null
  pid_limits?: PidLimitsGroup | null
}

/** GET /api/config response shape. */
export interface SystemConfigResponse {
  hardware: HardwareConfig
  safety_limits: SafetyLimitsConfig
  /** Tuning fields WITHOUT pid_limits (pid_limits is a separate top-level key in GET). */
  tuning: {
    update_interval?: number | null
    last_good_hold_period?: number | null
    binary_hysteresis?: number | null
  }
  pid_limits: PidLimitsGroup
  pending_restart_required_changes: string[]
  restart_hashes: {
    current: string | null
    sidecar: string | null
  }
}

/** PUT /api/config request body (ConfigUpdateRequest). tuning includes pid_limits here. */
export interface ConfigUpdateRequest {
  hardware?: HardwareConfig | null
  safety_limits?: SafetyLimitsConfig | null
  tuning?: TuningConfig | null
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
