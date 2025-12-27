/** Types for setpoint data. */

export type Mode = 'DAY' | 'NIGHT' | 'TRANSITION' | null;

export interface Setpoint {
  heating_setpoint?: number;
  cooling_setpoint?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  mode?: Mode;
}

export interface SetpointUpdate {
  heating_setpoint?: number;
  cooling_setpoint?: number;
  humidity?: number;
  co2?: number;
  vpd?: number;
  mode?: Mode;
}

