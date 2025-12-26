/** Types for device data. */

export interface Device {
  location: string;
  cluster: string;
  device_name: string;
  state: number;  // 0 = OFF, 1 = ON
  mode: string;   // 'auto', 'manual', 'scheduled'
  channel: number;
}

export interface DeviceState {
  state: number;
  mode: string;
  channel: number;
}

