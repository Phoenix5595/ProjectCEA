/** Types for PID parameters and control modes. */

/** PID control modes */
export type PIDControlMode = 'auto_pid' | 'pid' | 'on_off';

/** Autotune status values */
export type AutotuneStatus = 'idle' | 'running' | 'calculating' | 'complete' | 'error';

export interface PIDParameters {
  kp: number;
  ki: number;
  kd: number;
  updated_at?: string;
  updated_by?: string;
  source?: string;
  control_mode?: PIDControlMode;
  hysteresis_high?: number;
  hysteresis_low?: number;
}

export interface PIDParameterUpdate {
  kp?: number;
  ki?: number;
  kd?: number;
  source?: string;
  updated_by?: string;
}

/** PID mode info returned from API */
export interface PIDModeInfo {
  device_type: string;
  mode: PIDControlMode;
  hysteresis_high: number;
  hysteresis_low: number;
  autotune_active: boolean;
  updated_at?: string;
}

/** Request to update PID mode */
export interface PIDModeUpdate {
  mode: PIDControlMode;
  hysteresis_high?: number;
  hysteresis_low?: number;
  updated_by?: string;
}

/** Autotune state from API */
export interface AutotuneState {
  device_type: string;
  is_active: boolean;
  status: AutotuneStatus;
  cycles_completed: number;
  estimated_remaining_cycles: number;
  current_ku?: number;
  current_tu?: number;
  suggested_kp?: number;
  suggested_ki?: number;
  suggested_kd?: number;
  last_change_reason?: string;
}

/** Notification when PID parameters change (for WebSocket) */
export interface PIDChangeNotification {
  device_type: string;
  old_values: { kp: number; ki: number; kd: number };
  new_values: { kp: number; ki: number; kd: number };
  reason: string;
  timestamp: string;
}

/** PID parameter history entry */
export interface PIDHistoryEntry {
  device_type: string;
  old_values: { kp: number; ki: number; kd: number };
  new_values: { kp: number; ki: number; kd: number };
  reason: string;
  timestamp: string;
  tuning_metrics?: { ku?: number; tu?: number; method?: string };
}

