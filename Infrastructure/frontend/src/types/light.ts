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
  scheduler_nominal_intensity?: number | null;
  /** Present on zone-status aggregate; optional on per-device `/status`. */
  day_target_intensity?: number | null;
  schedule_sun_target_intensity?: number | null;
  board_info?: {
    board_id: number;
    i2c_address: number;
    name?: string;
  };
}

/** Response from POST .../lights/.../target */
export interface LightTargetSetResponse {
  success: boolean;
  location: string;
  cluster: string;
  device: string;
  target_intensity: number;
  rows_updated?: number;
}

export interface LightDevice {
  device_id?: number;
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
  /** MCP23017 relay channel when bound; null when unbound. */
  bound_relay_channel?: number | null;
  /** 1-based index within the room (device_registry.per_room_index). */
  per_room_index?: number;
  /** DFR0971 board ID (mirrors dimming_board_id for registry responses). */
  board_id?: number;
  /** Safety intensity level (0-100%). */
  safety_level?: number;
}

