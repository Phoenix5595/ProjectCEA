/** Types for PID parameters. */

export interface PIDParameters {
  kp: number;
  ki: number;
  kd: number;
  updated_at?: string;
  updated_by?: string;
  source?: string;
}

export interface PIDParameterUpdate {
  kp?: number;
  ki?: number;
  kd?: number;
  source?: string;
  updated_by?: string;
}

