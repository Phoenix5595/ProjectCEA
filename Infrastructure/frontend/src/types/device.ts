/** Types for device data. */

export interface Device {
  location: string;
  cluster: string;
  device_name: string;
  state: number;  // 0 = OFF, 1 = ON
  mode: string;   // 'auto', 'manual', 'scheduled'
  channel: number;
  load_percent?: number;
}

export interface DeviceState {
  state: number;
  mode: string;
  channel: number;
  load_percent?: number;
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

