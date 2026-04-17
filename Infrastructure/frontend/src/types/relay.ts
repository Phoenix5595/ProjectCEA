export const DEVICE_TYPES = [
  'heater',
  'dehumidifier',
  'extraction fan',
  'fan',
  'humidifier',
  'co2 tank',
  'light',
] as const

export type DeviceTypeOption = (typeof DEVICE_TYPES)[number]

export interface ChannelInfo {
  channel: number
  device_name: string | null
  display_name: string | null
  device_type: string | null
  location: string | null
  cluster: string | null
  light_name: string | null
}

export interface LightNameOption {
  name: string
  device_name: string
  location: string
  cluster: string
}

export interface RelayChannelsResponse {
  channels: Record<string, ChannelInfo>
  light_names: LightNameOption[]
}

export interface RelayBoardStateResponse {
  channels: boolean[]
  mcp_connected: boolean
  simulation: boolean
}

