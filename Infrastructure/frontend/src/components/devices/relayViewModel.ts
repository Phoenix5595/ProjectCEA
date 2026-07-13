import type { ControlHistoryEntry } from '../../types/device'
import type { ChannelInfo } from '../../types/relay'

export const RELAY_CHANNEL_COUNT = 16
export const RELAY_MATRIX_ROWS = 8
export const RELAY_MATRIX_BANK_SIZE = 8
export const RELAY_CHANNELS = Array.from({ length: RELAY_CHANNEL_COUNT }, (_, index) => index)

/**
 * Physical relay → code channel map.
 * Source: .omo/evidence/task-3-relay-mcp-bugfix.md — User bench verification (2026-06-29).
 * Port A (ch0-7) drives bottom row left→right; Port B (ch8-15) drives top row right→left.
 *
 * Mapping confirmed by direct observation (user toggled channels, watched physical relays):
 * relay  1=ch15,  2=ch0,  3=ch14,  4=ch1,  5=ch13,  6=ch2,  7=ch12,  8=ch3,
 * relay  9=ch11, 10=ch4, 11=ch10, 12=ch5, 13=ch9,  14=ch6,  15=ch8,  16=ch7
 */
export const RELAY_TO_CHANNEL: Readonly<Record<number, number>> = {
  1: 15, 2: 0, 3: 14, 4: 1, 5: 13, 6: 2, 7: 12, 8: 3,
  9: 11, 10: 4, 11: 10, 12: 5, 13: 9, 14: 6, 15: 8, 16: 7,
}

export const CHANNEL_TO_RELAY: Readonly<Record<number, number>> = Object.entries(RELAY_TO_CHANNEL).reduce(
  (acc, [relay, channel]) => {
    acc[channel] = Number(relay)
    return acc
  },
  {} as Record<number, number>
)

export function getRelayNumber(channel: number): number {
  return CHANNEL_TO_RELAY[channel] ?? channel + 1
}

/**
 * Split a 16-channel view-model array into two physical layout columns for matrix rendering.
 * - leftColumn[0..7]  = physical relays 1→8 (top→bottom)
 * - rightColumn[0..7] = physical relays 16→9 (top→bottom)
 *
 * Each entry is the RelayChannelViewModel whose channel matches RELAY_TO_CHANNEL[relay#].
 * Source: .omo/evidence/task-3-relay-mcp-bugfix.md — Port A drives the bottom row left→right,
 * Port B drives the top row right→left, producing the reversed wiring pattern.
 */
export function splitRelayByPhysicalLayout(
  channels: RelayChannelViewModel[]
): { leftColumn: RelayChannelViewModel[]; rightColumn: RelayChannelViewModel[] } {
  const byChannel = new Map<number, RelayChannelViewModel>(channels.map((vm) => [vm.channel, vm]))

  const leftColumn = [1, 2, 3, 4, 5, 6, 7, 8].map(
    (relay) => byChannel.get(RELAY_TO_CHANNEL[relay])!
  )
  const rightColumn = [16, 15, 14, 13, 12, 11, 10, 9].map(
    (relay) => byChannel.get(RELAY_TO_CHANNEL[relay])!
  )

  return { leftColumn, rightColumn }
}

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
  mode: string | null
  overrideExpiresAt: string | null
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
  // Prefer display_name (human-readable) for all devices, not just lights
  if (channel.display_name) {
    return channel.display_name
  }
  // Lights: fall back to light_name then device_name
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
  timestamps: (string | null)[],
  modes: (string | null)[],
  overrideExpiresAt: (string | null)[]
): RelayChannelViewModel[] {
  const byChannel = new Map<number, ChannelInfo>(
    channels.map((channelInfo) => [channelInfo.channel, channelInfo])
  )

  return RELAY_CHANNELS.map((channelNumber) => {
    const channelInfo = byChannel.get(channelNumber)

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
      lastStateChangeAt: timestamps[channelNumber] ?? null,
      mode: modes[channelNumber] ?? null,
      overrideExpiresAt: overrideExpiresAt[channelNumber] ?? null,
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

export function formatCountdown(expiresAt: string | null, nowMs: number): string {
  if (!expiresAt) return ''
  const expires = Date.parse(expiresAt)
  if (Number.isNaN(expires)) return ''
  const remainingMs = expires - nowMs
  if (remainingMs <= 0) return ''
  const totalSeconds = Math.floor(remainingMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

