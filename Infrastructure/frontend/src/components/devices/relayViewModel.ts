import type { ControlHistoryEntry } from '../../types/device'
import type { ChannelInfo } from '../../types/relay'

export const RELAY_CHANNEL_COUNT = 16
export const RELAY_MATRIX_ROWS = 8
export const RELAY_MATRIX_BANK_SIZE = 8
export const RELAY_CHANNELS = Array.from({ length: RELAY_CHANNEL_COUNT }, (_, index) => index)

export interface LocationClusterPair {
  location: string
  cluster: string
}

export interface RelayChannelViewModel {
  channel: number
  pinLabel: string
  isStateKnown: boolean
  isActive: boolean
  isAssigned: boolean
  assignedDeviceName: string | null
  deviceName: string | null
  displayType: string | null
  location: string | null
  cluster: string | null
  lastStateChangeAt: string | null
}

export function makeDeviceKey(location: string, cluster: string, deviceName: string): string {
  return `${location}::${cluster}::${deviceName}`
}

export function getRelayPinLabel(channel: number): string {
  if (channel < RELAY_MATRIX_BANK_SIZE) {
    return `GPA${channel}`
  }

  return `GPB${channel - RELAY_MATRIX_BANK_SIZE}`
}

export function getRelaySilkscreenLabel(channel: number): string {
  return `K${channel + 1}`
}

export function splitRelayBanks(channels: RelayChannelViewModel[]): {
  bankA: RelayChannelViewModel[]
  bankB: RelayChannelViewModel[]
} {
  return {
    bankA: channels.slice(0, RELAY_MATRIX_BANK_SIZE),
    bankB: channels.slice(RELAY_MATRIX_BANK_SIZE, RELAY_CHANNEL_COUNT),
  }
}

export function getChannelDisplayType(channel: ChannelInfo): string | null {
  if (!channel.device_type) {
    return null
  }

  return getReadableDeviceType(channel.device_type)
}

export function getChannelDisplayName(channel: ChannelInfo): string | null {
  if (!channel.device_name) {
    return null
  }

  if (channel.device_type === 'light') {
    return channel.light_name || channel.device_name
  }

  return channel.device_name
}

export function getReadableDeviceType(deviceType: string): string {
  if (deviceType === 'co2' || deviceType === 'co2_tank') {
    return 'CO2 Tank'
  }

  const normalized = deviceType.replace(/_/g, ' ')
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

export function getUniqueLocationClusterPairs(channels: ChannelInfo[]): LocationClusterPair[] {
  const uniquePairs = new Map<string, LocationClusterPair>()

  channels.forEach((channel) => {
    if (!channel.location || !channel.cluster) {
      return
    }

    const key = `${channel.location}::${channel.cluster}`
    if (!uniquePairs.has(key)) {
      uniquePairs.set(key, { location: channel.location, cluster: channel.cluster })
    }
  })

  return Array.from(uniquePairs.values())
}

export function buildLastStateChangeMap(
  historyEntries: ControlHistoryEntry[]
): Record<string, string> {
  const byDevice: Record<string, string> = {}

  historyEntries.forEach((entry) => {
    if (!entry.location || !entry.cluster || !entry.device_name || !entry.timestamp) {
      return
    }

    const key = makeDeviceKey(entry.location, entry.cluster, entry.device_name)
    const currentTimestamp = Date.parse(entry.timestamp)
    if (Number.isNaN(currentTimestamp)) {
      return
    }

    const previousTimestamp = byDevice[key] ? Date.parse(byDevice[key]) : Number.NEGATIVE_INFINITY
    if (currentTimestamp > previousTimestamp) {
      byDevice[key] = entry.timestamp
    }
  })

  return byDevice
}

export function buildRelayChannelViewModels(
  channels: ChannelInfo[],
  relayStates: boolean[] | null,
  lastStateChangeByDevice: Record<string, string>
): RelayChannelViewModel[] {
  const byChannel = new Map<number, ChannelInfo>(
    channels.map((channelInfo) => [channelInfo.channel, channelInfo])
  )

  return RELAY_CHANNELS.map((channelNumber) => {
    const channelInfo = byChannel.get(channelNumber)
    const deviceKey =
      channelInfo?.location && channelInfo.cluster && channelInfo.device_name
        ? makeDeviceKey(channelInfo.location, channelInfo.cluster, channelInfo.device_name)
        : null
    const lastStateChangeAt = deviceKey ? lastStateChangeByDevice[deviceKey] || null : null

    return {
      channel: channelNumber,
      pinLabel: getRelayPinLabel(channelNumber),
      isStateKnown: Array.isArray(relayStates) && channelNumber < relayStates.length,
      isActive: Array.isArray(relayStates) && channelNumber < relayStates.length
        ? Boolean(relayStates[channelNumber])
        : false,
      isAssigned: Boolean(channelInfo?.device_name),
      assignedDeviceName: channelInfo?.device_name || null,
      deviceName: channelInfo ? getChannelDisplayName(channelInfo) : null,
      displayType: channelInfo ? getChannelDisplayType(channelInfo) : null,
      location: channelInfo?.location || null,
      cluster: channelInfo?.cluster || null,
      lastStateChangeAt,
    }
  })
}

export function formatElapsedSince(
  timestamp: string | null,
  nowMs: number
): string {
  if (!timestamp) {
    return 'Unknown'
  }

  const parsed = Date.parse(timestamp)
  if (Number.isNaN(parsed)) {
    return 'Unknown'
  }

  const elapsedMs = Math.max(0, nowMs - parsed)
  const elapsedSeconds = Math.floor(elapsedMs / 1000)

  const days = Math.floor(elapsedSeconds / 86400)
  const hours = Math.floor((elapsedSeconds % 86400) / 3600)
  const minutes = Math.floor((elapsedSeconds % 3600) / 60)
  const seconds = elapsedSeconds % 60

  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`
  }

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`
  }

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`
  }

  return `${seconds}s`
}

