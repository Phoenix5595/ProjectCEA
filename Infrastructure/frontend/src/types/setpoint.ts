/** Types for setpoint data. */

export type Mode = 'DAY' | 'NIGHT' | 'TRANSITION' | null;

export interface Setpoint {
  heating_setpoint?: number;
  cooling_setpoint?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  mode?: Mode;
  updated_at?: string;  // ISO format timestamp for version tracking
}

export interface SetpointUpdate {
  heating_setpoint?: number;
  cooling_setpoint?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  ramp_in_duration?: number;
  mode?: Mode;
  expected_version?: string | null;  // ISO format timestamp for optimistic locking
}

