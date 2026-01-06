/** Types for light device data. */

export interface LightStatus {
  location: string;
  cluster: string;
  device: string;
  intensity: number;  // 0-100% (current intensity, changes during ramp)
  voltage: number;    // 0-10V
  board_id: number;
  channel: number;
  target_intensity?: number | null;  // 0-100% (target intensity from schedule, max for the day)
  board_info?: {
    board_id: number;
    i2c_address: number;
    name?: string;
  };
}

export interface LightDevice {
  location: string;
  cluster: string;
  device_name: string;
  display_name?: string;
  state: number;  // 0 = OFF, 1 = ON
  mode: string;
  channel: number;
  dimming_enabled?: boolean;
  dimming_type?: string;
  dimming_board_id?: number;
  dimming_channel?: number;
}

