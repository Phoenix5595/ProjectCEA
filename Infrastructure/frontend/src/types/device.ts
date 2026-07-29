/** Types for device data. */

export interface Device {
  location: string;
  cluster: string;
  device_name: string;
  state: number;  // 0 = OFF, 1 = ON
  mode: string;   // 'auto', 'manual', 'scheduled'
  channel: number | null;
  load_percent?: number;
}

export interface DeviceState {
  state: number;
  mode: string;
  channel: number | null;
  load_percent?: number;
}

/**
 * A row from GET /api/devices/registry — the unified flat list of all
 * devices (light and non-light) backed by the device_registry table.
 *
 * The backend returns a union of `Device` (non-light) and `LightDevice`
 * (light) Pydantic models. Non-lights carry `channel` (relay channel
 * 0-15); lights carry `relay_channel` (nullable), `board_id`, and
 * `dimming_channel` instead. Both `channel` and `relay_channel` are
 * optional here so the type accurately describes either JSON shape.
 */
export interface DeviceRegistryEntry {
  device_id: number;
  device_type: string;
  device_name: string;
  display_name: string | null;
  location: string;
  cluster: string;
  /** MCP23017 relay channel (0-15) for non-light devices. */
  channel?: number | null;
  /** MCP23017 relay channel when bound, for light devices. */
  relay_channel?: number | null;
  /** DFR0971 board ID (0, 1, 2) — lights only. */
  board_id?: number | null;
  /** DFR0971 channel on the board (0 or 1) — lights only. */
  dimming_channel?: number | null;
  /** 1-based index within the room — lights only. */
  per_room_index?: number | null;
  /** PID control enabled — non-lights only. */
  pid_enabled?: boolean;
  /** Devices to interlock with — non-lights only. */
  interlock_with?: string[];
  /** PID setpoint priorities — non-lights only. */
  pid_setpoints?: Record<string, number>;
}

/** Control history entry (recent on/off log). */
export interface ControlHistoryEntry {
  timestamp: string;
  location?: string;
  cluster?: string;
  channel?: number | null;
  device_name: string;
  old_state: number | null;
  new_state: number | null;
  mode?: string;
  reason?: string | null;
  load_percent?: number | null;
}
