/** Types for setpoint data. */

export type Mode = 'DAY' | 'NIGHT' | 'TRANSITION' | null;

export interface Setpoint {
  temperature?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  mode?: Mode;
}

export interface SetpointUpdate {
  temperature?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  mode?: Mode;
}

